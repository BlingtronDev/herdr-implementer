#!/usr/bin/env python3
"""Manual post-migration smoke for ticket 09 (evidence 09).

The coordinator chooses every action, performs the merges, and decides closeout.
There is no scheduler and no automatic sequence.

Usage (HERDR_ENV=1):
  export HPM_SMOKE_PROVIDER=... HPM_SMOKE_MODEL=... HPM_SMOKE_THINKING=...
  python3 smoke.py setup
  python3 smoke.py <pi|opencode> init
  python3 smoke.py <pi|opencode> start-a
  python3 smoke.py <pi|opencode> start-b
  python3 smoke.py <pi|opencode> quota-check   # rerun while A shows handing-off to record the replacement slot
  python3 smoke.py <pi|opencode> wait
  python3 smoke.py <pi|opencode> ack-a
  python3 smoke.py <pi|opencode> merge-a
  python3 smoke.py <pi|opencode> capture-a
  python3 smoke.py <pi|opencode> start-c
  python3 smoke.py <pi|opencode> release-b
  python3 smoke.py <pi|opencode> wait-final
  python3 smoke.py <pi|opencode> merge-final
  python3 smoke.py <pi|opencode> capture-final
  python3 smoke.py <pi|opencode> finish
  python3 smoke.py <pi|opencode> stop-a
  python3 smoke.py <pi|opencode> cleanup-a
  python3 smoke.py <pi|opencode> verify-cleanup-a  # read-only re-check of a completed cleanup
  python3 smoke.py verify [pi|opencode]            # offline re-check; defaults to both runtimes

Run each runtime in its own disposable repository. The per-run cap is 2; keep
the total across concurrent runs within the authorized aggregate cap. The
handoff threshold defaults to 35000 tokens and may be set with
HPM_SMOKE_HANDOFF_TOKENS. `verify-cleanup-a` never reruns the refusal, archive,
or deletion phases: it validates an already-successful cleanup from durable
facts so the original scene and logs stay intact. `wait-final` observes B and C
inside a bounded wall-clock window (HPM_SMOKE_FINAL_WINDOW seconds, default
180) instead of retrying a fixed number of times. Logs are append-only: an
existing label gets a numeric suffix instead of being overwritten.
"""

from __future__ import annotations

import ast
import datetime as dt
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent


def find_project(start: Path) -> Path:
    for candidate in [start, *start.parents]:
        if (candidate / "bin" / "plan_manager.py").is_file():
            return candidate
    return start.parents[min(3, len(start.parents) - 1)]


