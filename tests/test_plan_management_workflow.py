"""Issue 07: a master-driven workflow over the real CLI/Git and fake runtimes.

No scheduler is added: the test plays the master, including its integration gate.
Real-provider acceptance remains a separate smoke test.
"""

import json
from pathlib import Path

import pytest

from test_plan_manager import RUNTIMES, branch_exists, make_harness  # noqa: F401


@pytest.mark.parametrize("kind", RUNTIMES)
def test_master_integrates_a_then_starts_c_while_b_runs_after_handoff(make_harness, kind):
    h = make_harness("slow")
    h.env["HPM_FAKE_CONTEXT_COUNTER"] = "context-b"
    run_id = h.ensure_run(kind=kind, max_workers=2)
    b = h.start(kind=kind, run_id=run_id, ticket_id="B", worker_id="w-plan-b")
    assert b.returncode == 0, b.stderr
    h.wait_agent_status("w-plan-b", "working")

    # A crosses sessions while B continues; the same run still has only two slots.
    h.env.update({
        "HPM_FAKE_SCENARIO_BEHAVIOR": "handoff",
        "HPM_FAKE_CONTEXT_COUNTER": "context-a",
        "HPM_FAKE_CONTEXT_SPIKE_TOTAL": "100",
        "HPM_FAKE_CONTEXT_SPIKE_CALLS": "1",
        "HPM_SCENARIO_HANDOFF_DELAY": "2",
    })
    a = h.start(kind=kind, run_id=run_id, ticket_id="A", worker_id="w-plan-a", handoff_tokens=50)
    assert a.returncode == 0, a.stderr
    a_start = json.loads(a.stdout)
    h.wait_state("w-plan-a", {"handing-off"})
    during = h.run_status(run_id)
    assert during["active_count"] == 2
    assert set(during["active_worker_ids"]) == {"w-plan-a", "w-plan-b"}
    assert during["worker_count"] == 2

    delivery = h.wait(run_id, wait_seconds=30, timeout=45)
    assert [(item["worker_id"], item["code"]) for item in delivery["items"]] == [
        ("w-plan-a", "delivered")
    ]
    a_facts = h.wait_state("w-plan-a", {"delivered"})
    assert [session["index"] for session in a_facts["sessions"]] == [1, 2]
    assert a_facts["runtime"] == a_start["runtime"]
    assert a_facts["worktree"] == a_start["worktree"]
    assert a_facts["branch"] == a_start["branch"]
    assert a_facts["sessions"][0]["end_state"] == "replaced"
    assert "NEXT-STEP-MARKER-" in (Path(a_facts["worktree"]) / "continuation.txt").read_text()
    assert h.run_status(run_id)["active_worker_ids"] == ["w-plan-b"]

    # Delivery and ack do not integrate A. C is still deliberately NOT dispatched.
    record = {"A": {"delivered": a_facts["result"]["head"], "integrated": None}}
    assert h.ack(delivery["items"][0]["item_id"], note="accepted; integration pending").returncode == 0
    assert h.git("rev-parse", "HEAD") == h.base
    assert not (h.repo / "continuation.txt").exists()
    assert h.run_status(run_id)["worker_count"] == 2
    assert h.wait(run_id, wait_seconds=0.2)["items"] == []

    # This temporary repo chooses ff-only; the production method follows repo policy.
    assert h.git("status", "--porcelain") == ""
    h.git("merge", "--ff-only", record["A"]["delivered"])
    record["A"]["integrated"] = h.git("rev-parse", "HEAD")
    assert record["A"]["integrated"] != h.base
    assert (h.repo / "continuation.txt").is_file()

    h.env["HPM_FAKE_SCENARIO_BEHAVIOR"] = "deliver-code"
    h.env["HPM_FAKE_CONTEXT_COUNTER"] = "context-c"
    h.env["HPM_SCENARIO_DELAY"] = "2"
    for key in ("HPM_FAKE_CONTEXT_SPIKE_TOTAL", "HPM_FAKE_CONTEXT_SPIKE_CALLS"):
        h.env.pop(key)
    c = h.start(
        kind=kind, run_id=run_id, ticket_id="C", worker_id="w-plan-c",
        base=record["A"]["integrated"],
    )
    assert c.returncode == 0, c.stderr
    c_start = json.loads(c.stdout)
    assert c_start["base"] == record["A"]["integrated"]
    assert (Path(c_start["worktree"]) / "continuation.txt").read_text() == (
        h.repo / "continuation.txt"
    ).read_text()
    assert h.status("w-plan-b")["herdr"]["agent_status"] == "working"
    assert c_start["active_workers"] == 2
    assert h.run_status(run_id)["active_count"] <= 2
    later = h.wait(run_id, wait_seconds=15)
    assert [(item["worker_id"], item["code"]) for item in later["items"]] == [
        ("w-plan-c", "delivered")
    ]
    c_facts = h.wait_state("w-plan-c", {"delivered"})
    h.git("merge", "--ff-only", c_facts["result"]["head"])
    assert h.ack(later["items"][0]["item_id"], note="C integrated").returncode == 0
    assert h.run_status(run_id)["active_worker_ids"] == ["w-plan-b"]

    # Handoff left partial work. Integration is not permission to discard it.
    stopped = h.stop("w-plan-a")
    assert stopped.returncode == 0, stopped.stderr
    assert json.loads(stopped.stdout)["business_stopped"] is True
    refused = h.cleanup("w-plan-a", "--integrated", record["A"]["integrated"])
    assert refused.returncode == 3
    assert "uncommitted-content" in refused.stderr
    partial = Path(a_facts["worktree"]) / "partial.txt"
    content = partial.read_text()
    cleaned = h.cleanup(
        "w-plan-a", "--integrated", record["A"]["integrated"], "--archive-uncommitted"
    )
    assert cleaned.returncode == 0, cleaned.stderr
    archive = Path(json.loads(cleaned.stdout)["archive"]["path"])
    assert (archive / "untracked" / "partial.txt").read_text() == content
    assert branch_exists(h.repo, a_facts["branch"])
    assert Path(a_facts["paths"]["result"]).is_file()
    assert h.status("w-plan-a")["cleanup"]["cleaned"] is True

    # B is deliberately unfinished in this deterministic scenario, not a success.
    b_stopped = h.stop("w-plan-b")
    assert b_stopped.returncode == 0, b_stopped.stderr
    assert json.loads(b_stopped.stdout)["business_stopped"] is True
    b_facts = h.status("w-plan-b")
    assert b_facts["result"] is None
    assert Path(b_facts["worktree"]).is_dir()
    assert h.run_status(run_id)["active_count"] == 0
