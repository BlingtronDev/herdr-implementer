#!/usr/bin/env python3
"""Herdr Plan Manager: minimal single-worker lifecycle tool (ticket 02).

Scope: start one isolated Pi worker from an explicit base SHA, register its
resources, supervise it in the background, record a structured delivery or
exception, and stop it while retaining the scene. Automatic handoff, OpenCode,
concurrency, wait/ack and cleanup belong to later tickets.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

MANAGER_VERSION = 1
MANAGER_PATH = Path(__file__).resolve()
SKILL_DIR = MANAGER_PATH.parent.parent
THINKING_LEVELS = ("off", "minimal", "low", "medium", "high", "xhigh", "max")
SHA_RE = re.compile(r"^[0-9a-f]{40,64}$")
TICKET_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
WORKER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
SELECTION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+@-]*$")
AGENT_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
RESULT_STATUSES = ("delivered", "failed", "needs-decision")
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
START_ATTEMPTS = 40
START_DELAY = 0.25
READY_ATTEMPTS = 240
READY_DELAY = 0.5
PROMPT_ATTEMPTS = 3
PARSE_ATTEMPTS = 3
SUPERVISOR_START_WAIT = 10.0
DEFAULT_STOP_WAIT = 30.0


class ManagerError(RuntimeError):
    pass


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso(value: str) -> float:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ManagerError(f"invalid {name}: {raw!r}") from exc


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
        raise ManagerError(f"command timed out after {timeout}s: {' '.join(args)}") from exc
    if check and proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip()
        raise ManagerError(f"command failed ({proc.returncode}): {' '.join(args)}\n{detail}")
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


def git(repo: Path, *args: str, check: bool = True, timeout: float = 60) -> subprocess.CompletedProcess[str]:
    return run(["git", *args], cwd=repo, check=check, timeout=timeout)


def repo_context(repo: Path) -> tuple[Path, Path]:
    root = Path(git(repo, "rev-parse", "--show-toplevel").stdout.strip()).resolve()
    common_raw = git(root, "rev-parse", "--git-common-dir").stdout.strip()
    common = Path(common_raw).resolve() if os.path.isabs(common_raw) else (root / common_raw).resolve()
    return root, common


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
        root = Path(override).resolve() if override else (common_dir / "herdr-plan-manager").resolve()
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


class Herdr:
    def __init__(self, workspace: str | None = None):
        self.workspace = workspace

    def json_command(self, args: list[str], *, timeout: float = 60) -> dict[str, Any]:
        proc = run(["herdr", *args], timeout=timeout)
        if proc.returncode != 0:
            detail = proc.stderr.strip() or proc.stdout.strip()
            raise ManagerError(f"herdr command failed ({proc.returncode}): {' '.join(args)}\n{detail}")
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise ManagerError(f"herdr returned invalid JSON: {' '.join(args)}") from exc
        if not isinstance(payload, dict):
            raise ManagerError(f"herdr returned non-object JSON: {' '.join(args)}")
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

    def agent_start(self, name: str, kind: str, pane: str, argv: list[str]) -> dict[str, Any]:
        last: subprocess.CompletedProcess[str] | None = None
        for index in range(START_ATTEMPTS):
            last = run(
                ["herdr", "agent", "start", name, "--kind", kind, "--pane", pane, "--", *argv],
                timeout=90,
            )
            if last.returncode == 0:
                try:
                    payload = json.loads(last.stdout)
                except json.JSONDecodeError as exc:
                    raise ManagerError(f"agent start returned invalid JSON for {name}") from exc
                return payload if isinstance(payload, dict) else {}
            if herdr_error_code(last) != "agent_pane_busy":
                detail = last.stderr.strip() or last.stdout.strip()
                raise ManagerError(f"agent start failed for {name}: {detail}")
            if index + 1 < START_ATTEMPTS:
                time.sleep(START_DELAY)
        detail = last.stderr.strip() if last else "no attempt"
        raise ManagerError(f"agent start kept failing for {name}: {detail}")

    def tab_create(self, cwd: Path, label: str) -> tuple[str, str]:
        if not self.workspace:
            raise ManagerError("HERDR_WORKSPACE_ID is missing")
        payload = self.json_command([
            "tab",
            "create",
            "--workspace",
            self.workspace,
            "--cwd",
            str(cwd),
            "--label",
            label,
            "--no-focus",
        ])
        result = payload.get("result") or {}
        tab = result.get("tab")
        pane = result.get("root_pane")
        tab_id = tab if isinstance(tab, str) else (tab or {}).get("tab_id")
        pane_id = pane if isinstance(pane, str) else (pane or {}).get("pane_id")
        if not isinstance(tab_id, str) or not isinstance(pane_id, str):
            raise ManagerError("tab create response has no result.tab/result.root_pane IDs")
        return tab_id, pane_id

    def tab_close(self, tab: str) -> None:
        run(["herdr", "tab", "close", tab], timeout=30)

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


class RuntimeAdapter:
    kind = ""

    def start_args(self, provider: str, model: str, thinking: str) -> list[str]:
        raise NotImplementedError

    def interrupt_sequences(self) -> tuple[tuple[str, ...], ...]:
        raise NotImplementedError

    def validate(self, provider: str, model: str, thinking: str) -> None:
        raise NotImplementedError


class PiAdapter(RuntimeAdapter):
    kind = "pi"

    def start_args(self, provider: str, model: str, thinking: str) -> list[str]:
        return ["--provider", provider, "--model", model, "--thinking", thinking]

    def interrupt_sequences(self) -> tuple[tuple[str, ...], ...]:
        return (("escape",), ("escape",))

    def validate(self, provider: str, model: str, thinking: str) -> None:
        if thinking not in THINKING_LEVELS:
            raise ManagerError(f"unsupported thinking level: {thinking!r}")
        proc = run(["pi", "--list-models"], timeout=60)
        if proc.returncode != 0:
            raise ManagerError(f"cannot list Pi models: {proc.stderr.strip() or proc.stdout.strip()}")
        catalog = parse_pi_models(proc.stdout)
        entry = catalog.get((provider, model))
        if entry is None:
            raise ManagerError(f"Pi does not list model {provider}/{model}; no default fallback is allowed")
        if thinking != "off" and not entry["thinking"]:
            raise ManagerError(
                f"Pi model {provider}/{model} does not support thinking levels; choose 'off' or another model"
            )


def get_adapter(kind: str) -> RuntimeAdapter:
    if kind == "pi":
        return PiAdapter()
    if kind == "opencode":
        raise ManagerError("kind 'opencode' is not supported yet; ticket 03 adds it")
    raise ManagerError(f"unsupported runtime kind: {kind!r}")


def worker_id_for(ticket_id: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", ticket_id.lower()).strip("-") or "ticket"
    return f"w-{slug[:40]}-{uuid.uuid4().hex[:6]}"


def agent_name_for(worker_id: str) -> str:
    base = re.sub(r"[^a-z0-9_-]+", "-", worker_id.lower())
    if not base or not base[0].isalpha():
        base = "hpm-" + base.lstrip("-")
    base = base[:32]
    if not AGENT_RE.fullmatch(base):
        raise ManagerError(f"cannot derive a Herdr agent name from worker id {worker_id!r}")
    return base


def snapshot_materials(worker_dir: Path, sources: list[Path], worker_id: str) -> dict[str, Any]:
    materials_dir = worker_dir / "materials"
    materials_dir.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, Any]] = []
    for index, source in enumerate(sources, start=1):
        resolved = source.resolve()
        if not resolved.exists():
            raise ManagerError(f"material does not exist: {source}")
        target = materials_dir / f"{index:02d}-{resolved.name or 'material'}"
        if target.exists():
            raise ManagerError(f"material snapshot already exists: {target}")
        if resolved.is_dir():
            shutil.copytree(resolved, target, symlinks=True)
            kind = "directory"
        elif resolved.is_file():
            shutil.copy2(resolved, target)
            kind = "file"
        else:
            raise ManagerError(f"material is neither a regular file nor a directory: {source}")
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
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    leftover = re.findall(r"\{\{[A-Z0-9_]+\}\}", text)
    if leftover:
        raise ManagerError(f"contract template has unresolved placeholders: {', '.join(sorted(set(leftover)))}")
    return text


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
) -> dict[str, Any]:
    worker_dir = store.worker_dir(worker_id)
    timestamp = utc_now()
    return {
        "version": MANAGER_VERSION,
        "worker_id": worker_id,
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
        "recovery": {"re_report_sent_at": None, "settled_since": None, "parse_failures": 0},
        "items": [],
        "result": None,
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
        "acked_at": None,
    }
    state["items"].append(item)
    return item


def pid_alive(state: dict[str, Any]) -> bool:
    pid = state.get("supervisor", {}).get("pid")
    host = state.get("supervisor", {}).get("host")
    if not isinstance(pid, int) or host != os.uname().nodename:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def supervisor_facts(state: dict[str, Any], control: dict[str, Any]) -> dict[str, Any]:
    supervisor = state.get("supervisor", {})
    alive = pid_alive(state)
    heartbeat = supervisor.get("heartbeat_at")
    stale = False
    if alive and heartbeat:
        stale = time.time() - parse_iso(heartbeat) > max(30.0, env_float("HPM_POLL_SECONDS", 5.0) * 6)
    warnings: list[str] = []
    lifecycle = state.get("lifecycle", {}).get("state")
    inactive = lifecycle in TERMINAL_STATES or bool(control.get("stop_requested_at"))
    if not inactive:
        if supervisor.get("state") == "running" and not alive:
            warnings.append("supervisor-missing")
        elif stale:
            warnings.append("supervisor-stale")
    return {
        "pid": supervisor.get("pid"),
        "host": supervisor.get("host"),
        "state": supervisor.get("state"),
        "alive": alive,
        "heartbeat_at": heartbeat,
        "exit_reason": supervisor.get("exit_reason"),
        "warnings": warnings,
    }


def validate_result(payload: Any, state: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ManagerError("result must be a JSON object")
    if payload.get("ticket_id") != state["ticket"]["id"]:
        raise ManagerError(f"result ticket_id {payload.get('ticket_id')!r} does not match {state['ticket']['id']!r}")
    if payload.get("worker_id") != state["worker_id"]:
        raise ManagerError(f"result worker_id {payload.get('worker_id')!r} does not match {state['worker_id']!r}")
    status = payload.get("status")
    if status not in RESULT_STATUSES:
        raise ManagerError(f"result status must be one of {', '.join(RESULT_STATUSES)}; got {status!r}")
    if status in ("failed", "needs-decision"):
        reason = payload.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ManagerError(f"{status} declaration needs a non-empty reason")
        return payload

    summary = payload.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise ManagerError("delivered declaration needs a non-empty summary")
    acceptance = payload.get("acceptance")
    if not isinstance(acceptance, list) or not acceptance:
        raise ManagerError("delivered declaration needs a non-empty acceptance list")
    for entry in acceptance:
        if not isinstance(entry, dict):
            raise ManagerError("acceptance entries must be objects")
        if not isinstance(entry.get("criterion"), str) or not entry["criterion"].strip():
            raise ManagerError("acceptance entry needs a non-empty criterion")
        if not isinstance(entry.get("met"), bool):
            raise ManagerError("acceptance entry needs a boolean met field")
        if not isinstance(entry.get("evidence"), str) or not entry["evidence"].strip():
            raise ManagerError("acceptance entry needs non-empty evidence")
    verification = payload.get("verification")
    if not isinstance(verification, list) or not verification:
        raise ManagerError("delivered declaration needs a non-empty verification list")
    for entry in verification:
        if not isinstance(entry, dict):
            raise ManagerError("verification entries must be objects")
        if not isinstance(entry.get("command"), str) or not entry["command"].strip():
            raise ManagerError("verification entry needs a non-empty command")
        if not isinstance(entry.get("exit_code"), int) or isinstance(entry.get("exit_code"), bool):
            raise ManagerError("verification entry needs an integer exit_code")
        if not isinstance(entry.get("summary"), str) or not entry["summary"].strip():
            raise ManagerError("verification entry needs a non-empty summary")

    head = payload.get("head")
    artifacts = payload.get("artifacts", [])
    if head is not None and (not isinstance(head, str) or not SHA_RE.fullmatch(head)):
        raise ManagerError("head must be null or a full commit SHA")
    if not isinstance(artifacts, list) or any(not isinstance(item, str) or not item.strip() for item in artifacts):
        raise ManagerError("artifacts must be a list of non-empty paths")

    repo = Path(state["repo"]["root"])
    branch = state["branch"]
    base = state["repo"]["base"]
    branch_head = git(repo, "rev-parse", "--verify", f"{branch}^{{commit}}", check=False)
    if branch_head.returncode != 0:
        raise ManagerError(f"delivered branch {branch!r} cannot be resolved")
    resolved_head = branch_head.stdout.strip()
    ahead = int(git(repo, "rev-list", "--count", f"{base}..{branch}").stdout.strip() or "0")
    if ahead > 0 and head is None:
        raise ManagerError("branch has commits after the base but head is null")
    if head is not None and head != resolved_head:
        raise ManagerError(f"declared head {head} does not match branch HEAD {resolved_head}")
    if ahead == 0 and not artifacts:
        raise ManagerError("delivered declaration has no code commit and no artifact")

    worktree = Path(state["worktree"]).resolve()
    management = Path(state["paths"]["management"]).resolve()
    for item in artifacts:
        candidate = Path(item)
        resolved = candidate.resolve() if candidate.is_absolute() else (worktree / candidate).resolve()
        if not contained(resolved, worktree) and not contained(resolved, management):
            raise ManagerError(f"artifact path escapes the worktree and management dir: {item}")
        if not resolved.exists():
            raise ManagerError(f"artifact path does not exist: {item}")
    return payload


def record_result(store: Store, state: dict[str, Any]) -> str:
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
            return "protocol-failure"
        return "transient"
    try:
        normalized = validate_result(payload, state)
    except ManagerError as exc:
        append_item(state, "exception", "protocol-failure", f"result protocol check failed: {exc}")
        state["lifecycle"]["state"] = "protocol-failure"
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
    return "recorded"


def finalize(state: dict[str, Any], lifecycle_state: str, exit_reason: str) -> None:
    state["lifecycle"]["state"] = lifecycle_state
    state["lifecycle"]["reason"] = exit_reason
    supervisor = state["supervisor"]
    supervisor["state"] = "exited"
    supervisor["exit_reason"] = exit_reason
    supervisor["heartbeat_at"] = utc_now()


def supervisor_log(message: str) -> None:
    print(f"{utc_now()} {message}", flush=True)


def cmd_supervise(args: argparse.Namespace) -> int:
    store = Store(Path(args.management_root))
    worker_id = args.worker
    state = store.load_state(worker_id)
    if state is None:
        supervisor_log(f"no registered worker {worker_id}")
        return 1
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
    poll = env_float("HPM_POLL_SECONDS", 5.0)
    settle_grace = env_float("HPM_SETTLE_GRACE_SECONDS", 30.0)
    report_wait = env_float("HPM_REPORT_WAIT_SECONDS", 120.0)
    agent = state["herdr"]["agent"]

    while True:
        control = store.load_control(worker_id)
        if control.get("stop_requested_at"):
            outcome = record_result(store, state)
            if outcome not in {"recorded", "protocol-failure"}:
                finalize(state, "stopped", "stop-requested")
                supervisor_log("stopped by request")
            else:
                state["supervisor"]["exit_reason"] = "stop-requested"
                supervisor_log(f"stop requested after result: {outcome}")
            store.save_state(worker_id, state)
            return 0

        rc, status = herdr.agent_status(agent)
        if rc != 0:
            outcome = record_result(store, state)
            if outcome in {"recorded", "protocol-failure"}:
                supervisor_log(f"agent gone; result {outcome}")
            else:
                append_item(state, "exception", "agent-exited", "agent disappeared before writing a valid result")
                finalize(state, "agent-exited", "agent-exited-without-result")
                supervisor_log("agent exited without a result")
            store.save_state(worker_id, state)
            return 0

        if status in SETTLED_STATES:
            outcome = record_result(store, state)
            if outcome in {"recorded", "protocol-failure"}:
                supervisor_log(f"terminal settled; result {outcome}")
                state["lifecycle"]["reason"] = "result-recorded" if outcome == "recorded" else "result-invalid"
                state["supervisor"]["exit_reason"] = state["lifecycle"]["reason"]
                state["supervisor"]["state"] = "exited"
                state["supervisor"]["heartbeat_at"] = utc_now()
                store.save_state(worker_id, state)
                return 0
            if outcome == "transient":
                supervisor_log("result file present but not readable yet; will retry")
            else:
                sent = state["recovery"].get("re_report_sent_at")
                if not sent:
                    since = state["recovery"].get("settled_since")
                    if not since:
                        state["recovery"]["settled_since"] = utc_now()
                        supervisor_log("settled without a result; starting grace period")
                    elif time.time() - parse_iso(since) >= settle_grace:
                        try:
                            proc = herdr.agent_prompt(
                                agent,
                                "Your terminal is idle but no valid result file exists at "
                                f"{state['paths']['result']}. If the ticket work is complete, write the result "
                                "file now exactly as the contract specifies. If it is not complete, finish the "
                                "ticket first. Do not start unrelated work.",
                            )
                        except ManagerError as exc:
                            append_item(state, "exception", "protocol-failure", f"cannot send result re-report: {exc}")
                            finalize(state, "protocol-failure", "re-report-failed")
                            store.save_state(worker_id, state)
                            supervisor_log("failed to send result re-report")
                            return 0
                        if proc.returncode != 0:
                            rc2, status2 = herdr.agent_status(agent)
                            if status2 != "working" and rc2 == 0:
                                append_item(
                                    state,
                                    "exception",
                                    "protocol-failure",
                                    f"result re-report rejected: {proc.stderr.strip() or proc.stdout.strip()}",
                                )
                                finalize(state, "protocol-failure", "re-report-rejected")
                                store.save_state(worker_id, state)
                                supervisor_log("result re-report rejected")
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
            if not any(item.get("code") == "blocked" for item in state["items"]):
                append_item(state, "exception", "blocked", "worker is blocked and may need a decision")
                supervisor_log("worker blocked")
        else:
            state["recovery"]["settled_since"] = None

        state["supervisor"]["heartbeat_at"] = utc_now()
        store.save_state(worker_id, state)
        time.sleep(poll)


def deliver_prompt(herdr: Herdr, agent: str, text: str) -> None:
    last_error = ""
    for attempt in range(PROMPT_ATTEMPTS):
        try:
            proc = herdr.agent_prompt(agent, text)
        except ManagerError as exc:
            last_error = str(exc)
            rc, status = herdr.agent_status(agent)
            if rc == 0 and status == "working":
                return
            if attempt + 1 < PROMPT_ATTEMPTS and rc == 0 and status in SETTLED_STATES:
                time.sleep(1.0)
                continue
            raise ManagerError(f"prompt delivery failed: {last_error}")
        if proc.returncode == 0:
            return
        last_error = proc.stderr.strip() or proc.stdout.strip()
        rc, status = herdr.agent_status(agent)
        if rc == 0 and status == "working":
            return
        if rc == 0 and status == "blocked":
            raise ManagerError("agent is blocked; submission was rejected")
        if attempt + 1 < PROMPT_ATTEMPTS and rc == 0 and status in SETTLED_STATES:
            time.sleep(1.0)
            continue
        raise ManagerError(f"prompt delivery failed: {last_error}")
    raise ManagerError(f"prompt delivery failed: {last_error}")


def spawn_supervisor(store: Store, worker_id: str, worktree: Path) -> int:
    log = store.log_path(worker_id).open("ab")
    try:
        proc = subprocess.Popen(
            [
                sys.executable,
                str(MANAGER_PATH),
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
        raise ManagerError(f"worker {worker_id!r} is not registered under {store.root}")
    herdr = Herdr(state.get("herdr", {}).get("workspace"))
    rc, status = herdr.agent_status(state["herdr"]["agent"])
    worktree = Path(state["worktree"])
    repo = Path(state["repo"]["root"])
    branch_head = None
    if worktree.exists():
        try:
            resolved = git(repo, "rev-parse", "--verify", f"{state['branch']}^{{commit}}", check=False)
            if resolved.returncode == 0:
                branch_head = resolved.stdout.strip()
        except ManagerError:
            branch_head = None
    dirty = None
    if worktree.exists():
        try:
            porcelain = git(worktree, "status", "--porcelain=v1", check=False).stdout
            dirty = {"entries": len([line for line in porcelain.splitlines() if line.strip()])}
        except ManagerError:
            dirty = None
    return {
        "worker_id": state["worker_id"],
        "ticket": state["ticket"],
        "runtime": state["runtime"],
        "repo": state["repo"],
        "branch": state["branch"],
        "branch_head": branch_head,
        "worktree": state["worktree"],
        "worktree_dirty": dirty,
        "herdr": {**state["herdr"], "agent_status": status, "agent_present": rc == 0},
        "lifecycle": state["lifecycle"],
        "supervisor": supervisor_facts(state, store.load_control(worker_id)),
        "items": state["items"],
        "result": state["result"],
        "paths": state["paths"],
        "control": store.load_control(worker_id),
    }


def cmd_start(args: argparse.Namespace) -> int:
    adapter = get_adapter(args.kind)
    if args.thinking not in THINKING_LEVELS:
        raise ManagerError(f"unsupported thinking level: {args.thinking!r}")
    for label, value in (("provider", args.provider), ("model", args.model)):
        if not SELECTION_RE.fullmatch(value):
            raise ManagerError(f"{label} contains unsupported characters: {value!r}")
    if not TICKET_ID_RE.fullmatch(args.ticket_id):
        raise ManagerError(f"invalid ticket id: {args.ticket_id!r}")
    repo_root, common_dir = repo_context(Path(args.repo))
    base = args.base.strip().lower()
    if not SHA_RE.fullmatch(base):
        raise ManagerError("--base must be a full commit SHA (40-64 lowercase hex characters)")
    if git(repo_root, "cat-file", "-e", f"{base}^{{commit}}", check=False).returncode != 0:
        raise ManagerError(f"base commit {base} cannot be resolved in {repo_root}")
    if os.environ.get("HERDR_ENV") != "1":
        raise ManagerError("this tool must run inside Herdr (HERDR_ENV=1)")
    workspace = os.environ.get("HERDR_WORKSPACE_ID")
    if not workspace:
        raise ManagerError("HERDR_WORKSPACE_ID is missing")
    sources = [Path(item) for item in args.material]
    for source in sources:
        if not source.exists():
            raise ManagerError(f"material does not exist: {source}")
    adapter.validate(args.provider, args.model, args.thinking)

    worker_id = args.worker_id or worker_id_for(args.ticket_id)
    if not WORKER_ID_RE.fullmatch(worker_id):
        raise ManagerError(f"invalid worker id: {worker_id!r}")
    branch = args.branch or f"hpm/{worker_id}"
    if git(repo_root, "check-ref-format", "--branch", branch, check=False).returncode != 0:
        raise ManagerError(f"invalid branch name: {branch!r}")
    if args.worktree:
        worktree = Path(args.worktree).resolve()
    elif args.management_root:
        worktree = (Path(args.management_root).resolve() / "worktrees" / worker_id).resolve()
    else:
        worktree = (common_dir / "herdr-plan-manager" / "worktrees" / worker_id).resolve()
    store = Store.resolve(common_dir, args.management_root)
    agent = agent_name_for(worker_id)

    if store.worker_dir(worker_id).exists():
        raise ManagerError(f"worker id {worker_id!r} is already registered at {store.worker_dir(worker_id)}")
    if git(repo_root, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}", check=False).returncode == 0:
        raise ManagerError(f"branch {branch!r} already exists; refusing to reuse an unowned branch")
    if worktree.exists():
        raise ManagerError(f"worktree path already exists: {worktree}")
    herdr = Herdr(workspace)
    if herdr.agent_exists(agent):
        raise ManagerError(f"a live Herdr agent is already named {agent!r}")

    runtime = {"kind": args.kind, "provider": args.provider, "model": args.model, "thinking": args.thinking}
    state = initial_state(
        worker_id, args.ticket_id, args.title or args.ticket_id, runtime, repo_root, common_dir, base,
        branch, worktree, store, workspace, agent,
    )
    tab = ""
    pane = ""
    agent_started = False
    try:
        manifest = snapshot_materials(store.worker_dir(worker_id), sources, worker_id)
        contract_path = store.contract_path(worker_id)
        material_lines = "\n".join(
            f"- `{entry['snapshot']}` (source: `{entry['source']}`)" for entry in manifest["materials"]
        )
        template = SKILL_DIR / "prompts" / "plan-worker.md"
        if not template.is_file():
            raise ManagerError(f"worker contract template is missing: {template}")
        contract = render_contract(
            template,
            {
                "WORKER_ID": worker_id,
                "TICKET_ID": args.ticket_id,
                "TICKET_TITLE": args.title or args.ticket_id,
                "KIND": args.kind,
                "PROVIDER": args.provider,
                "MODEL": args.model,
                "THINKING": args.thinking,
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
        tab, pane = herdr.tab_create(worktree, agent)
        state["herdr"]["tab"] = tab
        state["herdr"]["pane"] = pane
        store.save_state(worker_id, state)

        herdr.agent_start(agent, args.kind, pane, adapter.start_args(args.provider, args.model, args.thinking))
        agent_started = True
        if not herdr.wait_interactive(agent):
            raise ManagerError(f"agent {agent} never reached an interactive state")
        state["lifecycle"]["state"] = "prompting"
        store.save_state(worker_id, state)
        deliver_prompt(
            herdr,
            agent,
            f"You are worker {worker_id} for ticket {args.ticket_id}. Read the contract at {contract_path} "
            "in full and follow it exactly. Do not start work before reading it.",
        )
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
                "ticket_id": args.ticket_id,
                "agent": agent,
                "tab": tab,
                "pane": pane,
                "branch": branch,
                "worktree": str(worktree),
                "base": base,
                "runtime": runtime,
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
            except ManagerError:
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


def cmd_status(args: argparse.Namespace) -> int:
    store = resolve_store(args)
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
                "ticket": state["ticket"],
                "lifecycle": state["lifecycle"]["state"],
                "items": len(state["items"]),
                "branch": state["branch"],
                "worktree": state["worktree"],
            }
        )
    print_json({"management_root": str(store.root), "workers": workers})
    return 0


def cmd_read(args: argparse.Namespace) -> int:
    store = resolve_store(args)
    state = store.load_state(args.worker)
    if state is None:
        raise ManagerError(f"worker {args.worker!r} is not registered under {store.root}")
    herdr = Herdr(state.get("herdr", {}).get("workspace"))
    proc = herdr.agent_read(state["herdr"]["agent"], args.lines)
    if proc.returncode != 0:
        raise ManagerError(f"cannot read agent {state['herdr']['agent']}: {proc.stderr.strip() or proc.stdout.strip()}")
    sys.stdout.write(proc.stdout)
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    store = resolve_store(args)
    state = store.load_state(args.worker)
    if state is None:
        raise ManagerError(f"worker {args.worker!r} is not registered under {store.root}")
    life = state["lifecycle"]["state"]
    if life == "stopped":
        print_json({"stopped": True, "already_stopped": True, "worker_id": args.worker, "lifecycle": life})
        return 0
    if life in TERMINAL_STATES and not pid_alive(state):
        print_json(
            {
                "stopped": True,
                "already_terminal": True,
                "worker_id": args.worker,
                "lifecycle": life,
                "branch": state["branch"],
                "worktree": state["worktree"],
            }
        )
        return 0

    control = store.load_control(args.worker)
    control.update({"stop_requested_at": utc_now(), "reason": args.reason or "stop requested"})
    store.save_control(args.worker, control)

    herdr = Herdr(state.get("herdr", {}).get("workspace"))
    agent = state["herdr"]["agent"]
    adapter = get_adapter(state["runtime"]["kind"])
    rc, status = herdr.agent_status(agent)
    if rc == 0 and status in {"working", "blocked"}:
        for keys in adapter.interrupt_sequences():
            herdr.send_keys(agent, keys)
            rc, status = herdr.wait_settled(agent, timeout=10)
            if rc != 0 or status in SETTLED_STATES:
                break

    if pid_alive(state):
        deadline = time.monotonic() + DEFAULT_STOP_WAIT
        current = state
        while time.monotonic() < deadline:
            current = store.load_state(args.worker) or current
            if current["supervisor"].get("state") == "exited" or current["lifecycle"]["state"] in TERMINAL_STATES:
                break
            time.sleep(0.25)
        current = store.load_state(args.worker) or current
        if pid_alive(current) and current["lifecycle"]["state"] not in TERMINAL_STATES:
            print_json(
                {
                    "error": "stop-incomplete",
                    "message": "supervisor did not exit; scene retained for inspection",
                    "worker_id": args.worker,
                    "worktree": current["worktree"],
                },
                stream=sys.stderr,
            )
            return 3
        state = current
    else:
        rc, status = herdr.agent_status(agent)
        if rc == 0 and status == "working":
            for keys in adapter.interrupt_sequences():
                herdr.send_keys(agent, keys)
                rc, status = herdr.wait_settled(agent, timeout=10)
                if rc != 0 or status in SETTLED_STATES:
                    break
        if state["lifecycle"]["state"] not in TERMINAL_STATES:
            finalize(state, "stopped", "supervisor-missing-at-stop")
            store.save_state(args.worker, state)

    print_json(
        {
            "stopped": True,
            "worker_id": args.worker,
            "lifecycle": state["lifecycle"]["state"],
            "reason": state["lifecycle"].get("reason"),
            "branch": state["branch"],
            "worktree": state["worktree"],
            "result_path": state["paths"]["result"],
            "items": len(state["items"]),
        }
    )
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="plan_manager.py",
        description="Minimal single-worker lifecycle manager for Herdr coding agents (ticket 02).",
        epilog=(
            "Example:\n"
            "  plan_manager.py start --repo /repo --ticket-id 02 --base <full-sha> "
            "--material /path/spec.md --kind pi --provider <p> --model <m> --thinking <t>\n"
            "  plan_manager.py status --repo /repo --worker <worker-id>\n"
            "  plan_manager.py read --repo /repo --worker <worker-id>\n"
            "  plan_manager.py stop --repo /repo --worker <worker-id>"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser("start", help="start one isolated worker from an explicit base SHA")
    start.add_argument("--repo", required=True, help="path to the target repository")
    start.add_argument("--ticket-id", required=True)
    start.add_argument("--title", default="")
    start.add_argument("--base", required=True, help="full commit SHA the worker branch starts from")
    start.add_argument("--material", action="append", required=True, help="ticket material file or directory (repeatable)")
    start.add_argument("--kind", required=True, choices=["pi"], help="worker runtime; opencode is added by ticket 03")
    start.add_argument("--provider", required=True)
    start.add_argument("--model", required=True)
    start.add_argument("--thinking", required=True)
    start.add_argument("--worker-id", default="")
    start.add_argument("--branch", default="")
    start.add_argument("--worktree", default="")
    start.add_argument("--management-root", default="")
    start.add_argument("--instructions", default="", help="optional extra task instructions for the worker contract")
    start.set_defaults(func=cmd_start)

    status = sub.add_parser("status", help="show execution facts for one or all workers")
    status.add_argument("--repo", default="")
    status.add_argument("--management-root", default="")
    status.add_argument("--worker", default="")
    status.set_defaults(func=cmd_status)

    read = sub.add_parser("read", help="read the worker's recent terminal output")
    read.add_argument("--repo", default="")
    read.add_argument("--management-root", default="")
    read.add_argument("--worker", required=True)
    read.add_argument("--lines", type=int, default=120)
    read.set_defaults(func=cmd_read)

    stop = sub.add_parser("stop", help="stop business execution and supervision, retaining the scene")
    stop.add_argument("--repo", default="")
    stop.add_argument("--management-root", default="")
    stop.add_argument("--worker", required=True)
    stop.add_argument("--reason", default="")
    stop.set_defaults(func=cmd_stop)

    supervise = sub.add_parser("_supervise", help=argparse.SUPPRESS)
    supervise.add_argument("--management-root", required=True)
    supervise.add_argument("--worker", required=True)
    supervise.set_defaults(func=cmd_supervise)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return args.func(args)
    except ManagerError as exc:
        print_json({"error": "manager-error", "message": str(exc)}, stream=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
