"""Required deviation declarations across every worker outcome."""

import json
import re
import subprocess

import pytest

from test_implementer import REPO_ROOT, load_implementer


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


CONTRACT = REPO_ROOT / "docs/implementation/plan-worker.md"


def contract_examples(text):
    return [json.loads(block) for block in re.findall(r"```json\n(.*?)\n```", text, re.S)]


def test_contract_outcome_examples_include_deviations():
    outcomes = [entry for entry in contract_examples(CONTRACT.read_text()) if "status" in entry]
    assert {entry["status"] for entry in outcomes} == {"delivered", "needs-decision"}
    assert all(entry["plan_deviations"] == [] for entry in outcomes)


@pytest.mark.parametrize("outcome", ["code", "artifact", "needs-decision", "failed"])
def test_rendered_contract_examples_validate(result_context, outcome):
    """Substitute observed Git/artifact values, then validate the documented payloads."""
    pm, state, _ = result_context
    names = set(re.findall(r"{{([A-Z_]+)}}", CONTRACT.read_text()))
    values = {name: f"value-for-{name}" for name in names}
    values.update(TICKET_ID=state["ticket"]["id"], WORKER_ID=state["worker_id"])
    examples = contract_examples(pm.render_contract(CONTRACT, values))
    status = "delivered" if outcome in {"code", "artifact"} else "needs-decision"
    payload = next(entry for entry in examples if entry.get("status") == status)
    if outcome == "code":
        subprocess.run(
            ["git", "-C", state["worktree"], "add", "findings.md"], check=True,
            capture_output=True, text=True,
        )
        subprocess.run(
            ["git", "-C", state["worktree"], "-c", "user.name=Test", "-c",
             "user.email=test@example.com", "commit", "-m", "Record findings"],
            check=True, capture_output=True, text=True,
        )
        payload["head"] = subprocess.run(
            ["git", "-C", state["worktree"], "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        payload["artifacts"] = []
    elif outcome == "artifact":
        payload.update(head=None, artifacts=["findings.md"])
    elif outcome == "failed":
        payload["status"] = "failed"  # Documented reuse of the decision example.
    assert pm.validate_result(payload, state) == payload


@pytest.mark.parametrize("status", ["delivered", "needs-decision", "failed"])
def test_documented_deviation_entry_validates(result_context, status):
    pm, state, payload = result_context
    entry = next(entry for entry in contract_examples(CONTRACT.read_text()) if "planned" in entry)
    assert set(entry) == {"planned", "actual", "reason", "impact", "needs_decision"}
    entry["needs_decision"] = status != "delivered"
    payload.update(status=status, plan_deviations=[entry])
    assert pm.validate_result(payload, state) == payload


def test_worker_contract_keeps_publication_and_acceptance_boundaries():
    """Text guards only; runtime tests separately exercise lifecycle mechanics."""
    text = CONTRACT.read_text()
    assert "Unconstrained implementation details are ordinary engineering choices" in text
    assert "goal, acceptance, external behavior, dependencies, or an explicitly specified approach" in text
    assert "An unmet criterion cannot be relabeled as a follow-up" in text
    assert "temporary sibling" in text and "rename it over the result path" in text
    assert "After publication, stop business writes" in text
    assert "replacement session can continue as the single writer" in text
    assert "shared plans, tickets and execution records are read-only" in text
    assert "An artifact reference grants no write permission" in text


def test_worker_contract_scopes_delegation_to_read_only_non_interactive_work():
    """A nested agent must not stall invisibly or take over business writes."""
    text = CONTRACT.read_text()
    assert "Prefer delegating" not in text
    assert "Delegation is encouraged and authorized by this contract" in text
    assert "read-only subagents within this ticket's scope" in text
    assert "in parallel for distinct questions" in text
    assert "bounded question and expected evidence" in text
    assert "must not write business files" in text
    assert "change branches or worktrees" in text
    assert "blocking interactive question UI" in text
    assert "stay the single writer" in text
    assert "collect and assess delegated findings before final delivery" in text


def test_coordinator_preserves_worker_delegation_authorization():
    """Dispatch must not turn the single-writer boundary into a single-agent ban."""
    text = (REPO_ROOT / "SKILL.md").read_text()
    assert "This skill authorizes that worker-level delegation" in text
    assert "a single business writer does not mean a single agent" in text
    assert "Encourage workers to use read-only subagents" in text


def test_local_translation_preserves_contract_interface():
    translation = REPO_ROOT / "zh-CN/docs/implementation/plan-worker.md"
    if not translation.is_file():
        pytest.skip("local translation is not distributed in Git")
    english, chinese = CONTRACT.read_text(), translation.read_text()
    assert sorted(re.findall(r"{{([A-Z_]+)}}", english)) == sorted(re.findall(r"{{([A-Z_]+)}}", chinese))

    def shape(value):
        if isinstance(value, dict):
            return {key: shape(child) for key, child in value.items()}
        if isinstance(value, list):
            return [shape(child) for child in value]
        return type(value).__name__

    assert [shape(item) for item in contract_examples(english)] == [
        shape(item) for item in contract_examples(chinese)
    ]
    assert "委派受本契约鼓励并授权" in chinese
    assert "唯一写入者" in chinese
