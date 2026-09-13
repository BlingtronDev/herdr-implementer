#!/usr/bin/env python3
"""Focused offline validation for smoke.py.

Uses mocked captures, captured-schema shapes, and the real read-only OpenCode
database to exercise the driver cases that syntax checks cannot cover:
numeric capture selection, refusal-payload parsing, the pure handoff/runtime
verifier for both runtime schemas, cleanup evidence against the captured
`last_archive` schema, wait-final window semantics, sanitized tool input and
completion times, and a setup sandbox that must not write repository-local Git
identity.

Usage:
  python3 validate_smoke.py              # focused offline checks
  python3 validate_smoke.py --replay [<evidence-dir>]
                                         # re-run smoke.py verify over real captures
"""

from __future__ import annotations

import importlib.util
import contextlib
import io
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
SMOKE = HERE / "smoke.py"
TMP_PARENT = Path("/tmp/opencode")
RESULTS: list[tuple[str, str]] = []


def load_smoke():
    spec = importlib.util.spec_from_file_location("smoke09", SMOKE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def check(name: str, condition: bool, detail: str = "") -> None:
    RESULTS.append((name, "PASS" if condition else f"FAIL: {detail}"))


def raises(name: str, needle: str, fn) -> None:
    try:
        fn()
    except SystemExit as exc:
        check(name, needle in str(exc), f"wrong rejection: {exc}")
    except Exception as exc:  # noqa: BLE001 - report any unexpected shape
        check(name, False, f"{type(exc).__name__}: {exc}")
    else:
        check(name, False, "expected a rejection")


def failure_contains(name: str, failures: list[str], needle: str) -> None:
    check(name, any(needle in failure for failure in failures), str(failures))


def replay(evidence: Path) -> int:
    """Re-run the offline verifier against a real capture tree without touching it.

    The tree is copied under /tmp/opencode first because `verify` writes derived
    captures; the original logs stay byte-identical.
    """
    TMP_PARENT.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="w09-replay-", dir=TMP_PARENT))
    work = scratch / evidence.name
    shutil.copytree(evidence, work, ignore=shutil.ignore_patterns("logs", "__pycache__"))
    (work / "logs").mkdir(exist_ok=True)
    kinds: list[str] = []
    for kind in ("pi", "opencode"):
        source = evidence / "logs" / f"real-{kind}"
        if source.is_dir():
            shutil.copytree(source, work / "logs" / f"real-{kind}")
            kinds.append(kind)
    if not kinds:
        print(f"FAIL  no logs/real-<kind> captures under {evidence}")
        shutil.rmtree(scratch, ignore_errors=True)
        return 1
    failed = False
    for kind in kinds:
        result = subprocess.run([sys.executable, str(work / "smoke.py"), "verify", kind], cwd=work, text=True, capture_output=True)
        print(f"{'PASS' if result.returncode == 0 else 'FAIL'}  replay {kind} captures (exit {result.returncode})")
        print(result.stdout.strip())
        if result.stderr.strip():
            print(result.stderr.strip())
        failed = failed or result.returncode != 0
    shutil.rmtree(scratch, ignore_errors=True)
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "--replay":
        evidence = Path(args[1]).expanduser().resolve() if len(args) > 1 else HERE
        return replay(evidence)
    smoke = load_smoke()
    TMP_PARENT.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="w09-validate-", dir=TMP_PARENT))

    # Numeric latest selection past a single digit.
    log = tmp / "latest-test"
    log.mkdir()
    for number in range(1, 13):
        (log / f"probe-{number}.json").write_text(json.dumps({"n": number}))
    (log / "probe.json").write_text(json.dumps({"n": 0}))
    check("latest prefers the highest numeric suffix", smoke.latest("probe", log)["n"] == 12, str(smoke.latest("probe", log)))

    # Refusal payload may arrive on either stream.
    check("parse_json_streams reads stdout", smoke.parse_json_streams({"stdout": '{"blockers":[{"code":"uncommitted-content"}]}', "stderr": ""})["blockers"][0]["code"] == "uncommitted-content")
    check("parse_json_streams reads stderr", smoke.parse_json_streams({"stdout": "", "stderr": '{"error":"cleanup-blocked","blockers":[{"code":"uncommitted-content"}]}'})["blockers"][0]["code"] == "uncommitted-content")
    check("parse_json_streams rejects noise", smoke.parse_json_streams({"stdout": "noise", "stderr": "noise"}) is None)

    check("sanitize_input keeps path/filePath/command", smoke.sanitize_input({"filePath": "/a/b", "command": "ls", "secret": "x"}) == {"filePath": "/a/b", "command": "ls"})
    check("normalize_time handles ms and ISO", smoke.normalize_time(1789263331330) == 1789263331.33 and smoke.normalize_time("2026-01-01T00:00:05Z") is not None)

    # A real repository commit gives the verifier a delivered HEAD with resumed.txt.
    repo = tmp / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    (repo / "resumed.txt").write_text("HPM09_RESUMED_OK\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.name=Verify", "-c", "user.email=v@example.invalid", "commit", "-m", "seed"], cwd=repo, check=True, capture_output=True)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, text=True, capture_output=True, check=True).stdout.strip()
    management = tmp / "management"
    (management / "handoffs").mkdir(parents=True)
    handoff = management / "handoffs" / "handoff-001.md"
    handoff.write_text("\n".join(f"## {section}\ncontent" for section in smoke.HANDOFF_SECTIONS), encoding="utf-8")

    def fact() -> dict:
        return {
            "worker_id": "w-mock-a",
            "sessions": [{"index": 1, "end_state": "replaced"}, {"index": 2, "end_state": "ended"}],
            "paths": {"management": str(management)},
            "result": {"head": head, "status": "delivered"},
            "worktree": str(repo),
        }

    config = {"provider": "opencode-go", "model": "deepseek-v4.1-flash", "thinking": "max"}
    pi_configs = [
        {"worker": "w-mock-a", "session": session, "events": [
            {"type": "session", "cwd": str(repo)},
            {"type": "model_change", "provider": config["provider"], "modelId": config["model"]},
            {"type": "thinking_level_change", "thinkingLevel": config["thinking"]},
        ]}
        for session in (1, 2)
    ]
    pi_activity = [
        {"worker": "w-mock-a", "session": 1, "started": "2026-01-01T00:00:01Z", "completed": "2026-01-01T00:00:05Z", "tool": "read", "input": {"path": str(repo / "corpus.txt")}},
        {"worker": "w-mock-a", "session": 2, "started": "2026-01-01T00:00:10Z", "completed": "2026-01-01T00:00:12Z", "tool": "read", "input": {"path": str(handoff)}},
    ]
    check("check_handoff accepts the Pi schema", smoke.check_handoff("pi", fact(), {"configs": pi_configs, "activity": pi_activity}, config, repo) is None)
    oc_configs = [
        {"worker": "w-mock-a", "session": session, "events": [
            {"providerID": config["provider"], "modelID": config["model"], "variant": config["thinking"], "path": {"cwd": str(repo)}},
        ]}
        for session in (1, 2)
    ]
    oc_activity = [
        {"worker": "w-mock-a", "session": 1, "started": 1789263331000, "completed": 1789263335000, "tool": "read", "input": {"filePath": str(repo / "corpus.txt")}},
        {"worker": "w-mock-a", "session": 2, "started": 1789263340000, "completed": 1789263342000, "tool": "read", "input": {"filePath": str(handoff)}},
    ]
    check("check_handoff accepts the OpenCode schema", smoke.check_handoff("opencode", fact(), {"configs": oc_configs, "activity": oc_activity}, config, repo) is None)

    raises("rejects a replacement that never read the handoff", "did not read", lambda: smoke.check_handoff("pi", fact(), {"configs": pi_configs, "activity": pi_activity[:1] + [dict(pi_activity[1], input={"path": str(repo / "corpus.txt")})]}, config, repo))
    raises("rejects an unconfirmed thinking level", "thinking", lambda: smoke.check_handoff("pi", fact(), {"configs": pi_configs, "activity": pi_activity}, dict(config, thinking="high"), repo))
    raises("rejects an unconfirmed provider", "configuration", lambda: smoke.check_handoff("opencode", fact(), {"configs": oc_configs, "activity": oc_activity}, dict(config, provider="other"), repo))
    raises("rejects old activity after new activity", "does not precede", lambda: smoke.check_handoff("pi", fact(), {"configs": pi_configs, "activity": [pi_activity[0], dict(pi_activity[1], started="2026-01-01T00:00:02Z", completed="2026-01-01T00:00:03Z")]}, config, repo))
    raises("rejects a missing replacement session", "no replacement session", lambda: smoke.check_handoff("pi", dict(fact(), sessions=[{"index": 1, "end_state": "replaced"}]), {"configs": pi_configs, "activity": pi_activity}, config, repo))
    check("read_handoff_paths finds the document", bool(smoke.read_handoff_paths([{"session": 2, "input": {"filePath": "/x/handoffs/handoff-001.md"}}], 2)))

    # Runtime configuration for workers without a handoff (B/C in the real smoke).
    check("runtime config accepts B/C-like Pi evidence", smoke.runtime_config_failures("pi", pi_configs, config, str(repo)) == [], str(smoke.runtime_config_failures("pi", pi_configs, config, str(repo))))
    failure_contains("runtime config rejects a wrong cwd", smoke.runtime_config_failures("pi", pi_configs, config, str(tmp / "other")), "session cwd")
    failure_contains("runtime config rejects an unconfirmed model", smoke.runtime_config_failures("pi", pi_configs, dict(config, model="other"), str(repo)), "provider/model")
    check("runtime config accepts OpenCode evidence", smoke.runtime_config_failures("opencode", oc_configs, config, str(repo)) == [], str(smoke.runtime_config_failures("opencode", oc_configs, config, str(repo))))
    raises("check_runtime rejects an unconfirmed configuration", "provider/model", lambda: smoke.check_runtime("pi", "w-mock-b", pi_configs, dict(config, model="other"), str(repo)))
    session_log = tmp / "pi-session-b.jsonl"
    session_log.write_text("\n".join(json.dumps(event) for event in (
        {"type": "session", "cwd": str(repo)},
        {"type": "model_change", "provider": config["provider"], "modelId": config["model"]},
        {"type": "thinking_level_change", "thinkingLevel": config["thinking"]},
    )), encoding="utf-8")
    recovered = smoke.recover_runtime_configs("pi", {"worker_id": "w-mock-b", "sessions": [{"index": 1, "context_ref": str(session_log)}]})
    check("recover_runtime_configs rebuilds sessions from refs", smoke.runtime_config_failures("pi", recovered, config, str(repo)) == [], str(smoke.runtime_config_failures("pi", recovered, config, str(repo))))

    # Cleanup evidence: the real Pi failure was reading `status.cleanup.archive`,
    # while the archive only exists as `last_archive` in the raw cleanup.json.
    archive_root = tmp / "cleanup-archive"
    (archive_root / "untracked").mkdir(parents=True)
    (archive_root / "untracked" / "seed-note.txt").write_text("seed prepared before handoff\n", encoding="utf-8")
    status_cleanup = {
        "cleaned": True,
        "decision": {"at": "2026-09-13T03:06:42.191494Z", "integrated": "fa344c044dc1eaee85fb49590aabfbe004220b20"},
        "last_attempt": {"at": "2026-09-13T03:06:42.736781Z", "blockers": [], "outcome": "removed", "removed": {"branch": False, "tabs": ["wJ:t1Y"], "worktree": True}},
        "blockers": [],
        "resources": {
            "branch": {"exists": True, "head": "fa344c0", "name": "hpm/w09-pi-a"},
            "worktree": {"dirty": False, "exists": False, "owned": False, "path": "/gone"},
            "sessions": [{"agent": "w09-pi-a", "agent_live": False, "agent_status": None, "index": 1, "session_status": "replaced", "tab": "wJ:t1Y"}],
        },
    }
    cleanup_record = {
        "completed_at": "2026-09-13T03:06:42.736781Z",
        "decision": {"at": "2026-09-13T03:06:42.191494Z", "integrated": "fa344c044dc1eaee85fb49590aabfbe004220b20"},
        "last_archive": {"path": str(archive_root), "tracked_patch_bytes": 0, "untracked_files": [{"bytes": 29, "path": "seed-note.txt"}]},
        "last_attempt": status_cleanup["last_attempt"],
        "worktree_removed_at": "2026-09-13T03:06:42.736781Z",
    }
    check("real status cleanup schema omits the archive field", "archive" not in status_cleanup)
    failures = smoke.cleanup_evidence_failures(status_cleanup, cleanup_record, archive=archive_root)
    check("cleanup evidence accepts the captured schema", failures == [], str(failures))
    stripped = {key: value for key, value in cleanup_record.items() if key != "last_archive"}
    failure_contains("cleanup evidence rejects a record without last_archive", smoke.cleanup_evidence_failures(status_cleanup, stripped), "no last_archive.path")
    blocked = dict(status_cleanup, last_attempt={"outcome": "blocked", "removed": {"worktree": False}}, blockers=[{"code": "uncommitted-content"}])
    failure_contains("cleanup evidence rejects a blocked attempt", smoke.cleanup_evidence_failures(blocked, cleanup_record), "last_attempt.outcome")
    failure_contains("cleanup evidence rejects a mismatched command archive", smoke.cleanup_evidence_failures(status_cleanup, cleanup_record, archive=tmp / "elsewhere"), "!= cleanup command archive")
    missing = dict(cleanup_record, last_archive=dict(cleanup_record["last_archive"], path=str(tmp / "gone")))
    failure_contains("cleanup evidence rejects a missing archive directory", smoke.cleanup_evidence_failures(status_cleanup, missing), "archive directory is missing")
    check("settled_failures accepts a delivered terminal worker", smoke.settled_failures("b", {"lifecycle": {"state": "delivered"}, "sessions": [{"index": 1, "status": "ended", "end_state": "delivered"}]}, {"state": "closed"}) == [])
    failure_contains("settled_failures rejects a live session", smoke.settled_failures("b", {"lifecycle": {"state": "working"}, "sessions": [{"index": 1, "status": "running", "end_state": None}]}, {}), "not delivered")

    # wait-final: a bounded window, no spin on the same pending item, no false failure.
    original = {name: getattr(smoke, name, None) for name in ("LOG", "RUN", "facts", "pm")}
    smoke.RUN = "w09-wait-final"

    def exercise_wait_final(window: float, payloads: list[dict], delivered_after: int, label: str) -> tuple[list[str], Path]:
        log_dir = tmp / label
        log_dir.mkdir()
        smoke.LOG = log_dir
        calls: list[str] = []

        def fake_pm(name: str, *args: str, expect: int | None = 0) -> dict:
            calls.append(name)
            time.sleep(0.05)
            return {"payload": payloads[min(len(calls) - 1, len(payloads) - 1)]}

        def fake_facts(role: str) -> dict:
            status = "delivered" if len(calls) >= delivered_after else "working"
            return {"result": {"status": status}}

        smoke.pm = fake_pm
        smoke.facts = fake_facts
        os.environ["HPM_SMOKE_FINAL_WINDOW"] = str(window)
        with contextlib.redirect_stdout(io.StringIO()):
            smoke.action_wait_final()
        return calls, log_dir

    calls, log_dir = exercise_wait_final(5, [{"items": [{"item_id": "w09-pi-b/i001"}], "timed_out": False}], 9, "wait-partial")
    check("wait-final returns a pending item once without spinning", len(calls) == 1 and (log_dir / "wait-final-partial.json").is_file(), str(calls))
    calls, log_dir = exercise_wait_final(5, [{"items": [], "timed_out": True}], 2, "wait-delivered")
    check("wait-final stops once both workers delivered", len(calls) == 2 and (log_dir / "wait-final-delivered.json").is_file(), str(calls))
    started = time.monotonic()
    calls, log_dir = exercise_wait_final(1, [{"items": [], "timed_out": True}], 999, "wait-no-result")
    elapsed = time.monotonic() - started
    no_result = json.loads((log_dir / "wait-final-no-result.json").read_text(encoding="utf-8"))
    check("wait-final window expiry is explicit and not a task failure", elapsed < 3 and bool(calls) and "not a task failure" in no_result["note"], f"{elapsed:.2f}s calls={calls}")
    for name, value in original.items():
        setattr(smoke, name, value)
    os.environ.pop("HPM_SMOKE_FINAL_WINDOW", None)

    # Real database: sanitized input and completion times must survive capture.
    if smoke.OPENCODE_DB.is_file():
        connection = sqlite3.connect(f"{smoke.OPENCODE_DB.resolve().as_uri()}?mode=ro", uri=True)
        session_id = None
        for (candidate,) in connection.execute("SELECT session_id FROM part ORDER BY time_created DESC LIMIT 400"):
            rows = connection.execute("SELECT data FROM part WHERE session_id=? LIMIT 80", (candidate,)).fetchall()
            if any('"type":"tool"' in row[0].replace(" ", "") for row in rows):
                session_id = candidate
                break
        messages, tools = smoke.opencode_messages(session_id, "w-real", 1)
        check("opencode capture keeps assistant configuration", bool(messages) and all(messages[0].get(key) for key in ("providerID", "modelID", "variant")))
        check("opencode capture keeps sanitized tool input", any(tool["input"] and set(tool["input"]) - {"raw"} for tool in tools))
        check("opencode capture keeps completion timestamps", all(isinstance(tool["completed"], (int, float)) for tool in tools))
    else:
        check("opencode capture checks skipped (database missing)", True, "")

    # Setup must rely on inherited identity and stay under /tmp/opencode.
    sandbox = tmp / "sandbox" / "09-end-to-end-and-migration"
    shutil.copytree(HERE, sandbox, ignore=shutil.ignore_patterns("logs", "location.json", "__pycache__"))
    proc = subprocess.run(["python3", "smoke.py", "setup"], cwd=sandbox, text=True, capture_output=True, env=dict(os.environ, HERDR_ENV="1"))
    root = proc.stdout.strip()
    check("setup completes", proc.returncode == 0, proc.stderr)
    check("setup uses /tmp/opencode as parent", root.startswith("/tmp/opencode/hpm09-migration-"), root)
    for kind in ("pi", "opencode"):
        local = subprocess.run(["git", "config", "--local", "--get", "user.name"], cwd=Path(root) / kind, text=True, capture_output=True)
        check(f"setup writes no local Git identity ({kind})", local.returncode == 1 and not local.stdout.strip(), local.stdout)

    shutil.rmtree(root, ignore_errors=True)
    shutil.rmtree(tmp, ignore_errors=True)
    for name, outcome in RESULTS:
        print(f"{outcome:5}  {name}")
    failures = [name for name, outcome in RESULTS if outcome != "PASS"]
    print(f"\n{len(RESULTS) - len(failures)}/{len(RESULTS)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
