"""Regression coverage for terminal release at the completion boundary."""
import json
import time
from pathlib import Path

import pytest

from test_implementer import make_harness  # noqa: F401


@pytest.mark.parametrize("kind", ["pi", "opencode"])
def test_handoff_closes_old_tab_before_replacement_finishes(make_harness, kind):
    h = make_harness("handoff")
    h.env["HI_FAKE_CONTEXT_SPIKE_TOTAL"] = "100"
    h.env["HI_FAKE_CONTEXT_SPIKE_CALLS"] = "1"
    h.env["HI_SCENARIO_CONTINUATION_DELAY"] = "30"
    proc = h.start(kind=kind, handoff_tokens=50)
    assert proc.returncode == 0, proc.stderr
    worker = json.loads(proc.stdout)["worker_id"]
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        facts = h.status(worker)
        if len(facts["sessions"]) == 2:
            old, new = facts["sessions"]
            if not h.tab_open(old["tab"]):
                break
        time.sleep(0.1)
    else:
        pytest.fail("handoff completed but the old tab stayed open")
    assert facts["lifecycle"]["state"] == "running"
    assert h.tab_open(new["tab"])
    assert Path(old["handoff"]["doc"]).is_file()


@pytest.mark.parametrize("kind", ["pi", "opencode"])
def test_delivery_closes_tab_without_deleting_uncommitted_artifacts(make_harness, kind):
    h = make_harness("deliver-noncode")
    proc = h.start(kind=kind)
    assert proc.returncode == 0, proc.stderr
    worker = json.loads(proc.stdout)["worker_id"]
    facts = h.wait_state(worker, {"delivered"})
    facts = h.wait_supervisor_exit(worker)
    assert facts["release"]["state"] == "closed"
    assert not h.tab_open(facts["herdr"]["tab"])
    assert (Path(facts["worktree"]) / "findings.md").is_file()
    assert Path(facts["paths"]["result"]).is_file()


@pytest.mark.parametrize("blocker", ["foreign-occupancy", "tab-close-failed"])
def test_handoff_release_failure_does_not_fail_replacement(make_harness, blocker):
    h = make_harness("handoff")
    h.env["HI_SCENARIO_CONTINUATION_DELAY"] = "30"
    proc = h.start(handoff_tokens=1000000)
    assert proc.returncode == 0, proc.stderr
    worker = json.loads(proc.stdout)["worker_id"]
    facts = h.status(worker)
    tab = facts["herdr"]["tab"]
    if blocker == "foreign-occupancy":
        h.add_pane(tab, "fp:foreign", cwd=str(h.tmp), agent="someone-else")
    else:
        h.fail_tab_close(tab)
    requested = h.handoff(worker)
    assert requested.returncode == 0, requested.stderr
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        facts = h.status(worker)
        old = facts["sessions"][0]
        if old.get("release"):
            break
        time.sleep(0.1)
    else:
        pytest.fail("handoff did not record the old tab release outcome")
    assert old["handoff"]["status"] == "completed"
    assert old["release"]["state"] == "retained"
    assert old["release"]["reason"] == blocker
    assert facts["lifecycle"]["state"] == "running"
    assert len(facts["sessions"]) == 2
    assert h.tab_open(tab)
    assert h.tab_open(facts["sessions"][1]["tab"])
    assert not any(item["kind"] == "exception" for item in facts["items"])
