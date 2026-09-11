#!/usr/bin/env python3
"""Deterministic fake worker driven by HPM_FAKE_SCENARIO_BEHAVIOR.

The scenario is started by the fake herdr on the first prompt, parses the
worker contract written by plan_manager, and simulates the requested worker
behavior against the real temporary git repository used by the tests.
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
    data = json.loads(path.read_text(encoding="utf-8"))
    data["status"] = value
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)


def current_status():
    return json.loads(status_file().read_text(encoding="utf-8")).get("status")


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
    payload = {"ticket_id": TICKET_ID, "worker_id": WORKER_ID, "status": status}
    payload.update(extra)
    return payload


def deliver_code():
    note = WORKTREE / "material-note.txt"
    note.write_text(MATERIAL_TEXT, encoding="utf-8")
    git("add", "material-note.txt")
    git("commit", "-m", "add material note")
    head = git("rev-parse", "HEAD")
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


def main():
    time.sleep(DELAY)
    if BEHAVIOR == "deliver-code":
        deliver_code()
    elif BEHAVIOR == "deliver-noncode":
        deliver_noncode()
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
