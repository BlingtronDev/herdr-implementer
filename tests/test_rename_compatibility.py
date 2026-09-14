"""Rename guards: new identity, stable protocol, and old CLI executions."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

import test_implementer as harness_module
from test_implementer import REPO_ROOT, load_implementer, make_harness  # noqa: F401


@pytest.mark.parametrize("operation,args", [
    ("init-run", ["--repo", "/repo", "--kind", "pi", "--provider", "p", "--model", "m", "--thinking", "max", "--max-workers", "1"]),
    ("start", ["--repo", "/repo", "--run", "r", "--ticket-id", "1", "--base", "a" * 40, "--material", "/spec"]),
    ("status", []), ("wait", ["--run", "r"]),
    ("ack", ["--item", "w/i"]), ("read", ["--worker", "w"]),
    ("handoff", ["--worker", "w"]), ("stop", ["--worker", "w"]),
    ("cleanup", ["--worker", "w"]), ("_supervise", ["--worker", "w"]),
])
def test_state_root_alias_on_every_operation(operation, args):
    tool = load_implementer()
    current = tool.parse_args([operation, *args, "--state-root", "/state"])
    legacy = tool.parse_args([operation, *args, "--management-root", "/state"])
    assert vars(current) == vars(legacy)
    assert current.management_root == "/state"


def test_environment_compatibility_and_precedence(monkeypatch, tmp_path):
    tool = load_implementer()
    monkeypatch.delenv("HI_POLL_SECONDS", raising=False)
    monkeypatch.setenv("HPM_POLL_SECONDS", "0.25")
    assert tool.env_float("HI_POLL_SECONDS", 5) == 0.25
    monkeypatch.setenv("HI_POLL_SECONDS", "0.5")
    assert tool.env_float("HI_POLL_SECONDS", 5) == 0.5
    monkeypatch.setenv("HI_POLL_SECONDS", "")
    assert tool.env_float("HI_POLL_SECONDS", 5) == 5
    monkeypatch.setenv("HI_POLL_SECONDS", "invalid")
    with pytest.raises(tool.ImplementerError, match="invalid HI_POLL_SECONDS"):
        tool.env_float("HI_POLL_SECONDS", 5)
    monkeypatch.delenv("HI_CONTEXT_HELPER", raising=False)
    monkeypatch.setenv("HPM_CONTEXT_HELPER", str(tmp_path / "old.py"))
    assert tool.context_helper_path() == tmp_path / "old.py"
    monkeypatch.setenv("HI_CONTEXT_HELPER", str(tmp_path / "new.py"))
    assert tool.context_helper_path() == tmp_path / "new.py"


def test_new_store_does_not_adopt_or_move_old_records(tmp_path):
    tool = load_implementer()
    old = tmp_path / "herdr-plan-manager"
    old.mkdir()
    evidence = old / "retained.txt"
    evidence.write_text("original paths remain authoritative")
    store = tool.Store.resolve(tmp_path)
    assert store.root == tmp_path / "herdr-implementer"
    assert not store.root.exists()
    assert tool.Store.resolve(tmp_path, str(old)).root == old
    assert evidence.read_text() == "original paths remain authoritative"
    assert tool.STATE_VERSION == 3


@pytest.mark.parametrize("legacy", [False, True])
def test_both_entries_deliver_and_preserve_their_namespaces(monkeypatch, make_harness, legacy):
    entry = REPO_ROOT / "bin" / ("plan_manager.py" if legacy else "implementer.py")
    monkeypatch.setattr(harness_module, "IMPLEMENTER", entry)
    h = make_harness("deliver-code")
    proc = h.start(worker_id="w-rename-01")
    assert proc.returncode == 0, proc.stderr
    started = json.loads(proc.stdout)
    prefix = "hpm" if legacy else "hi"
    namespace = "herdr-plan-manager" if legacy else "herdr-implementer"
    assert started["branch"] == f"{prefix}/w-rename-01"
    state_root = h.repo / ".git" / namespace
    assert Path(started["worktree"]) == state_root / "worktrees" / "w-rename-01"
    assert Path(started["management_dir"]) == state_root / "workers" / "w-rename-01"
    facts = h.wait_state("w-rename-01", {"delivered"})
    assert facts["result"]["status"] == "delivered"
    # The new CLI can explicitly inspect either namespace without relocating it.
    status = subprocess.run(
        [sys.executable, str(REPO_ROOT / "bin" / "implementer.py"), "status",
         "--repo", str(h.repo), "--worker", "w-rename-01", "--state-root", str(state_root)],
        env=h.env, capture_output=True, text=True, timeout=30,
    )
    assert status.returncode == 0, status.stderr
    assert json.loads(status.stdout)["branch"] == started["branch"]
    assert h.stop("w-rename-01").returncode == 0
