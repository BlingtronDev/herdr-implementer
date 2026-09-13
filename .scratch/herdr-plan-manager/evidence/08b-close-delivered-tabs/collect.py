#!/usr/bin/env python3
"""Capture worker delivery and prepare an apply_patch envelope for the coordinator.

Does not alter the implementation checkout. Refuses to overwrite changed source
files: each affected local file must still match the prepared snapshot baseline.
"""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[3]
meta = json.loads((HERE / "location.json").read_text())
repo = Path(meta["repo"])
worker = repo / ".git/herdr-plan-manager/workers" / meta["worker"]
state = json.loads((worker / "state.json").read_text())
result = json.loads((worker / "result.json").read_text())
assert result["status"] == "delivered"
head = result["head"]
out = HERE / "logs/captured-worker"
out.mkdir(exist_ok=True)
for name in ("state.json", "result.json", "contract.md", "supervisor.log"):
    shutil.copy2(worker / name, out / name)


def git(*args):
    return subprocess.check_output(["git", *args], cwd=repo)


changed = git("diff", "--name-only", meta["base"], head).decode().splitlines()
manifest = []
for relative in changed:
    assert relative.split("/", 1)[0] in ("bin", "tests", "docs", "prompts"), relative
    original = git("show", f"{meta['base']}:{relative}")
    assert (PROJECT / relative).read_bytes() == original, f"local source changed since snapshot: {relative}"
    delivered = git("show", f"{head}:{relative}")
    manifest.append({"path": relative, "original_sha256": hashlib.sha256(original).hexdigest(),
                     "delivered_sha256": hashlib.sha256(delivered).hexdigest()})
(out / "manifest.json").write_text(json.dumps({"base": meta["base"], "head": head, "files": manifest}, indent=2))
diff = git("diff", meta["base"], head).decode()
(out / "worker.diff").write_text(diff)
envelope = ["*** Begin Patch"]
for line in diff.splitlines():
    if line.startswith("diff --git "):
        relative = line.split(" b/", 1)[1]
        envelope.append(f"*** Update File: {PROJECT / relative}")
    elif line.startswith("@@"):
        envelope.append("@@")
    elif line.startswith(("index ", "--- ", "+++ ", "\\ No newline")):
        continue
    else:
        assert line.startswith(("+", "-", " ")), line
        envelope.append(line)
envelope.append("*** End Patch")
(out / "apply.patch").write_text("\n".join(envelope) + "\n")
events = [json.loads(line) for line in Path(state["sessions"][0]["context_ref"]).read_text().splitlines() if line.strip()]
(out / "runtime-config.json").write_text(json.dumps([e for e in events if e.get("type") in ("session", "model_change", "thinking_level_change")], indent=2))
(out / "test-tool-results.json").write_text(json.dumps([e for e in events if e.get("message", {}).get("role") == "toolResult" and e["message"].get("toolName") == "bash"], ensure_ascii=False, indent=2))
print(f"Captured {head}, {len(changed)} files; apply.patch is ready for apply_patch, source checkout untouched")