PROJECT = find_project(HERE)
HPM = PROJECT / "bin" / "plan_manager.py"
META = HERE / "location.json"
OPENCODE_DB = Path.home() / ".local/share/opencode/opencode.db"
DISPOSABLE_PARENT = Path("/tmp/opencode")
CORPUS_ROWS = 2400
MAX_WORKERS = 2  # per-run cap; the authorized aggregate cap stays a coordinator decision
HANDOFF_SECTIONS = ("Progress", "Decisions", "Verification", "Commits", "Uncommitted work", "Next steps")
INPUT_KEYS = ("path", "filePath", "file_path", "command", "pattern", "query", "url", "description", "skill")


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def call(argv: list[str], cwd: Path | None = None) -> dict:
    proc = subprocess.run([str(item) for item in argv], cwd=cwd, text=True, capture_output=True)
    return {
        "argv": [str(item) for item in argv],
        "cwd": str(cwd) if cwd else None,
        "exit_code": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def save(label: str, value: object, log_dir: Path | None = None) -> None:
    log = log_dir or LOG
    path = log / f"{label}.json"
    index = 2
    while path.exists():
        path = log / f"{label}-{index}.json"
        index += 1
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def latest(label: str, log_dir: Path | None = None) -> dict:
    """Read the newest capture for a label; numeric suffixes sort numerically."""
    log = log_dir or LOG
    candidates: list[tuple[int, Path]] = []
    plain = log / f"{label}.json"
    if plain.is_file():
        candidates.append((0, plain))
    for path in log.glob(f"{label}-*.json"):
        suffix = path.name[len(label) + 1 : -5]
        if suffix.isdigit():
            candidates.append((int(suffix), path))
    assert_state(bool(candidates), f"missing smoke capture: {label}")
    return json.loads(max(candidates)[1].read_text(encoding="utf-8"))


def save_call(label: str, argv: list[str], cwd: Path | None = None, *, expect: int | None = 0) -> dict:
    result = call(argv, cwd)
    save(label, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if expect is not None and result["exit_code"] != expect:
        raise SystemExit(f"{label}: expected exit {expect}, got {result['exit_code']}")
    return result


def parse_json_streams(result: dict) -> dict | None:
    """Find the JSON payload in whichever stream carries it (stdout first)."""
    for stream in ("stdout", "stderr"):
        text = (result.get(stream) or "").strip()
        if text.startswith(("{", "[")):
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                continue
    return None


def pm(label: str, *args: str, expect: int | None = 0) -> dict:
    result = save_call(label, [sys.executable, HPM, args[0], "--repo", REPO, *args[1:]], expect=expect)
    result["payload"] = parse_json_streams(result)
    return result


def git(*args: str) -> str:
    result = call(["git", *args], REPO)
    if result["exit_code"]:
        raise RuntimeError(result)
    return result["stdout"].strip()


def facts(role: str) -> dict:
    return pm(f"status-{role}", "status", "--worker", WORKER[role])["payload"]


def assert_state(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"assertion failed: {message}")


def setup() -> None:
    assert_state(os.environ.get("HERDR_ENV") == "1", "HERDR_ENV=1 is required")
    assert_state(not META.exists(), "location.json exists; preserve prior smoke evidence")
    DISPOSABLE_PARENT.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="hpm09-migration-", dir=DISPOSABLE_PARENT))
    for kind in ("pi", "opencode"):
        repo = root / kind
        repo.mkdir()
        assert_state(call(["git", "init", "-b", "main"], repo)["exit_code"] == 0, "git init failed")
        (repo / ".gitignore").write_text(".scratch/\n", encoding="utf-8")
        (repo / "README.md").write_text(
            "# Issue 09 post-migration smoke\nNormal integration policy: fast-forward when possible, otherwise a normal merge.\n",
            encoding="utf-8",
        )
        assert_state(call(["git", "add", "."], repo)["exit_code"] == 0, "git add failed")
        commit = call(["git", "commit", "-m", "initialize smoke fixture"], repo)
        assert_state(commit["exit_code"] == 0, f"git commit failed (inherited Git identity required): {commit['stderr']}")
        material = repo / ".scratch"
        material.mkdir()
        (material / "spec.md").write_text(
            """# Post-migration end-to-end smoke

Complete A, B and C with independent workers. A and B have no code dependency; C depends only on A integrated into main.

- A: after an automatic session handoff, commit api.txt containing exactly `HPM09_API_V1\\n` and resumed.txt containing exactly `HPM09_RESUMED_OK\\n`. Leave seed-note.txt uncommitted on purpose.
- B: commit b.txt containing exactly `HPM09_B_OK\\n` after the coordinator checkpoint appears.
- C: read the integrated api.txt and commit consumer.txt containing exactly `consumer:HPM09_API_V1\\n`.

Whole goal: all four outputs integrated into main with exact contents, automatic handoff, dynamic C ordering, dirty-A retention and archive, clean-C automatic tab release, and B completion.
Do not spawn subagents. This is a tiny fixture; use your own tools. Do not implement a scheduler.
""",
            encoding="utf-8",
        )
        gate = repo / ".git" / "release-b"
        (material / "B.md").write_text(
            f"""# Ticket B (no dependency)

Create b.txt containing exactly `HPM09_B_OK` plus newline, test the exact contents, and commit it.
Acceptance includes observing the coordinator checkpoint at absolute path `{gate}` before declaring delivered.
This is a controlled concurrency experiment, not a user prompt. Remain working while that file is absent.
Use a foreground Python loop that only reads gate existence and sleeps 1 second, with no total task deadline.
If your tool window expires, continue checking; only the actual file releases this checkpoint.
When present, verify b.txt again and write your contract result. Never create or change the gate yourself.
Keep your worktree clean: commit only b.txt and do not leave uncommitted files.
""",
            encoding="utf-8",
        )
        (material / "A.md").write_text(
            """# Ticket A (no dependency)

This ticket's acceptance tests automatic context handoff with meaningful remaining work.

INITIAL SESSION ONLY: read corpus.txt from the materials snapshot in chunks of at most 400 lines to bring corpus rows into your model context (not a summary or hash). Stop after 2400 rows or when the tool requests handoff, whichever comes first.
After at least one chunk, save uncommitted seed-note.txt saying `seed prepared before handoff` in your worktree.
Do NOT create api.txt or declare delivered in the initial session. After reading, keep a foreground Python sleep loop running until the lifecycle tool interrupts it and asks for handoff. This pause is the controlled handoff checkpoint, not a need for user input.
Follow the tool's handoff instructions. In the handoff Next steps, include the exact token HPM09_RESUMED_OK and say the next session must NOT reread corpus, must create api.txt with HPM09_API_V1 plus newline, and must create resumed.txt with HPM09_RESUMED_OK plus newline as proof of reading this handoff.
CONTINUATION SESSION (when given a previous-session handoff by the tool): immediately perform those Next steps, assert exact api.txt and resumed.txt contents, commit ONLY api.txt and resumed.txt, leave seed-note.txt uncommitted on purpose, and deliver. Report the uncommitted note in remaining. Do not return to the initial-session corpus or checkpoint.
""",
            encoding="utf-8",
        )
        (material / "C.md").write_text(
            """# Ticket C (depends on A integrated)

Read api.txt inherited from your base; assert it equals `HPM09_API_V1` plus newline.
Write consumer.txt as `consumer:` plus the content of api.txt; assert exact `consumer:HPM09_API_V1` plus newline.
Commit consumer.txt only and deliver with verification evidence. Do not modify api.txt or fabricate it if missing.
Keep your worktree clean: only consumer.txt is committed.
""",
            encoding="utf-8",
        )
        (material / "corpus.txt").write_text(
            "".join(
                f"row {i:04d}: record={i*7919:08x} test-vector {i*37:08d} alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike november oscar papa quebec romeo sierra tango uniform victor whiskey xray yankee zulu\n"
                for i in range(CORPUS_ROWS)
            ),
            encoding="utf-8",
        )
    META.write_text(json.dumps({"root": str(root), "created_at": now()}, indent=2), encoding="utf-8")
    print(root)


def smoke_config() -> dict:
    missing = [name for name in ("HPM_SMOKE_PROVIDER", "HPM_SMOKE_MODEL", "HPM_SMOKE_THINKING") if not os.environ.get(name)]
    assert_state(not missing, f"confirm and export the runtime configuration first: {missing}")
    return {
        "provider": os.environ["HPM_SMOKE_PROVIDER"],
        "model": os.environ["HPM_SMOKE_MODEL"],
        "thinking": os.environ["HPM_SMOKE_THINKING"],
        "handoff_tokens": os.environ.get("HPM_SMOKE_HANDOFF_TOKENS", "35000"),
    }


def sanitize_input(value: object) -> dict:
    if not isinstance(value, dict):
        return {"raw": str(value)[:200]} if value else {}
    result = {key: value[key] for key in INPUT_KEYS if isinstance(value.get(key), (str, int, float))}
    return result


