from __future__ import annotations

import importlib.util
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
RUNTIMES = ["pi", "opencode"]


def load_plan_manager():
    spec = importlib.util.spec_from_file_location("plan_manager_under_test", PLAN_MANAGER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def herdr_calls(harness: "Harness", prefix: tuple[str, ...]) -> list[list[str]]:
    """Return recorded herdr argv entries beginning with the given prefix."""
    entries = [
        json.loads(line)
        for line in (harness.fake / "herdr.log.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    return [entry["argv"] for entry in entries if tuple(entry["argv"][: len(prefix)]) == prefix]


def env_flag_values(argv: list[str]) -> list[str]:
    return [argv[index + 1] for index, value in enumerate(argv) if value == "--env"]


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
        os_temp = tmp_path / "os-temp"
        os_temp.mkdir()
        self.env.update(
            {
                "PATH": f"{FAKE_BIN}{os.pathsep}{self.env.get('PATH', '')}",
                "HERDR_ENV": "1",
                "HERDR_WORKSPACE_ID": "test-ws",
                "HPM_FAKE_DIR": str(self.fake),
                "HPM_FAKE_SCENARIO": str(SCENARIO),
                "HPM_FAKE_SCENARIO_BEHAVIOR": behavior,
                "HPM_CONTEXT_HELPER": str(FAKE_BIN / "get_context.py"),
                "HPM_CONTEXT_POLL_SECONDS": "0.2",
                "HPM_HANDOFF_SETTLE_SECONDS": "0.5",
                "HPM_HANDOFF_SETTLE_TIMEOUT_SECONDS": "1.0",
                "HPM_HANDOFF_WAIT_SECONDS": "6",
                "HPM_HANDOFF_CORRECTION_SECONDS": "1.5",
                "HPM_POLL_SECONDS": "0.2",
                "HPM_SETTLE_GRACE_SECONDS": "0.6",
                "HPM_REPORT_WAIT_SECONDS": "1.5",
                "HPM_SCENARIO_DELAY": "0.3",
                "HPM_PROMPT_CONFIRM_SECONDS": "0.5",
                "HPM_PROMPT_ATTEMPTS": "3",
                "TMPDIR": str(os_temp),
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
        if overrides.get("handoff_tokens") is not None:
            args += ["--handoff-tokens", str(overrides["handoff_tokens"])]
        if overrides.get("handoff_pct") is not None:
            args += ["--handoff-pct", str(overrides["handoff_pct"])]
        if overrides.get("context_window") is not None:
            args += ["--context-window", str(overrides["context_window"])]
        proc = self.run(*args)
        if proc.returncode == 0:
            self.started.append(json.loads(proc.stdout)["worker_id"])
        return proc

    def handoff(self, worker_id: str, reason: str = "", *, timeout: float = 30) -> subprocess.CompletedProcess[str]:
        return self.run("handoff", "--repo", str(self.repo), "--worker", worker_id, "--reason", reason, timeout=timeout)

    def agent(self, name: str) -> dict:
        return json.loads((self.fake / "agents" / f"{name}.json").read_text(encoding="utf-8"))

    def prompts(self, name: str) -> list[str]:
        files = sorted(
            (self.fake / "agents").glob(f"{name}.prompt.*.txt"),
            key=lambda path: int(path.stem.rsplit(".", 1)[1]),
        )
        return [path.read_text(encoding="utf-8") for path in files]

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


@pytest.mark.parametrize("kind", RUNTIMES)
def test_delivered_code_ticket(make_harness, kind):
    h = make_harness("deliver-code")
    proc = h.start(kind=kind)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["prompt_attempts"] == 1
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


@pytest.mark.parametrize("kind", RUNTIMES)
def test_delivered_noncode_ticket(make_harness, kind):
    h = make_harness("deliver-noncode")
    proc = h.start(kind=kind)
    assert proc.returncode == 0, proc.stderr
    worker_id = json.loads(proc.stdout)["worker_id"]
    facts = h.wait_state(worker_id, {"delivered"})
    assert facts["result"]["head"] is None
    assert facts["result"]["artifacts"] == ["findings.md"]
    assert facts["branch_head"] == facts["repo"]["base"]
    assert h.git("rev-list", "--count", f"{h.base}..{facts['branch']}") == "0"
    assert (Path(facts["worktree"]) / "findings.md").is_file()


@pytest.mark.parametrize("kind", RUNTIMES)
def test_idle_without_result_is_not_delivery(make_harness, kind):
    h = make_harness("missing-result")
    proc = h.start(kind=kind)
    assert proc.returncode == 0, proc.stderr
    worker_id = json.loads(proc.stdout)["worker_id"]
    facts = h.wait_state(worker_id, {"protocol-failure"})
    assert facts["result"] is None
    codes = [item["code"] for item in facts["items"]]
    assert "missing-result" in codes
    agent = json.loads((h.fake / "agents" / f"{facts['herdr']['agent']}.json").read_text(encoding="utf-8"))
    assert agent["prompt_count"] == 2, "exactly one result re-report may be sent"


@pytest.mark.parametrize("kind", RUNTIMES)
def test_wrong_head_is_protocol_failure(make_harness, kind):
    h = make_harness("wrong-head")
    proc = h.start(kind=kind)
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


@pytest.mark.parametrize("kind", RUNTIMES)
def test_needs_decision_is_an_exception(make_harness, kind):
    h = make_harness("needs-decision")
    proc = h.start(kind=kind)
    assert proc.returncode == 0, proc.stderr
    worker_id = json.loads(proc.stdout)["worker_id"]
    facts = h.wait_state(worker_id, {"needs-decision"})
    assert facts["items"][0]["kind"] == "exception"
    assert facts["items"][0]["code"] == "needs-decision"


@pytest.mark.parametrize("kind", RUNTIMES)
def test_stop_retains_scene_and_is_idempotent(make_harness, kind):
    h = make_harness("slow")
    proc = h.start(kind=kind)
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


@pytest.mark.parametrize("kind", RUNTIMES)
def test_uncertain_prompt_is_observed_not_resent(make_harness, kind):
    h = make_harness("deliver-code")
    (h.fake / "prompt_uncertain").write_text("1", encoding="utf-8")
    proc = h.start(kind=kind)
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


def test_opencode_injects_confirmed_config_without_touching_repo_config(make_harness):
    h = make_harness("deliver-code")
    repo_config = h.repo / "opencode.json"
    original_config = json.dumps(
        {"permission": {"webfetch": "deny"}, "agent": {"build": {"description": "repo local"}}}
    ) + "\n"
    repo_config.write_text(original_config, encoding="utf-8")
    h.git("add", "opencode.json")
    h.git("commit", "-m", "repo opencode config")
    h.base = h.git("rev-parse", "HEAD")

    proc = h.start(kind="opencode", worker_id="w-oc-cfg-01")
    assert proc.returncode == 0, proc.stderr
    worker_id = json.loads(proc.stdout)["worker_id"]
    facts = h.wait_state(worker_id, {"delivered"})

    tabs = herdr_calls(h, ("tab", "create"))
    assert len(tabs) == 1
    expected = '{"agent":{"build":{"model":"opencode-go/deepseek-v4.1-flash","variant":"max"}}}'
    assert env_flag_values(tabs[0]) == [f"OPENCODE_CONFIG_CONTENT={expected}"]

    starts = herdr_calls(h, ("agent", "start"))
    assert len(starts) == 1
    tail = starts[0][starts[0].index("--") + 1 :]
    assert tail == ["--auto"], "OpenCode worker must auto-approve non-denied permissions (E03)"

    assert facts["runtime"] == {
        "kind": "opencode",
        "provider": "opencode-go",
        "model": "deepseek-v4.1-flash",
        "thinking": "max",
        "env": {"OPENCODE_CONFIG_CONTENT": expected},
    }
    assert repo_config.read_text(encoding="utf-8") == original_config
    assert h.git("status", "--porcelain") == ""

    queries = [
        json.loads(line)["argv"]
        for line in (h.fake / "opencode.log.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert ["models", "opencode-go", "--verbose"] in queries

    read = h.run("read", "--repo", str(h.repo), "--worker", worker_id)
    assert read.returncode == 0, read.stderr


def test_pi_start_passes_explicit_args_without_config_injection(make_harness):
    h = make_harness("deliver-code")
    proc = h.start(kind="pi", worker_id="w-pi-args-01")
    assert proc.returncode == 0, proc.stderr
    worker_id = json.loads(proc.stdout)["worker_id"]
    facts = h.wait_state(worker_id, {"delivered"})

    tabs = herdr_calls(h, ("tab", "create"))
    assert env_flag_values(tabs[0]) == []
    starts = herdr_calls(h, ("agent", "start"))[0]
    tail = starts[starts.index("--") + 1 :]
    assert tail == ["--provider", "opencode-go", "--model", "deepseek-v4.1-flash", "--thinking", "max"]
    assert "env" not in facts["runtime"]


def test_opencode_rejects_unsupported_configuration_before_delivery(make_harness):
    h = make_harness("deliver-code")

    unknown_model = h.start(kind="opencode", model="does-not-exist")
    assert unknown_model.returncode == 2
    assert "does not list model" in unknown_model.stderr

    bad_variant = h.start(kind="opencode", thinking="turbo")
    assert bad_variant.returncode == 2
    assert "does not support variant" in bad_variant.stderr

    no_variant = h.start(kind="opencode", model="no-variant-model")
    assert no_variant.returncode == 2
    assert "no reasoning variants" in no_variant.stderr

    assert not (h.fake / "herdr.log.jsonl").exists()
    assert not list((h.repo / ".git" / "herdr-plan-manager").glob("workers/*"))


def test_opencode_blocked_is_an_exception_not_delivery(make_harness):
    h = make_harness("blocked")
    proc = h.start(kind="opencode")
    assert proc.returncode == 0, proc.stderr
    worker_id = json.loads(proc.stdout)["worker_id"]

    deadline = time.time() + 15
    facts = h.status(worker_id)
    while time.time() < deadline and not any(item["code"] == "blocked" for item in facts["items"]):
        time.sleep(0.2)
        facts = h.status(worker_id)
    assert any(item["code"] == "blocked" for item in facts["items"]), facts["items"]
    assert facts["result"] is None
    assert facts["lifecycle"]["state"] not in {"delivered", "failed", "needs-decision"}

    stopped = h.stop(worker_id)
    assert stopped.returncode == 0, stopped.stderr
    assert h.status(worker_id)["lifecycle"]["state"] == "stopped"


@pytest.mark.parametrize("kind", RUNTIMES)
def test_dropped_prompt_is_redelivered_until_confirmed(make_harness, kind):
    h = make_harness("deliver-code")
    (h.fake / "drop_prompts").write_text("1", encoding="utf-8")
    proc = h.start(kind=kind)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["prompt_attempts"] == 2
    worker_id = json.loads(proc.stdout)["worker_id"]
    facts = h.wait_state(worker_id, {"delivered"})
    assert facts["prompt"]["attempts"] == 2
    agent = json.loads((h.fake / "agents" / f"{facts['herdr']['agent']}.json").read_text(encoding="utf-8"))
    assert agent["prompt_count"] == 2


def test_context_observation_records_interpretable_sample(make_harness):
    h = make_harness("slow")
    proc = h.start()
    assert proc.returncode == 0, proc.stderr
    worker_id = json.loads(proc.stdout)["worker_id"]
    deadline = time.time() + 10
    facts = h.status(worker_id)
    while time.time() < deadline and not (facts["session"] or {}).get("context"):
        time.sleep(0.2)
        facts = h.status(worker_id)
    sample = facts["session"]["context"]
    assert sample["state"] == "current"
    assert sample["total"] == 10
    assert sample["window"] == 1000
    assert sample["freshness"] == "completed-call"
    assert sample["source"] == "precise"
    assert sample["session"] == 1
    assert sample["context_ref"]
    assert facts["handoff"]["history"] == []
    assert facts["session"]["index"] == 1
    assert h.stop(worker_id).returncode == 0


def test_stale_context_sample_never_triggers_a_handoff(make_harness):
    h = make_harness("deliver-code")
    h.env["HPM_FAKE_CONTEXT_MODE"] = "stale"
    proc = h.start(handoff_tokens=1, handoff_pct=0.001)
    assert proc.returncode == 0, proc.stderr
    worker_id = json.loads(proc.stdout)["worker_id"]
    facts = h.wait_state(worker_id, {"delivered"})
    assert facts["handoff"]["history"] == []
    assert facts["sessions"][0]["context"]["state"] == "stale"
    assert h.agent(facts["herdr"]["agent"])["prompt_count"] == 1


def test_unobservable_context_is_an_exception_not_zero_usage(make_harness):
    h = make_harness("missing-result")
    h.env["HPM_FAKE_CONTEXT_MODE"] = "error"
    proc = h.start(handoff_tokens=1, handoff_pct=0.001)
    assert proc.returncode == 0, proc.stderr
    worker_id = json.loads(proc.stdout)["worker_id"]
    facts = h.wait_state(worker_id, {"protocol-failure"})
    codes = [item["code"] for item in facts["items"]]
    assert "context-unobservable" in codes
    assert facts["handoff"]["history"] == []
    assert facts["session"]["context"]["state"] == "unobservable"
    assert h.agent(facts["herdr"]["agent"])["prompt_count"] == 2, "only the result re-report may be sent"


@pytest.mark.parametrize("kind", RUNTIMES)
def test_context_threshold_triggers_automatic_handoff(make_harness, kind):
    h = make_harness("handoff")
    h.env["HPM_FAKE_CONTEXT_SPIKE_TOTAL"] = "100"
    h.env["HPM_FAKE_CONTEXT_SPIKE_CALLS"] = "1"
    proc = h.start(kind=kind, handoff_tokens=50)
    assert proc.returncode == 0, proc.stderr
    worker_id = json.loads(proc.stdout)["worker_id"]
    facts = h.wait_state(worker_id, {"delivered"}, timeout=60)
    sessions = facts["sessions"]
    assert [session["index"] for session in sessions] == [1, 2]
    assert sessions[0]["status"] == "replaced"
    assert sessions[0]["handoff"]["status"] == "completed"
    assert sessions[0]["agent"] != sessions[1]["agent"]
    assert facts["herdr"]["agent"] == sessions[1]["agent"]
    history = facts["handoff"]["history"]
    assert len(history) == 1
    assert history[0]["trigger"] == "tokens"
    doc = Path(history[0]["doc"])
    assert doc.is_file()
    assert doc.parent == Path(facts["paths"]["management"]) / "handoffs"
    marker = f"NEXT-STEP-MARKER-{doc.stem}"
    assert marker in doc.read_text(encoding="utf-8")
    continuation = (Path(facts["worktree"]) / "continuation.txt").read_text(encoding="utf-8")
    assert marker in continuation
    assert facts["result"]["head"] == facts["branch_head"]
    assert not (h.fake / "agents" / f"{sessions[0]['agent']}.json").exists()
    assert len(herdr_calls(h, ("tab", "create"))) == 2
    old_prompts = h.prompts(sessions[0]["agent"])
    new_prompts = h.prompts(sessions[1]["agent"])
    if kind == "pi":
        assert old_prompts[1].startswith("/skill:handoff")
    else:
        assert "`skill`" in old_prompts[1] and "/skill:handoff" not in old_prompts[1]
    assert str(doc) in new_prompts[0]


def test_multiple_handoffs_stay_one_worker(make_harness):
    h = make_harness("handoff")
    h.env["HPM_FAKE_CONTEXT_SPIKE_TOTAL"] = "100"
    h.env["HPM_FAKE_CONTEXT_SPIKE_CALLS"] = "2"
    (h.fake / "handoff_repeat").write_text("1", encoding="utf-8")
    proc = h.start(handoff_tokens=50)
    assert proc.returncode == 0, proc.stderr
    worker_id = json.loads(proc.stdout)["worker_id"]
    facts = h.wait_state(worker_id, {"delivered"}, timeout=90)
    sessions = facts["sessions"]
    assert [session["index"] for session in sessions] == [1, 2, 3]
    assert len(facts["handoff"]["history"]) == 2
    assert len({session["agent"] for session in sessions}) == 3
    assert facts["herdr"]["agent"] == sessions[2]["agent"]
    assert facts["worktree"] and Path(facts["worktree"]).is_dir()
    assert len({session["handoff"]["doc"] for session in sessions[:2]}) == 2
    final_doc = Path(facts["handoff"]["history"][1]["doc"])
    assert f"NEXT-STEP-MARKER-{final_doc.stem}" in (Path(facts["worktree"]) / "continuation.txt").read_text(encoding="utf-8")


def test_handoff_document_is_copied_from_os_temp(make_harness):
    h = make_harness("handoff")
    h.env["HPM_SCENARIO_HANDOFF_MODE"] = "temp"
    h.env["HPM_FAKE_CONTEXT_SPIKE_TOTAL"] = "100"
    h.env["HPM_FAKE_CONTEXT_SPIKE_CALLS"] = "1"
    proc = h.start(handoff_tokens=50)
    assert proc.returncode == 0, proc.stderr
    worker_id = json.loads(proc.stdout)["worker_id"]
    facts = h.wait_state(worker_id, {"delivered"}, timeout=60)
    doc = Path(facts["handoff"]["history"][0]["doc"])
    assert doc.is_file()
    assert facts["sessions"][0]["handoff"]["document_source"] == "temp-copy"


def test_invalid_handoff_document_gets_one_correction(make_harness):
    h = make_harness("handoff")
    h.env["HPM_SCENARIO_HANDOFF_MODE"] = "invalid-first"
    h.env["HPM_FAKE_CONTEXT_SPIKE_TOTAL"] = "100"
    h.env["HPM_FAKE_CONTEXT_SPIKE_CALLS"] = "1"
    proc = h.start(handoff_tokens=50)
    assert proc.returncode == 0, proc.stderr
    worker_id = json.loads(proc.stdout)["worker_id"]
    facts = h.wait_state(worker_id, {"delivered"}, timeout=60)
    old_agent = facts["sessions"][0]["agent"]
    prompts = h.prompts(old_agent)
    assert len(prompts) == 3, "contract, handoff request and one correction"
    assert "cannot be used yet" in prompts[2]
    assert len(facts["handoff"]["history"]) == 1


def test_handoff_document_failure_is_a_pending_exception(make_harness):
    h = make_harness("handoff")
    h.env["HPM_SCENARIO_HANDOFF_MODE"] = "invalid"
    h.env["HPM_FAKE_CONTEXT_SPIKE_TOTAL"] = "100"
    h.env["HPM_FAKE_CONTEXT_SPIKE_CALLS"] = "1"
    proc = h.start(handoff_tokens=50)
    assert proc.returncode == 0, proc.stderr
    worker_id = json.loads(proc.stdout)["worker_id"]
    facts = h.wait_state(worker_id, {"handoff-failed"}, timeout=60)
    assert any(item["code"] == "handoff-document-invalid" for item in facts["items"])
    assert len(facts["sessions"]) == 1
    assert facts["sessions"][0]["handoff"]["status"] == "failed"
    old_agent = facts["sessions"][0]["agent"]
    assert (h.fake / "agents" / f"{old_agent}.json").is_file(), "the old session is retained"
    assert h.agent(old_agent)["prompt_count"] == 3, "exactly one correction is sent"
    assert Path(facts["worktree"]).is_dir()
    stopped = h.stop(worker_id)
    assert stopped.returncode == 0, stopped.stderr
    assert h.status(worker_id)["lifecycle"]["state"] == "stopped"


def test_handoff_document_timeout_retains_scene(make_harness):
    h = make_harness("handoff")
    h.env["HPM_SCENARIO_HANDOFF_MODE"] = "working"
    h.env["HPM_SCENARIO_HANDOFF_BUSY_SECONDS"] = "3"
    h.env["HPM_HANDOFF_WAIT_SECONDS"] = "2"
    h.env["HPM_FAKE_CONTEXT_SPIKE_TOTAL"] = "100"
    h.env["HPM_FAKE_CONTEXT_SPIKE_CALLS"] = "1"
    proc = h.start(handoff_tokens=50)
    assert proc.returncode == 0, proc.stderr
    worker_id = json.loads(proc.stdout)["worker_id"]
    facts = h.wait_state(worker_id, {"handoff-failed"}, timeout=60)
    assert any(item["code"] == "handoff-document-timeout" for item in facts["items"])
    assert len(facts["sessions"]) == 1
    assert h.status(worker_id)["supervisor"]["alive"] is True


@pytest.mark.parametrize("kind", RUNTIMES)
def test_replacement_start_failure_retains_scene_and_old_session(make_harness, kind):
    h = make_harness("handoff")
    h.env["HPM_FAKE_CONTEXT_SPIKE_TOTAL"] = "100"
    h.env["HPM_FAKE_CONTEXT_SPIKE_CALLS"] = "1"
    (h.fake / "start_hard_fail_at").write_text("2", encoding="utf-8")
    proc = h.start(kind=kind, handoff_tokens=50)
    assert proc.returncode == 0, proc.stderr
    worker_id = json.loads(proc.stdout)["worker_id"]
    facts = h.wait_state(worker_id, {"handoff-failed"}, timeout=60)
    assert any(item["code"] == "handoff-replacement-failed" for item in facts["items"])
    assert len(facts["sessions"]) == 1
    old_agent = facts["sessions"][0]["agent"]
    assert (h.fake / "agents" / f"{old_agent}.json").is_file()
    doc = Path(facts["sessions"][0]["handoff"]["doc"])
    assert doc.is_file()
    assert Path(facts["worktree"]).is_dir()
    stopped = h.stop(worker_id)
    assert stopped.returncode == 0, stopped.stderr
    assert h.status(worker_id)["lifecycle"]["state"] == "stopped"


def test_stop_during_handoff_aborts_without_replacement(make_harness):
    h = make_harness("handoff")
    h.env["HPM_SCENARIO_HANDOFF_DELAY"] = "3"
    h.env["HPM_FAKE_CONTEXT_SPIKE_TOTAL"] = "100"
    h.env["HPM_FAKE_CONTEXT_SPIKE_CALLS"] = "1"
    proc = h.start(handoff_tokens=50)
    assert proc.returncode == 0, proc.stderr
    worker_id = json.loads(proc.stdout)["worker_id"]
    h.wait_state(worker_id, {"handing-off"}, timeout=30)
    stopped = h.stop(worker_id)
    assert stopped.returncode == 0, stopped.stderr
    facts = h.status(worker_id)
    assert facts["lifecycle"]["state"] == "stopped"
    assert len(facts["sessions"]) == 1
    assert len(herdr_calls(h, ("tab", "create"))) == 1
    assert Path(facts["worktree"]).is_dir()


def test_explicit_handoff_request_is_not_repeated(make_harness):
    h = make_harness("handoff")
    proc = h.start()
    assert proc.returncode == 0, proc.stderr
    worker_id = json.loads(proc.stdout)["worker_id"]
    requested = h.handoff(worker_id, reason="manual check")
    assert requested.returncode == 0, requested.stderr
    assert json.loads(requested.stdout)["requested"] is True
    facts = h.wait_state(worker_id, {"delivered"}, timeout=60)
    history = facts["handoff"]["history"]
    assert len(history) == 1
    assert history[0]["trigger"] == "requested"
    time.sleep(2)
    assert len(h.status(worker_id)["handoff"]["history"]) == 1


def test_handoff_request_rejected_for_terminal_worker(make_harness):
    h = make_harness("deliver-code")
    proc = h.start()
    assert proc.returncode == 0, proc.stderr
    worker_id = json.loads(proc.stdout)["worker_id"]
    h.wait_state(worker_id, {"delivered"})
    requested = h.handoff(worker_id)
    assert requested.returncode == 2
    assert "cannot be requested" in requested.stderr


def test_context_trigger_and_staleness_rules():
    module = load_plan_manager()
    config = {"tokens": 300_000, "pct": 0.8}
    assert module.handoff_trigger_reason({"state": "current", "total": 300_000, "window": 1_000_000}, config) == "tokens"
    assert module.handoff_trigger_reason({"state": "current", "total": 800, "window": 1_000}, config) == "pct"
    assert module.handoff_trigger_reason({"state": "stale", "total": 900_000, "window": 1_000}, config) is None
    assert module.handoff_trigger_reason({"state": "pending", "total": 0, "window": 1_000}, config) is None
    assert module.handoff_trigger_reason({"state": "unobservable", "error": "x"}, config) is None
    assert module.classify_context({"error": "x"}, agent_status="working")["state"] == "pending"
    assert module.classify_context({"error": "x"}, agent_status="idle")["state"] == "unobservable"
    stale = module.classify_context({"total": 1, "window": 2, "freshness": "stale-model-change"}, agent_status="idle")
    assert stale["state"] == "stale"
    current = module.classify_context({"total": 1, "window": 2, "freshness": "completed-call"}, agent_status="idle")
    assert current["state"] == "current"


def test_stale_sample_guard_ignores_old_sessions():
    module = load_plan_manager()
    state = {"sessions": [{"index": 2, "context_ref": "ref-2"}]}
    assert module.sample_is_current({"session": 1, "context_ref": "ref-1"}, state) is False
    assert module.sample_is_current({"session": 2, "context_ref": "ref-3"}, state) is False
    assert module.sample_is_current({"session": 2, "context_ref": "ref-2"}, state) is True
    state["sessions"].append({"index": 3, "context_ref": "ref-3"})
    assert module.sample_is_current({"session": 2, "context_ref": "ref-2"}, state) is False


def test_handoff_document_validation_requires_structure(tmp_path):
    module = load_plan_manager()
    valid = tmp_path / "valid.md"
    valid.write_text(
        "# Handoff\n\n## Progress\nwork\n## Decisions\ndecision\n## Verification\nnone\n"
        "## Commits\nnone\n## Uncommitted work\npartial\n## Next steps\ndone\n",
        encoding="utf-8",
    )
    ok, missing, _ = module.validate_handoff_document(valid)
    assert ok is True
    assert missing == []
    incomplete = tmp_path / "incomplete.md"
    incomplete.write_text(
        "# Handoff\n\n## Progress\n" + "x" * 100 + "\n",
        encoding="utf-8",
    )
    ok, missing, problem = module.validate_handoff_document(incomplete)
    assert ok is False
    assert "Decisions" in missing
    assert "missing required sections" in problem
    assert module.validate_handoff_document(tmp_path / "absent.md")[0] is False


def test_handoff_retry_reuses_valid_document(make_harness):
    h = make_harness("handoff")
    h.env["HPM_FAKE_CONTEXT_SPIKE_TOTAL"] = "100"
    h.env["HPM_FAKE_CONTEXT_SPIKE_CALLS"] = "1"
    (h.fake / "start_hard_fail_at").write_text("2", encoding="utf-8")
    proc = h.start(handoff_tokens=50)
    assert proc.returncode == 0, proc.stderr
    worker_id = json.loads(proc.stdout)["worker_id"]
    facts = h.wait_state(worker_id, {"handoff-failed"}, timeout=60)
    old_agent = facts["sessions"][0]["agent"]
    doc = Path(facts["sessions"][0]["handoff"]["doc"])
    assert doc.is_file()

    (h.fake / "start_hard_fail_at").unlink()
    requested = h.handoff(worker_id, reason="retry after replacement failure")
    assert requested.returncode == 0, requested.stderr
    facts = h.wait_state(worker_id, {"delivered"}, timeout=60)
    assert [session["index"] for session in facts["sessions"]] == [1, 2]
    assert len(facts["handoff"]["history"]) == 1
    assert len(h.prompts(old_agent)) == 2, "the retry must reuse the valid document without re-requesting"
