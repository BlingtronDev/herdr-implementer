from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PLAN_MANAGER = REPO_ROOT / "bin" / "plan_manager.py"
FAKE_BIN = REPO_ROOT / "tests" / "fakes" / "bin"
SCENARIO = REPO_ROOT / "tests" / "fakes" / "scenario.py"
MATERIAL_TEXT = "SPEC MATERIAL 42\n"


class Harness:
    def __init__(self, tmp_path: Path, behavior: str):
        self.tmp = tmp_path
        self.fake = tmp_path / "fake"
        self.fake.mkdir(parents=True, exist_ok=True)
        self.repo = tmp_path / "repo"
        self.repo.mkdir()
        self.started: list[str] = []
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.com")
        (self.repo / "README.md").write_text("hello\n", encoding="utf-8")
        (self.repo / ".gitignore").write_text(".scratch/\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-m", "initial")
        self.base = self.git("rev-parse", "HEAD")
        scratch = self.repo / ".scratch"
        scratch.mkdir()
        self.material = scratch / "spec.md"
        self.material.write_text(MATERIAL_TEXT, encoding="utf-8")
        self.env = os.environ.copy()
        self.env.update(
            {
                "PATH": f"{FAKE_BIN}{os.pathsep}{self.env.get('PATH', '')}",
                "HERDR_ENV": "1",
                "HERDR_WORKSPACE_ID": "test-ws",
                "HPM_FAKE_DIR": str(self.fake),
                "HPM_FAKE_SCENARIO": str(SCENARIO),
                "HPM_FAKE_SCENARIO_BEHAVIOR": behavior,
                "HPM_POLL_SECONDS": "0.2",
                "HPM_SETTLE_GRACE_SECONDS": "0.6",
                "HPM_REPORT_WAIT_SECONDS": "1.5",
                "HPM_SCENARIO_DELAY": "0.3",
            }
        )

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=self.repo, text=True, capture_output=True, check=True
        ).stdout.strip()

    def run(self, *args: str, timeout: float = 30) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(PLAN_MANAGER), *args],
            cwd=self.repo,
            env=self.env,
            text=True,
            capture_output=True,
            timeout=timeout,
        )

    def start(self, **overrides) -> subprocess.CompletedProcess[str]:
        args = [
            "start",
            "--repo",
            str(self.repo),
            "--ticket-id",
            overrides.get("ticket_id", "02"),
            "--title",
            "test ticket",
            "--base",
            overrides.get("base", self.base),
            "--material",
            str(self.material),
            "--kind",
            overrides.get("kind", "pi"),
            "--provider",
            "opencode-go",
            "--model",
            overrides.get("model", "deepseek-v4.1-flash"),
            "--thinking",
            overrides.get("thinking", "max"),
        ]
        if overrides.get("worker_id"):
            args += ["--worker-id", overrides["worker_id"]]
        if overrides.get("branch"):
            args += ["--branch", overrides["branch"]]
        proc = self.run(*args)
        if proc.returncode == 0:
            self.started.append(json.loads(proc.stdout)["worker_id"])
        return proc

    def status(self, worker_id: str) -> dict:
        proc = self.run("status", "--repo", str(self.repo), "--worker", worker_id)
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout)

    def stop(self, worker_id: str, *, timeout: float = 30) -> subprocess.CompletedProcess[str]:
        return self.run("stop", "--repo", str(self.repo), "--worker", worker_id, timeout=timeout)

    def wait_state(self, worker_id: str, states: set[str], timeout: float = 30) -> dict:
        deadline = time.time() + timeout
        facts = None
        while time.time() < deadline:
            facts = self.status(worker_id)
            if facts["lifecycle"]["state"] in states:
                return facts
            time.sleep(0.2)
        raise AssertionError(f"worker {worker_id} never reached {states}: {facts and facts['lifecycle']}")

    def wait_agent_status(self, worker_id: str, status: str, timeout: float = 30) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.status(worker_id)["herdr"]["agent_status"] == status:
                return
            time.sleep(0.2)
        raise AssertionError(f"agent status never became {status}")

    def kill_supervisor(self, worker_id: str) -> None:
        facts = self.status(worker_id)
        pid = facts["supervisor"]["pid"]
        if pid and facts["supervisor"]["alive"]:
            os.kill(pid, signal.SIGKILL)