def normalize_time(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value) / 1000.0 if value > 1e11 else float(value)
    if isinstance(value, str):
        try:
            return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def pi_events(path: Path, worker: str, session: int) -> tuple[list[dict], list[dict]]:
    events: list[dict] = []
    tools: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = event.get("type")
        if kind == "session":
            events.append({"type": "session", "cwd": event.get("cwd")})
        elif kind == "model_change":
            events.append({"type": "model_change", "provider": event.get("provider"), "modelId": event.get("modelId")})
        elif kind == "thinking_level_change":
            events.append({"type": "thinking_level_change", "thinkingLevel": event.get("thinkingLevel")})
        elif kind == "message" and (event.get("message") or {}).get("role") == "assistant":
            for part in (event["message"].get("content") or []):
                if isinstance(part, dict) and part.get("type") == "toolCall":
                    arguments = part.get("arguments")
                    if isinstance(arguments, str):
                        try:
                            arguments = ast.literal_eval(arguments)
                        except (ValueError, SyntaxError):
                            arguments = {"raw": arguments[:200]}
                    tools.append({
                        "worker": worker,
                        "session": session,
                        "started": event.get("timestamp"),
                        "completed": event.get("timestamp"),
                        "tool": part.get("name"),
                        "input": sanitize_input(arguments),
                    })
    return events, tools


def opencode_messages(ref: str, worker: str, session: int) -> tuple[list[dict], list[dict]]:
    messages: list[dict] = []
    tools: list[dict] = []
    with sqlite3.connect(f"{OPENCODE_DB.resolve().as_uri()}?mode=ro", uri=True, timeout=10) as conn:
        for (raw,) in conn.execute("SELECT data FROM message WHERE session_id = ? ORDER BY time_created", (ref,)):
            data = json.loads(raw)
            if data.get("role") == "assistant":
                messages.append({key: data.get(key) for key in ("providerID", "modelID", "variant", "agent", "path", "time")})
        for raw, created in conn.execute("SELECT data, time_created FROM part WHERE session_id = ? ORDER BY time_created", (ref,)):
            part = json.loads(raw)
            if part.get("type") != "tool":
                continue
            state = part.get("state") if isinstance(part.get("state"), dict) else {}
            timestamps = state.get("time") if isinstance(state.get("time"), dict) else {}
            tools.append({
                "worker": worker,
                "session": session,
                "started": timestamps.get("start", created),
                "completed": timestamps.get("end", timestamps.get("completed", created)),
                "tool": part.get("tool"),
                "status": state.get("status"),
                "input": sanitize_input(state.get("input")),
            })
    return messages, tools


def activity_windows(activity: list[dict], worker: str, session: int) -> tuple[float | None, float | None]:
    entries = [entry for entry in activity if entry.get("worker") == worker and entry.get("session") == session]
    starts = [value for value in (normalize_time(entry.get("started")) for entry in entries) if value is not None]
    ends = [value for value in (normalize_time(entry.get("completed")) for entry in entries) if value is not None]
    return (min(starts) if starts else None, max(ends) if ends else None)


def read_handoff_paths(activity: list[dict], session: int) -> list[str]:
    found: list[str] = []
    for entry in activity:
        if entry.get("session") != session:
            continue
        for value in (entry.get("input") or {}).values():
            if isinstance(value, str) and "handoff" in value and value.endswith(".md"):
                found.append(value)
    return found


def runtime_config_failures(kind: str, configs: list[dict], config: dict, expected_cwd: str, label: str | None = None) -> list[str]:
    """Compare runtime-produced configuration and cwd with the confirmed run configuration."""
    prefix = label or kind
    failures: list[str] = []
    cwds: set = set()
    if kind == "pi":
        profiles = {
            tuple(event.get(key) for key in ("provider", "modelId"))
            for entry in configs
            for event in entry["events"]
            if event.get("type") == "model_change"
        }
        levels = {event.get("thinkingLevel") for entry in configs for event in entry["events"] if event.get("type") == "thinking_level_change"}
        cwds = {event.get("cwd") for entry in configs for event in entry["events"] if event.get("type") == "session"}
        if profiles != {(config["provider"], config["model"])}:
            failures.append(f"{prefix}: effective provider/model {profiles} != confirmed configuration")
        if levels != {config["thinking"]}:
            failures.append(f"{prefix}: effective thinking {levels} != confirmed configuration")
    else:
        profiles = {
            tuple(message.get(key) for key in ("providerID", "modelID", "variant"))
            for entry in configs
            for message in entry["events"]
        }
        cwds = {
            message.get("path", {}).get("cwd")
            for entry in configs
            for message in entry["events"]
            if isinstance(message.get("path"), dict)
        }
        if profiles != {(config["provider"], config["model"], config["thinking"])}:
            failures.append(f"{prefix}: effective configuration {profiles} != confirmed configuration")
    if cwds != {expected_cwd}:
        failures.append(f"{prefix}: session cwd {cwds} != worktree {expected_cwd}")
    return failures


def check_runtime(kind: str, worker: str, configs: list[dict], config: dict, expected_cwd: str) -> None:
    """Verify effective provider/model/thinking and cwd without requiring a handoff."""
    failures = runtime_config_failures(kind, configs, config, expected_cwd, label=f"{kind}:{worker}")
    assert_state(not failures, "; ".join(failures))


