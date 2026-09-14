"""Required deviation declarations across every worker outcome."""

import json
import subprocess

import pytest

from test_implementer import load_implementer


@pytest.fixture
def result_context(tmp_path):
    def git(*args):
        return subprocess.run(
            ["git", "-C", str(tmp_path), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    git("init", "-b", "worker")
    git("-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "--allow-empty", "-m", "base")
    (tmp_path / "findings.md").write_text("Verified findings\n")
    state = {
        "ticket": {"id": "T1"}, "worker_id": "w1", "branch": "worker",
        "repo": {"root": str(tmp_path), "base": git("rev-parse", "HEAD")},
        "worktree": str(tmp_path), "paths": {"management": str(tmp_path / "management")},
    }
    payload = {
        "ticket_id": "T1", "worker_id": "w1", "status": "delivered",
        "summary": "Investigation complete", "reason": "Need scope decision",
        "acceptance": [{"criterion": "Findings recorded", "met": True, "evidence": "findings.md"}],
        "verification": [{"command": "test -f findings.md", "exit_code": 0, "summary": "exists"}],
        "head": None, "artifacts": ["findings.md"], "remaining": "", "plan_deviations": [],
    }
    return load_implementer(), state, payload


def deviation(needs_decision=False):
    return {
        "planned": "Ticket T1: use endpoint A", "actual": "Endpoint B; findings.md",
        "reason": "A retired; B authorized by coordinator decision D1",
        "impact": "No acceptance change", "needs_decision": needs_decision,
    }


@pytest.mark.parametrize("status", ["delivered", "failed", "needs-decision"])
@pytest.mark.parametrize("entries", [[], [deviation()]])
def test_valid_deviation_declarations(result_context, status, entries):
    pm, state, payload = result_context
    payload.update(status=status, plan_deviations=entries)
    assert pm.validate_result(payload, state) == payload


@pytest.mark.parametrize("status", ["delivered", "failed", "needs-decision"])
@pytest.mark.parametrize("bad", ["missing", None, {}, "none", [None], [{}]])
def test_missing_or_invalid_deviation_declarations(result_context, status, bad):
    pm, state, payload = result_context
    payload["status"] = status
    if bad == "missing":
        del payload["plan_deviations"]
    else:
        payload["plan_deviations"] = bad
    with pytest.raises(pm.ImplementerError, match="plan_deviations"):
        pm.validate_result(payload, state)


@pytest.mark.parametrize("field", ["planned", "actual", "reason", "impact", "needs_decision"])
@pytest.mark.parametrize("bad", [None, "", " ", 0])
def test_deviation_fields_are_required_and_typed(result_context, field, bad):
    pm, state, payload = result_context
    entry = deviation()
    entry[field] = bad
    payload["plan_deviations"] = [entry]
    with pytest.raises(pm.ImplementerError, match=field):
        pm.validate_result(payload, state)


@pytest.mark.parametrize("status", ["delivered", "failed", "needs-decision"])
def test_unresolved_deviation_prevents_delivery(result_context, status):
    pm, state, payload = result_context
    payload.update(status=status, plan_deviations=[deviation(True)])
    if status == "delivered":
        with pytest.raises(pm.ImplementerError, match="needing a decision"):
            pm.validate_result(payload, state)
    else:
        assert pm.validate_result(payload, state)["plan_deviations"][0]["needs_decision"] is True


def test_contract_outcome_examples_include_deviations():
    import re
    from test_implementer import REPO_ROOT

    text = (REPO_ROOT / "docs/implementation/plan-worker.md").read_text()
    outcomes = [json.loads(block) for block in re.findall(r"```json\n(.*?)\n```", text, re.S)]
    outcomes = [entry for entry in outcomes if "status" in entry]
    assert {entry["status"] for entry in outcomes} == {"delivered", "needs-decision"}
    assert all(entry["plan_deviations"] == [] for entry in outcomes)
