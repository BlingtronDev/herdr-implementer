from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import sys
import os
import subprocess
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


dispatcher = load("dispatcher", ROOT / "bin/dispatcher.py")
monitor = load("monitor", ROOT / "bin/monitor.py")


def ticket_text(number: str, *, status: str = "ready-for-agent", blocked: str = "None (can start immediately)", title: str = "Slice") -> str:
    return f"""# {number}: {title}

**What to build:** behavior

**Blocked by:** {blocked}

**Status:** {status}

- [ ] first criterion
- [x] second criterion
"""


class TicketParsingTests(unittest.TestCase):
    def test_bold_fields_and_explicit_blockers(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "03-slice.md"
            path.write_text(ticket_text("03", blocked="01 — base, #02"), encoding="utf-8")
            ticket = dispatcher.parse_ticket(path, "feature")
            self.assertEqual(ticket.blockers, ("01", "02"))
            self.assertEqual(ticket.status, "ready-for-agent")
            self.assertEqual(ticket.acceptance, ("first criterion", "second criterion"))

    def test_em_dash_suffix_id_and_terminal_status_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "07b-snapshot.md"
            path.write_text(
                ticket_text("07b", status="done", blocked="07a — schema").replace("# 07b: Slice", "# 07b — Slice"),
                encoding="utf-8",
            )
            ticket = dispatcher.parse_ticket(path, "feature")
            self.assertEqual(ticket.number, "07b")
            self.assertEqual(ticket.blockers, ("07a",))
            self.assertEqual(ticket.status, "completed")

            path.write_text(
                ticket_text("07b", status="resolved", blocked="07a — schema").replace("# 07b: Slice", "# 07b — Slice"),
                encoding="utf-8",
            )
            self.assertEqual(dispatcher.parse_ticket(path, "feature").status, "completed")

    def test_invalid_status_is_not_normalized(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "01-slice.md"
            path.write_text(ticket_text("01", status="in progress"), encoding="utf-8")
            with self.assertRaisesRegex(dispatcher.DispatchError, "unknown Status"):
                dispatcher.parse_ticket(path, "feature")

    def test_text_without_ticket_number_is_ambiguous(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "01-slice.md"
            path.write_text(ticket_text("01", blocked="the setup ticket"), encoding="utf-8")
            with self.assertRaisesRegex(dispatcher.DispatchError, "no explicit ticket number"):
                dispatcher.parse_ticket(path, "feature")

    def test_duplicate_dependency_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "02-slice.md"
            path.write_text(ticket_text("02", blocked="01 and #01"), encoding="utf-8")
            with self.assertRaisesRegex(dispatcher.DispatchError, "duplicate dependency"):
                dispatcher.parse_ticket(path, "feature")


class GraphTests(unittest.TestCase):
    def make_ticket(self, number: str, blockers=(), status="ready-for-agent"):
        return dispatcher.Ticket(number, number, Path(f"{number}.md"), number, "", tuple(blockers), status, (), (), False)

    def test_cycle_prints_full_loop(self):
        tickets = {
            "01": self.make_ticket("01", ("03",)),
            "02": self.make_ticket("02", ("01",)),
            "03": self.make_ticket("03", ("02",)),
        }
        with self.assertRaisesRegex(dispatcher.DispatchError, r"01 -> 03 -> 02 -> 01"):
            dispatcher.validate_graph(tickets)


class BatchSelectionTests(unittest.TestCase):
    def test_normalizes_plan_style_ids_and_preserves_order(self):
        self.assertEqual(dispatcher.normalize_batch("t2, 07b T1"), ["02", "07b", "01"])

    def test_rejects_duplicate_and_invalid_ids(self):
        with self.assertRaisesRegex(dispatcher.DispatchError, "duplicate ticket"):
            dispatcher.normalize_batch("1 01")
        with self.assertRaisesRegex(dispatcher.DispatchError, "invalid ticket"):
            dispatcher.normalize_batch("ticket-1")


class ModelSelectionTests(unittest.TestCase):
    def test_pi_catalog_groups_models_under_provider(self):
        catalog = dispatcher.parse_pi_catalog(
            "provider model context max-out thinking images\n"
            "openai-codex gpt-5.6-sol 272K 128K yes yes\n"
            "opencode-go glm-5.3 1M 128K yes no\n"
        )
        self.assertEqual(catalog["openai-codex"], ["gpt-5.6-sol"])
        self.assertEqual(catalog["opencode-go"], ["glm-5.3"])

    def test_opencode_catalog_preserves_slashes_inside_model_id(self):
        catalog = dispatcher.parse_opencode_catalog("openrouter/anthropic/claude-sonnet\n")
        self.assertEqual(catalog, {"openrouter": ["anthropic/claude-sonnet"]})

    def test_codex_catalog_exposes_only_listed_supported_models(self):
        catalog = dispatcher.parse_codex_catalog(json.dumps({"models": [
            {"slug": "gpt-visible", "visibility": "list", "supported_in_api": True},
            {"slug": "gpt-hidden", "visibility": "hide", "supported_in_api": True},
        ]}))
        self.assertEqual(catalog, {"openai": ["gpt-visible"]})

    def test_native_args_pin_provider_and_model(self):
        self.assertEqual(
            dispatcher.native_model_args("pi", "openai-codex", "gpt-5.6-sol", "max"),
            ["--provider", "openai-codex", "--model", "gpt-5.6-sol", "--thinking", "max"],
        )
        self.assertEqual(
            dispatcher.native_model_args("opencode", "opencode-go", "glm-5.3", "high"),
            # The TUI root command has no --variant; the thinking level is
            # applied through the worktree's opencode.json instead.
            ["--model", "opencode-go/glm-5.3"],
        )
        self.assertEqual(
            dispatcher.native_model_args("codex", "openai", "gpt-5.6-sol", "xhigh"),
            [
                "-c", 'model_provider="openai"',
                "-c", 'model_reasoning_effort="xhigh"',
                "--model", "gpt-5.6-sol",
            ],
        )


class TargetGuardTests(unittest.TestCase):
    def make_repo(self, root: Path) -> tuple[Path, Path]:
        repo = root / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
        issue = repo / ".scratch/demo/issues/01-first.md"
        issue.parent.mkdir(parents=True)
        issue.write_text(ticket_text("01"), encoding="utf-8")
        (repo / "app.py").write_text("value = 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", "seed"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
        return repo, issue

    def test_ticket_control_changes_do_not_count_as_target_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, issue = self.make_repo(Path(tmp))
            state = {
                "slug": "demo",
                "target_branch": "main",
                "base_head": subprocess.run(
                    ["git", "rev-parse", "HEAD"], cwd=repo, check=True, text=True, stdout=subprocess.PIPE
                ).stdout.strip(),
                "protected_status": dispatcher.protected_worktree_status(repo, "demo"),
            }
            issue.write_text(ticket_text("01", status="claimed"), encoding="utf-8")
            dispatcher.verify_target(repo, state)

            (repo / "app.py").write_text("value = 2\n", encoding="utf-8")
            with self.assertRaisesRegex(dispatcher.DispatchError, r"outside \.scratch/demo"):
                dispatcher.verify_target(repo, state)

    def test_completed_declaration_is_not_quality_gated(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, issue = self.make_repo(Path(tmp))
            ticket = dispatcher.parse_ticket(issue, "demo")
            base = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=repo, check=True, text=True, stdout=subprocess.PIPE
            ).stdout.strip()
            subprocess.run(["git", "switch", "-c", "ticket-work"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
            spec = repo / ".scratch/demo/spec.md"
            spec.write_text("changed by worker\n", encoding="utf-8")
            subprocess.run(["git", "add", str(spec)], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "[t01] change control data"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=repo, check=True, text=True, stdout=subprocess.PIPE
            ).stdout.strip()
            result = {
                "status": "completed",
                "head_sha": head,
                "acceptance": [],
                "tests": [{"command": "false", "exit_code": 1, "summary": "ignored by executor"}],
                "unexpected": "also ignored",
            }
            result_path = repo / "result.json"
            result_path.write_text(json.dumps(result), encoding="utf-8")
            attempt = {"branch": "ticket-work", "ticket_base_sha": base, "worktree": str(repo)}
            self.assertEqual(dispatcher.read_worker_declaration(result_path, attempt, repo), result)


class BatchSummaryTests(unittest.TestCase):
    def make_repo(self, root: Path) -> tuple[Path, str]:
        repo = root / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
        (repo / "seed").write_text("seed\n", encoding="utf-8")
        subprocess.run(["git", "add", "seed"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", "seed"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, check=True, text=True, stdout=subprocess.PIPE
        ).stdout.strip()
        return repo, head

    def test_summary_preserves_batch_order_and_worker_declarations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, head = self.make_repo(root)
            run_dir = root / "run"
            results = run_dir / "results"
            results.mkdir(parents=True)
            completed_path = results / "01-a1.json"
            failed_path = results / "02-a1.json"
            completed_path.write_text(json.dumps({"status": "completed", "head_sha": head}), encoding="utf-8")
            failed_path.write_text(json.dumps({"status": "failed", "reason": "worker said no"}), encoding="utf-8")
            state = {
                "batch_number": 4,
                "batch_tickets": ["02", "01"],
                "base_head": head,
                "run_id": "run-1",
                "tickets": {
                    "01": {"status": "completed", "attempts": [{
                        "attempt": 1, "state": "completed", "branch": "main", "result_path": str(completed_path)
                    }]},
                    "02": {"status": "failed", "attempts": [{
                        "attempt": 1, "state": "failed", "branch": "main", "result_path": str(failed_path),
                        "reason": "worker-declared-failed: worker said no"
                    }]},
                },
            }
            with mock.patch("builtins.print"):
                summary, path = dispatcher.write_batch_summary(repo, state, run_dir)
            self.assertEqual([item["ticket"] for item in summary["workers"]], ["02", "01"])
            self.assertEqual(summary["workers"][1]["head_sha"], head)
            self.assertEqual(summary["failed"], [{"ticket": "02", "status": "failed", "reason": "worker said no"}])
            self.assertEqual(path.name, "batch-4-summary.json")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), summary)

    def test_rejected_completed_protocol_is_reported_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, head = self.make_repo(root)
            run_dir = root / "run"
            result_path = run_dir / "results/01-a1.json"
            result_path.parent.mkdir(parents=True)
            result_path.write_text(json.dumps({"status": "completed", "head_sha": head}), encoding="utf-8")
            state = {
                "batch_number": 1,
                "batch_tickets": ["01"],
                "base_head": head,
                "run_id": "run-1",
                "tickets": {"01": {"status": "failed", "attempts": [{
                    "attempt": 1, "state": "failed", "branch": "main", "result_path": str(result_path),
                    "reason": "declared head did not match branch"
                }]}},
            }
            summary = dispatcher.build_batch_summary(repo, state, run_dir)
            self.assertEqual(summary["workers"][0]["status"], "failed")
            self.assertTrue(summary["failed"])


class BatchBarrierTests(unittest.TestCase):
    class FakeProcess:
        def __init__(self):
            self.stdout = mock.Mock()
            self.stderr = mock.Mock()
            self.pid = 42
            self.returncode = 0
            self.terminated = False

        def communicate(self):
            return "", ""

        def poll(self):
            return None if not self.terminated else 0

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            return 0

        def kill(self):
            self.terminated = True

    class FakeSelector:
        def __init__(self):
            self.ready = []

        def register(self, fileobj, events, data):
            if data[0] == "watcher":
                self.ready.append(SimpleNamespace(fileobj=fileobj, data=data))

        def unregister(self, fileobj):
            return None

        def select(self, timeout=None):
            return [(self.ready.pop(0), None)] if self.ready else []

        def close(self):
            return None

    def test_explicit_batch_waits_for_every_ticket_and_never_merges(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            run_dir.mkdir()
            state_path = run_dir / "state.json"
            tickets = {
                number: dispatcher.Ticket(number, number, root / f"{number}.md", number, "", (), "ready-for-agent", (), (), False)
                for number in ("01", "02", "03")
            }
            state = {
                "batch_tickets": ["01", "02", "03"],
                "jobs": 2,
                "phase": "preflight",
                "fail_fast": False,
                "tickets": {
                    number: {"status": "ready-for-agent", "attempts": []} for number in tickets
                },
            }
            launched = []
            completed = []
            selector = self.FakeSelector()

            def launch(repo, worktree_root, actual_run_dir, actual_state_path, actual_state, ticket, live_names, template):
                launched.append(ticket.number)
                attempt = {
                    "attempt": 1, "state": "running", "agent": f"worker-{ticket.number}",
                    "result_path": str(run_dir / f"{ticket.number}.json"),
                }
                actual_state["tickets"][ticket.number]["attempts"].append(attempt)
                return attempt

            def complete(repo, actual_state_path, actual_state, ticket, attempt, result):
                completed.append(ticket.number)
                attempt["state"] = "completed"
                actual_state["tickets"][ticket.number]["status"] = "completed"

            with mock.patch.object(dispatcher, "verify_target"), \
                 mock.patch.object(dispatcher, "save_state"), \
                 mock.patch.object(dispatcher, "update_ticket"), \
                 mock.patch.object(dispatcher, "agent_list", return_value=[]), \
                 mock.patch.object(dispatcher, "ensure_context_ref"), \
                 mock.patch.object(dispatcher, "launch_attempt", side_effect=launch), \
                 mock.patch.object(dispatcher, "render_worker_prompt", return_value="prompt"), \
                 mock.patch.object(dispatcher, "start_prompt_watcher", side_effect=lambda *args: self.FakeProcess()), \
                 mock.patch.object(dispatcher, "read_worker_declaration", return_value={"status": "completed", "head_sha": "a" * 40}), \
                 mock.patch.object(dispatcher, "complete_attempt", side_effect=complete), \
                 mock.patch.object(dispatcher.selectors, "DefaultSelector", return_value=selector), \
                 mock.patch.object(dispatcher.subprocess, "Popen", return_value=self.FakeProcess()):
                dispatcher.run_dispatch(root, tickets, set(), run_dir, state_path, state)

            self.assertEqual(launched, ["01", "02", "03"])
            self.assertEqual(completed, ["01", "02", "03"])
            self.assertEqual(state["phase"], "completed")
            self.assertFalse(hasattr(dispatcher, "merge_attempt"))


class WorkerLifecycleTests(unittest.TestCase):
    def test_handoff_prompt_uses_skill_and_continuation_document(self):
        with tempfile.TemporaryDirectory() as tmp:
            ticket = dispatcher.Ticket(
                "01", "slice", Path(tmp) / "issues/01-slice.md", "Slice", "ticket body", (),
                "ready-for-agent", (), (), False,
            )
            state = {"run_id": "run-1"}
            directory = dispatcher.handoff_directory(state, ticket, 1)
            attempt = {"attempt": 1, "handoff_dir": str(directory), "handoffs": []}
            sample = {"total": 300000, "window": 1000000, "pct": 0.3}
            path, prompt = dispatcher.prepare_handoff(state, ticket, attempt, sample, "context-limit")

            self.assertTrue(prompt.startswith("/handoff "))
            self.assertIn(str(path), prompt)
            self.assertEqual(attempt["state"], "handing-off")
            path.write_text("# Handoff\n\n" + "Continue the remaining ticket work. " * 4, encoding="utf-8")
            self.assertIn("remaining ticket", dispatcher.validate_handoff(path, attempt))

    def test_rollover_agent_name_is_distinct_and_bounded(self):
        ticket = dispatcher.Ticket(
            "07b", "a-very-long-ticket-slug", Path("07b.md"), "Slice", "", (),
            "ready-for-agent", (), (), False,
        )
        original = dispatcher.short_agent_name(ticket, 12, set())
        continuation = dispatcher.short_agent_name(ticket, 12, {original}, rollover=3)
        self.assertNotEqual(original, continuation)
        self.assertLessEqual(len(continuation), 32)
        self.assertTrue(continuation.endswith("-a12-r3"))

    def test_continuation_reuses_worktree_branch_and_closes_old_tab(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ticket_path = root / "issues/01-slice.md"
            ticket_path.parent.mkdir()
            ticket = dispatcher.Ticket(
                "01", "slice", ticket_path, "Slice", "ticket body", (),
                "ready-for-agent", (), (), False,
            )
            state = {"run_id": "run-continuation", "tickets": {"01": {}}}
            directory = dispatcher.handoff_directory(state, ticket, 1)
            handoff_path = directory / "handoff-1.md"
            handoff_path.write_text("# Handoff\n\n" + "Continue existing work safely. " * 4, encoding="utf-8")
            attempt = {
                "attempt": 1, "kind": "pi", "provider": "openai", "model": "model", "thinking": "high",
                "agent": "old-agent", "tab": "old-tab", "pane": "old-pane", "context_ref": "old-ref",
                "branch": "ticket/01-slice/a1", "worktree": str(root / "worktree"), "ticket_base_sha": "a" * 40,
                "result_path": str(root / "result.json"), "handoff_dir": str(directory), "handoffs": [],
                "rollover_count": 0, "pending_handoff": {
                    "sequence": 1, "path": str(handoff_path), "reason": "context-limit", "sample": {},
                    "requested_at": "2026-01-01T00:00:00Z", "correction_sent": False,
                },
            }
            state["tickets"]["01"]["attempts"] = [attempt]
            json_results = [
                {"result": {"tab": {"tab_id": "new-tab"}, "root_pane": {"pane_id": "new-pane"}}},
                {"result": {"agent_session": {"value": "new-ref"}}},
            ]
            ready = subprocess.CompletedProcess([], 0, "", "")
            command_result = subprocess.CompletedProcess([], 0, "", "")
            with mock.patch.dict(os.environ, {"HERDR_WORKSPACE_ID": "workspace"}), \
                 mock.patch.object(dispatcher, "json_command", side_effect=json_results), \
                 mock.patch.object(dispatcher, "start_agent_when_shell_ready", return_value=ready), \
                 mock.patch.object(dispatcher, "wait_agent_ready", return_value=True), \
                 mock.patch.object(dispatcher, "command", return_value=command_result) as run, \
                 mock.patch.object(dispatcher, "save_state"):
                prompt = dispatcher.start_continuation(
                    root / "state.json", state, ticket, attempt, {"old-agent"}, "{{AGENT_NAME}} {{BRANCH}} {{HANDOFF_DIR}}"
                )

            self.assertEqual(attempt["worktree"], str(root / "worktree"))
            self.assertEqual(attempt["branch"], "ticket/01-slice/a1")
            self.assertEqual(attempt["context_ref"], "new-ref")
            self.assertEqual(attempt["rollover_count"], 1)
            self.assertEqual(attempt["handoffs"][0]["path"], str(handoff_path))
            self.assertIn(str(handoff_path), prompt)
            self.assertIn(mock.call(["herdr", "tab", "close", "old-tab"], check=False), run.call_args_list)

    def test_idle_worker_is_not_sent_ctrl_c_before_handoff(self):
        idle = subprocess.CompletedProcess([], 0, '{"agent_status":"idle"}', "")
        with mock.patch.object(dispatcher, "command", return_value=idle) as run:
            self.assertTrue(dispatcher.settle_agent_for_handoff({"agent": "worker"}))
        self.assertEqual(run.call_args_list, [mock.call(["herdr", "agent", "get", "worker"], check=False)])

    def test_opencode_worker_is_interrupted_with_double_escape_not_ctrl_c(self):
        working = subprocess.CompletedProcess([], 0, '{"agent_status":"working"}', "")
        sent = subprocess.CompletedProcess([], 0, '{"type":"ok"}', "")
        settled = subprocess.CompletedProcess([], 0, '{"agent_status":"done"}', "")
        with mock.patch.object(dispatcher, "command", side_effect=[working, sent, settled]) as run, mock.patch.object(
            dispatcher.time, "sleep"
        ):
            self.assertTrue(dispatcher.settle_agent_for_handoff({"agent": "worker", "kind": "opencode"}))
        self.assertEqual(
            run.call_args_list,
            [
                mock.call(["herdr", "agent", "get", "worker"], check=False),
                mock.call(["herdr", "agent", "send-keys", "worker", "escape", "escape"], check=False),
                mock.call(["herdr", "agent", "get", "worker"], check=False),
            ],
        )

    def test_non_opencode_worker_is_interrupted_with_ctrl_c(self):
        working = subprocess.CompletedProcess([], 0, '{"agent_status":"working"}', "")
        sent = subprocess.CompletedProcess([], 0, '{"type":"ok"}', "")
        settled = subprocess.CompletedProcess([], 0, '{"agent_status":"done"}', "")
        with mock.patch.object(dispatcher, "command", side_effect=[working, sent, settled]) as run, mock.patch.object(
            dispatcher.time, "sleep"
        ):
            self.assertTrue(dispatcher.settle_agent_for_handoff({"agent": "worker", "kind": "pi"}))
        self.assertEqual(
            run.call_args_list,
            [
                mock.call(["herdr", "agent", "get", "worker"], check=False),
                mock.call(["herdr", "agent", "send-keys", "worker", "ctrl+c"], check=False),
                mock.call(["herdr", "agent", "get", "worker"], check=False),
            ],
        )

    def test_settle_retries_unresolvable_agent_instead_of_failing_instantly(self):
        missing = subprocess.CompletedProcess([], 1, '{"error":{"code":"agent_not_found"}}', "")
        idle = subprocess.CompletedProcess([], 0, '{"agent_status":"idle"}', "")
        with mock.patch.object(dispatcher, "command", side_effect=[missing, missing, idle]) as run, mock.patch.object(
            dispatcher.time, "sleep"
        ):
            self.assertTrue(dispatcher.settle_agent_for_handoff({"agent": "worker", "kind": "opencode"}))
        self.assertEqual(run.call_count, 3)

    def test_settle_poll_tolerates_transient_get_failure_after_interrupt(self):
        working = subprocess.CompletedProcess([], 0, '{"agent_status":"working"}', "")
        missing = subprocess.CompletedProcess([], 1, '{"error":{"code":"agent_not_found"}}', "")
        settled = subprocess.CompletedProcess([], 0, '{"agent_status":"done"}', "")
        with mock.patch.object(dispatcher, "command", side_effect=[working, missing, settled]) as run, mock.patch.object(
            dispatcher.time, "sleep"
        ):
            self.assertTrue(dispatcher.settle_agent_for_handoff({"agent": "worker", "kind": "opencode"}))
        self.assertEqual(run.call_count, 3)

    def test_agent_start_retries_until_new_tab_shell_is_ready(self):
        busy = subprocess.CompletedProcess(
            [], 1, "", '{"error":{"code":"agent_pane_busy","message":"not an available shell"}}'
        )
        ready = subprocess.CompletedProcess([], 0, "started", "")
        with mock.patch.object(dispatcher, "command", side_effect=[busy, ready]) as run, mock.patch.object(
            dispatcher.time, "sleep"
        ) as pause:
            result = dispatcher.start_agent_when_shell_ready(["herdr", "agent", "start", "worker"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(run.call_count, 2)
        pause.assert_called_once()

    def test_prompt_watcher_uses_atomic_prompt_wait(self):
        attempt = {"agent": "worker"}
        process = mock.Mock(pid=42, stdout=mock.Mock(), stderr=mock.Mock())
        with mock.patch.object(dispatcher.subprocess, "Popen", return_value=process) as popen:
            actual = dispatcher.start_prompt_watcher(attempt, "do work")
        self.assertIs(actual, process)
        command_line = popen.call_args.args[0]
        self.assertEqual(command_line[:4], ["herdr", "agent", "prompt", "worker"])
        self.assertIn("--wait", command_line)
        self.assertIn("--timeout", command_line)
        self.assertEqual(attempt["watcher_pid"], 42)


class AttemptStateTests(unittest.TestCase):
    def test_tab_create_ids_are_read_from_json(self):
        payload = {"result": {"tab": {"tab_id": "w1:t2"}, "root_pane": {"pane_id": "w1:p3"}}}
        self.assertEqual(dispatcher.extract_tab_and_root_pane(payload), ("w1:t2", "w1:p3"))

    def test_historical_attempt_number_is_not_reused_after_abandonment(self):
        with tempfile.TemporaryDirectory() as tmp:
            common = Path(tmp)
            state_path = common / "herdr-ticket-dispatcher/demo/runs/old/state.json"
            state_path.parent.mkdir(parents=True)
            state_path.write_text(json.dumps({
                "tickets": {"01": {"attempts": [{"attempt": 3, "state": "abandoned"}]}}
            }), encoding="utf-8")
            self.assertEqual(dispatcher.historical_attempt_max(common, "demo", "01"), 3)

    def test_claimed_attempt_is_deleted_after_provenance_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
            (repo / "seed").write_text("seed\n", encoding="utf-8")
            subprocess.run(["git", "add", "seed"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "seed"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
            issue = repo / ".scratch/demo/issues/01-first.md"
            issue.parent.mkdir(parents=True)
            issue.write_text(ticket_text("01", status="claimed"), encoding="utf-8")
            ticket = dispatcher.parse_ticket(issue, "demo")
            worktree = repo / ".worktrees/demo/01-first/a1"
            worktree.parent.mkdir(parents=True)
            branch = "ticket/01-first/a1"
            subprocess.run(["git", "worktree", "add", "-b", branch, str(worktree), "HEAD"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
            common = repo / ".git"
            state_path = common / "herdr-ticket-dispatcher/demo/runs/old/state.json"
            state_path.parent.mkdir(parents=True)
            state_path.write_text(json.dumps({
                "run_id": "old",
                "slug": "demo",
                "tickets": {"01": {"status": "claimed", "attempts": [{
                    "attempt": 1, "state": "running", "agent": "t01-first-a1",
                    "tab": "w1:t9", "pane": "w1:p9", "context_ref": "old-session",
                    "branch": branch, "worktree": str(worktree),
                }]}}
            }), encoding="utf-8")
            fake_bin = Path(tmp) / "bin"
            fake_bin.mkdir()
            fake_herdr = fake_bin / "herdr"
            fake_herdr.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            fake_herdr.chmod(0o755)
            with mock.patch.dict(os.environ, {"PATH": f"{fake_bin}:{os.environ['PATH']}"}):
                dispatcher.abandon_claimed_attempt(repo, common, "demo", ticket)
            self.assertFalse(worktree.exists())
            self.assertNotEqual(subprocess.run(["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], cwd=repo).returncode, 0)
            self.assertIn("**Status:** ready-for-agent", issue.read_text(encoding="utf-8"))
            old = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(old["tickets"]["01"]["attempts"][0]["state"], "abandoned")


class TicketWriteTests(unittest.TestCase):
    def test_status_replacement_preserves_bold_and_appends_comments(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "01-slice.md"
            path.write_text(ticket_text("01") + "\n## Comments\n\n- old\n", encoding="utf-8")
            dispatcher.update_ticket(path, "claimed", {"event": "new"})
            text = path.read_text(encoding="utf-8")
            self.assertIn("**Status:** claimed", text)
            self.assertLess(text.index("- old"), text.index('"event":"new"'))


class MonitorTests(unittest.TestCase):
    def test_trips_on_first_valid_sample_at_300k(self):
        counter = {"over": 0, "errors": 0}
        sample = {"total": 300000, "window": 1000000, "pct": 0.3, "freshness": "completed-call"}
        first = monitor.advance_counter(counter, 0, sample, abs_limit=300000, pct_limit=.8, error_limit=3)
        second = monitor.advance_counter(counter, 0, sample, abs_limit=300000, pct_limit=.8, error_limit=3)
        self.assertEqual(first, "trip")
        self.assertIsNone(second)

    def test_stale_and_errors_do_not_count_as_over_limit(self):
        counter = {"over": 0, "errors": 0}
        stale = {"total": 900, "window": 1000, "pct": .9, "freshness": "stale-model-change"}
        self.assertIsNone(monitor.advance_counter(counter, 0, stale, abs_limit=300000, pct_limit=.8, error_limit=3))
        self.assertEqual(counter["over"], 0)
        for expected in (None, None, "error"):
            actual = monitor.advance_counter(counter, 1, {"error": "broken"}, abs_limit=300000, pct_limit=.8, error_limit=3)
            self.assertEqual(actual, expected)


class PromptRetryTests(unittest.TestCase):
    class ScriptedProcess:
        def __init__(self, returncode=0, stdout="", stderr=""):
            self.stdout = stdout
            self.stderr = stderr
            self.pid = 42
            self.returncode = returncode
            self.terminated = False

        def communicate(self):
            return self.stdout, self.stderr

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            return self.returncode

        def kill(self):
            self.terminated = True

    class FakeSelector:
        def __init__(self):
            self.ready = []

        def register(self, fileobj, events, data):
            if data[0] == "watcher":
                self.ready.append(SimpleNamespace(fileobj=fileobj, data=data))

        def unregister(self, fileobj):
            return None

        def select(self, timeout=None):
            return [(self.ready.pop(0), None)] if self.ready else []

        def close(self):
            return None

    def run_one_ticket(self, prompt_processes):
        """Run dispatch for a single ticket with scripted prompt watcher outcomes."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            run_dir.mkdir()
            state_path = run_dir / "state.json"
            ticket = dispatcher.Ticket("01", "first", root / "01.md", "First", "", (), "ready-for-agent", (), (), False)
            tickets = {"01": ticket}
            state = {
                "batch_tickets": ["01"],
                "jobs": 1,
                "phase": "preflight",
                "fail_fast": False,
                "kind": "opencode",
                "context_window": None,
                "tickets": {"01": {"status": "ready-for-agent", "attempts": []}},
            }
            selector = self.FakeSelector()
            failed_reasons = []
            completions = []

            def launch(repo, worktree_root, actual_run_dir, actual_state_path, actual_state, ticket, live_names, template):
                attempt = {
                    "attempt": 1, "state": "running", "agent": "worker-01",
                    "context_ref": "", "result_path": str(run_dir / "01.json"),
                }
                actual_state["tickets"]["01"]["attempts"].append(attempt)
                return attempt

            def fail(repo, actual_run_dir, actual_state_path, actual_state, actual_ticket, attempt, reason):
                failed_reasons.append(reason)
                actual_state["tickets"][actual_ticket.number]["status"] = "failed"
                return True

            def complete(repo, actual_state_path, actual_state, actual_ticket, attempt, result):
                completions.append(actual_ticket.number)
                actual_state["tickets"][actual_ticket.number]["status"] = "completed"

            with mock.patch.object(dispatcher, "verify_target"), \
                 mock.patch.object(dispatcher, "save_state"), \
                 mock.patch.object(dispatcher, "update_ticket"), \
                 mock.patch.object(dispatcher, "agent_list", return_value=[]), \
                 mock.patch.object(dispatcher, "launch_attempt", side_effect=launch), \
                 mock.patch.object(dispatcher, "render_worker_prompt", return_value="prompt"), \
                 mock.patch.object(dispatcher, "start_prompt_watcher", side_effect=prompt_processes), \
                 mock.patch.object(dispatcher, "read_worker_declaration", return_value={"status": "completed", "head_sha": "a" * 40}), \
                 mock.patch.object(dispatcher, "complete_attempt", side_effect=complete), \
                 mock.patch.object(dispatcher, "fail_attempt", side_effect=fail), \
                 mock.patch.object(dispatcher, "ensure_context_ref"), \
                 mock.patch.object(dispatcher, "context_snapshot", return_value={"pct": 0.0}), \
                 mock.patch.object(dispatcher, "wait_agent_ready", return_value=True), \
                 mock.patch.object(dispatcher.time, "sleep"), \
                 mock.patch.object(dispatcher.selectors, "DefaultSelector", return_value=selector), \
                 mock.patch.object(dispatcher.subprocess, "Popen", return_value=self.ScriptedProcess(stdout=mock.Mock())):
                dispatcher.run_dispatch(root, tickets, set(), run_dir, state_path, state)
            return state, failed_reasons, completions

    def test_stalled_submission_is_retried_and_worker_prompt_is_delivered(self):
        stalled = self.ScriptedProcess(
            returncode=1,
            stderr=json.dumps({"error": {"code": "agent_prompt_stalled"}}),
        )
        settled = self.ScriptedProcess(returncode=0, stdout=json.dumps({"agent_status": "idle"}))
        state, failed_reasons, completions = self.run_one_ticket([stalled, settled])
        self.assertEqual(completions, ["01"])
        self.assertEqual(failed_reasons, [])
        self.assertEqual(state["phase"], "completed")

    def test_retries_are_bounded_then_attempt_fails_with_delivery_reason(self):
        stalled = lambda: self.ScriptedProcess(
            returncode=1,
            stderr=json.dumps({"error": {"code": "agent_prompt_stalled"}}),
        )
        state, failed_reasons, completions = self.run_one_ticket([stalled() for _ in range(4)])
        self.assertEqual(completions, [])
        self.assertEqual(len(failed_reasons), 1)
        self.assertIn("prompt-submission-failed", failed_reasons[0])
        self.assertIn("agent_prompt_stalled", failed_reasons[0])
        self.assertEqual(state["phase"], "failed")

    def test_prompt_timeout_is_not_resent_and_falls_back_to_wait(self):
        timed_out = self.ScriptedProcess(
            returncode=1,
            stderr=json.dumps({"error": {"code": "timeout"}}),
        )
        # The only scripted prompt process is consumed by the initial
        # submission; a resend attempt would exhaust the side_effect list and
        # error. After the timeout fallback the (unscripted) wait watcher
        # reports the agent settled and the mocked declaration completes.
        state, failed_reasons, completions = self.run_one_ticket([timed_out])
        self.assertEqual(completions, ["01"])
        self.assertEqual(failed_reasons, [])
        self.assertEqual(state["phase"], "completed")


if __name__ == "__main__":
    unittest.main()