def check_handoff(kind: str, fact: dict, captured: dict, config: dict, repo: Path, worktree: str | None = None) -> None:
    """Pure verification of a completed A handoff from captured runtime facts."""
    sessions = fact.get("sessions") or []
    worker = fact.get("worker_id")
    expected_cwd = str(worktree or fact.get("worktree"))
    assert_state(len(sessions) >= 2, f"{worker} has no replacement session: {len(sessions)}")
    assert_state(sessions[0].get("end_state") == "replaced", f"{worker} old session end_state is {sessions[0].get('end_state')}")
    handoffs = sorted((Path(fact["paths"]["management"]) / "handoffs").glob("*.md"))
    assert_state(bool(handoffs), f"{worker} has no durable handoff document")
    text = handoffs[0].read_text(encoding="utf-8")
    for section in HANDOFF_SECTIONS:
        assert_state(section.lower() in text.lower(), f"handoff document lacks {section}")
    head = fact["result"]["head"]
    assert_state(call(["git", "cat-file", "-e", f"{head}:resumed.txt"], repo)["exit_code"] == 0, "resumed.txt is not committed at the delivered HEAD")
    assert_state(any(handoffs[0].name in path for path in read_handoff_paths(captured["activity"], 2)), "the replacement session did not read the handoff document")
    failures = runtime_config_failures(kind, captured["configs"], config, expected_cwd)
    assert_state(not failures, failures[0] if failures else "runtime configuration mismatch")
    old_start, old_end = activity_windows(captured["activity"], worker, 1)
    new_start, new_end = activity_windows(captured["activity"], worker, 2)
    assert_state(old_end is not None and new_start is not None, "tool activity is missing; cannot prove write ordering")
    assert_state(old_end < new_start, f"old session activity {old_end} does not precede new session activity {new_start}")


def capture_worker_files(role: str, worker: str) -> list[str]:
    fact = facts(role)
    worker_dir = Path(fact["paths"]["management"])
    target = LOG / "captured" / worker
    target.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for relative in ("state.json", "contract.md", "result.json", "cleanup.json", "ack.json", "supervisor.log", "materials/manifest.json"):
        source = worker_dir / relative
        if source.is_file():
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            copied.append(str(destination.relative_to(HERE)))
    handoff_dir = worker_dir / "handoffs"
    if handoff_dir.is_dir():
        for handoff in sorted(handoff_dir.glob("*.md")):
            destination = target / "handoffs" / handoff.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(handoff, destination)
            copied.append(str(destination.relative_to(HERE)))
    save(f"captured-files-{role}", {"worker": worker, "copied": copied})
    return copied


def capture_runtime(role: str) -> dict:
    fact = facts(role)
    worker = WORKER[role]
    configs: list[dict] = []
    activity: list[dict] = []
    for session in fact.get("sessions") or []:
        index = session.get("index")
        ref = session.get("context_ref")
        if KIND == "pi":
            events, tools = pi_events(Path(ref), worker, index)
        else:
            events, tools = opencode_messages(ref, worker, index)
        configs.append({"worker": worker, "session": index, "ref": ref, "events": events})
        activity.extend(tools)
    save(f"runtime-config-{role}", configs)
    save(f"tool-activity-{role}", activity)
    return {"configs": configs, "activity": activity}


def recover_runtime_configs(kind: str, state: dict) -> list[dict]:
    """Rebuild runtime config captures from a worker state's recorded session refs.

    Used when an earlier smoke run did not save `runtime-config-<role>.json`; the
    events still come from the runtime-produced session log or database, so this
    stays runtime evidence instead of a status-only restatement.
    """
    worker = state.get("worker_id")
    configs: list[dict] = []
    for session in state.get("sessions") or []:
        index = session.get("index")
        ref = session.get("context_ref")
        if kind == "pi":
            events, _ = pi_events(Path(ref), worker, index)
        else:
            events, _ = opencode_messages(ref, worker, index)
        configs.append({"worker": worker, "session": index, "ref": ref, "events": events, "recovered": True})
    return configs


def runtime_configs_for(role: str, kind: str, state: dict) -> list[dict]:
    """Prefer the live runtime capture; otherwise recover it from the session refs."""
    capture = LOG / f"runtime-config-{role}.json"
    if capture.is_file():
        return json.loads(capture.read_text(encoding="utf-8"))
    configs = recover_runtime_configs(kind, state)
    save(f"runtime-config-{role}-recovered", configs)
    return configs


def settled_failures(role: str, state: dict, release: dict) -> list[str]:
    """A worker is settled only when its lifecycle, sessions, and tab release are terminal."""
    failures: list[str] = []
    lifecycle = (state.get("lifecycle") or {}).get("state")
    if lifecycle != "delivered":
        failures.append(f"{role}: lifecycle {lifecycle!r} is not delivered")
    for session in state.get("sessions") or []:
        if session.get("end_state") not in ("delivered", "replaced") and session.get("status") not in ("ended", "replaced"):
            failures.append(f"{role}: session {session.get('index')} is {session.get('status')!r}/{session.get('end_state')!r}")
    if (release or {}).get("state") not in ("closed", "retained"):
        failures.append(f"{role}: release state {(release or {}).get('state')!r} is not terminal")
    return failures


