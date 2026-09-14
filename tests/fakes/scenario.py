#!/usr/bin/env python3
"""Deterministic fake worker driven by HPM_FAKE_SCENARIO_BEHAVIOR.

The scenario is started by the fake herdr on the first processed prompt,
parses the worker contract written by plan_manager, and simulates the
requested worker behavior against the real temporary git repository used by
the tests.
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

NAME = sys.argv[1]
PROMPT_FILE = Path(sys.argv[2])
ROOT = Path(os.environ["HPM_FAKE_DIR"])
BEHAVIOR = os.environ.get("HPM_FAKE_SCENARIO_BEHAVIOR", "deliver-code")
DELAY = float(os.environ.get("HPM_SCENARIO_DELAY", "0.2"))


def status_file():
    return ROOT / "agents" / f"{NAME}.json"


def set_status(value):
    path = status_file()
    if not path.is_file():
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    data["status"] = value
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)


def current_status():
    try:
        return json.loads(status_file().read_text(encoding="utf-8")).get("status")
    except FileNotFoundError:
        return None


def contract_field(contract, label):
    match = re.search(rf"^- {re.escape(label)}[^:]*: `([^`]+)`", contract, re.MULTILINE)
    if not match:
        raise SystemExit(f"scenario: contract field missing: {label}")
    return match.group(1)


def git(*args):
    return subprocess.run(
        ["git", *args], cwd=WORKTREE, text=True, capture_output=True, check=True
    ).stdout.strip()


prompt_text = PROMPT_FILE.read_text(encoding="utf-8")
contract_match = re.search(r"(/[^\s`]+contract\.md)", prompt_text)
if not contract_match:
    raise SystemExit("scenario: cannot find contract path in prompt")
CONTRACT = Path(contract_match.group(1)).read_text(encoding="utf-8")
WORKTREE = Path(contract_field(CONTRACT, "Worktree")).resolve()
RESULT_PATH = Path(contract_field(CONTRACT, "Result file"))
TICKET_ID = re.search(r"ticket \*\*(\S+?):", CONTRACT).group(1)
WORKER_ID = re.search(r"worker \*\*(\S+?)\*\*", CONTRACT).group(1)

materials = re.findall(r"^- `([^`]+)` \(source: `([^`]+)`\)", CONTRACT, re.MULTILINE)
MATERIAL_TEXT = Path(materials[0][0]).read_text(encoding="utf-8") if materials else ""
MATERIAL_SHA = hashlib.sha256(MATERIAL_TEXT.encode("utf-8")).hexdigest()


def write_result(payload):
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = RESULT_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, RESULT_PATH)


def base_result(status, **extra):
    payload = {"ticket_id": TICKET_ID, "worker_id": WORKER_ID, "status": status, "plan_deviations": []}
    payload.update(extra)
    return payload


def commit_material_note():
    note = WORKTREE / "material-note.txt"
    note.write_text(MATERIAL_TEXT, encoding="utf-8")
    git("add", "material-note.txt")
    git("commit", "-m", "add material note")
    return git("rev-parse", "HEAD")


def deliver_code():
    head = commit_material_note()
    write_result(
        base_result(
            "delivered",
            summary=f"wrote the material into material-note.txt (sha {MATERIAL_SHA})",
            acceptance=[{"criterion": "material is visible to the worker", "met": True, "evidence": "material-note.txt"}],
            verification=[{"command": "git rev-parse HEAD", "exit_code": 0, "summary": head}],
            head=head,
            artifacts=[],
            remaining="",
        )
    )
    set_status("idle")


def deliver_then_work():
    """Declare delivery but keep reporting `working` for a while.

    Delivery must stop automatic handoff even though the session has not
    settled yet.
    """
    commit_material_note()
    write_result(
        base_result(
            "delivered",
            summary=f"wrote the material into material-note.txt (sha {MATERIAL_SHA})",
            acceptance=[{"criterion": "material is visible to the worker", "met": True, "evidence": "material-note.txt"}],
            verification=[{"command": "true", "exit_code": 0, "summary": "done"}],
            head=git("rev-parse", "HEAD"),
            artifacts=[],
            remaining="",
        )
    )
    set_status("working")
    deadline = time.time() + float(os.environ.get("HPM_SCENARIO_EXTRA_WORK_SECONDS", "6"))
    while time.time() < deadline and current_status() == "working":
        time.sleep(0.1)
    settle_to_idle()


def deliver_noncode():
    findings = WORKTREE / "findings.md"
    findings.write_text(f"material sha: {MATERIAL_SHA}\n", encoding="utf-8")
    write_result(
        base_result(
            "delivered",
            summary=f"investigation complete, material sha {MATERIAL_SHA}",
            acceptance=[{"criterion": "investigation result recorded", "met": True, "evidence": "findings.md"}],
            verification=[{"command": "cat findings.md", "exit_code": 0, "summary": "findings written"}],
            head=None,
            artifacts=["findings.md"],
            remaining="",
        )
    )
    set_status("idle")


def settle_to_idle():
    """Keep the turn visibly running for a moment so delivery confirmation sees it."""
    time.sleep(float(os.environ.get("HPM_SCENARIO_SETTLE_DELAY", "0.25")))
    set_status("idle")


def prompt_files():
    files = []
    for path in (ROOT / "agents").glob(f"{NAME}.prompt.*.txt"):
        try:
            number = int(path.stem.rsplit(".", 1)[1])
        except (ValueError, IndexError):
            continue
        files.append((number, path))
    return [path for _, path in sorted(files)]


def is_handoff_prompt(text):
    return "handoff" in text and "skill" in text


def is_continuation_prompt(text):
    return "A previous session of this worker" in text


def document_path_from_prompt(text):
    match = re.search(r"`(/[^`]+\.md)`", text)
    if not match:
        raise SystemExit("scenario: prompt has no handoff document path")
    return Path(match.group(1))


def handoff_document(doc_path, marker):
    return (
        f"# Handoff for worker {WORKER_ID} (ticket {TICKET_ID})\n\n"
        "## Progress\n"
        "The first session started the ticket and left partial work in the worktree.\n\n"
        "## Decisions\n"
        "Continue in the same worktree and branch with the confirmed runtime configuration.\n\n"
        "## Verification\n"
        "No verification has been run yet.\n\n"
        "## Commits\n"
        "This session has not committed yet.\n\n"
        "## Uncommitted work\n"
        "partial.txt holds the in-progress marker.\n\n"
        "## Next steps\n"
        f"Create continuation.txt containing {marker} and commit it, then write the result file.\n"
    )


def write_document(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def handle_handoff_prompt(text):
    doc_path = document_path_from_prompt(text)
    mode = os.environ.get("HPM_SCENARIO_HANDOFF_MODE", "valid")
    delay = float(os.environ.get("HPM_SCENARIO_HANDOFF_DELAY", "0"))
    if delay > 0:
        time.sleep(delay)
    if mode == "missing":
        settle_to_idle()
        return
    if mode == "working":
        set_status("working")
        busy_until = time.time() + float(os.environ.get("HPM_SCENARIO_HANDOFF_BUSY_SECONDS", "30"))
        while time.time() < busy_until and status_file().is_file():
            time.sleep(0.1)
        set_status("idle")
        return
    if mode == "invalid" or (mode == "invalid-first" and not (ROOT / "handoff_invalid_sent").is_file()):
        if mode == "invalid-first":
            (ROOT / "handoff_invalid_sent").write_text("1", encoding="utf-8")
        write_document(doc_path, f"# Handoff\n\n## Progress\nStarted on ticket {TICKET_ID}.\n")
        settle_to_idle()
        return
    marker = f"NEXT-STEP-MARKER-{doc_path.stem}"
    if mode == "temp":
        target = Path(os.environ.get("TMPDIR", "/tmp")) / "handoff-from-scenario.md"
    else:
        target = doc_path
    write_document(target, handoff_document(doc_path, marker))
    settle_to_idle()


def continuation_window():
    """Hold the continuation turn open so an interrupting stop can be observed.

    Returns False when the turn was settled (interrupted) before the window
    elapsed, meaning the scenario must not write anything else.
    """
    delay = float(os.environ.get("HPM_SCENARIO_CONTINUATION_DELAY", "0"))
    if delay <= 0:
        return True
    deadline = time.time() + delay
    while time.time() < deadline:
        if current_status() != "working":
            return False
        time.sleep(0.1)
    return current_status() == "working"


def handle_continuation(text):
    if not continuation_window():
        return
    doc_path = document_path_from_prompt(text)
    document = doc_path.read_text(encoding="utf-8")
    match = re.search(r"NEXT-STEP-MARKER-[A-Za-z0-9._-]+", document)
    if not match:
        raise SystemExit("scenario: handoff document has no next-step marker")
    marker = match.group(0)
    repeat = ROOT / "handoff_repeat"
    if repeat.is_file() and int(repeat.read_text(encoding="utf-8") or "0") > 0:
        repeat.write_text(str(int(repeat.read_text(encoding="utf-8")) - 1), encoding="utf-8")
        settle_to_idle()
        return
    (WORKTREE / "continuation.txt").write_text(marker + "\n", encoding="utf-8")
    git("add", "continuation.txt")
    git("commit", "-m", "continue after handoff")
    head = git("rev-parse", "HEAD")
    write_result(
        base_result(
            "delivered",
            summary=f"continued from {doc_path.name} and delivered marker {marker}",
            acceptance=[
                {"criterion": "handoff document was read by the new session", "met": True, "evidence": "continuation.txt"}
            ],
            verification=[{"command": "git rev-parse HEAD", "exit_code": 0, "summary": head}],
            head=head,
            artifacts=["continuation.txt"],
            remaining="",
        )
    )
    settle_to_idle()


def handoff_behavior():
    """Partial work, then serve handoff and continuation prompts until replaced."""
    (WORKTREE / "partial.txt").write_text(f"partial work by {NAME}\n", encoding="utf-8")
    set_status("working")
    # Prompts may already exist before this process starts; the initial contract
    # prompt matches neither handler, and handoff prompts must not be missed.
    seen: set[str] = set()
    deadline = time.time() + 300
    while time.time() < deadline:
        if not status_file().is_file():
            return
        for path in prompt_files():
            if path.name in seen:
                continue
            seen.add(path.name)
            text = path.read_text(encoding="utf-8")
            if is_handoff_prompt(text):
                handle_handoff_prompt(text)
            elif is_continuation_prompt(text):
                handle_continuation(text)
        time.sleep(0.1)


def main():
    if BEHAVIOR == "deliver-then-work":
        # Declare delivery before the supervisor can sample context, so the
        # automatic handoff guard is exercised deterministically.
        deliver_then_work()
        return
    time.sleep(DELAY)
    if BEHAVIOR == "deliver-code":
        deliver_code()
    elif BEHAVIOR == "deliver-noncode":
        deliver_noncode()
    elif BEHAVIOR == "handoff":
        handoff_behavior()
    elif BEHAVIOR == "wrong-head":
        note = WORKTREE / "material-note.txt"
        note.write_text("wrong head scenario\n", encoding="utf-8")
        git("add", "material-note.txt")
        git("commit", "-m", "add note")
        write_result(
            base_result(
                "delivered",
                summary="claims delivery with an invented head",
                acceptance=[{"criterion": "x", "met": True, "evidence": "y"}],
                verification=[{"command": "true", "exit_code": 0, "summary": "ok"}],
                head="a" * 40,
                artifacts=[],
                remaining="",
            )
        )
        set_status("idle")
    elif BEHAVIOR == "wrong-worker":
        write_result(
            base_result(
                "delivered",
                summary="claims delivery under the wrong worker id",
                acceptance=[{"criterion": "x", "met": True, "evidence": "y"}],
                verification=[{"command": "true", "exit_code": 0, "summary": "ok"}],
                head=None,
                artifacts=["findings.md"],
                remaining="",
            )
            | {"worker_id": "someone-else"}
        )
        set_status("idle")
    elif BEHAVIOR == "needs-decision":
        write_result(base_result("needs-decision", reason="which of the two options should be used?"))
        set_status("idle")
    elif BEHAVIOR == "missing-result":
        set_status("idle")
        deadline = time.time() + 120
        while time.time() < deadline:
            if int(json.loads(status_file().read_text(encoding="utf-8")).get("prompt_count", 0)) >= 2:
                time.sleep(0.3)
                set_status("idle")
                return
            time.sleep(0.1)
    elif BEHAVIOR == "blocked":
        set_status("blocked")
        deadline = time.time() + 120
        while time.time() < deadline and current_status() == "blocked":
            time.sleep(0.1)
    elif BEHAVIOR == "slow":
        set_status("working")
        deadline = time.time() + 120
        while time.time() < deadline and current_status() == "working":
            time.sleep(0.1)
        set_status("idle")
    else:
        raise SystemExit(f"scenario: unknown behavior {BEHAVIOR!r}")


if __name__ == "__main__":
    main()
