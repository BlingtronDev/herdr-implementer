#!/usr/bin/env python3
"""Herdr Implementer: lifecycle tool for implementing confirmed plans and specs.

Scope: confirm one execution (run) with its runtime configuration and
max_workers, start isolated Pi or OpenCode workers from an explicit base SHA,
register their resources, supervise them in the background, observe their
context, hand the ticket to a fresh session of the same worker at the
configured threshold, record structured deliveries or exceptions as durable
items, wait for any pending item, acknowledge it, stop business execution
while retaining the scene, and remove registered resources only after an
explicit master decision. The tool never orders tickets, picks new work,
retries business tasks or merges results.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

STATE_VERSION = 3
DEFAULT_STATE_DIR = "herdr-implementer"
DEFAULT_BRANCH_PREFIX = "hi"
DEFAULT_MAX_WORKERS = 4
IMPLEMENTER_PATH = Path(__file__).resolve()
SKILL_DIR = IMPLEMENTER_PATH.parent.parent
THINKING_LEVELS = ("off", "minimal", "low", "medium", "high", "xhigh", "max")
SHA_RE = re.compile(r"^[0-9a-f]{40,64}$")
TICKET_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
OPENCODE_MODEL_LINE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+@-]*/[A-Za-z0-9][A-Za-z0-9._:/+@-]*$")
WORKER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
RUN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
SELECTION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+@-]*$")
AGENT_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
RESULT_STATUSES = ("delivered", "failed", "needs-decision")
HANDOFF_SECTIONS = ("Progress", "Decisions", "Verification", "Commits", "Uncommitted work", "Next steps")
DEFAULT_HANDOFF_TOKENS = 300_000
DEFAULT_HANDOFF_PCT = 0.9
DEFAULT_CONTEXT_POLL_SECONDS = 15.0
DEFAULT_HANDOFF_WAIT_SECONDS = 900.0
DEFAULT_WAIT_POLL_SECONDS = 1.0
DEFAULT_LOCK_TIMEOUT_SECONDS = 30.0
SUPERVISOR_MISSING_ITEM = "supervisor-missing"
START_STALLED_ITEM = "start-stalled"
DERIVED_ITEM_IDS = (SUPERVISOR_MISSING_ITEM, START_STALLED_ITEM)
STARTED_STATES = {"allocating", "prompting"}
TERMINAL_STATES = {
    "delivered",
    "failed",
    "needs-decision",
    "protocol-failure",
    "launch-failed",
    "agent-exited",
    "stopped",
}
INTERACTIVE_STATES = {"idle", "done", "working", "blocked"}
SETTLED_STATES = {"idle", "done"}
STATUS_PRIORITY = ("blocked", "done", "idle", "unknown", "working")
MAX_RESULT_BYTES = 1024 * 1024
MAX_HANDOFF_DOC_BYTES = 512 * 1024
START_ATTEMPTS = 40
START_DELAY = 0.25
READY_ATTEMPTS = 240
READY_DELAY = 0.5
PROMPT_ATTEMPTS = 3
PARSE_ATTEMPTS = 3
SUPERVISOR_START_WAIT = 10.0
DEFAULT_STOP_WAIT = 60.0
DEFAULT_SESSION_CLOSE_TIMEOUT = 5.0
PROMPT_CONFIRM_ATTEMPTS = 3
DEFAULT_PROMPT_CONFIRM_TIMEOUT = 10.0


class ImplementerError(RuntimeError):
    pass


class HandoffError(ImplementerError):
    """A standard handoff step failed; the scene is retained for the caller."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class HandoffAbort(Exception):
    """A stop request arrived while a handoff was in progress."""


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso(value: str) -> float:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def env_value(name: str) -> str | None:
    """Prefer HI_* settings; fall back to HPM_* for existing installations."""
    if name in os.environ:
        return os.environ[name]
    legacy = "HPM_" + name[3:] if name.startswith("HI_") else name
    return os.environ.get(legacy)


def env_float(name: str, default: float) -> float:
    raw = env_value(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ImplementerError(f"invalid {name}: {raw!r}") from exc


def run(
    args: list[str],
    *,
    cwd: Path | None = None,
    check: bool = False,
    timeout: float = 60,
) -> subprocess.CompletedProcess[str]:
    try:
        proc = subprocess.run(
            args,
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise ImplementerError(f"command timed out after {timeout}s: {' '.join(args)}") from exc
    if check and proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip()
        raise ImplementerError(f"command failed ({proc.returncode}): {' '.join(args)}\n{detail}")
    return proc


def herdr_error_code(proc: subprocess.CompletedProcess[str]) -> str | None:
    for raw in (proc.stderr, proc.stdout):
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict) and isinstance(error.get("code"), str):
            return error["code"]
    return None


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def contained(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


@contextmanager
def file_lock(path: Path, timeout: float | None = None) -> Iterator[None]:
    """Serialize one read-check-write sequence across processes.

    Allocation and acknowledgement are the only two operations that need
    mutual exclusion; the lock stays a plain file so a crashed process
    releases it through the OS instead of leaving a stale lock behind.
    """
    if timeout is None:
        timeout = env_float("HI_LOCK_TIMEOUT_SECONDS", DEFAULT_LOCK_TIMEOUT_SECONDS)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ImplementerError(f"timed out after {timeout}s waiting for lock {path}")
                time.sleep(0.05)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def git(repo: Path, *args: str, check: bool = True, timeout: float = 60) -> subprocess.CompletedProcess[str]:
    return run(["git", *args], cwd=repo, check=check, timeout=timeout)


def repo_context(repo: Path) -> tuple[Path, Path]:
    root = Path(git(repo, "rev-parse", "--show-toplevel").stdout.strip()).resolve()
    common_raw = git(root, "rev-parse", "--git-common-dir").stdout.strip()
    common = Path(common_raw).resolve() if os.path.isabs(common_raw) else (root / common_raw).resolve()
    return root, common


def find_agent_session(payload: Any) -> str | None:
    """Return the runtime session reference published by `herdr agent get`."""
    found: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            session = value.get("agent_session")
            if isinstance(session, dict) and isinstance(session.get("value"), str):
                found.append(session["value"])
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(payload)
    return found[0] if found else None


def find_agent_status(payload: Any) -> str | None:
    found: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"agent_status", "status"} and isinstance(child, str):
                    found.append(child)
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(payload)
    for status in STATUS_PRIORITY:
        if status in found:
            return status
    return None


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    entries: list[str] = []
    for item in sorted(root.rglob("*")):
        rel = item.relative_to(root).as_posix()
        if item.is_symlink():
            entries.append(f"{rel}\0link:{os.readlink(item)}")
        elif item.is_file():
            entries.append(f"{rel}\0{file_sha256(item)}")
        elif item.is_dir():
            entries.append(f"{rel}\0dir")
    for line in entries:
        digest.update(line.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


class Store:
    def __init__(self, root: Path):
        self.root = root.resolve()

    @staticmethod
    def resolve(common_dir: Path, override: str | None = None) -> "Store":
        root = Path(override).resolve() if override else (common_dir / DEFAULT_STATE_DIR).resolve()
        return Store(root)

    def worker_dir(self, worker_id: str) -> Path:
        return self.root / "workers" / worker_id

    def state_path(self, worker_id: str) -> Path:
        return self.worker_dir(worker_id) / "state.json"

    def control_path(self, worker_id: str) -> Path:
        return self.worker_dir(worker_id) / "control.json"

    def result_path(self, worker_id: str) -> Path:
        return self.worker_dir(worker_id) / "result.json"

    def contract_path(self, worker_id: str) -> Path:
        return self.worker_dir(worker_id) / "contract.md"

    def handoffs_dir(self, worker_id: str) -> Path:
        return self.worker_dir(worker_id) / "handoffs"

    def handoff_path(self, worker_id: str, session_index: int) -> Path:
        return self.handoffs_dir(worker_id) / f"handoff-{session_index:03d}.md"

    def log_path(self, worker_id: str) -> Path:
        return self.worker_dir(worker_id) / "supervisor.log"

    def load_state(self, worker_id: str) -> dict[str, Any] | None:
        value = read_json(self.state_path(worker_id))
        return value if isinstance(value, dict) else None

    def save_state(self, worker_id: str, state: dict[str, Any]) -> None:
        state["updated_at"] = utc_now()
        atomic_json(self.state_path(worker_id), state)

    def load_control(self, worker_id: str) -> dict[str, Any]:
        value = read_json(self.control_path(worker_id))
        return value if isinstance(value, dict) else {}

    def save_control(self, worker_id: str, control: dict[str, Any]) -> None:
        atomic_json(self.control_path(worker_id), control)

    def worker_ids(self) -> list[str]:
        directory = self.root / "workers"
        if not directory.is_dir():
            return []
        return sorted(item.name for item in directory.iterdir() if (item / "state.json").is_file())

    def run_dir(self, run_id: str) -> Path:
        return self.root / "runs" / run_id

    def run_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "run.json"

    def run_lock_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "allocate.lock"

    def load_run(self, run_id: str) -> dict[str, Any] | None:
        value = read_json(self.run_path(run_id))
        return value if isinstance(value, dict) else None

    def ack_path(self, worker_id: str) -> Path:
        return self.worker_dir(worker_id) / "ack.json"

    def ack_lock_path(self, worker_id: str) -> Path:
        return self.worker_dir(worker_id) / "ack.lock"

    def load_acks(self, worker_id: str) -> dict[str, dict[str, Any]]:
        """Acknowledgements live in their own file: `ack` never rewrites state.json."""
        value = read_json(self.ack_path(worker_id))
        if not isinstance(value, dict):
            return {}
        acks = value.get("acked")
        if not isinstance(acks, dict):
            return {}
        return {key: entry for key, entry in acks.items() if isinstance(entry, dict)}

    def cleanup_path(self, worker_id: str) -> Path:
        return self.worker_dir(worker_id) / "cleanup.json"

    def cleanup_lock_path(self, worker_id: str) -> Path:
        return self.worker_dir(worker_id) / "cleanup.lock"

    def stop_lock_path(self, worker_id: str) -> Path:
        return self.worker_dir(worker_id) / "stop.lock"

    def load_cleanup(self, worker_id: str) -> dict[str, Any]:
        value = read_json(self.cleanup_path(worker_id))
        return value if isinstance(value, dict) else {}

    def save_cleanup(self, worker_id: str, record: dict[str, Any]) -> None:
        record["updated_at"] = utc_now()
        atomic_json(self.cleanup_path(worker_id), record)


class Herdr:
    def __init__(self, workspace: str | None = None):
        self.workspace = workspace

    def json_command(self, args: list[str], *, timeout: float = 60) -> dict[str, Any]:
        proc = run(["herdr", *args], timeout=timeout)
        if proc.returncode != 0:
            detail = proc.stderr.strip() or proc.stdout.strip()
            raise ImplementerError(f"herdr command failed ({proc.returncode}): {' '.join(args)}\n{detail}")
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise ImplementerError(f"herdr returned invalid JSON: {' '.join(args)}") from exc
        if not isinstance(payload, dict):
            raise ImplementerError(f"herdr returned non-object JSON: {' '.join(args)}")
        return payload

    def agent_get(self, name: str) -> tuple[int, dict[str, Any] | None]:
        proc = run(["herdr", "agent", "get", name], timeout=30)
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError:
            payload = None
        return proc.returncode, payload if isinstance(payload, dict) else None

    def agent_status(self, name: str) -> tuple[int, str | None]:
        rc, payload = self.agent_get(name)
        return rc, find_agent_status(payload) if rc == 0 else None

    def pane_list(self) -> list[dict[str, Any]]:
        """Every pane in this workspace, for registered-tab occupancy checks."""
        args = ["pane", "list"]
        if self.workspace:
            args += ["--workspace", self.workspace]
        payload = self.json_command(args)
        result = payload.get("result")
        if not isinstance(result, dict):
            raise ImplementerError("herdr pane list response has a non-object result")
        panes = result.get("panes")
        if not isinstance(panes, list):
            raise ImplementerError("herdr pane list response has no result.panes list")
        for index, pane in enumerate(panes):
            if not isinstance(pane, dict):
                raise ImplementerError(f"herdr pane list entry {index} is not an object")
            if (
                not isinstance(pane.get("pane_id"), str)
                or not pane["pane_id"]
                or not isinstance(pane.get("tab_id"), str)
                or not pane["tab_id"]
            ):
                raise ImplementerError(f"herdr pane list entry {index} has no usable pane_id/tab_id")
        return panes

    def agent_list(self) -> list[dict[str, Any]]:
        """Every live agent Herdr knows, for registered-pane occupancy checks."""
        payload = self.json_command(["agent", "list"])
        result = payload.get("result")
        if not isinstance(result, dict):
            raise ImplementerError("herdr agent list response has a non-object result")
        agents = result.get("agents")
        if not isinstance(agents, list):
            raise ImplementerError("herdr agent list response has no result.agents list")
        for index, agent in enumerate(agents):
            if not isinstance(agent, dict):
                raise ImplementerError(f"herdr agent list entry {index} is not an object")
            if (
                not isinstance(agent.get("pane_id"), str)
                or not agent["pane_id"]
                or not isinstance(agent.get("tab_id"), str)
                or not agent["tab_id"]
            ):
                raise ImplementerError(f"herdr agent list entry {index} has no usable pane_id/tab_id")
        return agents

    def agent_fact(self, name: str) -> dict[str, Any]:
        """Strict lookup: only an explicit agent_not_found proves the agent exited."""
        proc = run(["herdr", "agent", "get", name], timeout=30)
        if proc.returncode == 0:
            try:
                payload = json.loads(proc.stdout)
            except json.JSONDecodeError:
                payload = None
            if not isinstance(payload, dict):
                return {"known": False, "exists": False, "status": None, "error": "agent get returned unreadable JSON"}
            result = payload.get("result")
            agent = result.get("agent") if isinstance(result, dict) else None
            if not isinstance(agent, dict):
                return {
                    "known": False,
                    "exists": False,
                    "status": None,
                    "error": "agent get response has no result.agent object",
                }
            status = find_agent_status(payload)
            if not isinstance(status, str) or not status:
                return {
                    "known": True,
                    "exists": True,
                    "status": None,
                    "error": "agent get response has no usable status",
                }
            return {"known": True, "exists": True, "status": status, "error": None}
        code = herdr_error_code(proc)
        if code == "agent_not_found":
            return {"known": True, "exists": False, "status": None, "error": None}
        detail = code or proc.stderr.strip() or proc.stdout.strip() or "agent get failed"
        return {"known": False, "exists": False, "status": None, "error": detail}

    def wait_agent_exited(self, name: str, *, timeout: float = 15.0) -> tuple[bool, str | None]:
        """Only an explicit agent_not_found confirms exit; errors never do."""
        deadline = time.monotonic() + timeout
        detail: str | None = None
        while True:
            fact = self.agent_fact(name)
            if fact["known"] and not fact["exists"]:
                return True, None
            if fact["error"]:
                detail = fact["error"]
            elif fact["exists"]:
                detail = f"still present with status {fact['status']!r}"
            else:
                detail = "exit could not be confirmed"
            if time.monotonic() >= deadline:
                return False, detail
            time.sleep(0.25)

    def agent_start(self, name: str, kind: str, pane: str, argv: list[str]) -> dict[str, Any]:
        command = ["herdr", "agent", "start", name, "--kind", kind, "--pane", pane]
        if argv:
            command += ["--", *argv]
        last: subprocess.CompletedProcess[str] | None = None
        for index in range(START_ATTEMPTS):
            last = run(command, timeout=90)
            if last.returncode == 0:
                try:
                    payload = json.loads(last.stdout)
                except json.JSONDecodeError as exc:
                    raise ImplementerError(f"agent start returned invalid JSON for {name}") from exc
                return payload if isinstance(payload, dict) else {}
            if herdr_error_code(last) != "agent_pane_busy":
                detail = last.stderr.strip() or last.stdout.strip()
                raise ImplementerError(f"agent start failed for {name}: {detail}")
            if index + 1 < START_ATTEMPTS:
                time.sleep(START_DELAY)
        detail = last.stderr.strip() if last else "no attempt"
        raise ImplementerError(f"agent start kept failing for {name}: {detail}")

    def tab_create(self, cwd: Path, label: str, env: dict[str, str] | None = None) -> tuple[str, str]:
        if not self.workspace:
            raise ImplementerError("HERDR_WORKSPACE_ID is missing")
        args = [
            "tab",
            "create",
            "--workspace",
            self.workspace,
            "--cwd",
            str(cwd),
            "--label",
            label,
        ]
        for key, value in (env or {}).items():
            args += ["--env", f"{key}={value}"]
        payload = self.json_command([*args, "--no-focus"])
        result = payload.get("result") or {}
        tab = result.get("tab")
        pane = result.get("root_pane")
        tab_id = tab if isinstance(tab, str) else (tab or {}).get("tab_id")
        pane_id = pane if isinstance(pane, str) else (pane or {}).get("pane_id")
        if not isinstance(tab_id, str) or not isinstance(pane_id, str):
            raise ImplementerError("tab create response has no result.tab/result.root_pane IDs")
        return tab_id, pane_id

    def tab_close(self, tab: str) -> tuple[int, str | None]:
        """Close one registered tab; a missing tab is already closed."""
        proc = run(["herdr", "tab", "close", tab], timeout=30)
        return proc.returncode, herdr_error_code(proc)

    def agent_prompt(self, name: str, text: str) -> subprocess.CompletedProcess[str]:
        return run(["herdr", "agent", "prompt", name, text], timeout=60)

    def agent_read(self, name: str, lines: int) -> subprocess.CompletedProcess[str]:
        return run(
            ["herdr", "agent", "read", name, "--source", "recent-unwrapped", "--lines", str(lines)],
            timeout=30,
        )

    def send_keys(self, name: str, keys: tuple[str, ...]) -> None:
        run(["herdr", "agent", "send-keys", name, *keys], timeout=30)

    def agent_exists(self, name: str) -> bool:
        rc, _ = self.agent_get(name)
        return rc == 0

    def agent_session_ref(self, name: str) -> str | None:
        rc, payload = self.agent_get(name)
        if rc != 0:
            return None
        return find_agent_session(payload)

    def wait_agent_session(self, name: str, *, attempts: int = 20, delay: float = 0.5) -> str | None:
        """Poll for the runtime session reference; fresh sessions register it late."""
        for index in range(attempts):
            ref = self.agent_session_ref(name)
            if ref:
                return ref
            if index + 1 < attempts:
                time.sleep(delay)
        return None

    def wait_agent_gone(self, name: str, *, timeout: float = 15.0) -> bool:
        """Wait until the runtime no longer knows the agent (TUI exited)."""
        deadline = time.monotonic() + timeout
        while True:
            rc, _ = self.agent_get(name)
            if rc != 0:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.25)

    def wait_settled(self, name: str, timeout: float) -> tuple[int, str | None]:
        deadline = time.monotonic() + timeout
        rc, status = self.agent_status(name)
        while time.monotonic() < deadline:
            if rc != 0 or status in {"idle", "done", "blocked"}:
                return rc, status
            time.sleep(0.25)
            rc, status = self.agent_status(name)
        return rc, status

    def wait_interactive(self, name: str) -> bool:
        for index in range(READY_ATTEMPTS):
            rc, status = self.agent_status(name)
            if rc == 0 and status in INTERACTIVE_STATES:
                return True
            if index + 1 < READY_ATTEMPTS:
                time.sleep(READY_DELAY)
        return False


def parse_pi_models(output: str) -> dict[tuple[str, str], dict[str, Any]]:
    catalog: dict[tuple[str, str], dict[str, Any]] = {}
    for line in output.splitlines():
        columns = line.split()
        if len(columns) < 5 or columns[0].lower() == "provider":
            continue
        provider, model = columns[0], columns[1]
        if not SELECTION_RE.fullmatch(provider) or not SELECTION_RE.fullmatch(model):
            continue
        catalog[(provider, model)] = {"thinking": columns[4].lower() == "yes"}
    return catalog


def parse_opencode_models(output: str) -> dict[str, dict[str, Any]]:
    """Parse `opencode models --verbose` output into {provider/model: metadata}."""
    catalog: dict[str, dict[str, Any]] = {}
    lines = output.splitlines()
    index = 0
    while index < len(lines):
        marker = lines[index].strip()
        if not OPENCODE_MODEL_LINE_RE.fullmatch(marker):
            index += 1
            continue
        cursor = index + 1
        while cursor < len(lines) and lines[cursor].strip() == "":
            cursor += 1
        if cursor >= len(lines) or not lines[cursor].lstrip().startswith("{"):
            index += 1
            continue
        buffer: list[str] = []
        depth = 0
        parsed: Any = None
        while cursor < len(lines):
            line = lines[cursor]
            buffer.append(line)
            depth += line.count("{") - line.count("}")
            cursor += 1
            if depth <= 0:
                try:
                    parsed = json.loads("\n".join(buffer))
                except json.JSONDecodeError:
                    parsed = None
                break
        if isinstance(parsed, dict):
            catalog[marker] = parsed
        index = cursor
    return catalog


class RuntimeAdapter:
    kind = ""

    def start_args(self, provider: str, model: str, thinking: str) -> list[str]:
        raise NotImplementedError

    def tab_env(self, provider: str, model: str, thinking: str) -> dict[str, str]:
        return {}

    def interrupt_sequences(self) -> tuple[tuple[str, ...], ...]:
        raise NotImplementedError

    def exit_sequences(self) -> tuple[tuple[str, ...], ...]:
        raise NotImplementedError

    def handoff_prefix(self, ticket_id: str, ticket_title: str) -> str:
        raise NotImplementedError

    def handoff_request_prompt(
        self,
        worker_id: str,
        ticket_id: str,
        ticket_title: str,
        doc_path: Path,
        reason: str,
        sample: Any,
    ) -> str:
        return handoff_instruction(
            self.handoff_prefix(ticket_id, ticket_title), worker_id, ticket_id, doc_path, reason, sample
        )

    def handoff_correction_prompt(self, ticket_id: str, ticket_title: str, doc_path: Path, problem: str) -> str:
        return handoff_correction_instruction(
            self.handoff_prefix(ticket_id, ticket_title), doc_path, problem
        )

    def validate(self, provider: str, model: str, thinking: str) -> None:
        raise NotImplementedError


class OpenCodeAdapter(RuntimeAdapter):
    kind = "opencode"

    def start_args(self, provider: str, model: str, thinking: str) -> list[str]:
        # `--auto` auto-approves permissions that are not explicitly denied.
        # There is no human at the worker terminal: an unanswered permission
        # prompt (for example `external_directory` for the management
        # directory) would stall the session while Herdr keeps reporting
        # `working` (E03). Explicit denials in repo/user config still apply.
        return ["--auto"]

    def tab_env(self, provider: str, model: str, thinking: str) -> dict[str, str]:
        content = {"agent": {"build": {"model": f"{provider}/{model}", "variant": thinking}}}
        return {"OPENCODE_CONFIG_CONTENT": json.dumps(content, separators=(",", ":"), ensure_ascii=False)}

    def interrupt_sequences(self) -> tuple[tuple[str, ...], ...]:
        return (("escape", "escape"),)

    def exit_sequences(self) -> tuple[tuple[str, ...], ...]:
        return (("ctrl+c",),)

    def handoff_prefix(self, ticket_id: str, ticket_title: str) -> str:
        return (
            f"Use the `skill` tool to load and run the `handoff` skill now and follow it. "
            f"Continue ticket {ticket_id}: {ticket_title} in a new session of the same worker. "
        )

    def validate(self, provider: str, model: str, thinking: str) -> None:
        proc = run(["opencode", "models", provider, "--verbose"], timeout=120)
        if proc.returncode != 0:
            raise ImplementerError(f"cannot list OpenCode models: {proc.stderr.strip() or proc.stdout.strip()}")
        catalog = parse_opencode_models(proc.stdout)
        key = f"{provider}/{model}"
        entry = catalog.get(key)
        if entry is None:
            raise ImplementerError(f"OpenCode does not list model {key}; no default fallback is allowed")
        variants = entry.get("variants")
        available = sorted(variants) if isinstance(variants, dict) else []
        if not available:
            raise ImplementerError(
                f"OpenCode model {key} exposes no reasoning variants; thinking cannot be expressed explicitly"
            )
        if thinking not in available:
            raise ImplementerError(
                f"OpenCode model {key} does not support variant {thinking!r}; "
                f"available variants: {', '.join(available)}"
            )


class PiAdapter(RuntimeAdapter):
    kind = "pi"

    def start_args(self, provider: str, model: str, thinking: str) -> list[str]:
        return ["--provider", provider, "--model", model, "--thinking", thinking]

    def interrupt_sequences(self) -> tuple[tuple[str, ...], ...]:
        return (("escape",), ("escape",))

    def exit_sequences(self) -> tuple[tuple[str, ...], ...]:
        return (("ctrl+c", "ctrl+c"),)

    def handoff_prefix(self, ticket_id: str, ticket_title: str) -> str:
        return (
            f"/skill:handoff Continue ticket {ticket_id}: {ticket_title} in a new session of the same worker. "
            "Run the handoff skill now and follow its instructions. "
        )

    def validate(self, provider: str, model: str, thinking: str) -> None:
        if thinking not in THINKING_LEVELS:
            raise ImplementerError(f"unsupported thinking level: {thinking!r}")
        proc = run(["pi", "--list-models"], timeout=60)
        if proc.returncode != 0:
            raise ImplementerError(f"cannot list Pi models: {proc.stderr.strip() or proc.stdout.strip()}")
        catalog = parse_pi_models(proc.stdout)
        entry = catalog.get((provider, model))
        if entry is None:
            raise ImplementerError(f"Pi does not list model {provider}/{model}; no default fallback is allowed")
        if thinking != "off" and not entry["thinking"]:
            raise ImplementerError(
                f"Pi model {provider}/{model} does not support thinking levels; choose 'off' or another model"
            )


def get_adapter(kind: str) -> RuntimeAdapter:
    if kind == "pi":
        return PiAdapter()
    if kind == "opencode":
        return OpenCodeAdapter()
    raise ImplementerError(f"unsupported runtime kind: {kind!r}")


def worker_id_for(ticket_id: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", ticket_id.lower()).strip("-") or "ticket"
    return f"w-{slug[:40]}-{uuid.uuid4().hex[:6]}"


def agent_name_for(worker_id: str) -> str:
    base = re.sub(r"[^a-z0-9_-]+", "-", worker_id.lower())
    if not base or not base[0].isalpha():
        base = "hi-" + base.lstrip("-")
    base = base[:32]
    if not AGENT_RE.fullmatch(base):
        raise ImplementerError(f"cannot derive a Herdr agent name from worker id {worker_id!r}")
    return base


def snapshot_materials(worker_dir: Path, sources: list[Path], worker_id: str) -> dict[str, Any]:
    materials_dir = worker_dir / "materials"
    materials_dir.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, Any]] = []
    for index, source in enumerate(sources, start=1):
        resolved = source.resolve()
        if not resolved.exists():
            raise ImplementerError(f"material does not exist: {source}")
        target = materials_dir / f"{index:02d}-{resolved.name or 'material'}"
        if target.exists():
            raise ImplementerError(f"material snapshot already exists: {target}")
        if resolved.is_dir():
            shutil.copytree(resolved, target, symlinks=True)
            kind = "directory"
        elif resolved.is_file():
            shutil.copy2(resolved, target)
            kind = "file"
        else:
            raise ImplementerError(f"material is neither a regular file nor a directory: {source}")
        digest = tree_sha256(target) if kind == "directory" else file_sha256(target)
        entries.append(
            {
                "index": index,
                "source": str(resolved),
                "snapshot": str(target),
                "kind": kind,
                "sha256": digest,
            }
        )
    manifest = {"worker_id": worker_id, "created_at": utc_now(), "materials": entries}
    atomic_json(materials_dir / "manifest.json", manifest)
    return manifest