def load_cleanup_record(fact: dict) -> dict:
    """Read the raw worker cleanup.json; `status --worker` omits the archive details."""
    path = Path(fact["paths"]["management"]) / "cleanup.json"
    assert_state(path.is_file(), f"missing raw cleanup record: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def cleanup_evidence_failures(cleanup: dict, record: dict, archive: Path | None = None) -> list[str]:
    """Validate a completed cleanup from status facts plus the raw cleanup.json record.

    `status --worker` exposes `cleaned`, `last_attempt`, `blockers`, and resources
    only; the archive path lives in the raw record as `last_archive` (and in the
    cleanup command payload), not as `status.cleanup.archive`.
    """
    failures: list[str] = []
    if cleanup.get("cleaned") is not True:
        failures.append("status cleanup.cleaned is not true")
    attempt = cleanup.get("last_attempt") or {}
    if attempt.get("outcome") != "removed":
        failures.append(f"status last_attempt.outcome is {attempt.get('outcome')!r}, not 'removed'")
    if (attempt.get("removed") or {}).get("worktree") is not True:
        failures.append("status last_attempt removed no worktree")
    if cleanup.get("blockers"):
        failures.append(f"status cleanup still reports blockers: {cleanup['blockers']}")
    resources = cleanup.get("resources") or {}
    if (resources.get("worktree") or {}).get("exists") is not False:
        failures.append("status still reports the worker worktree present")
    if (resources.get("branch") or {}).get("exists") is not True:
        failures.append("status no longer reports the worker branch retained")
    if any(entry.get("agent_live") for entry in resources.get("sessions") or []):
        failures.append("status still reports a live registered session")
    if not record.get("decision"):
        failures.append("raw cleanup.json has no decision")
    if not record.get("worktree_removed_at"):
        failures.append("raw cleanup.json has no worktree_removed_at")
    if not record.get("completed_at"):
        failures.append("raw cleanup.json has no completed_at")
    archive_record = record.get("last_archive")
    if not isinstance(archive_record, dict) or not archive_record.get("path"):
        failures.append("raw cleanup.json has no last_archive.path")
        return failures
    root = Path(archive_record["path"])
    if archive is not None and root != Path(archive):
        failures.append(f"raw last_archive.path {root} != cleanup command archive {archive}")
    if not root.is_dir():
        failures.append(f"archive directory is missing: {root}")
    else:
        notes = list(root.rglob("seed-note.txt"))
        if not notes:
            failures.append(f"seed-note.txt was not archived under {root}")
        elif "seed prepared before handoff" not in notes[0].read_text(encoding="utf-8"):
            failures.append("archived note content changed")
    untracked = {entry.get("path") for entry in archive_record.get("untracked_files") or []}
    if "seed-note.txt" not in untracked:
        failures.append("raw last_archive.untracked_files does not list seed-note.txt")
    return failures


def integration(role: str) -> dict:
    return latest(f"integration-{role}")


def merge(role: str) -> dict:
    fact = facts(role)
    assert_state(fact["result"] is not None and fact["result"]["status"] == "delivered", f"{role} is not delivered: {fact['result']}")
    assert_state(git("status", "--porcelain") == "", "target checkout is not clean before the merge")
    before = git("rev-parse", "HEAD")
    head = fact["result"]["head"]
    result = call(["git", "merge", "--ff-only", head], REPO)
    if result["exit_code"]:
        result = call(["git", "merge", "--no-edit", head], REPO)
    save(f"merge-{role}", result)
    assert_state(result["exit_code"] == 0, f"merge of {role} failed: {result['stderr']}")
    record = {"role": role, "before": before, "delivery": head, "integrated": git("rev-parse", "HEAD")}
    save(f"integration-{role}", record)
    for item in fact["pending_items"]:
        pm(f"ack-{role}", "ack", "--item", item["item_id"], "--note", f"{role.upper()} integrated")
    return record


def action_init() -> None:
    config = smoke_config()
    pm(
        "init",
        "init-run",
        "--run-id",
        RUN,
        "--kind",
        KIND,
        "--provider",
        config["provider"],
        "--model",
        config["model"],
        "--thinking",
        config["thinking"],
        "--max-workers",
        str(MAX_WORKERS),
    )
    save("smoke-config", config)


def action_start_a() -> None:
    config = smoke_config()
    pm(
        "start-a",
        "start",
        "--run",
        RUN,
        "--ticket-id",
        "A",
        "--worker-id",
        WORKER["a"],
        "--title",
        "post-migration smoke A with handoff and dirty worktree",
        "--base",
        git("rev-parse", "HEAD"),
        "--material",
        str(REPO / ".scratch" / "spec.md"),
        "--material",
        str(REPO / ".scratch" / "A.md"),
        "--material",
        str(REPO / ".scratch" / "corpus.txt"),
        "--handoff-tokens",
        config["handoff_tokens"],
        "--instructions",
        "Read the ticket and spec. Follow the staged acceptance exactly. Do not spawn subagents.",
    )


def action_start_b() -> None:
    pm(
        "start-b",
        "start",
        "--run",
        RUN,
        "--ticket-id",
        "B",
        "--worker-id",
        WORKER["b"],
        "--title",
        "post-migration smoke B independent deliverable",
        "--base",
        git("rev-parse", "HEAD"),
        "--material",
        str(REPO / ".scratch" / "spec.md"),
        "--material",
        str(REPO / ".scratch" / "B.md"),
        "--instructions",
        "Read the ticket and spec. Follow the staged acceptance exactly. Do not spawn subagents.",
    )


def action_quota_check() -> None:
    before = pm("quota-before", "status", "--run", RUN)["payload"]
    assert_state(before["active_count"] == MAX_WORKERS, f"quota check needs both A and B active: {before['active_count']}")
    refusal = pm(
        "quota-refused",
        "start",
        "--run",
        RUN,
        "--ticket-id",
        "Q",
        "--worker-id",
        WORKER["quota"],
        "--title",
        "quota refusal probe",
        "--base",
        git("rev-parse", "HEAD"),
        "--material",
        str(REPO / ".scratch" / "spec.md"),
        expect=2,
    )
    after = pm("quota-after", "status", "--run", RUN)["payload"]
    fact_a = facts("a")
    assert_state(after["active_count"] == MAX_WORKERS, f"quota probe changed the active count: {after['active_count']}")
    assert_state(after["worker_count"] == MAX_WORKERS, f"quota probe registered a third worker: {after['worker_count']}")
    assert_state(not (REPO / ".git" / "herdr-plan-manager" / "workers" / WORKER["quota"]).exists(), "quota probe left a worker record")
    assert_state("concurrency limit" in (refusal["stdout"] + refusal["stderr"]), "refusal does not mention the concurrency limit")
    save("quota-check", {
        "before": before,
        "after": after,
        "refusal": {"exit_code": refusal["exit_code"], "stdout": refusal["stdout"], "stderr": refusal["stderr"]},
        "a_lifecycle": fact_a["lifecycle"]["state"],
        "a_sessions": len(fact_a.get("sessions") or []),
    })


def action_wait() -> None:
    pm("wait", "wait", "--run", RUN, "--timeout", "60", "--poll", "1")


def action_ack_a() -> None:
    before = git("rev-parse", "HEAD")
    fact = facts("a")
    assert_state(fact["result"] is not None and fact["result"]["status"] == "delivered", "A is not delivered yet")
    for item in fact["pending_items"]:
        pm("ack-a", "ack", "--item", item["item_id"], "--note", "A accepted; integration pending")
    assert_state(git("rev-parse", "HEAD") == before, "ack changed the target HEAD")
    assert_state(not (REPO / "api.txt").exists(), "A delivery was treated as integration")
    save("a-before-merge", {"before": before, "delivery": fact["result"]["head"]})


def action_capture_a() -> None:
    captured = capture_runtime("a")
    capture_worker_files("a", WORKER["a"])
    check_handoff(KIND, facts("a"), captured, smoke_config(), REPO)


def action_start_c() -> None:
    record = integration("a")
    pm(
        "start-c",
        "start",
        "--run",
        RUN,
        "--ticket-id",
        "C",
        "--worker-id",
        WORKER["c"],
        "--title",
        "post-migration smoke C consumes integrated A",
        "--base",
        record["integrated"],
        "--material",
        str(REPO / ".scratch" / "spec.md"),
        "--material",
        str(REPO / ".scratch" / "C.md"),
        "--instructions",
        "Read the ticket and spec. Follow the staged acceptance exactly. Do not spawn subagents.",
    )
    save("b-at-release", {"b": facts("b")["herdr"].get("agent_status"), "run": pm("status-run-c", "status", "--run", RUN)["payload"]})


def action_release_b() -> None:
    assert_state(latest("start-c") is not None, "start C before releasing B")
    (REPO / ".git" / "release-b").write_text("C started from integrated A; B may deliver.\n", encoding="utf-8")
    save("release-b-written", {"gate": str(REPO / ".git" / "release-b"), "at": now()})


def wait_window_timeout(remaining: float, cap: float = 30.0) -> float:
    """One wait call stays inside the remaining observation window and never returns zero."""
    return max(1.0, min(cap, remaining))


def action_wait_final() -> None:
    """Observe B and C inside a bounded window without re-reading the same pending item.

    `wait` returns an unacknowledged delivery immediately, so a fixed retry count
    can spin on the same item and then fail the task. This phase keeps a wall-clock
    window, returns a partial observation when an item is pending, and records an
    explicit no-result scene instead of failing when the window expires.
    """
    window = float(os.environ.get("HPM_SMOKE_FINAL_WINDOW", "180"))
    started = time.monotonic()
    deadline = started + window
    attempts: list[dict] = []
    states: dict[str, str | None] = {}
    while time.monotonic() < deadline:
        attempt = len(attempts) + 1
        result = pm(
            f"wait-final-{attempt:02d}",
            "wait",
            "--run",
            RUN,
            "--timeout",
            str(int(wait_window_timeout(deadline - time.monotonic()))),
            "--poll",
            "1",
        )
        payload = result["payload"] or {}
        items = [item.get("item_id") for item in payload.get("items") or [] if isinstance(item, dict)]
        states = {role: (facts(role)["result"] or {}).get("status") for role in ("b", "c")}
        attempts.append({
            "attempt": attempt,
            "timed_out": payload.get("timed_out"),
            "items": items,
            "states": states,
            "elapsed_seconds": round(time.monotonic() - started, 1),
        })
        if all(value == "delivered" for value in states.values()):
            save("wait-final-delivered", {"attempt": attempt, "states": states, "elapsed_seconds": attempts[-1]["elapsed_seconds"]})
            return
        if items:
            save("wait-final-partial", {
                "attempt": attempt,
                "states": states,
                "items": items,
                "elapsed_seconds": attempts[-1]["elapsed_seconds"],
                "note": "an unacknowledged delivery is pending; acknowledge or merge it and rerun wait-final",
            })
            print(json.dumps({"wait-final": "partial", "states": states, "items": items}, ensure_ascii=False))
            return
    save("wait-final-no-result", {
        "states": states,
        "attempts": attempts,
        "window_seconds": window,
        "note": "no confirmed B/C delivery inside the bounded observation window; a wait timeout is not a task failure",
    })
    print(json.dumps({"wait-final": "no-result", "states": states, "window_seconds": window}, ensure_ascii=False))


def action_merge_final() -> None:
    merge("c")
    merge("b")


def action_capture_final() -> None:
    deadline = time.time() + 60
    release: dict[str, dict] = {}
    while time.time() < deadline:
        release = {role: (facts(role).get("release") or {}) for role in ("a", "b", "c")}
        if release["a"].get("state") == "retained" and release["b"].get("state") == "closed" and release["c"].get("state") == "closed":
            break
        time.sleep(2)
    save("release-facts", release)
    assert_state(release["a"]["state"] == "retained" and release["a"]["reason"] == "uncommitted-content", f"A release is not retained for uncommitted content: {release['a']}")
    for role in ("b", "c"):
        assert_state(release[role]["state"] == "closed", f"{role} clean delivery was not released: {release[role]}")
    for role in ("a", "b", "c"):
        capture_runtime(role)
        capture_worker_files(role, WORKER[role])
        fact = facts(role)
        save(f"disk-preserved-{role}", {
            "worktree": fact["worktree"],
            "worktree_exists": Path(fact["worktree"]).is_dir(),
            "result_exists": Path(fact["paths"]["result"]).is_file(),
            "branch": fact["branch"],
            "branch_head": fact["branch_head"],
        })
        assert_state(Path(fact["worktree"]).is_dir(), f"{role} worktree was not preserved")
        assert_state(Path(fact["paths"]["result"]).is_file(), f"{role} result was not preserved")
        assert_state(call(["git", "rev-parse", "--verify", fact["branch"]], REPO)["exit_code"] == 0, f"{role} branch was not preserved")


def action_finish() -> None:
    expected = {
        "api.txt": "HPM09_API_V1\n",
        "resumed.txt": "HPM09_RESUMED_OK\n",
        "consumer.txt": "consumer:HPM09_API_V1\n",
        "b.txt": "HPM09_B_OK\n",
    }
    for name, content in expected.items():
        actual = (REPO / name).read_text(encoding="utf-8")
        assert_state(actual == content, f"{name}: expected {content!r}, got {actual!r}")
    save("combined-verification", {"files": expected, "result": "all four integrated outputs match"})
    save_call("git-log", ["git", "log", "--oneline", "--graph", "--all"], REPO, expect=None)
    final = pm("final-status-run", "status", "--run", RUN)["payload"]
    assert_state(final["active_count"] == 0, f"workers still active: {final['active_worker_ids']}")
    save("goal-closeout", {
        "goal": "all four outputs integrated with exact contents",
        "integrations": {role: integration(role) for role in ("a", "b", "c")},
        "final_run": final,
        "conclusion": "complete on the smoke fixture",
    })


def action_stop_a() -> None:
    result = pm("stop-a", "stop", "--worker", WORKER["a"], "--reason", "smoke complete; preserve scene for archive decision")
    assert_state(result["payload"]["business_stopped"] is True, "A business writes were not confirmed stopped")


def action_cleanup_a() -> None:
    record = integration("a")
    refused = pm("cleanup-a-refused", "cleanup", "--worker", WORKER["a"], "--integrated", record["integrated"], expect=3)
    blocked = parse_json_streams(refused)
    assert_state(blocked is not None, "cleanup refusal produced no JSON payload")
    codes = [blocker.get("code") for blocker in blocked.get("blockers") or []]
    assert_state("uncommitted-content" in codes, f"cleanup blockers do not include uncommitted-content: {codes}")
    save("cleanup-a-refused-blockers", {"codes": codes, "payload": blocked, "stdout": bool(refused["stdout"]), "stderr": bool(refused["stderr"])})
    archived = pm("cleanup-a-archived", "cleanup", "--worker", WORKER["a"], "--integrated", record["integrated"], "--archive-uncommitted")
    archive = Path(archived["payload"]["archive"]["path"])
    seed = list((archive / "untracked").rglob("seed-note.txt"))
    assert_state(bool(seed), f"seed-note.txt was not archived under {archive}")
    assert_state("seed prepared before handoff" in seed[0].read_text(encoding="utf-8"), "archived note content changed")
    assert_state(call(["git", "rev-parse", "--verify", facts("a")["branch"]], REPO)["exit_code"] == 0, "A branch was not retained")
    assert_state(Path(facts("a")["paths"]["result"]).is_file(), "A result was not retained")
    # Refresh the durable capture after cleanup so verified state is the final one.
    capture_worker_files("a", WORKER["a"])
    target = LOG / "captured" / WORKER["a"] / "cleanup-archive"
    target.mkdir(parents=True, exist_ok=True)
    for relative in ("manifest.json",):
        source = archive / relative
        if source.is_file():
            shutil.copy2(source, target / relative)
    if seed:
        shutil.copy2(seed[0], target / "seed-note.txt")
    state = facts("a")
    durable = load_cleanup_record(state)
    save("cleanup-a-state", {"cleanup": state["cleanup"], "release": state.get("release"), "record": durable})
    # `status --worker` reports cleanup.cleaned/last_attempt but has no `archive`
    # field; the archive path is `last_archive` in the raw record and the command
    # payload. Assert the real schema instead of the earlier fixture assumption.
    failures = cleanup_evidence_failures(state["cleanup"], durable, archive=archive)
    failures.extend(settled_failures("a", state, state.get("release") or {}))
    assert_state(not failures, "cleanup evidence mismatch: " + "; ".join(failures))


def action_verify_cleanup_a() -> None:
    """Read-only recovery: validate an already-successful cleanup without rerunning it.

    `cleanup-a` removes the worktree; rerunning its refusal/archive phases would
    change the scene and duplicate logs. This phase re-reads durable facts only,
    so the coordinator can accept an existing successful cleanup.
    """
    state = facts("a")
    durable = load_cleanup_record(state)
    failures = cleanup_evidence_failures(state["cleanup"], durable)
    failures.extend(settled_failures("a", state, state.get("release") or {}))
    save("cleanup-a-verified", {
        "worker": WORKER["a"],
        "cleanup": state["cleanup"],
        "record": durable,
        "release": state.get("release"),
        "failures": failures,
    })
    assert_state(not failures, "cleanup verification failed: " + "; ".join(failures))
    print(json.dumps({"verified": WORKER["a"], "archive": (durable.get("last_archive") or {}).get("path")}, ensure_ascii=False))


def verify(kinds: tuple[str, ...] | None = None) -> None:
    root = Path(json.loads(META.read_text(encoding="utf-8"))["root"])
    (HERE / "logs").mkdir(parents=True, exist_ok=True)
    summaries = []
    for kind in (kinds or ("pi", "opencode")):
        global LOG
        LOG = HERE / "logs" / f"real-{kind}"
        worker_a = f"w09-{kind}-a"
        worker_b = f"w09-{kind}-b"
        worker_c = f"w09-{kind}-c"
        repo = root / kind
        config = latest("smoke-config")
        init = latest("init")
        run = json.loads(init["stdout"])
        assert_state(run["max_workers"] == MAX_WORKERS, f"{kind}: max_workers is {run['max_workers']}")
        assert_state(run["runtime"]["kind"] == kind, f"{kind}: run kind is {run['runtime']['kind']}")
        assert_state(run["runtime"]["provider"] == config["provider"] and run["runtime"]["model"] == config["model"] and run["runtime"]["thinking"] == config["thinking"], f"{kind}: registered configuration differs from smoke-config")
        integration_a = latest("integration-a")
        start_c = json.loads(latest("start-c")["stdout"])
        assert_state(start_c["base"] == integration_a["integrated"], f"{kind}: C did not start from integrated A")
        b_at_release = latest("b-at-release")
        assert_state(b_at_release["b"] == "working", f"{kind}: B was not working when C started")
        assert_state(b_at_release["run"]["active_count"] <= MAX_WORKERS, f"{kind}: active count exceeded the cap")
        quota = latest("quota-check")
        assert_state(quota["refusal"]["exit_code"] == 2, f"{kind}: quota refusal exit code")
        assert_state(quota["before"]["active_count"] == MAX_WORKERS and quota["after"]["active_count"] == MAX_WORKERS, f"{kind}: quota probe changed active workers")
        assert_state(quota["after"]["worker_count"] == MAX_WORKERS, f"{kind}: quota probe registered a worker")
        captured_a = LOG / "captured" / worker_a
        state_a = json.loads((captured_a / "state.json").read_text(encoding="utf-8"))
        runtime_a = latest("runtime-config-a")
        check_handoff(kind, state_a, {"configs": runtime_a, "activity": latest("tool-activity-a")}, config, repo)
        # B and C never hand off, but their effective configuration and cwd come
        # from runtime-produced session evidence too; recover it from the recorded
        # session refs when the live run did not save runtime-config-<role>.json.
        runtime_failures: list[str] = []
        runtime_sources: dict[str, str] = {}
        for role, worker in (("b", worker_b), ("c", worker_c)):
            state = json.loads((LOG / "captured" / worker / "state.json").read_text(encoding="utf-8"))
            captured_config = (LOG / f"runtime-config-{role}.json").is_file()
            runtime_sources[role] = "captured" if captured_config else "recovered-from-session-refs"
            configs = runtime_configs_for(role, kind, state)
            runtime_failures.extend(runtime_config_failures(kind, configs, config, state["worktree"], label=f"{kind}:{worker}"))
        assert_state(not runtime_failures, "; ".join(runtime_failures))
        release = latest("release-facts")
        assert_state(release["a"]["state"] == "retained" and release["a"]["reason"] == "uncommitted-content", f"{kind}: dirty A release")
        assert_state(release["b"]["state"] == "closed" and release["c"]["state"] == "closed", f"{kind}: clean release")
        settled: list[str] = []
        for role, worker in (("a", worker_a), ("b", worker_b), ("c", worker_c)):
            state = json.loads((LOG / "captured" / worker / "state.json").read_text(encoding="utf-8"))
            settled.extend(settled_failures(role, state, release.get(role) or {}))
        assert_state(not settled, f"{kind}: active leftovers: {'; '.join(settled)}")
        refused = latest("cleanup-a-refused-blockers")
        assert_state("uncommitted-content" in refused["codes"], f"{kind}: cleanup refusal blockers")
        cleanup_state = latest("cleanup-a-state")
        record = json.loads((captured_a / "cleanup.json").read_text(encoding="utf-8"))
        archived = latest("cleanup-a-archived")
        command_payload = parse_json_streams(archived) or {}
        command_archive = (command_payload.get("archive") or {}).get("path") if isinstance(command_payload, dict) else None
        cleanup_failures = cleanup_evidence_failures(cleanup_state["cleanup"], record)
        if command_archive != (record.get("last_archive") or {}).get("path"):
            cleanup_failures.append(f"cleanup command archive {command_archive!r} != raw cleanup.json last_archive")
        if (record.get("decision") or {}).get("integrated") != integration_a["integrated"]:
            cleanup_failures.append("cleanup decision does not match the A integration SHA")
        assert_state(not cleanup_failures, f"{kind}: cleanup evidence: {'; '.join(cleanup_failures)}")
        goal = latest("goal-closeout")
        assert_state(goal["final_run"]["active_count"] == 0, f"{kind}: workers still active at closeout")
        summaries.append({
            "kind": kind,
            "run": run["run_id"],
            "handoff_sessions": len(runtime_a),
            "runtime_config": runtime_sources,
            "integration_a": integration_a["integrated"],
            "integration_b": latest("integration-b")["integrated"],
            "integration_c": latest("integration-c")["integrated"],
            "release": {role: release[role]["state"] for role in ("a", "b", "c")},
            "repo": str(repo),
        })
    save("verify", summaries, HERE / "logs")
    print(json.dumps({"verified": summaries}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    if action == "setup":
        setup()
        raise SystemExit(0)
    if action == "verify":
        verify(tuple(sys.argv[2:]) or None)
        raise SystemExit(0)
    assert_state(os.environ.get("HERDR_ENV") == "1", "HERDR_ENV=1 is required")
    KIND = action
    ACTION = sys.argv[2] if len(sys.argv) > 2 else ""
    assert_state(KIND in ("pi", "opencode"), f"unknown runtime {KIND!r}")
    root = Path(json.loads(META.read_text(encoding="utf-8"))["root"])
    REPO = root / KIND
    LOG = HERE / "logs" / f"real-{KIND}"
    LOG.mkdir(parents=True, exist_ok=True)
    RUN = f"issue09-{KIND}"
    WORKER = {"a": f"w09-{KIND}-a", "b": f"w09-{KIND}-b", "c": f"w09-{KIND}-c", "quota": f"w09-{KIND}-quota"}
    actions = {
        "init": action_init,
        "start-a": action_start_a,
        "start-b": action_start_b,
        "quota-check": action_quota_check,
        "wait": action_wait,
        "ack-a": action_ack_a,
        "merge-a": lambda: save("merge-a-summary", merge("a")),
        "capture-a": action_capture_a,
        "start-c": action_start_c,
        "release-b": action_release_b,
        "wait-final": action_wait_final,
        "merge-final": action_merge_final,
        "capture-final": action_capture_final,
        "finish": action_finish,
        "stop-a": action_stop_a,
        "cleanup-a": action_cleanup_a,
        "verify-cleanup-a": action_verify_cleanup_a,
    }
    assert_state(ACTION in actions, f"unknown action {ACTION!r}; known: {sorted(actions)}")
    actions[ACTION]()
