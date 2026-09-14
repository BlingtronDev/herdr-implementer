"""Repair document guards and real-Git recipe fixtures.

Fixtures simulate worker edits, not Herdr/model execution or an automatic
coordinator. Ownership/acceptance decisions remain document-level checks.
"""
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_repair_brief_contains_only_task_inputs():
    brief = (ROOT / "docs/implementation/repair-brief.md").read_text()
    for required in ("each side", "full base SHA", "incoming SHA", "provenance",
                     "reproduction command", "observed exit code", "combined behavior"):
        assert required in brief
    for duplicate in ("Publish once", "plan_deviations", "Commit code results",
                      "shared plan", "atomic", "coordinator will"):
        assert duplicate not in brief
    assert "injected worker contract" in brief


def test_normal_closeout_does_not_load_repair_and_covers_failed_goals():
    skill = (ROOT / "SKILL.md").read_text()
    closeout = skill.split("## 5. Check the overall goal, report, and optionally clean up", 1)[1]
    assert "execution-record.md#important-decisions-and-closeout" in closeout
    assert "repair-and-closeout.md" not in closeout
    assert "simple supplementary checks directly" in closeout
    assert "independent or substantial verification" in closeout
    premerge = skill.split("## 4. Integrate serially and unlock dependencies", 1)[1].split("Perform normal merges", 1)[0]
    assert "repair-and-closeout.md" not in premerge
    for evidence in ("target branch", "incoming delivered SHAs", "tracked/untracked", "ownership"):
        assert evidence in premerge
    record = (ROOT / "docs/implementation/execution-record.md").read_text()
    for gate in ("every in-scope goal", "non-code artifacts explicitly accepted",
                 "no unresolved acceptance failure", "**executing**", "**blocked**",
                 "later changes do not invalidate", "before worktree cleanup"):
        assert gate in record


def test_repair_safety_and_baseline_decisions_are_preserved():
    repair = (ROOT / "docs/implementation/repair-and-closeout.md").read_text()
    for guard in ("target branch and `HEAD` match", "`MERGE_HEAD`", "no other writer",
                  "known-safe preconditions", "abort succeeded", "clean status is restored",
                  "Abort unavailable, failed, or unsafe", "Do not force-reset, clean or overwrite",
                  "actual integration and compatibility evidence", "new ticket/worker",
                  "isolated worktree", "squash or patch", "original delivery -> repair delivery",
                  "current target with the repair baseline", "fresh compatibility evidence",
                  "Further implementation needs a new repair worker"):
        assert guard in repair


def git(repo, *args, ok=True):
    result = subprocess.run(["git", "-C", str(repo), *args], text=True, capture_output=True)
    if ok:
        assert result.returncode == 0, result.stderr
    return result


def commit(repo, name, text):
    (repo / name).write_text(text)
    git(repo, "add", name)
    git(repo, "commit", "-m", name)
    return git(repo, "rev-parse", "HEAD").stdout.strip()


@pytest.fixture
def repo(tmp_path):
    tmp_path = tmp_path / "target"
    tmp_path.mkdir()
    git(tmp_path, "init", "-b", "main")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    git(tmp_path, "config", "user.name", "Repair fixture")
    commit(tmp_path, "behavior.txt", "seed\n")
    return tmp_path


@pytest.mark.parametrize("movement", ["none", "documentation", "behavior"])
def test_conflict_abort_isolated_repair_and_actual_combination(repo, movement):
    """Real Git conflict/abort; fixture code stands in for new repair workers."""
    git(repo, "checkout", "-b", "incoming")
    incoming = commit(repo, "behavior.txt", "greeting\n")
    git(repo, "checkout", "main")
    base = commit(repo, "behavior.txt", "normalize\n")
    assert git(repo, "status", "--porcelain").stdout == ""
    conflict = git(repo, "merge", "--no-edit", incoming, ok=False)
    assert conflict.returncode == 1
    assert git(repo, "ls-files", "-u").stdout
    assert git(repo, "rev-parse", "HEAD").stdout.strip() == base
    assert git(repo, "rev-parse", "MERGE_HEAD").stdout.strip() == incoming
    git(repo, "merge", "--abort")
    assert git(repo, "rev-parse", "HEAD").stdout.strip() == base
    assert not git(repo, "ls-files", "-u").stdout
    assert git(repo, "rev-parse", "--verify", "MERGE_HEAD", ok=False).returncode != 0
    assert git(repo, "status", "--porcelain").stdout == ""

    isolated = repo.parent / "repair"
    git(repo, "worktree", "add", "-b", "repair", str(isolated), base)
    assert git(isolated, "merge", "--no-edit", incoming, ok=False).returncode == 1
    repaired = commit(isolated, "behavior.txt", "normalize + greeting\n")
    for sha in (base, incoming):
        git(isolated, "merge-base", "--is-ancestor", sha, repaired)
    assert git(repo, "rev-parse", "HEAD").stdout.strip() == base

    if movement == "documentation":
        commit(repo, "notes.md", "unrelated documentation\n")
    elif movement == "behavior":
        # Textually disjoint target movement can still invalidate repair evidence.
        commit(repo, "requirements.txt", "normalize + greeting + punctuation\n")
    target = git(repo, "rev-parse", "HEAD").stdout.strip()
    assert (target != base) == (movement != "none")
    git(repo, "merge", "--no-edit", repaired)
    expected = "normalize + greeting + punctuation\n" if movement == "behavior" else "normalize + greeting\n"
    compatible = (repo / "behavior.txt").read_text() == expected
    assert compatible == (movement != "behavior")
    if not compatible:
        # A new repair identity from the updated target, never terminal continuation.
        fresh = repo.parent / "repair-next"
        git(repo, "worktree", "add", "-b", "repair-next", str(fresh), "HEAD")
        fixed = commit(fresh, "behavior.txt", expected)
        git(repo, "merge", "--no-edit", fixed)
        assert (repo / "behavior.txt").read_text() == expected
    git(repo, "merge-base", "--is-ancestor", repaired, "HEAD")


def test_non_code_evidence_survives_worktree_removal_without_commit(repo):
    """Git/artifact fixture only; explicit acceptance is guarded in the record doc."""
    before = git(repo, "rev-parse", "HEAD").stdout.strip()
    worker = repo.parent / "verification"
    git(repo, "worktree", "add", "-b", "verification", str(worker), before)
    artifact = worker / "findings.md"
    artifact.write_text("Combined check passed on " + before + "\n")
    durable = repo.parent / "accepted-findings.md"
    durable.write_bytes(artifact.read_bytes())
    assert durable.read_bytes() == artifact.read_bytes()
    artifact.unlink()  # fixture disposition after checking the durable copy
    git(repo, "worktree", "remove", str(worker))
    assert durable.is_file()
    assert git(repo, "rev-parse", "verification").stdout.strip() == before
