#!/usr/bin/env python3
"""Check saved real-smoke evidence; does not start or control any worker."""
import datetime as dt
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(path):
    return json.loads(path.read_text())


def response(path):
    call = load(path)
    assert call["exit_code"] == 0, path
    return json.loads(call["stdout"])


for kind in ("pi", "opencode"):
    log = HERE / "logs" / f"real-{kind}"
    captured = log / "captured"
    root = Path(load(HERE / "smoke-location.json")["root"]) / kind
    configs = load(captured / "runtime-config.json")
    assert len(configs) == 4
    for config in configs:
        cwd = str(root / ".git/herdr-plan-manager/worktrees" / config["worker"])
        if kind == "pi":
            events = config["events"]
            models = [e for e in events if e["type"] == "model_change"]
            thinking = [e for e in events if e["type"] == "thinking_level_change"]
            assert models and thinking
            assert all((e["provider"], e["modelId"]) == ("opencode-go", "deepseek-v4.1-flash") for e in models)
            assert all(e["thinkingLevel"] == "max" for e in thinking)
            assert all(e["cwd"] == cwd for e in events if e["type"] == "session")
        else:
            assert config["messages"]
            assert all((m["providerID"], m["modelID"], m["variant"]) == ("opencode-go", "deepseek-v4.1-flash", "max") for m in config["messages"])
            assert all(m["path"]["cwd"] == cwd for m in config["messages"])

    a = load(captured / f"w07-{kind}-a/state.json")
    history = a["handoff"]["history"]
    assert len(history) == 1 and history[0]["trigger"] == "tokens"
    assert history[0]["sample"]["total"] >= 35000
    assert [s["index"] for s in a["sessions"]] == [1, 2]
    assert a["sessions"][0]["end_state"] == "replaced"
    assert a["sessions"][0]["runtime"] == a["sessions"][1]["runtime"]
    assert a["sessions"][1]["context"]["total"] < 35000
    tool_calls = [c for c in load(captured / "tool-metadata.json") if c["worker"] == f"w07-{kind}-a"]
    first = [c for c in tool_calls if c["session"] == 1]
    second = [c for c in tool_calls if c["session"] == 2]
    assert any(c["tool"] == "read" and "handoff-001.md" in str(c["input"]) for c in second)
    assert not any(c["tool"] == "read" and "corpus.txt" in str(c["input"]) for c in second)
    if kind == "pi":
        # Pi tool-call timestamps show dispatch order; old final command is a
        # synchronous read-only handoff/Git check, not detached business work.
        last_old = max(dt.datetime.fromisoformat(c["timestamp"]) for c in first)
        first_new = min(dt.datetime.fromisoformat(c["timestamp"]) for c in second)
    else:
        last_old = max(c["time"]["end"] for c in first)
        first_new = min(c["time"]["start"] for c in second)
    assert last_old < first_new

    start_c = response(log / "start-c.json")
    integrated_a = load(log / "integration-a.json")
    b = response(log / "b-at-release.json")
    assert start_c["base"] == integrated_a["integrated"]
    assert start_c["active_workers"] == 2 <= start_c["max_workers"] == 3
    assert b["herdr"]["agent_status"] == "working" and b["result"] is None
    for role in ("a", "b", "c"):
        assert load(captured / f"w07-{kind}-{role}/result.json")["status"] == "delivered"
        assert response(log / f"stop-{role}.json")["business_stopped"] is True
    assert load(log / "combined-verification.json")["exit_code"] == 0
    final = response(log / "final-run.json")
    assert final["active_count"] == 0 and final["pending_items"] == []

    refused = load(log / "cleanup-a-refused.json")
    assert refused["exit_code"] == 3
    assert "uncommitted-content" in refused["stderr"]
    cleaned = response(log / "cleanup-a-archived.json")
    assert cleaned["cleaned"] and not cleaned["branch"]["deleted"]
    # Verify both the durable archive and the copied evidence retain the note.
    note = Path(cleaned["archive"]["path"]) / "untracked/seed-note.txt"
    assert note.read_bytes() == b"seed prepared before handoff\n"
    assert (captured / f"w07-{kind}-a/archived-seed-note.txt").read_bytes() == note.read_bytes()
    registered_tabs = {s["tab"] for s in a["sessions"]}
    assert set(cleaned["sessions"]["closed_tabs"]) <= registered_tabs
    print(f"{kind}: 4 sessions verified; automatic handoff {history[0]['sample']['total']} tokens; "
          "A integrated -> C started while B working; all outputs integrated; stopped and archived safely")
print("PASS: both real runtimes; total concurrency <= 3 (runs executed sequentially)")
