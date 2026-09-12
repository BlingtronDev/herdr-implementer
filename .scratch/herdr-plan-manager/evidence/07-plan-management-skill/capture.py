#!/usr/bin/env python3
"""Capture only this smoke's registered resources and runtime config/tool metadata.

Never copies full runtime transcripts or corpus/tool outputs into the repository.
"""
import json
from pathlib import Path
import shutil
import sqlite3
import sys

HERE = Path(__file__).resolve().parent
kind = sys.argv[1]
assert kind in ("pi", "opencode")
repo = Path(json.loads((HERE / "smoke-location.json").read_text())["root"]) / kind
out = HERE / "logs" / f"real-{kind}" / "captured"
out.mkdir(parents=True, exist_ok=True)
configs, calls = [], []
for worker in sorted((repo / ".git/herdr-plan-manager/workers").iterdir()):
    state = json.loads((worker / "state.json").read_text())
    dest = out / worker.name
    dest.mkdir(exist_ok=True)
    for name in ("state.json", "result.json", "contract.md", "supervisor.log", "ack.json", "cleanup.json"):
        if (worker / name).exists():
            shutil.copy2(worker / name, dest / name)
    if (worker / "cleanup.json").exists():
        cleanup = json.loads((worker / "cleanup.json").read_text())
        archive = cleanup.get("last_archive") or {}
        if archive.get("path"):
            note = Path(archive["path"]) / "untracked/seed-note.txt"
            if note.is_file():
                shutil.copy2(note, dest / "archived-seed-note.txt")
    if (worker / "handoffs").exists():
        shutil.copytree(worker / "handoffs", dest / "handoffs", dirs_exist_ok=True)
    if (worker / "materials/manifest.json").exists():
        shutil.copy2(worker / "materials/manifest.json", dest / "materials-manifest.json")
    for session in state.get("sessions", []):
        ref = session.get("context_ref")
        if not ref:
            continue
        ident = {"worker": worker.name, "session": session["index"], "ref": ref}
        if kind == "pi":
            events = [json.loads(line) for line in Path(ref).read_text().splitlines() if line.strip()]
            config = [event for event in events if event.get("type") in ("session", "model_change", "thinking_level_change")]
            configs.append({**ident, "events": config})
            for event in events:
                message = event.get("message", {})
                content = message.get("content", [])
                if not isinstance(content, list):
                    continue
                for part in content:
                    if part.get("type") == "toolCall":
                        args = part.get("arguments", {})
                        calls.append({**ident, "timestamp": event.get("timestamp"), "tool": part.get("name"),
                                      "input": {key: value for key, value in args.items() if key in ("path", "command", "offset", "limit")}})
        else:
            db = Path.home() / ".local/share/opencode/opencode.db"
            with sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True) as conn:
                messages = [json.loads(row[0]) for row in conn.execute("SELECT data FROM message WHERE session_id=? ORDER BY time_created,id", (ref,))]
                rows = conn.execute("SELECT data FROM part WHERE session_id=? ORDER BY time_created,id", (ref,)).fetchall()
            configs.append({**ident, "messages": [{key: msg.get(key) for key in ("id", "role", "providerID", "modelID", "variant", "agent", "path", "time")} for msg in messages if msg.get("role") == "assistant"]})
            for row in rows:
                part = json.loads(row[0])
                if part.get("type") != "tool":
                    continue
                status = part.get("state", {})
                args = status.get("input", {})
                calls.append({**ident, "tool": part.get("tool"), "status": status.get("status"), "time": status.get("time"),
                              "input": {key: value for key, value in args.items() if key in ("filePath", "command", "offset", "limit", "name")}})
(out / "runtime-config.json").write_text(json.dumps(configs, ensure_ascii=False, indent=2))
(out / "tool-metadata.json").write_text(json.dumps(calls, ensure_ascii=False, indent=2))
for name in ("spec.md", "A.md", "B.md", "C.md"):
    shutil.copy2(repo / ".scratch" / name, out / name)
print(f"captured {len(configs)} sessions, {len(calls)} tool calls into {out}")