@pytest.fixture
def make_harness(tmp_path):
    created: list[Harness] = []

    def factory(behavior: str) -> Harness:
        harness = Harness(tmp_path, behavior)
        created.append(harness)
        return harness

    yield factory
    for harness in created:
        for worker_id in harness.started:
            try:
                harness.stop(worker_id, timeout=15)
            except Exception:
                pass
            try:
                harness.kill_supervisor(worker_id)
            except Exception:
                pass


def test_delivered_code_ticket(make_harness):
    h = make_harness("deliver-code")
    proc = h.start()
    assert proc.returncode == 0, proc.stderr
    worker_id = json.loads(proc.stdout)["worker_id"]
    facts = h.wait_state(worker_id, {"delivered"})
    assert facts["result"]["status"] == "delivered"
    assert facts["result"]["head"] == facts["branch_head"]
    assert facts["items"][0]["kind"] == "delivery"
    assert facts["supervisor"]["state"] == "exited"
    worktree = Path(facts["worktree"])
    assert not (worktree / ".scratch").exists(), "worktree must not carry ignored main-checkout material"
    committed = subprocess.run(
        ["git", "show", "HEAD:material-note.txt"],
        cwd=worktree,
        text=True,
        capture_output=True,
        check=True,
    ).stdout
    assert committed == MATERIAL_TEXT, "worker must read the controlled material snapshot"
    manifest = json.loads(
        (Path(facts["paths"]["management"]) / "materials" / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["materials"][0]["source"] == str(h.material)
    assert manifest["materials"][0]["sha256"]
    transcript = Path(facts["paths"]["management"]) / "supervisor.log"
    assert "supervisor started" in transcript.read_text(encoding="utf-8")


def test_delivered_noncode_ticket(make_harness):
    h = make_harness("deliver-noncode")
    proc = h.start()
    assert proc.returncode == 0, proc.stderr
    worker_id = json.loads(proc.stdout)["worker_id"]
    facts = h.wait_state(worker_id, {"delivered"})
    assert facts["result"]["head"] is None
    assert facts["result"]["artifacts"] == ["findings.md"]
    assert facts["branch_head"] == facts["repo"]["base"]
    assert h.git("rev-list", "--count", f"{h.base}..{facts['branch']}") == "0"
    assert (Path(facts["worktree"]) / "findings.md").is_file()


def test_idle_without_result_is_not_delivery(make_harness):
    h = make_harness("missing-result")
    proc = h.start()
    assert proc.returncode == 0, proc.stderr
    worker_id = json.loads(proc.stdout)["worker_id"]
    facts = h.wait_state(worker_id, {"protocol-failure"})
    assert facts["result"] is None
    codes = [item["code"] for item in facts["items"]]
    assert "missing-result" in codes
    agent = json.loads((h.fake / "agents" / f"{facts['herdr']['agent']}.json").read_text(encoding="utf-8"))
    assert agent["prompt_count"] == 2, "exactly one result re-report may be sent"


def test_wrong_head_is_protocol_failure(make_harness):
    h = make_harness("wrong-head")
    proc = h.start()
    assert proc.returncode == 0, proc.stderr
    worker_id = json.loads(proc.stdout)["worker_id"]
    facts = h.wait_state(worker_id, {"protocol-failure"})
    assert facts["result"] is None
    messages = " ".join(item["message"] for item in facts["items"])
    assert "does not match branch HEAD" in messages


def test_wrong_worker_identity_is_protocol_failure(make_harness):
    h = make_harness("wrong-worker")
    proc = h.start()
    assert proc.returncode == 0, proc.stderr
    worker_id = json.loads(proc.stdout)["worker_id"]
    facts = h.wait_state(worker_id, {"protocol-failure"})
    assert any("worker_id" in item["message"] for item in facts["items"])


def test_needs_decision_is_an_exception(make_harness):
    h = make_harness("needs-decision")
    proc = h.start()
    assert proc.returncode == 0, proc.stderr
    worker_id = json.loads(proc.stdout)["worker_id"]
    facts = h.wait_state(worker_id, {"needs-decision"})
    assert facts["items"][0]["kind"] == "exception"
    assert facts["items"][0]["code"] == "needs-decision"


def test_stop_retains_scene_and_is_idempotent(make_harness):
    h = make_harness("slow")
    proc = h.start()
    assert proc.returncode == 0, proc.stderr
    worker_id = json.loads(proc.stdout)["worker_id"]
    h.wait_agent_status(worker_id, "working")
    stopped = h.stop(worker_id)
    assert stopped.returncode == 0, stopped.stderr
    assert json.loads(stopped.stdout)["stopped"] is True
    facts = h.status(worker_id)
    assert facts["lifecycle"]["state"] == "stopped"
    assert facts["supervisor"]["state"] == "exited"
    assert Path(facts["worktree"]).is_dir()
    assert h.git("rev-parse", "--verify", facts["branch"])
    again = h.stop(worker_id)
    assert again.returncode == 0
    assert json.loads(again.stdout)["already_stopped"] is True


def test_start_retries_transient_pane_busy(make_harness):
    h = make_harness("deliver-code")
    (h.fake / "start_failures").write_text("1", encoding="utf-8")
    proc = h.start()
    assert proc.returncode == 0, proc.stderr
    entries = [json.loads(line) for line in (h.fake / "herdr.log.jsonl").read_text(encoding="utf-8").splitlines()]
    starts = [entry for entry in entries if entry["argv"][:2] == ["agent", "start"]]
    assert len(starts) == 2


def test_uncertain_prompt_is_observed_not_resent(make_harness):
    h = make_harness("deliver-code")
    (h.fake / "prompt_uncertain").write_text("1", encoding="utf-8")
    proc = h.start()
    assert proc.returncode == 0, proc.stderr
    worker_id = json.loads(proc.stdout)["worker_id"]
    facts = h.wait_state(worker_id, {"delivered"})
    agent = json.loads((h.fake / "agents" / f"{facts['herdr']['agent']}.json").read_text(encoding="utf-8"))
    assert agent["prompt_count"] == 1, "an accepted-but-unconfirmed prompt must not be resent"


def test_invalid_configuration_fails_before_delivery(make_harness):
    h = make_harness("deliver-code")
    bad_thinking = h.start(thinking="turbo")
    assert bad_thinking.returncode == 2
    assert "unsupported thinking level" in bad_thinking.stderr
    bad_model = h.start(model="does-not-exist")
    assert bad_model.returncode == 2
    assert "does not list model" in bad_model.stderr
    bad_base = h.start(base="deadbeef")
    assert bad_base.returncode == 2
    assert "full commit SHA" in bad_base.stderr
    assert not (h.fake / "herdr.log.jsonl").exists()
    assert not list((h.repo / ".git" / "herdr-plan-manager").glob("workers/*"))


def test_registered_worker_and_branch_are_not_reused(make_harness):
    h = make_harness("deliver-code")
    first = h.start(worker_id="w-fixed-01")
    assert first.returncode == 0, first.stderr
    duplicated = h.start(worker_id="w-fixed-01")
    assert duplicated.returncode == 2
    assert "already registered" in duplicated.stderr
    branch_taken = h.start(worker_id="w-other-01", branch="hpm/w-fixed-01")
    assert branch_taken.returncode == 2
    assert "already exists" in branch_taken.stderr


def test_supervisor_missing_is_visible_and_stoppable(make_harness):
    h = make_harness("slow")
    proc = h.start()
    assert proc.returncode == 0, proc.stderr
    worker_id = json.loads(proc.stdout)["worker_id"]
    h.wait_agent_status(worker_id, "working")
    h.kill_supervisor(worker_id)
    deadline = time.time() + 10
    facts = h.status(worker_id)
    while time.time() < deadline and facts["supervisor"]["alive"]:
        time.sleep(0.2)
        facts = h.status(worker_id)
    assert facts["supervisor"]["alive"] is False
    assert "supervisor-missing" in facts["supervisor"]["warnings"]
    stopped = h.stop(worker_id)
    assert stopped.returncode == 0, stopped.stderr
    assert h.status(worker_id)["lifecycle"]["state"] == "stopped"
