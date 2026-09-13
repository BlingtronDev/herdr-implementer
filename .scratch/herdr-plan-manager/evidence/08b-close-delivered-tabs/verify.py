#!/usr/bin/env python3
"""Verify exact patch transfer and capture durable real-runtime release evidence."""
import ast
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[3]
meta = json.loads((HERE / "location.json").read_text())
root = Path(meta["root"])
manifest = json.loads((HERE / "logs/captured-worker/manifest.json").read_text())
for item in manifest["files"]:
    actual = hashlib.sha256((PROJECT / item["path"]).read_bytes()).hexdigest()
    assert actual == item["delivered_sha256"], item["path"]
for path in HERE.glob("*.py"):
    ast.parse(path.read_text(), filename=str(path))
for relative in ("bin/plan_manager.py", "tests/test_plan_manager.py", "tests/fakes/bin/herdr"):
    ast.parse((PROJECT / relative).read_text(), filename=relative)
proc = subprocess.run(["git", "diff", "--check"], cwd=PROJECT, text=True, capture_output=True)
assert proc.returncode == 0, proc.stdout + proc.stderr
output = {"worker_head": manifest["head"], "identical_files": [i["path"] for i in manifest["files"]],
          "python_syntax": "passed", "git_diff_check": "passed", "runtimes": []}
for kind in ("pi", "opencode"):
    repo = root / f"{kind}-smoke"
    worker = f"w08-tabs-{kind}"
    management = repo / ".git/herdr-plan-manager/workers" / worker
    dest = HERE / "logs" / kind / "captured"
    dest.mkdir(exist_ok=True)
    for name in ("state.json", "result.json", "contract.md", "supervisor.log", "ack.json"):
        shutil.copy2(management / name, dest / name)
    state = json.loads((management / "state.json").read_text())
    assert state["result"]["status"] == "delivered"
    assert state["release"]["state"] == "closed"
    assert all(item["kind"] == "delivery" for item in state["items"])
    assert Path(state["worktree"]).is_dir()
    assert len(state["sessions"]) == 1
    ref = state["sessions"][0]["context_ref"]
    if kind == "pi":
        events = [json.loads(line) for line in Path(ref).read_text().splitlines() if line.strip()]
        config = [e for e in events if e.get("type") in ("session", "model_change", "thinking_level_change")]
        assert next(e["cwd"] for e in config if e["type"] == "session") == state["worktree"]
        models = [e for e in config if e["type"] == "model_change"]
        levels = [e for e in config if e["type"] == "thinking_level_change"]
        assert models and levels
        assert all((e["provider"], e["modelId"]) == ("opencode-go", "deepseek-v4.1-flash") for e in models)
        assert all(e["thinkingLevel"] == "max" for e in levels)
    else:
        db = Path.home() / ".local/share/opencode/opencode.db"
        with sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True) as conn:
            messages = [json.loads(row[0]) for row in conn.execute("SELECT data FROM message WHERE session_id=? ORDER BY time_created,id", (ref,))]
        config = [{k: m.get(k) for k in ("role", "providerID", "modelID", "variant", "path")} for m in messages if m.get("role") == "assistant"]
        assert config
        assert all((m["providerID"], m["modelID"], m["variant"], m["path"]["cwd"]) ==
                   ("opencode-go", "deepseek-v4.1-flash", "max", state["worktree"]) for m in config)
    (dest / "runtime-config.json").write_text(json.dumps(config, indent=2))
    output["runtimes"].append({"kind": kind, "worker": worker, "release": state["release"], "effective_configuration": "confirmed"})
old = json.loads((HERE / "logs/old/verified.json").read_text())
assert old == {"count": 7, "disk_unchanged": True, "tabs_closed": True}
output["old_workers"] = old
repair_after = json.loads(json.loads((HERE / "logs/repair-worker/after.json").read_text())["stdout"])
assert repair_after["release"]["state"] == "closed" and not repair_after["herdr"]["agent_present"]
output["repair_worker_closed"] = True
(HERE / "logs/verification.json").write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
print("PASS exact transfer of 5 files, Python syntax, diff check, real Pi/OpenCode auto-close, 7 old tabs released with disk unchanged, repair worker tab closed")
