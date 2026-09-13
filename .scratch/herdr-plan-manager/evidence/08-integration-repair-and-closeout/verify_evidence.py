#!/usr/bin/env python3
"""Offline assertions over captured real evidence; does not invoke providers."""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(path):
    return json.loads(path.read_text())


def response(log, name):
    command = load(log / f"{name}.json")
    assert command["exit_code"] == 0
    return json.loads(command["stdout"])


intervals = []
for kind in ("pi", "opencode"):
    log = HERE / "logs" / kind
    capture = log / "captured"
    integration = load(log / "integration.json")
    assert load(log / "repair-inputs.json")["goal"] == "executing"
    assert integration["goal"] == "complete" and not integration["unresolved"]
    assert load(log / "combined-success.json")["exit_code"] == 0
    assert response(log, "retained-run")["active_count"] == 0
    configs = load(capture / "runtime-config.json")
    assert len(configs) == 3
    states = {}
    for role in ("a", "b", "r"):
        worker = f"w08-{kind}-{role}"
        s = load(capture / worker / "state.json")
        result = load(capture / worker / "result.json")
        states[role] = s
        assert s["worker_id"] == result["worker_id"] == worker
        assert result["status"] == "delivered" and not result["remaining"]
        assert all(item["met"] for item in result["acceptance"])
        assert response(log, f"stop-{role.upper()}")["business_stopped"]
        assert len(s["sessions"]) == 1
        session = s["sessions"][0]
        intervals.extend([(session["started_at"], 1), (session["ended_at"], -1)])
        config = next(c for c in configs if c["worker"] == worker)
        if kind == "pi":
            events = config["events"]
            models = [e for e in events if e["type"] == "model_change"]
            thinking = [e for e in events if e["type"] == "thinking_level_change"]
            assert models and thinking
            assert all((e["provider"], e["modelId"]) == ("opencode-go", "deepseek-v4.1-flash") for e in models)
            assert all(e["thinkingLevel"] == "max" for e in thinking)
            assert next(e["cwd"] for e in events if e["type"] == "session") == s["worktree"]
        else:
            assert config["messages"]
            assert all((m["providerID"], m["modelID"], m["variant"], m["path"]["cwd"]) ==
                       ("opencode-go", "deepseek-v4.1-flash", "max", s["worktree"]) for m in config["messages"])
    assert states["r"]["repo"]["base"] == integration["base"]
    for role, key in (("a", "a"), ("b", "b"), ("r", "repair")):
        assert load(capture / f"w08-{kind}-{role}" / "result.json")["head"] == integration[key]
    assert load(log / "merge-repair.json")["argv"][-1] == integration["repair"]
    assert load(log / "integrated-head.json")["stdout"].strip() == integration["integrated"]
    assert states["r"]["sessions"][0]["started_at"] > max(states[r]["sessions"][0]["ended_at"] for r in ("a", "b"))
    assert len({s["worktree"] for s in states.values()}) == 3
    assert load(log / "after-ack-head.json")["stdout"].strip() == load(log / "fixture.json")["seed"]
    assert load(log / "ack-repair-target.json")["stdout"].strip() == integration["pre_merge"]
    calls = [c for c in load(capture / "tool-calls.json") if c["worker"] == f"w08-{kind}-r"]
    commands = [c["input"].get("command", "") for c in calls]
    assert any("git commit" in c for c in commands)
    assert any("python3" in c and "test_" in c for c in commands)
    source = "formatter.py" if kind == "pi" else "consumer.py"
    assert any(c["tool"] in ("write", "edit") and
               Path(c["input"].get("path", c["input"].get("filePath", ""))).name == source for c in calls)
    # Retain raw tool outputs as evidence; commands containing an echo can exit
    # successfully despite a failed inner check, so inspect the test output too.
    outputs = json.dumps(load(capture / "repair-tool-results.json"))
    assert "Ran 3 tests" in outputs and "OK" in outputs
    # The coordinator's logged target operations never resolve conflict files.
    for path in log.glob("*.json"):
        argv = load(path).get("argv", [])
        if argv[:1] == ["git"]:
            assert argv[1] not in ("reset", "clean", "checkout", "restore")
    print(f"PASS {kind}: actual configuration/cwd, new isolated repair, commit/test attribution, ack/integration, stopped retention")

pi = HERE / "logs/pi"
assert load(pi / "merge-B.json")["exit_code"] == 1
assert "formatter.py" in load(pi / "conflict-index.json")["stdout"]
assert load(pi / "before-B.json") == load(pi / "after-abort.json")
assert response(pi, "status-R")["lifecycle"]["state"] == "running"
assert load(pi / "integration.json")["target_delta"] == "NOTICE.md"
assert load(pi / "integration.json")["base"] != load(pi / "integration.json")["pre_merge"]
oc = HERE / "logs/opencode"
assert load(oc / "merge-B.json")["exit_code"] == 0
assert load(oc / "combined-failure.json")["exit_code"] == 1
assert "AssertionError: $1200.00" in load(oc / "combined-failure.json")["stderr"]
fault = load(HERE / "logs/abort-boundaries.json")
assert fault["before"] == fault["after"] and fault["preserved"]
assert any(c["argv"] == ["git", "merge", "--abort"] and c["exit_code"] == 128 for c in fault["commands"])
active = peak = 0
for _, change in sorted(intervals):
    active += change
    peak = max(peak, active)
assert active == 0 and peak <= 3
print(f"PASS conflict/abort, moving target, behavior failure then repair, preserved abort failure; 6 real sessions, peak={peak} <= 3")