def render_contract(template_path: Path, values: dict[str, str]) -> str:
    text = template_path.read_text(encoding="utf-8")
    pattern = re.compile(r"\{\{([^{}]*)\}\}")
    placeholders = set(pattern.findall(text))
    missing = set(values) - placeholders
    unknown = placeholders - set(values)
    malformed = pattern.sub("", text)
    if missing or unknown or "{{" in malformed or "}}" in malformed:
        raise ImplementerError(
            f"contract template placeholder mismatch: {template_path}; "
            f"missing={sorted(missing)}, unknown={sorted(unknown)}, "
            f"malformed_delimiters={'{{' in malformed or '}}' in malformed}"
        )
    # Substitute once: task text may itself contain template-like literals.
    return pattern.sub(lambda match: values[match.group(1)], text)


def initial_state(
    worker_id: str,
    ticket_id: str,
    title: str,
    runtime: dict[str, str],
    repo_root: Path,
    common_dir: Path,
    base: str,
    branch: str,
    worktree: Path,
    store: Store,
    workspace: str | None,
    agent: str,
    run_id: str,
) -> dict[str, Any]:
    worker_dir = store.worker_dir(worker_id)
    timestamp = utc_now()
    return {
        "version": STATE_VERSION,
        "worker_id": worker_id,
        "run_id": run_id,
        "ticket": {"id": ticket_id, "title": title},
        "runtime": runtime,
        "repo": {"root": str(repo_root), "common_dir": str(common_dir), "base": base},
        "branch": branch,
        "worktree": str(worktree),
        "herdr": {"workspace": workspace, "agent": agent, "tab": None, "pane": None},
        "paths": {
            "management": str(worker_dir),
            "contract": str(store.contract_path(worker_id)),
            "result": str(store.result_path(worker_id)),
            "supervisor_log": str(store.log_path(worker_id)),
        },
        "lifecycle": {"state": "allocating", "reason": None, "started_at": timestamp, "updated_at": timestamp},
        "supervisor": {
            "pid": None,
            "host": None,
            "state": "starting",
            "started_at": None,
            "heartbeat_at": None,
            "exit_reason": None,
        },
        "starter": {"pid": os.getpid(), "host": os.uname().nodename},
        "recovery": {"re_report_sent_at": None, "settled_since": None, "parse_failures": 0},
        "prompt": None,
        "items": [],
        "result": None,
        "session_index": 0,
        "sessions": [],
        "handoff": {
            "tokens": DEFAULT_HANDOFF_TOKENS,
            "pct": DEFAULT_HANDOFF_PCT,
            "window": None,
            "history": [],
            "auto_suppressed": False,
            "handled_request_id": None,
        },
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def append_item(state: dict[str, Any], kind: str, code: str, message: str, details: Any = None) -> dict[str, Any]:
    item = {
        "id": f"i{len(state['items']) + 1:03d}",
        "kind": kind,
        "code": code,
        "message": message,
        "details": details,
        "created_at": utc_now(),
    }
    state["items"].append(item)
    return item


def session_agent_name(worker_id: str, index: int) -> str:
    """Derive a per-session Herdr agent name from the worker id."""
    suffix = f"-s{index}"
    base = re.sub(r"[^a-z0-9_-]+", "-", worker_id.lower()).strip("-")
    if not base or not base[0].isalpha():
        base = "hi-" + base.lstrip("-")
    base = base[: 32 - len(suffix)].rstrip("-")
    name = f"{base}{suffix}"
    if not AGENT_RE.fullmatch(name):
        raise ImplementerError(f"cannot derive a Herdr agent name for session {index} of worker {worker_id!r}")
    return name


def new_session_record(
    index: int, agent: str, tab: str | None, pane: str | None, runtime: dict[str, Any]
) -> dict[str, Any]:
    return {
        "index": index,
        "agent": agent,
        "tab": tab,
        "pane": pane,
        "runtime": {key: runtime[key] for key in ("kind", "provider", "model", "thinking") if key in runtime},
        "status": "active",
        "started_at": utc_now(),
        "ended_at": None,
        "end_state": None,
        "context_ref": None,
        "context": None,
        "prompt": None,
        "handoff": None,
    }


def ensure_session_state(state: dict[str, Any]) -> None:
    """Backfill session bookkeeping for states written before ticket 04."""
    handoff = state.setdefault("handoff", {})
    handoff.setdefault("tokens", DEFAULT_HANDOFF_TOKENS)
    handoff.setdefault("pct", DEFAULT_HANDOFF_PCT)
    handoff.setdefault("window", None)
    handoff.setdefault("history", [])
    handoff.setdefault("auto_suppressed", False)
    handoff.setdefault("handled_request_id", None)
    if isinstance(state.get("sessions"), list) and state["sessions"]:
        return
    herdr = state.get("herdr") or {}
    state["sessions"] = [
        new_session_record(
            1,
            str(herdr.get("agent") or ""),
            herdr.get("tab"),
            herdr.get("pane"),
            state.get("runtime") or {},
        )
    ]
    state["session_index"] = 1


def current_session(state: dict[str, Any]) -> dict[str, Any] | None:
    sessions = state.get("sessions")
    if isinstance(sessions, list) and sessions:
        return sessions[-1]
    return None


def current_agent(state: dict[str, Any]) -> str:
    session = current_session(state)
    if session and session.get("agent"):
        return str(session["agent"])
    return str((state.get("herdr") or {}).get("agent") or "")


def mark_session_ended(state: dict[str, Any], end_state: str) -> None:
    session = current_session(state)
    if session is not None and session.get("status") in {"active", "handing-off"}:
        session["status"] = "ended"
        session["ended_at"] = utc_now()
        session["end_state"] = end_state


def context_helper_path() -> Path:
    override = env_value("HI_CONTEXT_HELPER")
    if override:
        return Path(override)
    return IMPLEMENTER_PATH.with_name("get_context.py")


def context_snapshot(kind: str, ref: str, window: int | None = None, timeout: float = 60.0) -> dict[str, Any]:
    """Run the context adapter once; failures become an explicit unobservable sample."""
    args = [sys.executable, str(context_helper_path()), kind, ref, "--json"]
    if window:
        args += ["--window", str(int(window))]
    try:
        proc = run(args, timeout=timeout)
    except ImplementerError as exc:
        return {"error": str(exc), "observed_at": utc_now()}
    payload: Any = None
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        payload = None
    if proc.returncode != 0:
        message = payload.get("error") if isinstance(payload, dict) else None
        if not isinstance(message, str) or not message.strip():
            message = proc.stderr.strip() or "context adapter failed"
        return {"error": message, "observed_at": utc_now()}
    if not isinstance(payload, dict) or not isinstance(payload.get("total"), (int, float)):
        return {"error": "context adapter returned an unusable payload", "observed_at": utc_now()}
    return payload


def classify_context(payload: dict[str, Any], *, agent_status: str | None) -> dict[str, Any]:
    """Name what an observation means: current call, stale sample, pending or unobservable."""
    sample = dict(payload)
    sample.setdefault("observed_at", utc_now())
    if sample.get("error"):
        sample["state"] = "pending" if agent_status not in SETTLED_STATES else "unobservable"
    elif sample.get("freshness") and sample["freshness"] != "completed-call":
        sample["state"] = "stale"
    else:
        sample["state"] = "current"
    return sample


def mark_context_unobservable(state: dict[str, Any], session: dict[str, Any], sample: dict[str, Any]) -> None:
    for item in state["items"]:
        if item.get("code") == "context-unobservable" and (item.get("details") or {}).get("session") == session["index"]:
            return
    append_item(
        state,
        "exception",
        "context-unobservable",
        f"current context cannot be observed: {sample.get('error')}",
        {"session": session["index"], "context_ref": session.get("context_ref"), "error": sample.get("error")},
    )


def sample_current_session(
    state: dict[str, Any], herdr: Herdr, agent: str, agent_status: str | None
) -> dict[str, Any]:
    session = current_session(state)
    if session is None:
        return {"state": "unobservable", "error": "worker has no session record", "observed_at": utc_now()}
    ref = session.get("context_ref")
    if not ref:
        ref = herdr.wait_agent_session(agent, attempts=1, delay=0.0)
        if ref:
            session["context_ref"] = ref
    if not ref:
        sample: dict[str, Any] = {
            "state": "pending" if agent_status not in SETTLED_STATES else "unobservable",
            "error": "herdr has not published a runtime session reference yet",
            "observed_at": utc_now(),
        }
    else:
        sample = classify_context(
            context_snapshot(state["runtime"]["kind"], ref, (state.get("handoff") or {}).get("window")),
            agent_status=agent_status,
        )
        sample["context_ref"] = ref
    sample["session"] = session["index"]
    sample["sampled_at"] = utc_now()
    session["context"] = sample
    if sample["state"] == "unobservable":
        mark_context_unobservable(state, session, sample)
    return sample


def handoff_trigger_reason(sample: Any, config: dict[str, Any]) -> str | None:
    if not isinstance(sample, dict) or sample.get("state") != "current":
        return None
    total = sample.get("total")
    if not isinstance(total, (int, float)):
        return None
    tokens = config.get("tokens", DEFAULT_HANDOFF_TOKENS)
    if isinstance(tokens, (int, float)) and total >= tokens:
        return "tokens"
    window = sample.get("window")
    pct = config.get("pct", DEFAULT_HANDOFF_PCT)
    if isinstance(window, (int, float)) and window > 0 and isinstance(pct, (int, float)) and total / window >= pct:
        return "pct"
    return None


def sample_is_current(sample: Any, state: dict[str, Any]) -> bool:
    """A sample may only trigger the session it belongs to; old samples are stale."""
    session = current_session(state)
    if session is None or not isinstance(sample, dict):
        return False
    if sample.get("session") != session.get("index"):
        return False
    ref = sample.get("context_ref")
    if ref and session.get("context_ref") and ref != session["context_ref"]:
        return False
    return True


_SECTION_PATTERNS: dict[str, re.Pattern[str]] = {}


def section_pattern(section: str) -> re.Pattern[str]:
    pattern = _SECTION_PATTERNS.get(section)
    if pattern is None:
        pattern = re.compile(
            rf"(?im)^\s*(?:[-*]\s*)?(?:\#{{1,6}}|\*\*)\s*\**\s*{re.escape(section)}\b"
        )
        _SECTION_PATTERNS[section] = pattern
    return pattern


def validate_handoff_document(path: Path) -> tuple[bool, list[str], str]:
    """Check structure, not prose quality: every required section must be present."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return False, list(HANDOFF_SECTIONS), "no file at the requested path"
    except (OSError, UnicodeDecodeError) as exc:
        return False, list(HANDOFF_SECTIONS), f"the file cannot be read: {exc}"
    if len(text.encode("utf-8", "replace")) > MAX_HANDOFF_DOC_BYTES:
        return False, list(HANDOFF_SECTIONS), "the document exceeds the size limit"
    if len(text.strip()) < 80:
        return False, list(HANDOFF_SECTIONS), "the document has no meaningful content"
    missing = [section for section in HANDOFF_SECTIONS if not section_pattern(section).search(text)]
    if missing:
        return False, missing, f"missing required sections: {', '.join(missing)}"
    return True, [], ""


def locate_handoff_document(
    state: dict[str, Any], doc_path: Path, record: dict[str, Any]
) -> tuple[Path | None, str]:
    """Prefer the requested path; fall back to copying a skill temp-directory document."""
    if doc_path.is_file():
        return doc_path, "requested-path"
    try:
        requested_at = parse_iso(str(record.get("requested_at") or utc_now()))
    except ValueError:
        requested_at = time.time()
    tempdir = Path(tempfile.gettempdir())
    if not tempdir.is_dir():
        return None, "missing"
    worker_id = str(state.get("worker_id") or "")
    ticket_id = str((state.get("ticket") or {}).get("id") or "")
    newest: Path | None = None
    newest_mtime = 0.0
    for item in sorted(set(tempdir.glob("*.md")) | set(tempdir.glob("handoff*.md"))):
        try:
            stat = item.stat()
        except OSError:
            continue
        if not item.is_file() or stat.st_mtime < requested_at - 5:
            continue
        try:
            text = item.read_text(encoding="utf-8", errors="replace")[:MAX_HANDOFF_DOC_BYTES]
        except OSError:
            continue
        identified = (worker_id and worker_id in text) or (ticket_id and ticket_id in text)
        if identified and stat.st_mtime > newest_mtime:
            newest = item
            newest_mtime = stat.st_mtime
    if newest is None:
        return None, "missing"
    atomic_text(doc_path, newest.read_text(encoding="utf-8", errors="replace"))
    return doc_path, "temp-copy"


def handoff_instruction(
    prefix: str, worker_id: str, ticket_id: str, doc_path: Path, reason: str, sample: Any
) -> str:
    sample_note = ""
    if isinstance(sample, dict) and isinstance(sample.get("total"), (int, float)):
        sample_note = f" Observed context use: {int(sample['total'])} tokens"
        window = sample.get("window")
        if isinstance(window, (int, float)) and window > 0:
            sample_note += f" of {int(window)} ({sample['total'] / window:.0%})"
        sample_note += "."
    sections = "\n".join(f"## {section}" for section in HANDOFF_SECTIONS)
    return (
        f"{prefix}"
        f"Reason for the handoff: {reason}.{sample_note}\n"
        f"Save the handoff document exactly to `{doc_path}` (absolute path). This overrides any default "
        "temporary-directory location from the handoff skill; do not write it anywhere else.\n"
        "The document must contain these exact section headings, each with concrete content for this ticket:\n"
        f"{sections}\n"
        "Record progress, decisions, commits and uncommitted work, verification already run, and the precise "
        "remaining work so the next session can continue without repeating anything or asking the caller.\n"
        "Do not make further business edits after the document is saved; end your turn then."
    )


def handoff_correction_instruction(prefix: str, doc_path: Path, problem: str) -> str:
    sections = "\n".join(f"## {section}" for section in HANDOFF_SECTIONS)
    return (
        f"{prefix}"
        f"The handoff document at `{doc_path}` cannot be used yet: {problem}.\n"
        "Rewrite it now at the same absolute path with every required section heading and concrete content:\n"
        f"{sections}\n"
        "Then end your turn."
    )


def continuation_prompt(state: dict[str, Any], doc_path: Path) -> str:
    ticket = state["ticket"]
    return (
        "A previous session of this worker reached the context-handoff threshold and wrote a handoff document. "
        f"Read `{doc_path}` in full before doing anything else. Then read your worker contract at "
        f"`{state['paths']['contract']}` in full. "
        f"You are worker {state['worker_id']} for ticket {ticket['id']}: {ticket['title']}, continuing the same "
        f"ticket in the same worktree `{state['worktree']}` on branch `{state['branch']}` with the same runtime "
        "configuration. Treat the handoff document as progress context; the contract, ticket and materials remain "
        "authoritative. Continue the unfinished work, preserve committed and uncommitted changes, and finish by "
        "writing the result file exactly as the contract requires."
    )


def wait_for_settled(
    herdr: Herdr, agent: str, timeout: float, poll: float = 0.25
) -> tuple[int, str | None]:
    deadline = time.monotonic() + timeout
    rc, status = herdr.agent_status(agent)
    while True:
        if rc != 0 or status in SETTLED_STATES:
            return rc, status
        if time.monotonic() >= deadline:
            return rc, status
        time.sleep(poll)
        rc, status = herdr.agent_status(agent)


def settle_session_for_control(herdr: Herdr, agent: str, adapter: RuntimeAdapter) -> bool:
    """Pause business execution so the session can accept a control instruction."""
    grace = env_float("HI_HANDOFF_SETTLE_SECONDS", 15.0)
    timeout = env_float("HI_HANDOFF_SETTLE_TIMEOUT_SECONDS", 20.0)
    rc, status = wait_for_settled(herdr, agent, grace)
    if rc != 0:
        return False
    if status in SETTLED_STATES:
        return True
    for keys in adapter.interrupt_sequences():
        try:
            herdr.send_keys(agent, keys)
        except ImplementerError:
            return False
        rc, status = wait_for_settled(herdr, agent, timeout)
        if rc != 0:
            return False
        if status in SETTLED_STATES:
            return True
    return False


def result_declared(state: dict[str, Any]) -> bool:
    """True once the worker has written its result file; delivery stops auto-handoff."""
    raw = str((state.get("paths") or {}).get("result") or "")
    return bool(raw) and Path(raw).is_file()


def live_session_facts(herdr: Herdr, state: dict[str, Any]) -> list[dict[str, Any]]:
    """Registered sessions paired with their actual Herdr agent state (read-only)."""
    sessions = state.get("sessions")
    if not isinstance(sessions, list) or not sessions:
        agent = str((state.get("herdr") or {}).get("agent") or "")
        if not agent:
            return []
        rc, status = herdr.agent_status(agent)
        return [
            {
                "index": state.get("session_index"),
                "agent": agent,
                "tab": (state.get("herdr") or {}).get("tab"),
                "session_status": None,
                "agent_live": rc == 0,
                "agent_status": status if rc == 0 else None,
            }
        ]
    facts: list[dict[str, Any]] = []
    for session in sessions:
        agent = str(session.get("agent") or "")
        rc, status = herdr.agent_status(agent) if agent else (1, None)
        facts.append(
            {
                "index": session.get("index"),
                "agent": agent,
                "tab": session.get("tab"),
                "session_status": session.get("status"),
                "agent_live": rc == 0,
                "agent_status": status if rc == 0 else None,
            }
        )
    return facts


def mark_session_stopped(state: dict[str, Any], fact: dict[str, Any], end_state: str = "stopped") -> None:
    for session in state.get("sessions") or []:
        if session.get("index") == fact.get("index") and session.get("status") in {"active", "handing-off"}:
            session["status"] = "ended"
            session["ended_at"] = utc_now()
            session["end_state"] = end_state


def stop_registered_sessions(herdr: Herdr, adapter: RuntimeAdapter, state: dict[str, Any]) -> dict[str, Any]:
    """Pause business execution in every registered session; never closes a TUI.

    Stop must also cover the handoff competition: whichever session is current
    when the request lands is the one that gets paused, so a replacement
    session cannot keep writing behind a stopped worker.
    """
    facts: list[dict[str, Any]] = []
    stopped = True
    for fact in reversed(live_session_facts(herdr, state)):
        agent = fact["agent"]
        if not agent or not fact["agent_live"] or fact["agent_status"] in {"idle", "done", "blocked"}:
            mark_session_stopped(state, fact)
            facts.append({**fact, "stopped": True})
            continue
        ok = settle_session_for_control(herdr, agent, adapter)
        rc, status = herdr.agent_status(agent)
        if ok:
            mark_session_stopped(state, fact)
        else:
            stopped = False
        facts.append({**fact, "agent_status": status if rc == 0 else None, "stopped": ok})
    return {"business_stopped": stopped, "sessions": facts}


def worktree_listing(repo: Path) -> dict[str, dict[str, str]]:
    """Parse `git worktree list --porcelain` into {resolved path: entry}."""
    proc = git(repo, "worktree", "list", "--porcelain", check=False)
    if proc.returncode != 0:
        raise ImplementerError(f"cannot list git worktrees in {repo}: {proc.stderr.strip() or proc.stdout.strip()}")
    listing: dict[str, dict[str, str]] = {}
    current: dict[str, str] = {}
    for raw in [*proc.stdout.splitlines(), ""]:
        line = raw.rstrip("\n")
        if not line.strip():
            path = current.get("worktree")
            if path:
                listing[str(Path(path).resolve())] = current
            current = {}
            continue
        key, _, value = line.partition(" ")
        current[key] = value
    return listing


def uncommitted_entries(worktree: Path) -> list[dict[str, str]]:
    """Tracked modifications and untracked files (ignored files are not evidence)."""
    proc = git(worktree, "status", "--porcelain=v1", check=False)
    if proc.returncode != 0:
        return []
    entries: list[dict[str, str]] = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        entries.append({"code": line[:2].strip() or "??", "path": line[3:]})
    return entries


def archive_uncommitted(worker_dir: Path, worktree: Path) -> dict[str, Any]:
    """Copy every uncommitted change into the durable management dir before removal."""
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = worker_dir / "cleanup" / f"uncommitted-{stamp}"
    if target.exists():
        target = worker_dir / "cleanup" / f"uncommitted-{stamp}-{uuid.uuid4().hex[:6]}"
    target.mkdir(parents=True)
    patch = git(worktree, "diff", "HEAD", "--binary", check=False)
    if patch.returncode != 0:
        raise ImplementerError(f"cannot read uncommitted changes: {patch.stderr.strip() or patch.stdout.strip()}")
    atomic_text(target / "tracked.patch", patch.stdout)
    untracked_proc = git(worktree, "ls-files", "--others", "--exclude-standard", "-z", check=False)
    if untracked_proc.returncode != 0:
        raise ImplementerError(f"cannot list untracked files: {untracked_proc.stderr.strip()}")
    untracked = [item for item in untracked_proc.stdout.split("\0") if item]
    archived: list[dict[str, Any]] = []
    for relative in untracked:
        source = worktree / relative
        destination = target / "untracked" / relative
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if source.is_symlink():
                os.symlink(os.readlink(source), destination)
                archived.append({"path": relative, "kind": "symlink"})
                continue
            shutil.copy2(source, destination)
        except OSError as exc:
            raise ImplementerError(f"cannot archive uncommitted file {relative!r}: {exc}") from exc
        archived.append({"path": relative, "bytes": source.stat().st_size})
    manifest = {
        "created_at": utc_now(),
        "worktree": str(worktree),
        "tracked_patch_bytes": len(patch.stdout.encode("utf-8")),
        "tracked_entries": uncommitted_entries(worktree),
        "untracked_files": archived,
    }
    atomic_json(target / "manifest.json", manifest)
    return {
        "path": str(target),
        "tracked_patch_bytes": manifest["tracked_patch_bytes"],
        "untracked_files": archived,
    }


def close_registered_sessions(
    herdr: Herdr,
    adapter: RuntimeAdapter,
    state: dict[str, Any],
    *,
    timeout: float = DEFAULT_SESSION_CLOSE_TIMEOUT,
    skip_tabs: set[str] | None = None,
) -> dict[str, Any]:
    """Close only the tabs recorded for this worker, after ending their live TUIs."""
    live = live_session_facts(herdr, state)
    for fact in live:
        agent = fact["agent"]
        if not fact["agent_live"]:
            continue
        for keys in adapter.exit_sequences():
            try:
                herdr.send_keys(agent, keys)
            except ImplementerError:
                break
            if herdr.wait_agent_gone(agent, timeout=timeout):
                break
    tabs: list[str] = []
    for fact in live:
        tab = fact.get("tab")
        if isinstance(tab, str) and tab and tab not in tabs:
            tabs.append(tab)
    closed: list[str] = []
    failed: list[dict[str, Any]] = []
    for tab in tabs:
        if skip_tabs and tab in skip_tabs:
            continue
        rc, code = herdr.tab_close(tab)
        if rc == 0 or code == "tab_not_found":
            closed.append(tab)
        else:
            failed.append({"tab": tab, "error": code or "tab-close-failed"})
    remaining = [fact for fact in live_session_facts(herdr, state) if fact["agent_live"]]
    return {
        "closed_tabs": closed,
        "remaining_agents": remaining,
        "failed_tabs": failed,
        "closed": not remaining and not failed,
    }


def registered_tab_facts(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Tabs registered to this worker, with the panes and agents it owns in each."""
    tabs: dict[str, dict[str, Any]] = {}
    sessions = state.get("sessions") if isinstance(state.get("sessions"), list) else []
    records = sessions or [
        {
            "tab": (state.get("herdr") or {}).get("tab"),
            "pane": (state.get("herdr") or {}).get("pane"),
            "agent": (state.get("herdr") or {}).get("agent"),
        }
    ]
    for session in records:
        if not isinstance(session, dict):
            continue
        tab = session.get("tab")
        if not isinstance(tab, str) or not tab:
            continue
        entry = tabs.setdefault(tab, {"tab": tab, "panes": [], "agents": []})
        pane = session.get("pane")
        if isinstance(pane, str) and pane and pane not in entry["panes"]:
            entry["panes"].append(pane)
        agent = session.get("agent")
        if isinstance(agent, str) and agent and agent not in entry["agents"]:
            entry["agents"].append(agent)
    return list(tabs.values())


def registered_agent_facts(herdr: Herdr, state: dict[str, Any]) -> dict[str, Any]:
    """Strictly classify the worker's registered agents for release decisions.

    Only an explicit `agent_not_found` proves that a session is gone. A query
    error, a missing status or a missing agent name is an unconfirmed state
    that must retain the terminal instead of being read as an exit.
    """
    sessions = state.get("sessions") if isinstance(state.get("sessions"), list) else []
    records = sessions or [state.get("herdr") or {}]
    unknown: list[dict[str, Any]] = []
    seen: list[tuple[Any, str]] = []
    for position, session in enumerate(records):
        if not isinstance(session, dict):
            unknown.append({"index": None, "agent": "", "error": "the session record is not an object"})
            continue
        index = session.get("index", position)
        agent = session.get("agent")
        if not isinstance(agent, str) or not agent:
            if session.get("tab"):
                unknown.append({"index": index, "agent": "", "error": "the session record has no agent name"})
            continue
        if (index, agent) not in seen:
            seen.append((index, agent))
    facts: list[dict[str, Any]] = []
    busy: list[dict[str, Any]] = []
    for index, agent in seen:
        fact = herdr.agent_fact(agent)
        entry = {
            "index": index,
            "agent": agent,
            "exists": fact["exists"],
            "status": fact["status"],
            "error": fact["error"],
        }
        facts.append(entry)
        if fact["error"]:
            unknown.append(entry)
        elif fact["exists"] and fact["status"] not in SETTLED_STATES:
            busy.append(entry)
    return {"agents": facts, "unknown": unknown, "busy": busy, "names": [agent for _, agent in seen]}


def tab_occupancy_facts(herdr: Herdr, tabs: list[dict[str, Any]]) -> dict[str, Any]:
    """Check that registered tabs still host only this worker's panes and agents.

    Both listings are shape-validated by `Herdr`; a malformed response is an
    unconfirmed state, not an empty one. A registered pane reported under a
    tab other than the one it was registered for is a conflict, never an
    unrelated pane. An agent on one of our panes that cannot be attributed to
    one of our registered names is also foreign.
    """
    try:
        panes = herdr.pane_list()
        agents = herdr.agent_list()
    except ImplementerError as exc:
        return {"confirmed": False, "error": str(exc), "foreign": [], "agents": []}
    tab_ids = {entry["tab"] for entry in tabs}
    pane_tabs: dict[str, set[str]] = {}
    for entry in tabs:
        for pane in entry["panes"]:
            pane_tabs.setdefault(pane, set()).add(entry["tab"])
    registered_agents = {agent for entry in tabs for agent in entry["agents"]}
    foreign: list[dict[str, Any]] = []
    for pane in panes:
        pane_id = pane["pane_id"]
        tab_id = pane["tab_id"]
        owned_tabs = pane_tabs.get(pane_id)
        if owned_tabs is not None:
            if tab_id not in owned_tabs:
                foreign.append(
                    {
                        "tab": tab_id,
                        "pane": pane_id,
                        "reason": "pane-tab-conflict",
                        "registered_tabs": sorted(owned_tabs),
                    }
                )
            continue
        if tab_id in tab_ids:
            foreign.append({"tab": tab_id, "pane": pane_id, "reason": "unregistered-pane"})
    for agent in agents:
        pane_id = agent["pane_id"]
        tab_id = agent["tab_id"]
        name = agent.get("name")
        owned_tabs = pane_tabs.get(pane_id)
        if owned_tabs is not None:
            if tab_id not in owned_tabs:
                foreign.append(
                    {
                        "tab": tab_id,
                        "pane": pane_id,
                        "agent": name if isinstance(name, str) and name else None,
                        "reason": "pane-tab-conflict",
                        "registered_tabs": sorted(owned_tabs),
                    }
                )
                continue
            if isinstance(name, str) and name and name in registered_agents:
                continue
            foreign.append(
                {
                    "tab": tab_id,
                    "pane": pane_id,
                    "agent": name if isinstance(name, str) and name else None,
                    "reason": "foreign-agent" if isinstance(name, str) and name else "unidentified-agent",
                }
            )
            continue
        if tab_id in tab_ids:
            foreign.append(
                {
                    "tab": tab_id,
                    "pane": pane_id,
                    "agent": name if isinstance(name, str) and name else None,
                    "reason": "foreign-agent" if isinstance(name, str) and name else "unidentified-agent",
                }
            )
    return {"confirmed": True, "error": None, "foreign": foreign, "agents": agents}


def release_delivered_tabs(
    herdr: Herdr,
    adapter: RuntimeAdapter,
    state: dict[str, Any],
    *,
    timeout: float = DEFAULT_SESSION_CLOSE_TIMEOUT,
) -> dict[str, Any]:
    """Release terminals only after a valid delivery has been durably recorded."""
    recorded = state.get("result")
    if not (isinstance(recorded, dict) and recorded.get("status") == "delivered"):
        previous = state.get("release") or {}
        if previous.get("reason") != "no-valid-delivery":
            state["release"] = {
                "state": "retained", "reason": "no-valid-delivery",
                "message": "no valid delivered result is recorded; the terminal is retained",
                "at": utc_now(), "first_at": previous.get("first_at") or utc_now(),
                "attempts": int(previous.get("attempts") or 0) + 1,
                "tabs": {"closed": [], "registered": [t["tab"] for t in registered_tab_facts(state)]},
            }
        return {"attempted": False, "closed": False, "reason": "no-valid-delivery"}
    return release_terminal_tabs(herdr, adapter, state, timeout=timeout)


def release_terminal_tabs(
    herdr: Herdr,
    adapter: RuntimeAdapter,
    state: dict[str, Any],
    *,
    timeout: float = DEFAULT_SESSION_CLOSE_TIMEOUT,
) -> dict[str, Any]:
    """Release a completed scope's tabs, never its files or branch.

    Callers persist delivery or handoff evidence first. For a handoff, the
    scope contains only the replaced session, not the working replacement.
    Confirm session exit and exclusive occupancy again at each close. Git
    cleanliness belongs to disk cleanup, not terminal release.
    """
    previous = state.get("release") if isinstance(state.get("release"), dict) else {}
    tabs = registered_tab_facts(state)

    def remember(outcome: str, reason: str, **extra: Any) -> dict[str, Any]:
        closed_tabs = [tab for tab in (previous.get("tabs") or {}).get("closed") or [] if isinstance(tab, str)]
        for tab in extra.get("closed_tabs") or []:
            if tab not in closed_tabs:
                closed_tabs.append(tab)
        record: dict[str, Any] = {
            "state": outcome,
            "reason": reason,
            "message": extra.pop("message", reason),
            "at": utc_now(),
            "first_at": previous.get("first_at") or utc_now(),
            "attempts": int(previous.get("attempts") or 0) + 1,
            "tabs": {"closed": closed_tabs, "registered": [entry["tab"] for entry in tabs]},
        }
        record.update(extra)
        state["release"] = record
        return {"attempted": True, "closed": outcome == "closed", "reason": reason, "tabs": tabs, "record": record}

    if not tabs:
        return remember("not-applicable", "no-registered-tab", message="the worker has no registered tab")

    def registered_busy(occupancy: dict[str, Any], names: list[str]) -> list[dict[str, Any]]:
        known = set(names)
        return [
            {"agent": agent.get("name"), "status": agent.get("agent_status"), "pane": agent.get("pane_id")}
            for agent in occupancy.get("agents") or []
            if isinstance(agent.get("name"), str)
            and agent["name"] in known
            and agent.get("agent_status") not in SETTLED_STATES
        ]

    sessions = registered_agent_facts(herdr, state)
    if previous.get("state") == "closed" and not sessions["busy"] and not sessions["unknown"]:
        return {
            "attempted": False,
            "closed": True,
            "already_closed": True,
            "reason": "already-released",
            "tabs": tabs,
        }
    if sessions["unknown"]:
        return remember(
            "retained",
            "session-state-unknown",
            message="a registered session state cannot be confirmed; the terminal is retained",
            sessions=sessions["unknown"],
        )
    if sessions["busy"]:
        return remember(
            "retained",
            "session-not-settled",
            message="a registered session has not settled after delivery; the terminal is retained",
            sessions=sessions["busy"],
        )
    occupancy = tab_occupancy_facts(herdr, tabs)
    if not occupancy["confirmed"]:
        return remember(
            "retained",
            "tab-occupancy-unverified",
            message="the registered tab contents cannot be confirmed; the terminal is retained",
            error=occupancy["error"],
        )
    if occupancy["foreign"]:
        return remember(
            "retained",
            "foreign-occupancy",
            message="a registered tab holds an unregistered pane or another agent; it is not closed",
            foreign=occupancy["foreign"],
        )
    listed = registered_busy(occupancy, sessions["names"])
    if listed:
        return remember(
            "retained",
            "session-not-settled",
            message="Herdr still lists a registered agent as active; the terminal is retained",
            sessions=listed,
        )

    remaining: list[dict[str, Any]] = []
    for fact in sessions["agents"]:
        if not fact["exists"]:
            continue
        agent = fact["agent"]
        gone = False
        detail: str | None = None
        for keys in adapter.exit_sequences():
            try:
                herdr.send_keys(agent, keys)
            except ImplementerError as exc:
                detail = str(exc)
                break
            gone, detail = herdr.wait_agent_exited(agent, timeout=timeout)
            if gone:
                break
        if not gone:
            remaining.append({"index": fact["index"], "agent": agent, "status": fact["status"], "detail": detail})
    if remaining:
        return remember(
            "retained",
            "session-exit-failed",
            message="a registered session could not be confirmed exited; the terminal is retained",
            remaining_sessions=remaining,
        )

    # The exit waits took time: re-verify ownership on the state that exists
    # now, not the one observed before the exits.
    after = registered_agent_facts(herdr, state)
    if after["unknown"]:
        return remember(
            "retained",
            "session-state-unknown",
            message="a session state cannot be confirmed after the exit attempt; the terminal is retained",
            phase="post-exit",
            sessions=after["unknown"],
        )
    if after["busy"]:
        return remember(
            "retained",
            "session-exit-failed",
            message="a registered session is still present after the exit attempt; the terminal is retained",
            phase="post-exit",
            remaining_sessions=after["busy"],
        )
    occupancy_after = tab_occupancy_facts(herdr, tabs)
    if not occupancy_after["confirmed"]:
        return remember(
            "retained",
            "tab-occupancy-unverified",
            message="the registered tab contents cannot be confirmed after the exit attempt; the terminal is retained",
            phase="post-exit",
            error=occupancy_after["error"],
        )
    if occupancy_after["foreign"]:
        return remember(
            "retained",
            "foreign-occupancy",
            message="a registered tab gained an unregistered pane or another agent during the exit; it is not closed",
            phase="post-exit",
            foreign=occupancy_after["foreign"],
        )
    listed_after = registered_busy(occupancy_after, after["names"])
    if listed_after:
        return remember(
            "retained",
            "session-not-settled",
            message="Herdr lists a registered agent as active after the exit attempt; the terminal is retained",
            phase="post-exit",
            sessions=listed_after,
        )

    def tab_conditions(tab: str) -> dict[str, Any] | None:
        """Re-verify one tab immediately before its close; None means go ahead.

        Herdr exposes no conditional close that checks ownership in the same
        operation, so closing earlier tabs leaves any snapshot stale. Each
        tab is checked on the state that exists at its own close time.
        """
        fresh = registered_agent_facts(herdr, state)
        if fresh["unknown"]:
            return {
                "reason": "session-state-unknown",
                "message": "a registered session state cannot be confirmed before closing a tab; the tab is retained",
                "phase": "pre-close",
                "tab": tab,
                "sessions": fresh["unknown"],
            }
        if fresh["busy"]:
            return {
                "reason": "session-exit-failed",
                "message": "a registered session is active before closing a tab; the tab is retained",
                "phase": "pre-close",
                "tab": tab,
                "remaining_sessions": fresh["busy"],
            }
        fresh_occupancy = tab_occupancy_facts(herdr, [entry for entry in tabs if entry["tab"] == tab])
        if not fresh_occupancy["confirmed"]:
            return {
                "reason": "tab-occupancy-unverified",
                "message": "the tab contents cannot be confirmed before its close; the tab is retained",
                "phase": "pre-close",
                "tab": tab,
                "error": fresh_occupancy["error"],
            }
        if fresh_occupancy["foreign"]:
            return {
                "reason": "foreign-occupancy",
                "message": "a tab gained an unregistered pane or another agent before its close; it is not closed",
                "phase": "pre-close",
                "tab": tab,
                "foreign": fresh_occupancy["foreign"],
            }
        fresh_listed = registered_busy(fresh_occupancy, fresh["names"])
        if fresh_listed:
            return {
                "reason": "session-not-settled",
                "message": "Herdr lists a registered agent as active before closing a tab; the tab is retained",
                "phase": "pre-close",
                "tab": tab,
                "sessions": fresh_listed,
            }
        return None

    closed: list[str] = []
    for entry in tabs:
        blocked = tab_conditions(entry["tab"])
        if blocked:
            reason = blocked.pop("reason")
            return remember("retained", reason, closed_tabs=closed, **blocked)
        rc, code = herdr.tab_close(entry["tab"])
        if rc == 0 or code == "tab_not_found":
            closed.append(entry["tab"])
            continue
        return remember(
            "retained",
            "tab-close-failed",
            message="a registered tab could not be closed; the delivered result and worktree are retained",
            closed_tabs=closed,
            failed_tabs=[{"tab": entry["tab"], "error": code or "tab-close-failed"}],
        )
    return remember(
        "closed",
        "completed-session-released",
        message="completed session tabs were closed; branch, worktree and evidence are retained",
        closed_tabs=closed,
    )


def worker_paths(store: Store, state: dict[str, Any]) -> dict[str, str]:
    """Resolve management paths, tolerating legacy states with a partial `paths` map."""
    paths = state.get("paths") if isinstance(state.get("paths"), dict) else {}
    worker_dir = store.worker_dir(state["worker_id"])
    return {
        "management": str(paths.get("management") or worker_dir),
        "result": str(paths.get("result") or (worker_dir / "result.json")),
        "contract": str(paths.get("contract") or (worker_dir / "contract.md")),
        "supervisor_log": str(paths.get("supervisor_log") or store.log_path(state["worker_id"])),
    }


def cleanup_state_facts(
    store: Store, state: dict[str, Any], *, live: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Read-only retention facts: resources, recorded decision and why cleanup is not done."""
    worker_id = state["worker_id"]
    repo = Path(state["repo"]["root"])
    worktree = Path(state["worktree"])
    branch = state["branch"]
    paths = worker_paths(store, state)
    record = store.load_cleanup(worker_id)
    exists = worktree.exists()
    entries = uncommitted_entries(worktree) if exists else []
    listing: dict[str, dict[str, str]] = {}
    listing_error: str | None = None
    try:
        listing = worktree_listing(repo)
    except ImplementerError as exc:
        listing_error = str(exc)
    resolved = str(worktree.resolve())
    entry = listing.get(resolved)
    main_checkout = Path(resolved) == Path(state["repo"]["root"]).resolve()
    owned = bool(entry) and entry.get("branch") == f"refs/heads/{branch}" and not main_checkout
    branch_exists = git(repo, "rev-parse", "--verify", f"refs/heads/{branch}", check=False).returncode == 0
    branch_head = None
    if branch_exists:
        head_proc = git(repo, "rev-parse", "--verify", f"{branch}^{{commit}}", check=False)
        if head_proc.returncode == 0:
            branch_head = head_proc.stdout.strip()
    if live is None:
        live = live_session_facts(Herdr(state.get("herdr", {}).get("workspace")), state)
    blockers: list[dict[str, Any]] = []
    if state["lifecycle"]["state"] not in TERMINAL_STATES:
        blockers.append(
            {
                "code": "worker-not-stopped",
                "message": f"lifecycle is {state['lifecycle']['state']!r}; stop the worker before cleanup",
            }
        )
    if not record.get("decision"):
        blockers.append({"code": "decision-missing", "message": "no explicit cleanup decision is recorded"})
    if pid_alive(state):
        blockers.append(
            {"code": "supervisor-running", "message": "the worker supervisor is still running; stop the worker first"}
        )
    for fact in live:
        if fact["agent_live"] and fact["agent_status"] == "working":
            blockers.append(
                {
                    "code": "active-session",
                    "message": f"session {fact['index']} is still working; stop the worker first",
                    "agent": fact["agent"],
                }
            )
    if exists:
        if main_checkout:
            blockers.append(
                {"code": "worktree-is-main-checkout", "message": "the recorded worktree is the repository checkout itself"}
            )
        elif listing_error is not None:
            blockers.append({"code": "worktree-unowned", "message": listing_error})
        elif not owned:
            blockers.append(
                {
                    "code": "worktree-unowned",
                    "message": "the recorded path is not the registered worktree of this repository and branch",
                }
            )
    if entries:
        blockers.append(
            {
                "code": "uncommitted-content",
                "message": (
                    f"{len(entries)} uncommitted entr{'y' if len(entries) == 1 else 'ies'}; "
                    "pass --archive-uncommitted or --discard-uncommitted"
                ),
                "entries": entries[:50],
            }
        )
    return {
        "decision": record.get("decision"),
        "cleaned": bool(record.get("worktree_removed_at")) and not exists,
        "worktree_removed_at": record.get("worktree_removed_at"),
        "branch_deleted_at": record.get("branch_deleted_at"),
        "last_attempt": record.get("last_attempt"),
        "blockers": blockers,
        "resources": {
            "management_dir": paths["management"],
            "result": paths["result"],
            "contract": paths["contract"],
            "handoffs": sorted(str(path) for path in (store.worker_dir(worker_id) / "handoffs").glob("*.md")),
            "worktree": {
                "path": state["worktree"],
                "exists": exists,
                "owned": owned,
                "dirty": bool(entries),
                "uncommitted_entries": entries[:50],
            },
            "branch": {"name": branch, "exists": branch_exists, "head": branch_head},
            "sessions": live,
        },
    }


def delete_worker_branch(repo: Path, state: dict[str, Any], *, force: bool) -> dict[str, Any]:
    """Delete the worker branch only on an explicit decision and never in place."""
    name = state["branch"]
    if git(repo, "rev-parse", "--verify", f"refs/heads/{name}", check=False).returncode != 0:
        return {"name": name, "existed": False, "deleted": False, "retained_reason": "branch does not exist"}
    try:
        listing = worktree_listing(repo)
    except ImplementerError as exc:
        return {"name": name, "existed": True, "deleted": False, "retained_reason": str(exc)}
    for path, entry in listing.items():
        if entry.get("branch") == f"refs/heads/{name}":
            return {
                "name": name,
                "existed": True,
                "deleted": False,
                "retained_reason": f"branch is still checked out at {path}",
            }
    proc = git(repo, "branch", "-D" if force else "-d", name, check=False)
    if proc.returncode == 0:
        return {"name": name, "existed": True, "deleted": True, "forced": force}
    return {
        "name": name,
        "existed": True,
        "deleted": False,
        "retained_reason": proc.stderr.strip() or proc.stdout.strip() or "branch deletion was refused",
    }


def save_cleanup_record(
    store: Store,
    state: dict[str, Any],
    record: dict[str, Any],
    decision: dict[str, Any],
    *,
    outcome: str,
    removed: dict[str, Any],
    blockers: list[dict[str, Any]],
    archive: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = utc_now()
    record["version"] = 1
    record["worker_id"] = state["worker_id"]
    record["decision"] = decision
    record["last_attempt"] = {"at": now, "outcome": outcome, "removed": removed, "blockers": blockers}
    if archive is not None:
        record["last_archive"] = archive
    if removed.get("worktree"):
        record["worktree_removed_at"] = now
    if removed.get("branch"):
        record["branch_deleted_at"] = now
    closed = [tab for tab in (record.get("closed_tabs") or []) if isinstance(tab, str)]
    for tab in removed.get("tabs") or []:
        if tab not in closed:
            closed.append(tab)
    if closed:
        record["closed_tabs"] = closed
    if outcome == "removed" and not blockers:
        record["completed_at"] = record.get("completed_at") or now
    store.save_cleanup(state["worker_id"], record)
    return record


def handoff_checkpoint(store: Store, worker_id: str) -> None:
    control = store.load_control(worker_id)
    if control.get("stop_requested_at"):
        raise HandoffAbort()


def start_replacement_session(
    store: Store,
    state: dict[str, Any],
    herdr: Herdr,
    adapter: RuntimeAdapter,
    source_session: dict[str, Any],
    doc_path: Path,
) -> dict[str, Any]:
    """Start and confirm the continuation session before the old one is ended."""
    worker_id = state["worker_id"]
    runtime = state["runtime"]
    index = int(source_session["index"]) + 1
    agent = session_agent_name(worker_id, index)
    if herdr.agent_exists(agent):
        raise HandoffError("handoff-replacement-failed", f"a live Herdr agent already uses the name {agent!r}")
    worktree = Path(state["worktree"])
    tab = ""
    pane = ""
    try:
        tab, pane = herdr.tab_create(worktree, agent, runtime.get("env") or {})
        herdr.agent_start(
            agent,
            runtime["kind"],
            pane,
            adapter.start_args(runtime["provider"], runtime["model"], runtime["thinking"]),
        )
        if not herdr.wait_interactive(agent):
            raise ImplementerError(f"replacement agent {agent} never reached an interactive state")
        attempts = deliver_prompt_confirmed(herdr, agent, continuation_prompt(state, doc_path))
        session = new_session_record(index, agent, tab, pane, runtime)
        session["context_ref"] = herdr.wait_agent_session(agent, attempts=20, delay=0.5)
        session["prompt"] = {"continuation_attempts": attempts, "confirmed_at": utc_now()}
        return session
    except ImplementerError as exc:
        if tab:
            herdr.tab_close(tab)
        raise HandoffError(
            "handoff-replacement-failed", f"could not start and confirm the replacement session: {exc}"
        ) from exc


def wait_for_handoff_document(
    store: Store,
    state: dict[str, Any],
    herdr: Herdr,
    adapter: RuntimeAdapter,
    session: dict[str, Any],
    doc_path: Path,
    record: dict[str, Any],
) -> Path:
    """Wait for a structurally usable handoff document, allowing one correction."""
    worker_id = state["worker_id"]
    wait_seconds = env_float("HI_HANDOFF_WAIT_SECONDS", DEFAULT_HANDOFF_WAIT_SECONDS)
    correction_grace = env_float("HI_HANDOFF_CORRECTION_SECONDS", 30.0)
    deadline = time.monotonic() + wait_seconds
    corrected_at: float | None = None
    settled_since: float | None = None
    last_problem = "no handoff document at the requested path"
    while True:
        handoff_checkpoint(store, worker_id)
        doc, source = locate_handoff_document(state, doc_path, record)
        if doc is not None:
            ok, missing, problem = validate_handoff_document(doc)
            if ok:
                record["document_source"] = source
                record["document_validated_at"] = utc_now()
                store.save_state(worker_id, state)
                supervisor_log(f"handoff document validated for session {session['index']}: {doc}")
                return doc
            last_problem = problem or f"missing required sections: {', '.join(missing)}"
        rc, status = herdr.agent_status(session["agent"])
        if rc != 0:
            raise HandoffError(
                "handoff-session-exited",
                "the session ended before a readable handoff document was produced",
            )
        if status in SETTLED_STATES:
            if corrected_at is None:
                corrected_at = time.monotonic()
                record["phase"] = "correcting-document"
                record["correction_sent_at"] = utc_now()
                store.save_state(worker_id, state)
                problem = last_problem if doc is not None else "no handoff document was found at the requested path"
                correction = adapter.handoff_correction_prompt(
                    state["ticket"]["id"], state["ticket"]["title"], doc_path, problem
                )
                deliver_prompt_confirmed(herdr, session["agent"], correction)
                continue
            if settled_since is None:
                settled_since = time.monotonic()
            if time.monotonic() - settled_since >= correction_grace:
                raise HandoffError(
                    "handoff-document-invalid",
                    f"the handoff document is still not usable after one correction ({last_problem})",
                )
        else:
            settled_since = None
        if time.monotonic() >= deadline:
            if doc is None:
                raise HandoffError(
                    "handoff-document-timeout",
                    "no handoff document appeared within the handoff wait window",
                )
            raise HandoffError(
                "handoff-document-invalid", f"the handoff document is not usable ({last_problem})"
            )
        time.sleep(1.0)


def attempt_handoff(
    store: Store,
    state: dict[str, Any],
    herdr: Herdr,
    adapter: RuntimeAdapter,
    *,
    reason: str,
    trigger: str,
    sample: Any,
) -> str:
    """Run the standard session handoff; never raises, always retains the scene."""
    worker_id = state["worker_id"]
    session = current_session(state)
    if session is None:
        raise HandoffError("handoff-failed", "worker has no current session record")
    doc_path = store.handoff_path(worker_id, int(session["index"]))
    record: dict[str, Any] = {
        "index": session["index"],
        "agent": session["agent"],
        "trigger": trigger,
        "reason": reason,
        "sample": sample,
        "requested_at": utc_now(),
        "status": "running",
        "phase": "settling",
        "doc": str(doc_path),
        "document_source": None,
        "request_attempts": None,
        "correction_sent_at": None,
        "completed_at": None,
        "error": None,
    }
    session["handoff"] = record
    session["status"] = "handing-off"
    state["lifecycle"].update(state="handing-off", reason=f"handoff: {trigger}")
    store.save_state(worker_id, state)
    supervisor_log(f"handoff ({trigger}) requested for session {session['index']} ({session['agent']}): {reason}")
    try:
        handoff_checkpoint(store, worker_id)
        if not settle_session_for_control(herdr, session["agent"], adapter):
            raise HandoffError("handoff-settle-failed", "the current session could not be paused for the handoff")
        handoff_checkpoint(store, worker_id)
        record["phase"] = "requesting-document"
        store.save_state(worker_id, state)
        handoff_document: Path | None = None
        if doc_path.is_file() and validate_handoff_document(doc_path)[0]:
            # A retry after a failed replacement may already have a usable document.
            handoff_document = doc_path
            record["document_source"] = "requested-path"
            record["document_validated_at"] = utc_now()
            record["phase"] = "awaiting-document"
            store.save_state(worker_id, state)
            supervisor_log(f"handoff document already usable for session {session['index']}: {doc_path}")
        else:
            prompt = adapter.handoff_request_prompt(
                state["worker_id"], state["ticket"]["id"], state["ticket"]["title"], doc_path, reason, sample
            )
            record["request_attempts"] = deliver_prompt_confirmed(herdr, session["agent"], prompt)
            record["phase"] = "awaiting-document"
            store.save_state(worker_id, state)
            handoff_document = wait_for_handoff_document(store, state, herdr, adapter, session, doc_path, record)
        handoff_checkpoint(store, worker_id)
        if not settle_session_for_control(herdr, session["agent"], adapter):
            raise HandoffError(
                "handoff-settle-failed",
                "the session did not stop business work after writing the handoff document",
            )
        record["phase"] = "starting-replacement"
        store.save_state(worker_id, state)
        replacement = start_replacement_session(store, state, herdr, adapter, session, handoff_document)
        record["phase"] = "ending-old-session"
        record["status"] = "completed"
        record["completed_at"] = utc_now()
        session["status"] = "replaced"
        session["ended_at"] = record["completed_at"]
        session["end_state"] = "replaced"
        replacement["handoff_from"] = session["index"]
        state["sessions"].append(replacement)
        state["session_index"] = replacement["index"]
        state["herdr"].update(agent=replacement["agent"], tab=replacement["tab"], pane=replacement["pane"])
        state["handoff"]["history"].append(
            {
                "from": session["index"],
                "to": replacement["index"],
                "trigger": trigger,
                "reason": reason,
                "doc": str(handoff_document),
                "at": record["completed_at"],
                "sample": sample,
            }
        )
        state["handoff"]["auto_suppressed"] = False
        state["lifecycle"].update(
            state="running", reason=f"session {session['index']} handed off to {replacement['index']}"
        )
        store.save_state(worker_id, state)
        # Completion is durable. Release only the old tab: the replacement
        # may already be writing to their shared worktree.
        scope = {"sessions": [session], "release": session.get("release")}
        release = release_terminal_tabs(herdr, adapter, scope)
        session["release"] = scope["release"]
        record["phase"] = "completed"
        store.save_state(worker_id, state)
        supervisor_log(
            f"handoff tab release {release['reason']}: {'closed' if release['closed'] else 'retained'}"
        )
        supervisor_log(
            f"handoff complete: session {session['index']} -> {replacement['index']} agent {replacement['agent']}"
        )
        return "completed"
    except HandoffAbort:
        record.update(status="aborted", phase="aborted-stop", error="stop requested during handoff")
        session["status"] = "active"
        state["lifecycle"].update(state="running", reason="handoff aborted by stop request")
        store.save_state(worker_id, state)
        supervisor_log(f"handoff aborted by stop request for session {session['index']}")
        return "aborted-stop"
    except HandoffError as exc:
        return record_handoff_failure(store, state, session, record, exc.code, str(exc), trigger, doc_path)
    except ImplementerError as exc:
        return record_handoff_failure(store, state, session, record, "handoff-failed", str(exc), trigger, doc_path)


def record_handoff_failure(
    store: Store,
    state: dict[str, Any],
    session: dict[str, Any],
    record: dict[str, Any],
    code: str,
    message: str,
    trigger: str,
    doc_path: Path,
) -> str:
    """Record a failed handoff as a pending exception and keep the scene."""
    record.update(status="failed", phase=record.get("phase"), error=f"{code}: {message}")
    if session.get("status") == "handing-off":
        session["status"] = "active"
    append_item(
        state,
        "exception",
        code,
        message,
        {"session": session["index"], "agent": session["agent"], "trigger": trigger, "doc": str(doc_path)},
    )
    state["handoff"]["auto_suppressed"] = True
    state["lifecycle"].update(state="handoff-failed", reason=code)
    store.save_state(state["worker_id"], state)
    supervisor_log(f"handoff failed ({code}) for session {session['index']}: {message}")
    return "failed"


def process_alive(pid: Any, host: Any) -> bool:
    if not isinstance(pid, int) or isinstance(pid, bool) or host != os.uname().nodename:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def pid_alive(state: dict[str, Any]) -> bool:
    supervisor = state.get("supervisor", {})
    return process_alive(supervisor.get("pid"), supervisor.get("host"))


def supervisor_facts(state: dict[str, Any], control: dict[str, Any]) -> dict[str, Any]:
    supervisor = state.get("supervisor", {})
    alive = pid_alive(state)
    heartbeat = supervisor.get("heartbeat_at")
    stale = False
    if alive and heartbeat:
        stale = time.time() - parse_iso(heartbeat) > max(30.0, env_float("HI_POLL_SECONDS", 5.0) * 6)
    warnings: list[str] = []
    lifecycle = state.get("lifecycle", {}).get("state")
    inactive = lifecycle in TERMINAL_STATES or bool(control.get("stop_requested_at"))
    if not inactive:
        if supervisor.get("state") == "running" and not alive:
            warnings.append("supervisor-missing")
        elif stale:
            warnings.append("supervisor-stale")
        if start_stall_applies(state, control):
            warnings.append("start-stalled")
    return {
        "pid": supervisor.get("pid"),
        "host": supervisor.get("host"),
        "state": supervisor.get("state"),
        "alive": alive,
        "heartbeat_at": heartbeat,
        "exit_reason": supervisor.get("exit_reason"),
        "warnings": warnings,
    }


def item_id_for(worker_id: str, item: dict[str, Any]) -> str:
    """Global identity of one durable item: `<worker-id>/<item-id>`."""
    return f"{worker_id}/{item['id']}"


def split_item_id(value: str) -> tuple[str, str]:
    worker_id, _, item_id = value.partition("/")
    if not worker_id or not item_id or not WORKER_ID_RE.fullmatch(worker_id):
        raise ImplementerError(f"invalid item id {value!r}; expected <worker-id>/<item-id>")
    return worker_id, item_id


def item_view(state: dict[str, Any], item: dict[str, Any], ack: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "item_id": item_id_for(state["worker_id"], item),
        "worker_id": state["worker_id"],
        "run_id": state.get("run_id"),
        "ticket": state["ticket"],
        "kind": item["kind"],
        "code": item["code"],
        "message": item["message"],
        "details": item.get("details"),
        "created_at": item["created_at"],
        "acked": ack is not None,
        "acked_at": (ack or {}).get("acked_at"),
        "lifecycle": state["lifecycle"]["state"],
        "branch": state["branch"],
        "worktree": state["worktree"],
        "result_path": state["paths"]["result"],
        "session": state.get("session_index"),
    }


def supervisor_missing_applies(state: dict[str, Any], control: dict[str, Any]) -> bool:
    """A missing supervisor is a derived, currently actionable exception.

    It only counts while the worker is not terminal and no stop was already
    requested, so stopping the worker resolves the condition instead of
    leaving a permanently pending item behind.
    """
    if state["lifecycle"]["state"] in TERMINAL_STATES:
        return False
    if control.get("stop_requested_at"):
        return False
    return state["supervisor"].get("state") == "running" and not pid_alive(state)


def supervisor_missing_item(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": SUPERVISOR_MISSING_ITEM,
        "kind": "exception",
        "code": "supervisor-missing",
        "message": (
            "the worker supervisor is not running; the worker cannot progress, hand off or "
            "report a result by itself"
        ),
        "details": {"pid": state["supervisor"].get("pid"), "host": state["supervisor"].get("host")},
        "created_at": state["supervisor"].get("started_at") or state["created_at"],
    }


def start_stall_applies(state: dict[str, Any], control: dict[str, Any]) -> bool:
    """A start that died before any supervisor existed silently holds a slot.

    The start process records its own pid at registration; if that process is
    gone while the lifecycle is still pre-supervisor, the worker cannot make
    progress and must be surfaced instead of looking healthy.
    """
    if state["lifecycle"]["state"] not in STARTED_STATES:
        return False
    if control.get("stop_requested_at"):
        return False
    if state["supervisor"].get("state") != "starting":
        return False
    starter = state.get("starter") or {}
    return not process_alive(starter.get("pid"), starter.get("host"))


def start_stall_item(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": START_STALLED_ITEM,
        "kind": "exception",
        "code": "start-stalled",
        "message": (
            "the start process ended before a supervisor existed; the worker occupies a slot "
            "without anyone able to progress or report it"
        ),
        "details": {"lifecycle": state["lifecycle"]["state"], "updated_at": state.get("updated_at")},
        "created_at": state.get("updated_at") or state["created_at"],
    }


def annotated_items(store: Store, state: dict[str, Any]) -> list[dict[str, Any]]:
    """Every durable item of one worker with its acknowledgement state."""
    acks = store.load_acks(state["worker_id"])
    return [
        item_view(state, item, acks.get(item_id_for(state["worker_id"], item))) for item in state["items"]
    ]


def worker_pending_items(store: Store, state: dict[str, Any]) -> list[dict[str, Any]]:
    """Unacknowledged durable items plus a derived supervision exception if any."""
    worker_id = state["worker_id"]
    acks = store.load_acks(worker_id)
    views = [
        item_view(state, item, acks.get(item_id_for(worker_id, item)))
        for item in state["items"]
        if item_id_for(worker_id, item) not in acks
    ]
    control = store.load_control(worker_id)
    if supervisor_missing_applies(state, control):
        item = supervisor_missing_item(state)
        item_id = item_id_for(worker_id, item)
        if item_id not in acks:
            views.append(item_view(state, item, acks.get(item_id)))
    elif start_stall_applies(state, control):
        item = start_stall_item(state)
        item_id = item_id_for(worker_id, item)
        if item_id not in acks:
            views.append(item_view(state, item, acks.get(item_id)))
    views.sort(key=lambda view: (view["created_at"], view["item_id"]))
    return views


def run_workers(store: Store, run_id: str) -> list[dict[str, Any]]:
    states = []
    for worker_id in store.worker_ids():
        state = store.load_state(worker_id)
        if state is not None and state.get("run_id") == run_id:
            states.append(state)
    return states


def is_active_worker(state: dict[str, Any]) -> bool:
    """A worker occupies one slot until its lifecycle reaches a terminal state."""
    return state["lifecycle"]["state"] not in TERMINAL_STATES


def active_workers(store: Store, run_id: str) -> list[dict[str, Any]]:
    return [state for state in run_workers(store, run_id) if is_active_worker(state)]


def run_pending_items(store: Store, run_id: str) -> list[dict[str, Any]]:
    views: list[dict[str, Any]] = []
    for state in run_workers(store, run_id):
        views.extend(worker_pending_items(store, state))
    views.sort(key=lambda view: (view["created_at"], view["item_id"]))
    return views


def run_summary(store: Store, run: dict[str, Any]) -> dict[str, Any]:
    run_id = run["run_id"]
    workers = run_workers(store, run_id)
    active = [state for state in workers if is_active_worker(state)]
    pending = run_pending_items(store, run_id)
    return {
        "run_id": run_id,
        "repo_root": run.get("repo_root"),
        "runtime": run.get("runtime"),
        "max_workers": run.get("max_workers"),
        "created_at": run.get("created_at"),
        "active_count": len(active),
        "active_worker_ids": [state["worker_id"] for state in active],
        "worker_count": len(workers),
        "pending_item_count": len(pending),
        "workers": [
            {
                "worker_id": state["worker_id"],
                "ticket": state["ticket"],
                "lifecycle": state["lifecycle"]["state"],
                "active": is_active_worker(state),
                "session": state.get("session_index"),
                "branch": state["branch"],
                "worktree": state["worktree"],
                "pending_items": len(worker_pending_items(store, state)),
            }
            for state in workers
        ],
        "pending_items": pending,
    }


def validate_result(payload: Any, state: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ImplementerError("result must be a JSON object")
    if payload.get("ticket_id") != state["ticket"]["id"]:
        raise ImplementerError(f"result ticket_id {payload.get('ticket_id')!r} does not match {state['ticket']['id']!r}")
    if payload.get("worker_id") != state["worker_id"]:
        raise ImplementerError(f"result worker_id {payload.get('worker_id')!r} does not match {state['worker_id']!r}")
    status = payload.get("status")
    if status not in RESULT_STATUSES:
        raise ImplementerError(f"result status must be one of {', '.join(RESULT_STATUSES)}; got {status!r}")
    deviations = payload.get("plan_deviations")
    if not isinstance(deviations, list):
        raise ImplementerError("result needs a plan_deviations list (empty means no deviations)")
    for entry in deviations:
        if not isinstance(entry, dict):
            raise ImplementerError("plan_deviations entries must be objects")
        for field in ("planned", "actual", "reason", "impact"):
            if not isinstance(entry.get(field), str) or not entry[field].strip():
                raise ImplementerError(f"plan_deviations entry needs a non-empty {field}")
        if not isinstance(entry.get("needs_decision"), bool):
            raise ImplementerError("plan_deviations entry needs a boolean needs_decision")
        if status == "delivered" and entry["needs_decision"]:
            raise ImplementerError("delivered declaration cannot contain a deviation needing a decision")
    if status in ("failed", "needs-decision"):
        reason = payload.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ImplementerError(f"{status} declaration needs a non-empty reason")
        return payload

    summary = payload.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise ImplementerError("delivered declaration needs a non-empty summary")
    acceptance = payload.get("acceptance")
    if not isinstance(acceptance, list) or not acceptance:
        raise ImplementerError("delivered declaration needs a non-empty acceptance list")
    for entry in acceptance:
        if not isinstance(entry, dict):
            raise ImplementerError("acceptance entries must be objects")
        if not isinstance(entry.get("criterion"), str) or not entry["criterion"].strip():
            raise ImplementerError("acceptance entry needs a non-empty criterion")
        if not isinstance(entry.get("met"), bool):
            raise ImplementerError("acceptance entry needs a boolean met field")
        if not isinstance(entry.get("evidence"), str) or not entry["evidence"].strip():
            raise ImplementerError("acceptance entry needs non-empty evidence")
    verification = payload.get("verification")
    if not isinstance(verification, list) or not verification:
        raise ImplementerError("delivered declaration needs a non-empty verification list")
    for entry in verification:
        if not isinstance(entry, dict):
            raise ImplementerError("verification entries must be objects")
        if not isinstance(entry.get("command"), str) or not entry["command"].strip():
            raise ImplementerError("verification entry needs a non-empty command")
        if not isinstance(entry.get("exit_code"), int) or isinstance(entry.get("exit_code"), bool):
            raise ImplementerError("verification entry needs an integer exit_code")
        if not isinstance(entry.get("summary"), str) or not entry["summary"].strip():
            raise ImplementerError("verification entry needs a non-empty summary")

    head = payload.get("head")
    artifacts = payload.get("artifacts", [])
    if head is not None and (not isinstance(head, str) or not SHA_RE.fullmatch(head)):
        raise ImplementerError("head must be null or a full commit SHA")
    if not isinstance(artifacts, list) or any(not isinstance(item, str) or not item.strip() for item in artifacts):
        raise ImplementerError("artifacts must be a list of non-empty paths")

    repo = Path(state["repo"]["root"])
    branch = state["branch"]
    base = state["repo"]["base"]
    branch_head = git(repo, "rev-parse", "--verify", f"{branch}^{{commit}}", check=False)
    if branch_head.returncode != 0:
        raise ImplementerError(f"delivered branch {branch!r} cannot be resolved")
    resolved_head = branch_head.stdout.strip()
    ahead = int(git(repo, "rev-list", "--count", f"{base}..{branch}").stdout.strip() or "0")
    if ahead > 0 and head is None:
        raise ImplementerError("branch has commits after the base but head is null")
    if head is not None and head != resolved_head:
        raise ImplementerError(f"declared head {head} does not match branch HEAD {resolved_head}")
    if ahead == 0 and not artifacts:
        raise ImplementerError("delivered declaration has no code commit and no artifact")

    worktree = Path(state["worktree"]).resolve()
    management = Path(state["paths"]["management"]).resolve()
    for item in artifacts:
        candidate = Path(item)
        resolved = candidate.resolve() if candidate.is_absolute() else (worktree / candidate).resolve()
        if not contained(resolved, worktree) and not contained(resolved, management):
            raise ImplementerError(f"artifact path escapes the worktree and management dir: {item}")
        if not resolved.exists():
            raise ImplementerError(f"artifact path does not exist: {item}")
    return payload


def record_result(store: Store, state: dict[str, Any]) -> str:
    if state.get("result") is not None and state["lifecycle"]["state"] in {"delivered", "failed", "needs-decision"}:
        return "recorded"
    if state["lifecycle"]["state"] == "protocol-failure":
        return "protocol-failure"
    result_path = Path(state["paths"]["result"])
    if not result_path.is_file():
        return "missing"
    try:
        raw = result_path.read_text(encoding="utf-8")
        if len(raw.encode("utf-8")) > MAX_RESULT_BYTES:
            raise ValueError("result file exceeds the size limit")
        payload = json.loads(raw)
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        state["recovery"]["parse_failures"] = state["recovery"].get("parse_failures", 0) + 1
        if state["recovery"]["parse_failures"] >= PARSE_ATTEMPTS:
            append_item(state, "exception", "protocol-failure", "result file exists but cannot be read as JSON")
            state["lifecycle"]["state"] = "protocol-failure"
            mark_session_ended(state, "protocol-failure")
            return "protocol-failure"
        return "transient"
    try:
        normalized = validate_result(payload, state)
    except ImplementerError as exc:
        append_item(state, "exception", "protocol-failure", f"result protocol check failed: {exc}")
        state["lifecycle"]["state"] = "protocol-failure"
        mark_session_ended(state, "protocol-failure")
        return "protocol-failure"
    state["recovery"]["parse_failures"] = 0
    state["result"] = normalized
    if normalized["status"] == "delivered":
        append_item(
            state,
            "delivery",
            "delivered",
            normalized.get("summary", ""),
            {"head": normalized.get("head"), "artifacts": normalized.get("artifacts", [])},
        )
        state["lifecycle"]["state"] = "delivered"
    else:
        append_item(
            state,
            "exception",
            normalized["status"],
            normalized.get("reason", ""),
            {"remaining": normalized.get("remaining")},
        )
        state["lifecycle"]["state"] = normalized["status"]
    mark_session_ended(state, normalized["status"])
    return "recorded"


def finalize(state: dict[str, Any], lifecycle_state: str, exit_reason: str) -> None:
    state["lifecycle"]["state"] = lifecycle_state
    state["lifecycle"]["reason"] = exit_reason
    supervisor = state["supervisor"]
    supervisor["state"] = "exited"
    supervisor["exit_reason"] = exit_reason
    supervisor["heartbeat_at"] = utc_now()
    mark_session_ended(state, lifecycle_state)


def supervisor_log(message: str) -> None:
    print(f"{utc_now()} {message}", flush=True)


def cmd_supervise(args: argparse.Namespace) -> int:
    store = Store(Path(args.management_root))
    worker_id = args.worker
    state = store.load_state(worker_id)
    if state is None:
        supervisor_log(f"no registered worker {worker_id}")
        return 1
    ensure_session_state(state)
    state["supervisor"].update(
        pid=os.getpid(),
        host=os.uname().nodename,
        state="running",
        started_at=utc_now(),
        heartbeat_at=utc_now(),
        exit_reason=None,
    )
    store.save_state(worker_id, state)
    supervisor_log("supervisor started")
    herdr = Herdr(state.get("herdr", {}).get("workspace"))
    adapter = get_adapter(state["runtime"]["kind"])
    poll = env_float("HI_POLL_SECONDS", 5.0)
    settle_grace = env_float("HI_SETTLE_GRACE_SECONDS", 30.0)
    report_wait = env_float("HI_REPORT_WAIT_SECONDS", 120.0)
    sample_interval = env_float("HI_CONTEXT_POLL_SECONDS", DEFAULT_CONTEXT_POLL_SECONDS)
    last_sample = 0.0

    while True:
        control = store.load_control(worker_id)
        if control.get("stop_requested_at"):
            outcome = record_result(store, state)
            stopped = stop_registered_sessions(herdr, adapter, state)
            if outcome not in {"recorded", "protocol-failure"}:
                if stopped["business_stopped"]:
                    finalize(state, "stopped", "stop-requested")
                    supervisor_log("stopped by request")
                else:
                    if not any(item.get("code") == "stop-incomplete" for item in state["items"]):
                        append_item(
                            state,
                            "exception",
                            "stop-incomplete",
                            "a registered session could not be confirmed stopped; the scene is retained",
                            {"sessions": stopped["sessions"]},
                        )
                    state["supervisor"].update(state="exited", exit_reason="stop-incomplete")
                    supervisor_log("stop requested but business execution could not be confirmed stopped")
            else:
                state["supervisor"]["exit_reason"] = "stop-requested"
                supervisor_log(f"stop requested after result: {outcome}")
            store.save_state(worker_id, state)
            return 0

        session = current_session(state)
        agent = current_agent(state)
        if session is None or not agent:
            append_item(state, "exception", "agent-exited", "the worker has no usable session record")
            finalize(state, "agent-exited", "no-session")
            store.save_state(worker_id, state)
            supervisor_log("worker has no usable session record")
            return 0

        request_id = control.get("handoff_request_id")
        if isinstance(request_id, str) and request_id and request_id != state["handoff"].get("handled_request_id"):
            attempt_handoff(
                store,
                state,
                herdr,
                adapter,
                reason=str(control.get("handoff_reason") or "requested by caller"),
                trigger="requested",
                sample=session.get("context"),
            )
            state["handoff"]["handled_request_id"] = request_id
            store.save_state(worker_id, state)
            last_sample = time.monotonic()
            continue

        rc, status = herdr.agent_status(agent)
        if rc != 0:
            outcome = record_result(store, state)
            if outcome in {"recorded", "protocol-failure"}:
                supervisor_log(f"agent gone; result {outcome}")
            else:
                append_item(
                    state,
                    "exception",
                    "agent-exited",
                    "agent disappeared before writing a valid result",
                    {"session": session["index"], "agent": agent},
                )
                finalize(state, "agent-exited", "agent-exited-without-result")
                supervisor_log("agent exited without a result")
            store.save_state(worker_id, state)
            if outcome in {"recorded", "protocol-failure"}:
                release = release_delivered_tabs(herdr, adapter, state)
                store.save_state(worker_id, state)
                if release.get("attempted"):
                    supervisor_log(
                        f"tab release {release['reason']}: {'closed' if release['closed'] else 'retained'}"
                    )
            return 0

        if status in SETTLED_STATES:
            outcome = record_result(store, state)
            if outcome in {"recorded", "protocol-failure"}:
                supervisor_log(f"terminal settled; result {outcome}")
                state["lifecycle"]["reason"] = "result-recorded" if outcome == "recorded" else "result-invalid"
                state["supervisor"]["exit_reason"] = state["lifecycle"]["reason"]
                state["supervisor"]["state"] = "exited"
                state["supervisor"]["heartbeat_at"] = utc_now()
                # The delivery record must be durable before its terminal is released.
                store.save_state(worker_id, state)
                release = release_delivered_tabs(herdr, adapter, state)
                store.save_state(worker_id, state)
                if release.get("attempted"):
                    supervisor_log(
                        f"tab release {release['reason']}: {'closed' if release['closed'] else 'retained'}"
                    )
                return 0
            if outcome == "transient":
                supervisor_log("result file present but not readable yet; will retry")
            elif state["lifecycle"]["state"] == "running":
                sent = state["recovery"].get("re_report_sent_at")
                if not sent:
                    since = state["recovery"].get("settled_since")
                    if not since:
                        state["recovery"]["settled_since"] = utc_now()
                        supervisor_log("settled without a result; starting grace period")
                    elif time.time() - parse_iso(since) >= settle_grace:
                        try:
                            deliver_prompt_confirmed(
                                herdr,
                                agent,
                                "Your terminal is idle but no valid result file exists at "
                                f"{state['paths']['result']}. If the ticket work is complete, write the result "
                                "file now exactly as the contract specifies. If it is not complete, finish the "
                                "ticket first. Do not start unrelated work.",
                            )
                        except ImplementerError as exc:
                            append_item(state, "exception", "protocol-failure", f"cannot send result re-report: {exc}")
                            finalize(state, "protocol-failure", "re-report-failed")
                            store.save_state(worker_id, state)
                            supervisor_log("failed to send result re-report")
                            return 0
                        state["recovery"]["re_report_sent_at"] = utc_now()
                        state["recovery"]["settled_since"] = None
                        supervisor_log("sent one result re-report")
                else:
                    if time.time() - parse_iso(sent) >= report_wait:
                        append_item(
                            state,
                            "exception",
                            "missing-result",
                            "terminal settled without a valid result after one re-report",
                        )
                        finalize(state, "protocol-failure", "missing-result")
                        store.save_state(worker_id, state)
                        supervisor_log("missing result after one re-report")
                        return 0
            state["recovery"]["settled_since"] = state["recovery"].get("settled_since") or utc_now()
        elif status == "blocked":
            state["recovery"]["settled_since"] = None
            blocked_item = any(
                item.get("code") == "blocked" and (item.get("details") or {}).get("session") == session["index"]
                for item in state["items"]
            )
            if not blocked_item:
                append_item(
                    state,
                    "exception",
                    "blocked",
                    "worker is blocked and may need a decision",
                    {"session": session["index"], "agent": agent},
                )
                supervisor_log("worker blocked")
        else:
            state["recovery"]["settled_since"] = None

        if state["lifecycle"]["state"] == "running" and not state["handoff"].get("auto_suppressed"):
            now = time.monotonic()
            if now - last_sample >= sample_interval:
                last_sample = now
                sample = sample_current_session(state, herdr, agent, status)
                store.save_state(worker_id, state)
                if sample_is_current(sample, state):
                    # A declared delivery stops automatic handoff; observation
                    # itself continues so the recorded facts stay complete.
                    trigger = None if result_declared(state) else handoff_trigger_reason(sample, state["handoff"])
                    if trigger:
                        supervisor_log(f"context threshold ({trigger}) reached for session {session['index']}")
                        attempt_handoff(
                            store,
                            state,
                            herdr,
                            adapter,
                            reason=f"context threshold reached ({trigger})",
                            trigger=trigger,
                            sample=sample,
                        )
                        last_sample = time.monotonic()
                        store.save_state(worker_id, state)
                        continue

        state["supervisor"]["heartbeat_at"] = utc_now()
        store.save_state(worker_id, state)
        time.sleep(poll)


def deliver_prompt(herdr: Herdr, agent: str, text: str) -> None:
    last_error = ""
    for attempt in range(PROMPT_ATTEMPTS):
        try:
            proc = herdr.agent_prompt(agent, text)
        except ImplementerError as exc:
            last_error = str(exc)
            rc, status = herdr.agent_status(agent)
            if rc == 0 and status == "working":
                return
            if attempt + 1 < PROMPT_ATTEMPTS and rc == 0 and status in SETTLED_STATES:
                time.sleep(1.0)
                continue
            raise ImplementerError(f"prompt delivery failed: {last_error}")
        if proc.returncode == 0:
            return
        last_error = proc.stderr.strip() or proc.stdout.strip()
        rc, status = herdr.agent_status(agent)
        if rc == 0 and status == "working":
            return
        if rc == 0 and status == "blocked":
            raise ImplementerError("agent is blocked; submission was rejected")
        if attempt + 1 < PROMPT_ATTEMPTS and rc == 0 and status in SETTLED_STATES:
            time.sleep(1.0)
            continue
        raise ImplementerError(f"prompt delivery failed: {last_error}")
    raise ImplementerError(f"prompt delivery failed: {last_error}")


def notify(message: str) -> None:
    print(f"{utc_now()} {message}", file=sys.stderr, flush=True)


def wait_agent_started(herdr: "Herdr", agent: str, timeout: float) -> tuple[bool, str | None]:
    """Wait for the agent to leave a settled state after a prompt delivery.

    Returns (agent_present, last_status). A settled status after the timeout
    means the terminal never accepted the delivered prompt.
    """
    deadline = time.monotonic() + timeout
    while True:
        rc, status = herdr.agent_status(agent)
        if rc != 0:
            return False, None
        if status not in SETTLED_STATES:
            return True, status
        if time.monotonic() >= deadline:
            return True, status
        time.sleep(0.25)


def deliver_prompt_confirmed(herdr: "Herdr", agent: str, text: str) -> int:
    """Deliver one prompt and confirm the runtime actually started working.

    TUI runtimes can report interactive readiness before their input editor
    accepts keys, so a successful `agent prompt` alone does not prove the
    prompt was submitted. When the agent stays settled for the confirmation
    window, the delivery is repeated a bounded number of times.
    """
    attempts = max(1, int(env_float("HI_PROMPT_ATTEMPTS", PROMPT_CONFIRM_ATTEMPTS)))
    window = env_float("HI_PROMPT_CONFIRM_SECONDS", DEFAULT_PROMPT_CONFIRM_TIMEOUT)
    for attempt in range(1, attempts + 1):
        deliver_prompt(herdr, agent, text)
        present, status = wait_agent_started(herdr, agent, window)
        if not present:
            raise ImplementerError(f"agent {agent} disappeared after prompt delivery")
        if status not in SETTLED_STATES:
            return attempt
        notify(f"prompt attempt {attempt} to {agent} was not picked up (status {status}); retrying")
    raise ImplementerError(
        f"prompt was delivered to {agent} {attempts} times but the runtime never left the settled state"
    )


def spawn_supervisor(store: Store, worker_id: str, worktree: Path) -> int:
    log = store.log_path(worker_id).open("ab")
    try:
        proc = subprocess.Popen(
            [
                sys.executable,
                str(IMPLEMENTER_PATH),
                "_supervise",
                "--management-root",
                str(store.root),
                "--worker",
                worker_id,
            ],
            cwd=str(worktree),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
            close_fds=True,
        )
    finally:
        log.close()
    return proc.pid


def resolve_store(args: argparse.Namespace) -> Store:
    if getattr(args, "management_root", None):
        return Store(Path(args.management_root))
    _, common = repo_context(Path(getattr(args, "repo", None) or "."))
    return Store.resolve(common)


def print_json(value: Any, *, stream: Any = sys.stdout) -> None:
    json.dump(value, stream, indent=2, ensure_ascii=False, sort_keys=True)
    stream.write("\n")


def worker_facts(store: Store, worker_id: str) -> dict[str, Any]:
    state = store.load_state(worker_id)
    if state is None:
        raise ImplementerError(f"worker {worker_id!r} is not registered under {store.root}")
    herdr = Herdr(state.get("herdr", {}).get("workspace"))
    agent = current_agent(state) or state["herdr"].get("agent", "")
    rc, status = herdr.agent_status(agent)
    worktree = Path(state["worktree"])
    repo = Path(state["repo"]["root"])
    branch_head = None
    if worktree.exists():
        try:
            resolved = git(repo, "rev-parse", "--verify", f"{state['branch']}^{{commit}}", check=False)
            if resolved.returncode == 0:
                branch_head = resolved.stdout.strip()
        except ImplementerError:
            branch_head = None
    dirty = None
    if worktree.exists():
        try:
            porcelain = git(worktree, "status", "--porcelain=v1", check=False).stdout
            dirty = {"entries": len([line for line in porcelain.splitlines() if line.strip()])}
        except ImplementerError:
            dirty = None
    live_sessions = live_session_facts(herdr, state)
    return {
        "worker_id": state["worker_id"],
        "run_id": state.get("run_id"),
        "run": store.load_run(state["run_id"]) if state.get("run_id") else None,
        "ticket": state["ticket"],
        "runtime": state["runtime"],
        "repo": state["repo"],
        "branch": state["branch"],
        "branch_head": branch_head,
        "worktree": state["worktree"],
        "worktree_dirty": dirty,
        "herdr": {**state["herdr"], "agent_status": status, "agent_present": rc == 0},
        "lifecycle": state["lifecycle"],
        "prompt": state.get("prompt"),
        "session": current_session(state),
        "sessions": state.get("sessions") or [],
        "handoff": state.get("handoff") or {},
        "supervisor": supervisor_facts(state, store.load_control(worker_id)),
        "items": annotated_items(store, state),
        "pending_items": worker_pending_items(store, state),
        "result": state["result"],
        "release": state.get("release"),
        "cleanup": cleanup_state_facts(store, state, live=live_sessions),
        "paths": state["paths"],
        "control": store.load_control(worker_id),
    }


def cmd_start(args: argparse.Namespace) -> int:
    if not TICKET_ID_RE.fullmatch(args.ticket_id):
        raise ImplementerError(f"invalid ticket id: {args.ticket_id!r}")
    repo_root, common_dir = repo_context(Path(args.repo))
    store = Store.resolve(common_dir, args.management_root)
    run = store.load_run(args.run)
    if run is None:
        raise ImplementerError(f"run {args.run!r} is not registered under {store.root}; create it with init-run")
    run_repo = Path(str(run.get("repo_root") or ".")).resolve()
    if run_repo != repo_root:
        raise ImplementerError(f"run {args.run!r} belongs to repository {run_repo}; refusing to mix repositories")
    run_runtime = run.get("runtime") if isinstance(run.get("runtime"), dict) else {}
    effective: dict[str, str] = {}
    for field in ("kind", "provider", "model", "thinking"):
        requested = getattr(args, field)
        confirmed = run_runtime.get(field)
        if requested and confirmed and requested != confirmed:
            raise ImplementerError(
                f"worker {field} {requested!r} does not match the confirmed run configuration {confirmed!r}; "
                "runtime configuration is confirmed once per run"
            )
        chosen = requested or confirmed
        if not chosen:
            raise ImplementerError(f"run {args.run!r} does not define {field}; pass it explicitly")
        effective[field] = str(chosen)
    adapter = get_adapter(effective["kind"])
    for label, value in (("provider", effective["provider"]), ("model", effective["model"])):
        if not SELECTION_RE.fullmatch(value):
            raise ImplementerError(f"{label} contains unsupported characters: {value!r}")
    base = args.base.strip().lower()
    if not SHA_RE.fullmatch(base):
        raise ImplementerError("--base must be a full commit SHA (40-64 lowercase hex characters)")
    if git(repo_root, "cat-file", "-e", f"{base}^{{commit}}", check=False).returncode != 0:
        raise ImplementerError(f"base commit {base} cannot be resolved in {repo_root}")
    if os.environ.get("HERDR_ENV") != "1":
        raise ImplementerError("this tool must run inside Herdr (HERDR_ENV=1)")
    workspace = os.environ.get("HERDR_WORKSPACE_ID")
    if not workspace:
        raise ImplementerError("HERDR_WORKSPACE_ID is missing")
    sources = [Path(item) for item in args.material]
    for source in sources:
        if not source.exists():
            raise ImplementerError(f"material does not exist: {source}")
    if args.handoff_tokens < 1:
        raise ImplementerError("--handoff-tokens must be a positive token count")
    if not 0 < args.handoff_pct <= 1:
        raise ImplementerError("--handoff-pct must be within (0, 1]")
    if args.context_window < 0:
        raise ImplementerError("--context-window must not be negative")
    adapter.validate(effective["provider"], effective["model"], effective["thinking"])

    worker_id = args.worker_id or worker_id_for(args.ticket_id)
    if not WORKER_ID_RE.fullmatch(worker_id):
        raise ImplementerError(f"invalid worker id: {worker_id!r}")
    branch = args.branch or f"{DEFAULT_BRANCH_PREFIX}/{worker_id}"
    if git(repo_root, "check-ref-format", "--branch", branch, check=False).returncode != 0:
        raise ImplementerError(f"invalid branch name: {branch!r}")
    if args.worktree:
        worktree = Path(args.worktree).resolve()
    else:
        worktree = (store.root / "worktrees" / worker_id).resolve()
    agent = agent_name_for(worker_id)
    herdr = Herdr(workspace)
    runtime: dict[str, Any] = dict(effective)
    tab_env = adapter.tab_env(effective["provider"], effective["model"], effective["thinking"])
    if tab_env:
        runtime["env"] = tab_env
    max_workers = run.get("max_workers")
    if not isinstance(max_workers, int) or isinstance(max_workers, bool) or max_workers < 1:
        raise ImplementerError(f"run {args.run!r} has no usable max_workers; re-create the run")

    tab = ""
    pane = ""
    agent_started = False
    # The slot claim is the registration itself: quota check and initial state
    # write happen under one lock, so concurrent starts cannot oversubscribe.
    with file_lock(store.run_lock_path(args.run)):
        if store.worker_dir(worker_id).exists():
            raise ImplementerError(f"worker id {worker_id!r} is already registered at {store.worker_dir(worker_id)}")
        if git(repo_root, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}", check=False).returncode == 0:
            raise ImplementerError(f"branch {branch!r} already exists; refusing to reuse an unowned branch")
        if worktree.exists():
            raise ImplementerError(f"worktree path already exists: {worktree}")
        if herdr.agent_exists(agent):
            raise ImplementerError(f"a live Herdr agent is already named {agent!r}")
        active = active_workers(store, args.run)
        if len(active) >= max_workers:
            raise ImplementerError(
                f"run {args.run!r} is at its concurrency limit: {len(active)} active worker(s) of "
                f"max_workers={max_workers} ({', '.join(item['worker_id'] for item in active)})"
            )
        state: dict[str, Any] = initial_state(
            worker_id, args.ticket_id, args.title or args.ticket_id, runtime, repo_root, common_dir, base,
            branch, worktree, store, workspace, agent, args.run,
        )
        state["handoff"].update(
            tokens=args.handoff_tokens,
            pct=args.handoff_pct,
            window=args.context_window or None,
        )
        store.save_state(worker_id, state)
    try:
        manifest = snapshot_materials(store.worker_dir(worker_id), sources, worker_id)
        contract_path = store.contract_path(worker_id)
        material_lines = "\n".join(
            f"- `{entry['snapshot']}` (source: `{entry['source']}`)" for entry in manifest["materials"]
        )
        template = SKILL_DIR / "docs" / "implementation" / "plan-worker.md"
        if not template.is_file():
            raise ImplementerError(f"worker contract template is missing: {template}")
        contract = render_contract(
            template,
            {
                "WORKER_ID": worker_id,
                "TICKET_ID": args.ticket_id,
                "TICKET_TITLE": args.title or args.ticket_id,
                "KIND": effective["kind"],
                "PROVIDER": effective["provider"],
                "MODEL": effective["model"],
                "THINKING": effective["thinking"],
                "BASE_SHA": base,
                "BRANCH": branch,
                "WORKTREE": str(worktree),
                "RESULT_FILE": str(store.result_path(worker_id)),
                "MANAGEMENT_DIR": str(store.worker_dir(worker_id)),
                "MATERIALS": material_lines,
                "INSTRUCTIONS": args.instructions or "(none beyond the ticket materials)",
            },
        )
        atomic_text(contract_path, contract)
        store.save_state(worker_id, state)

        worktree.parent.mkdir(parents=True, exist_ok=True)
        git(repo_root, "worktree", "add", "-b", branch, "--", str(worktree), base)
        tab, pane = herdr.tab_create(worktree, agent, tab_env)
        state["herdr"]["tab"] = tab
        state["herdr"]["pane"] = pane
        store.save_state(worker_id, state)

        herdr.agent_start(
            agent,
            effective["kind"],
            pane,
            adapter.start_args(effective["provider"], effective["model"], effective["thinking"]),
        )
        agent_started = True
        if not herdr.wait_interactive(agent):
            raise ImplementerError(f"agent {agent} never reached an interactive state")
        ensure_session_state(state)
        state["lifecycle"]["state"] = "prompting"
        store.save_state(worker_id, state)
        attempts = deliver_prompt_confirmed(
            herdr,
            agent,
            f"You are worker {worker_id} for ticket {args.ticket_id}. Read the contract at {contract_path} "
            "in full and follow it exactly. Do not start work before reading it.",
        )
        state["prompt"] = {"attempts": attempts, "confirmed_at": utc_now()}
        session = current_session(state)
        if session is not None:
            session["prompt"] = {"attempts": attempts, "confirmed_at": utc_now()}
            session["context_ref"] = herdr.wait_agent_session(agent, attempts=8, delay=0.5)
        state["lifecycle"]["state"] = "running"
        store.save_state(worker_id, state)
        pid = spawn_supervisor(store, worker_id, worktree)
        deadline = time.monotonic() + SUPERVISOR_START_WAIT
        while time.monotonic() < deadline:
            current = store.load_state(worker_id)
            if current and current["supervisor"].get("pid"):
                state = current
                break
            time.sleep(0.1)
        print_json(
            {
                "worker_id": worker_id,
                "run_id": args.run,
                "ticket_id": args.ticket_id,
                "agent": agent,
                "tab": tab,
                "pane": pane,
                "branch": branch,
                "worktree": str(worktree),
                "base": base,
                "runtime": runtime,
                "max_workers": max_workers,
                "active_workers": len(active_workers(store, args.run)),
                "prompt_attempts": state["prompt"]["attempts"],
                "session": state.get("session_index"),
                "result_path": str(store.result_path(worker_id)),
                "management_dir": str(store.worker_dir(worker_id)),
                "supervisor_pid": state["supervisor"].get("pid") or pid,
            }
        )
        return 0
    except Exception as exc:
        state["lifecycle"].update(state="launch-failed", reason=str(exc))
        state["supervisor"].update(state="exited", exit_reason="launch-failed")
        if agent_started and tab:
            try:
                herdr.send_keys(agent,("escape",))
            except ImplementerError:
                pass
        store.save_state(worker_id, state)
        print_json(
            {
                "error": "launch-failed",
                "message": str(exc),
                "worker_id": worker_id,
                "worktree": str(worktree),
                "result_path": str(store.result_path(worker_id)),
                "management_dir": str(store.worker_dir(worker_id)),
            },
            stream=sys.stderr,
        )
        return 2


def cmd_init_run(args: argparse.Namespace) -> int:
    """Register one execution: the confirmed runtime config and its quota."""
    run_id = args.run_id or f"run-{uuid.uuid4().hex[:8]}"
    if not RUN_ID_RE.fullmatch(run_id):
        raise ImplementerError(f"invalid run id: {run_id!r}")
    if args.max_workers < 1:
        raise ImplementerError("--max-workers must be a positive integer")
    for label, value in (("provider", args.provider), ("model", args.model)):
        if not SELECTION_RE.fullmatch(value):
            raise ImplementerError(f"{label} contains unsupported characters: {value!r}")
    adapter = get_adapter(args.kind)
    repo_root, common_dir = repo_context(Path(args.repo))
    store = Store.resolve(common_dir, args.management_root)
    if store.load_run(run_id) is not None:
        raise ImplementerError(f"run id {run_id!r} is already registered at {store.run_path(run_id)}")
    adapter.validate(args.provider, args.model, args.thinking)
    run = {
        "version": 1,
        "run_id": run_id,
        "repo_root": str(repo_root),
        "runtime": {
            "kind": args.kind,
            "provider": args.provider,
            "model": args.model,
            "thinking": args.thinking,
        },
        "max_workers": args.max_workers,
        "created_at": utc_now(),
    }
    with file_lock(store.run_lock_path(run_id)):
        if store.load_run(run_id) is not None:
            raise ImplementerError(f"run id {run_id!r} is already registered at {store.run_path(run_id)}")
        atomic_json(store.run_path(run_id), run)
    print_json(run)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    store = resolve_store(args)
    if args.run:
        run = store.load_run(args.run)
        if run is None:
            raise ImplementerError(f"run {args.run!r} is not registered under {store.root}")
        print_json(run_summary(store, run))
        return 0
    if args.worker:
        print_json(worker_facts(store, args.worker))
        return 0
    workers = []
    for worker_id in store.worker_ids():
        state = store.load_state(worker_id)
        if state is None:
            continue
        workers.append(
            {
                "worker_id": state["worker_id"],
                "run_id": state.get("run_id"),
                "ticket": state["ticket"],
                "lifecycle": state["lifecycle"]["state"],
                "session": state.get("session_index"),
                "items": len(state["items"]),
                "pending_items": len(worker_pending_items(store, state)),
                "branch": state["branch"],
                "worktree": state["worktree"],
            }
        )
    print_json({"management_root": str(store.root), "workers": workers})
    return 0


def cmd_wait(args: argparse.Namespace) -> int:
    """Return any unacknowledged delivery or exception, waiting for change.

    A timeout only means no item appeared in the wait window; it is not a
    result and implies nothing about the workers. Herdr events are not needed
    for correctness: every returned fact is read from durable state.
    """
    store = resolve_store(args)
    run = store.load_run(args.run)
    if run is None:
        raise ImplementerError(f"run {args.run!r} is not registered under {store.root}")
    poll = args.poll if args.poll > 0 else env_float("HI_WAIT_POLL_SECONDS", DEFAULT_WAIT_POLL_SECONDS)
    poll = max(0.05, poll)
    deadline = None if args.timeout <= 0 else time.monotonic() + args.timeout
    while True:
        items = run_pending_items(store, run["run_id"])
        if items:
            print_json(
                {
                    "run_id": run["run_id"],
                    "items": items,
                    "timed_out": False,
                    "observed_at": utc_now(),
                    "note": (
                        "each item is a durable fact awaiting ack; a delivery is not integration and "
                        "an idle terminal is not a result"
                    ),
                }
            )
            return 0
        if deadline is not None and time.monotonic() >= deadline:
            print_json(
                {
                    "run_id": run["run_id"],
                    "items": [],
                    "timed_out": True,
                    "observed_at": utc_now(),
                    "note": (
                        "no pending item within the wait window; a wait timeout is not a task result "
                        "and implies nothing about the workers"
                    ),
                }
            )
            return 0
        remaining = poll
        if deadline is not None:
            remaining = min(poll, max(0.05, deadline - time.monotonic()))
        time.sleep(remaining)


def cmd_ack(args: argparse.Namespace) -> int:
    """Mark items as handled; acknowledgement never changes delivery or integration."""
    store = resolve_store(args)
    results = []
    for raw in args.item:
        worker_id, item_id = split_item_id(raw)
        state = store.load_state(worker_id)
        if state is None:
            raise ImplementerError(f"worker {worker_id!r} is not registered under {store.root}")
        if item_id not in DERIVED_ITEM_IDS:
            known = {item["id"] for item in state["items"]}
            if item_id not in known:
                raise ImplementerError(f"worker {worker_id!r} has no item {item_id!r}")
        with file_lock(store.ack_lock_path(worker_id)):
            payload = read_json(store.ack_path(worker_id))
            acks = payload.get("acked") if isinstance(payload, dict) else None
            if not isinstance(acks, dict):
                acks = {}
            already = raw in acks
            if not already:
                acks[raw] = {"acked_at": utc_now(), "note": args.note or ""}
                atomic_json(
                    store.ack_path(worker_id),
                    {"version": 1, "worker_id": worker_id, "acked": acks},
                )
            entry = acks[raw] if isinstance(acks.get(raw), dict) else {}
        results.append(
            {
                "item_id": raw,
                "worker_id": worker_id,
                "acked": True,
                "already_acked": already,
                "acked_at": entry.get("acked_at"),
                "note": entry.get("note") or "",
                "effect": "acknowledgement only; delivery and integration conclusions are unchanged",
            }
        )
    print_json({"acked": results})
    return 0


def cmd_read(args: argparse.Namespace) -> int:
    store = resolve_store(args)
    state = store.load_state(args.worker)
    if state is None:
        raise ImplementerError(f"worker {args.worker!r} is not registered under {store.root}")
    agent = current_agent(state)
    if not agent:
        raise ImplementerError(f"worker {args.worker!r} has no current session agent")
    herdr = Herdr(state.get("herdr", {}).get("workspace"))
    proc = herdr.agent_read(agent, args.lines)
    if proc.returncode != 0:
        raise ImplementerError(f"cannot read agent {agent}: {proc.stderr.strip() or proc.stdout.strip()}")
    sys.stdout.write(proc.stdout)
    return 0


def cmd_handoff(args: argparse.Namespace) -> int:
    """Request the standard session handoff; the supervisor performs it."""
    store = resolve_store(args)
    state = store.load_state(args.worker)
    if state is None:
        raise ImplementerError(f"worker {args.worker!r} is not registered under {store.root}")
    life = state["lifecycle"]["state"]
    if life in TERMINAL_STATES:
        raise ImplementerError(f"worker is in lifecycle {life!r}; a handoff cannot be requested")
    if not pid_alive(state):
        raise ImplementerError("the worker supervisor is not running; a handoff cannot be requested")
    if result_declared(state):
        raise ImplementerError(
            "the worker has already written its result file; delivery stops automatic handoff"
        )
    session = current_session(state)
    if session is None:
        raise ImplementerError("the worker has no current session record")
    request_id = uuid.uuid4().hex
    control = store.load_control(args.worker)
    control.update(
        {
            "handoff_request_id": request_id,
            "handoff_requested_at": utc_now(),
            "handoff_requested_session": session["index"],
            "handoff_reason": args.reason or "requested by the calling agent",
        }
    )
    store.save_control(args.worker, control)
    print_json(
        {
            "requested": True,
            "worker_id": args.worker,
            "session": session["index"],
            "agent": session["agent"],
            "request_id": request_id,
            "reason": control["handoff_reason"],
            "note": "the supervisor performs the standard handoff process; inspect status for progress",
        }
    )
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    """Stop business execution and supervision while retaining the whole scene.

    A stop must also cover the handoff competition: the supervisor pauses
    whichever session is current when the request lands, so a replacement
    session cannot keep writing behind a stopped worker. Without a live
    supervisor this command performs the same stop itself.
    """
    store = resolve_store(args)
    state = store.load_state(args.worker)
    if state is None:
        raise ImplementerError(f"worker {args.worker!r} is not registered under {store.root}")
    herdr = Herdr(state.get("herdr", {}).get("workspace"))
    adapter = get_adapter(state["runtime"]["kind"])
    life = state["lifecycle"]["state"]

    paths = worker_paths(store, state)

    def stop_payload(stop_facts: dict[str, Any], **extra: Any) -> dict[str, Any]:
        payload = {
            "stopped": True,
            "worker_id": args.worker,
            "session": state.get("session_index"),
            "lifecycle": state["lifecycle"]["state"],
            "reason": state["lifecycle"].get("reason"),
            "business_stopped": stop_facts["business_stopped"],
            "sessions": stop_facts["sessions"],
            "branch": state["branch"],
            "worktree": state["worktree"],
            "result_path": paths["result"],
            "management_dir": paths["management"],
            "release": state.get("release"),
            "items": len(state["items"]),
        }
        payload.update(extra)
        return payload

    def stop_incomplete(stop_facts: dict[str, Any], message: str, **extra: Any) -> int:
        payload = stop_payload(stop_facts, **extra)
        payload.update({"error": "stop-incomplete", "message": message})
        print_json(payload, stream=sys.stderr)
        return 3

    def release_after_stop(stop_facts: dict[str, Any]) -> None:
        """Reuse the delivered-release path; the caller persists the state."""
        if stop_facts["business_stopped"]:
            release_delivered_tabs(herdr, adapter, state)

    if life == "stopped" and not pid_alive(state):
        with file_lock(store.stop_lock_path(args.worker)):
            state = store.load_state(args.worker) or state
            stop_facts = stop_registered_sessions(herdr, adapter, state)
            release_after_stop(stop_facts)
            store.save_state(args.worker, state)
        if not stop_facts["business_stopped"]:
            return stop_incomplete(stop_facts, "a registered session is still working; the scene is retained")
        print_json(stop_payload(stop_facts, already_stopped=True))
        return 0

    if life in TERMINAL_STATES and not pid_alive(state):
        with file_lock(store.stop_lock_path(args.worker)):
            state = store.load_state(args.worker) or state
            stop_facts = stop_registered_sessions(herdr, adapter, state)
            release_after_stop(stop_facts)
            store.save_state(args.worker, state)
        if not stop_facts["business_stopped"]:
            return stop_incomplete(
                stop_facts, "a registered session is still working; the scene is retained", already_terminal=True
            )
        print_json(stop_payload(stop_facts, already_terminal=True))
        return 0

    control = store.load_control(args.worker)
    control.update(
        {
            "stop_requested_at": utc_now(),
            "reason": args.reason or "stop requested",
            "handoff_request_id": None,
            "handoff_requested_at": None,
            "handoff_requested_session": None,
            "handoff_reason": None,
        }
    )
    store.save_control(args.worker, control)

    if pid_alive(state):
        deadline = time.monotonic() + env_float("HI_STOP_WAIT_SECONDS", DEFAULT_STOP_WAIT)
        while time.monotonic() < deadline:
            state = store.load_state(args.worker) or state
            if not pid_alive(state):
                break
            time.sleep(0.25)
        # A live supervisor owns the state until it exits, even when the
        # lifecycle already looks terminal: the supervisor writes the delivery
        # first and then releases the terminal, so taking over here would add
        # a second writer and a second release.
        if pid_alive(state):
            stop_facts = {"business_stopped": False, "sessions": live_session_facts(herdr, state)}
            return stop_incomplete(
                stop_facts,
                "the supervisor is still running; retry stop after it exits",
            )

    # Concurrent stops for the same worker serialize here, so an exit or a
    # release decision cannot be duplicated or overwritten.
    with file_lock(store.stop_lock_path(args.worker)):
        state = store.load_state(args.worker) or state
        outcome = record_result(store, state)
        stop_facts = stop_registered_sessions(herdr, adapter, state)
        if outcome in {"recorded", "protocol-failure"}:
            state["lifecycle"]["reason"] = state["lifecycle"].get("reason") or "stop-requested"
            state["supervisor"].update(
                state="exited",
                exit_reason=state["supervisor"].get("exit_reason") or "stop-requested",
                heartbeat_at=utc_now(),
            )
            store.save_state(args.worker, state)
            if not stop_facts["business_stopped"]:
                return stop_incomplete(stop_facts, "a registered session is still working; the scene is retained")
            release_after_stop(stop_facts)
            store.save_state(args.worker, state)
            print_json(stop_payload(stop_facts, already_terminal=state["lifecycle"]["state"] != "stopped"))
            return 0
        if stop_facts["business_stopped"]:
            finalize(state, "stopped", "stop-requested")
            store.save_state(args.worker, state)
        else:
            if not any(item.get("code") == "stop-incomplete" for item in state["items"]):
                append_item(
                    state,
                    "exception",
                    "stop-incomplete",
                    "business execution could not be confirmed stopped after the stop request; the scene is retained",
                    {"sessions": stop_facts["sessions"]},
                )
            state["supervisor"].update(state="exited", exit_reason="stop-incomplete", heartbeat_at=utc_now())
            store.save_state(args.worker, state)
    if stop_facts["business_stopped"]:
        print_json(stop_payload(stop_facts))
        return 0
    return stop_incomplete(stop_facts, "business execution could not be confirmed stopped; the scene is retained")


def cmd_cleanup(args: argparse.Namespace) -> int:
    """Remove registered resources only after an explicit master decision.

    The tool verifies ownership, absence of live business writes and the
    disposition of uncommitted content; it never removes an unowned path, a
    branch checked out elsewhere, or a worktree that is still writing. The
    management directory (result, contract, handoffs, logs, archive) is kept.
    """
    store = resolve_store(args)
    state = store.load_state(args.worker)
    if state is None:
        raise ImplementerError(f"worker {args.worker!r} is not registered under {store.root}")
    if args.archive_uncommitted and args.discard_uncommitted:
        raise ImplementerError("--archive-uncommitted and --discard-uncommitted are mutually exclusive")
    if args.force_branch and not args.delete_branch:
        raise ImplementerError("--force-branch requires --delete-branch")
    decision: dict[str, Any] = {"at": utc_now()}
    if args.integrated:
        sha = args.integrated.strip().lower()
        if not SHA_RE.fullmatch(sha):
            raise ImplementerError("--integrated must be a full commit SHA (40-64 lowercase hex characters)")
        decision["integrated"] = sha
    if args.disposition.strip():
        decision["disposition"] = args.disposition.strip()
    if "integrated" not in decision and "disposition" not in decision:
        raise ImplementerError(
            "cleanup needs an explicit decision: pass --integrated <sha> for integrated work "
            "or --disposition <text> for another disposition"
        )

    with file_lock(store.cleanup_lock_path(args.worker)):
        state = store.load_state(args.worker) or state
        herdr = Herdr(state.get("herdr", {}).get("workspace"))
        adapter = get_adapter(state["runtime"]["kind"])
        record = store.load_cleanup(args.worker)
        paths = worker_paths(store, state)
        live = live_session_facts(herdr, state)
        facts = cleanup_state_facts(store, state, live=live)
        worktree = Path(state["worktree"])
        worktree_existed = worktree.exists()
        entries = facts["resources"]["worktree"]["uncommitted_entries"] if worktree_existed else []
        handle_uncommitted = bool(entries) and (args.archive_uncommitted or args.discard_uncommitted)
        blockers = [
            blocker
            for blocker in facts["blockers"]
            if blocker["code"] != "decision-missing"
            and not (blocker["code"] == "uncommitted-content" and handle_uncommitted)
        ]
        removed: dict[str, Any] = {"worktree": False, "branch": False, "tabs": []}
        archive: dict[str, Any] | None = None

        def blocked(payload_blockers: list[dict[str, Any]], message: str) -> int:
            save_cleanup_record(
                store, state, record, decision, outcome="blocked", removed=removed, blockers=payload_blockers
            )
            print_json(
                {
                    "error": "cleanup-blocked",
                    "message": message,
                    "worker_id": args.worker,
                    "cleaned": False,
                    "already_cleaned": False,
                    "decision": decision,
                    "blockers": payload_blockers,
                    "resources": facts["resources"],
                    "retained": {
                        "worktree": state["worktree"],
                        "branch": state["branch"],
                        "management_dir": paths["management"],
                        "result": paths["result"],
                    },
                },
                stream=sys.stderr,
            )
            return 3

        if blockers:
            return blocked(blockers, "cleanup cannot proceed; no registered resource was removed")

        if worktree_existed and entries and args.archive_uncommitted:
            try:
                archive = archive_uncommitted(Path(paths["management"]), worktree)
            except ImplementerError as exc:
                return blocked([{"code": "archive-failed", "message": str(exc)}], str(exc))

        sessions = close_registered_sessions(
            herdr,
            adapter,
            state,
            skip_tabs={tab for tab in (record.get("closed_tabs") or []) if isinstance(tab, str)},
        )
        removed["tabs"] = sessions["closed_tabs"]
        if not sessions["closed"]:
            return blocked(
                [
                    {
                        "code": "session-close-failed",
                        "message": "a registered session could not be closed; the worktree is retained",
                        "remaining_agents": sessions["remaining_agents"],
                        "failed_tabs": sessions["failed_tabs"],
                    }
                ],
                "a registered session could not be closed; the worktree is retained",
            )

        if worktree.exists() or facts["resources"]["worktree"]["owned"]:
            proc = git(state["repo"]["root"], "worktree", "remove", "--force", "--", str(worktree), check=False)
            if proc.returncode != 0:
                return blocked(
                    [
                        {
                            "code": "worktree-remove-failed",
                            "message": proc.stderr.strip() or proc.stdout.strip() or "git worktree remove failed",
                        }
                    ],
                    "the registered worktree could not be removed; the scene is retained",
                )
            removed["worktree"] = True

        if args.delete_branch:
            branch_action = delete_worker_branch(Path(state["repo"]["root"]), state, force=args.force_branch)
            removed["branch"] = bool(branch_action.get("deleted"))
        else:
            branch_action = {
                "name": state["branch"],
                "existed": facts["resources"]["branch"]["exists"],
                "deleted": False,
                "retained_reason": "branch deletion needs an explicit --delete-branch",
            }

        already_cleaned = (
            bool(record.get("worktree_removed_at"))
            and not removed["worktree"]
            and not removed["branch"]
            and not removed["tabs"]
        )
        save_cleanup_record(
            store,
            state,
            record,
            decision,
            outcome="removed",
            removed=removed,
            blockers=[],
            archive=archive,
        )
        print_json(
            {
                "worker_id": args.worker,
                "cleaned": True,
                "already_cleaned": already_cleaned,
                "decision": decision,
                "worktree": {
                    "path": state["worktree"],
                    "existed": worktree_existed,
                    "removed": removed["worktree"],
                    "uncommitted": "archived" if archive else ("discarded" if handle_uncommitted else "none"),
                },
                "branch": branch_action,
                "sessions": {"closed_tabs": removed["tabs"], "remaining_agents": []},
                "archive": archive,
                "evidence": {
                    "management_dir": paths["management"],
                    "result": paths["result"],
                    "handoffs": facts["resources"]["handoffs"],
                },
                "blockers": [],
            }
        )
        return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="implementer.py",
        description="Herdr Implementer: worker lifecycle for implementing a confirmed plan or spec.",
        epilog=(
            "Example:\n"
            "  implementer.py init-run --repo /repo --run-id plan-01 --kind pi --provider <p> --model <m> "
            "--thinking <t>\n"
            "  implementer.py start --repo /repo --run plan-01 --ticket-id 05 --base <full-sha> "
            "--material /path/ticket.md\n"
            "  implementer.py status --repo /repo --run plan-01\n"
            "  implementer.py wait --repo /repo --run plan-01 --timeout 600\n"
            "  implementer.py ack --repo /repo --item w-05-abc123/i001\n"
            "  implementer.py read --repo /repo --worker <worker-id>\n"
            "  implementer.py stop --repo /repo --worker <worker-id>\n"
            "  implementer.py cleanup --repo /repo --worker <worker-id> --integrated <full-sha>"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    init_run = sub.add_parser(
        "init-run", help="register one execution with its confirmed configuration and worker quota"
    )
    init_run.add_argument("--repo", required=True, help="path to the target repository")
    init_run.add_argument("--state-root", "--management-root", dest="management_root", default="", help="override the execution state directory (--management-root is a compatibility alias)")
    init_run.add_argument("--run-id", default="", help="stable execution id; generated when omitted")
    init_run.add_argument("--kind", required=True, choices=["pi", "opencode"])
    init_run.add_argument("--provider", required=True)
    init_run.add_argument("--model", required=True)
    init_run.add_argument("--thinking", required=True)
    init_run.add_argument(
        "--max-workers", type=int, default=DEFAULT_MAX_WORKERS,
        help="maximum active workers for this run; positive integer (default: %(default)s)",
    )
    init_run.set_defaults(func=cmd_init_run)

    start = sub.add_parser("start", help="start one isolated worker from an explicit base SHA")
    start.add_argument("--repo", required=True, help="path to the target repository")
    start.add_argument("--run", required=True, help="registered run that owns the worker and its quota")
    start.add_argument("--ticket-id", required=True)
    start.add_argument("--title", default="")
    start.add_argument("--base", required=True, help="full commit SHA the worker branch starts from")
    start.add_argument("--material", action="append", required=True, help="ticket material file or directory (repeatable)")
    start.add_argument("--kind", default="", choices=["pi", "opencode"], help="must match the run when given")
    start.add_argument("--provider", default="", help="must match the run when given")
    start.add_argument("--model", default="", help="must match the run when given")
    start.add_argument("--thinking", default="", help="must match the run when given")
    start.add_argument("--worker-id", default="")
    start.add_argument("--branch", default="")
    start.add_argument("--worktree", default="")
    start.add_argument("--state-root", "--management-root", dest="management_root", default="")
    start.add_argument("--instructions", default="", help="optional extra task instructions for the worker contract")
    start.add_argument(
        "--handoff-tokens",
        type=int,
        default=DEFAULT_HANDOFF_TOKENS,
        help="absolute context-token threshold for an automatic session handoff",
    )
    start.add_argument(
        "--handoff-pct",
        type=float,
        default=DEFAULT_HANDOFF_PCT,
        help="context-window occupancy threshold for an automatic session handoff",
    )
    start.add_argument(
        "--context-window",
        type=int,
        default=0,
        help="optional explicit context window override; observations become estimated",
    )
    start.set_defaults(func=cmd_start)

    status = sub.add_parser("status", help="show execution facts for one run, one worker, or all workers")
    status.add_argument("--repo", default="")
    status.add_argument("--state-root", "--management-root", dest="management_root", default="")
    status.add_argument("--run", default="")
    status.add_argument("--worker", default="")
    status.set_defaults(func=cmd_status)

    wait = sub.add_parser("wait", help="return any pending delivery or exception, waiting for change")
    wait.add_argument("--repo", default="")
    wait.add_argument("--state-root", "--management-root", dest="management_root", default="")
    wait.add_argument("--run", required=True)
    wait.add_argument(
        "--timeout",
        type=float,
        default=0.0,
        help="seconds to wait; 0 waits until an item appears; a timeout is not a task result",
    )
    wait.add_argument("--poll", type=float, default=0.0, help="seconds between fact checks (default 1s)")
    wait.set_defaults(func=cmd_wait)

    ack = sub.add_parser("ack", help="acknowledge pending items without changing delivery or integration")
    ack.add_argument("--repo", default="")
    ack.add_argument("--state-root", "--management-root", dest="management_root", default="")
    ack.add_argument("--item", action="append", required=True, help="<worker-id>/<item-id> (repeatable)")
    ack.add_argument("--note", default="", help="optional note about how the item was handled")
    ack.set_defaults(func=cmd_ack)

    read = sub.add_parser("read", help="read the worker's recent terminal output")
    read.add_argument("--repo", default="")
    read.add_argument("--state-root", "--management-root", dest="management_root", default="")
    read.add_argument("--worker", required=True)
    read.add_argument("--lines", type=int, default=120)
    read.set_defaults(func=cmd_read)

    handoff = sub.add_parser("handoff", help="request the standard session handoff for a running worker")
    handoff.add_argument("--repo", default="")
    handoff.add_argument("--state-root", "--management-root", dest="management_root", default="")
    handoff.add_argument("--worker", required=True)
    handoff.add_argument("--reason", default="")
    handoff.set_defaults(func=cmd_handoff)

    stop = sub.add_parser("stop", help="stop business execution and supervision, retaining the scene")
    stop.add_argument("--repo", default="")
    stop.add_argument("--state-root", "--management-root", dest="management_root", default="")
    stop.add_argument("--worker", required=True)
    stop.add_argument("--reason", default="")
    stop.set_defaults(func=cmd_stop)

    cleanup = sub.add_parser(
        "cleanup", help="remove registered resources only after an explicit master decision"
    )
    cleanup.add_argument("--repo", default="")
    cleanup.add_argument("--state-root", "--management-root", dest="management_root", default="")
    cleanup.add_argument("--worker", required=True)
    cleanup.add_argument("--integrated", default="", help="integration SHA supplied by the master")
    cleanup.add_argument("--disposition", default="", help="free-text disposition for non-code or discarded work")
    cleanup.add_argument(
        "--delete-branch", action="store_true", help="delete the worker branch (safe -d unless --force-branch)"
    )
    cleanup.add_argument(
        "--force-branch", action="store_true", help="use -D when deleting a branch that is not merged"
    )
    cleanup.add_argument(
        "--archive-uncommitted",
        action="store_true",
        help="copy uncommitted content into the management dir before removing the worktree",
    )
    cleanup.add_argument(
        "--discard-uncommitted", action="store_true", help="explicitly discard uncommitted content"
    )
    cleanup.set_defaults(func=cmd_cleanup)

    supervise = sub.add_parser("_supervise", help=argparse.SUPPRESS)
    supervise.add_argument("--state-root", "--management-root", dest="management_root", required=True)
    supervise.add_argument("--worker", required=True)
    supervise.set_defaults(func=cmd_supervise)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return args.func(args)
    except ImplementerError as exc:
        print_json({"error": "manager-error", "message": str(exc)}, stream=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
