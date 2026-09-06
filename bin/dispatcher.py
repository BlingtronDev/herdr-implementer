#!/usr/bin/env python3
"""Execute one explicit ticket batch in isolated Herdr agent sessions.

The calling agent owns dependency planning and merges. This program owns worker
launching, the batch barrier, context monitoring, stale cleanup, and factual
result summaries.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from typing import Any
import uuid

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
SELECTION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+@-]*$")
TICKET_FILE_RE = re.compile(r"^(\d{2}[a-z]?)-([a-z0-9][a-z0-9-]*)\.md$")
TITLE_RE = re.compile(r"^#\s*(\d{2}[a-z]?)\s*(?::|—)\s*(.+?)\s*$", re.MULTILINE)
FIELD_RE = re.compile(r"^\s*(?:\*\*)?(Blocked by|Status|Likely touches)\s*:\s*(?:\*\*)?\s*(.*?)\s*$", re.IGNORECASE | re.MULTILINE)
BLOCKER_RE = re.compile(r"(?<![A-Za-z0-9])#?(\d{2}[a-z]?)(?![A-Za-z0-9])")
CHECKBOX_RE = re.compile(r"^\s*-\s*\[([ xX])\]\s*(.+?)\s*$", re.MULTILINE)
SHA_RE = re.compile(r"\b[0-9a-f]{40,64}\b")
VALID_STATUSES = {"ready-for-agent", "claimed", "completed", "failed"}
STATUS_ALIASES = {"resolved": "completed", "done": "completed"}
SUPPORTED_KINDS = {"opencode", "codex", "pi"}
THINKING_LEVELS = ("off", "minimal", "low", "medium", "high", "xhigh", "max")
WAIT_TIMEOUT_MS = 3_600_000
MIN_BATCH_TIMEOUT_MINUTES = 120
PROMPT_RETRIES = 3
PROMPT_RETRY_BASE_DELAY = 1.0
READY_POLL_ATTEMPTS = 240
READY_POLL_DELAY = 0.5


class DispatchError(RuntimeError):
    pass


@dataclass(frozen=True)
class Ticket:
    number: str
    slug: str
    path: Path
    title: str
    body: str
    blockers: tuple[str, ...]
    status: str
    acceptance: tuple[str, ...]
    likely_touches: tuple[str, ...]
    force_serial: bool


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def command(
    args: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    timeout: float | None = 60,
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        args,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=timeout,
    )
    if check and proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip()
        raise DispatchError(f"command failed ({proc.returncode}): {' '.join(args)}\n{detail}")
    return proc


def herdr_error_code(proc: subprocess.CompletedProcess[str]) -> str | None:
    return error_code_from_output(proc.stdout, proc.stderr)


def error_code_from_output(stdout: str, stderr: str) -> str | None:
    for raw in (stderr, stdout):
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict) and isinstance(error.get("code"), str):
            return error["code"]
    return None


def start_agent_when_shell_ready(
    args: list[str],
    *,
    attempts: int = 40,
    delay: float = 0.25,
) -> subprocess.CompletedProcess[str]:
    last: subprocess.CompletedProcess[str] | None = None
    for index in range(attempts):
        last = command(args, check=False, timeout=45)
        if last.returncode == 0 or herdr_error_code(last) != "agent_pane_busy":
            return last
        if index + 1 < attempts:
            time.sleep(delay)
    assert last is not None
    return last


def json_command(args: list[str], *, cwd: Path | None = None, accept: tuple[int, ...] = (0,)) -> dict[str, Any]:
    proc = command(args, cwd=cwd, check=False)
    if proc.returncode not in accept:
        raise DispatchError(f"command failed ({proc.returncode}): {' '.join(args)}\n{proc.stderr.strip()}")
    try:
        value = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise DispatchError(f"command returned invalid JSON: {' '.join(args)}: {exc}") from exc
    if not isinstance(value, dict):
        raise DispatchError(f"command returned non-object JSON: {' '.join(args)}")
    return value


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
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def contained(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def field_values(body: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for match in FIELD_RE.finditer(body):
        key = match.group(1).lower()
        if key in values:
            raise DispatchError(f"duplicate field {match.group(1)!r}")
        values[key] = match.group(2)
    return values


def parse_ticket(path: Path, feature_slug: str) -> Ticket:
    match = TICKET_FILE_RE.fullmatch(path.name)
    if not match:
        raise DispatchError(f"invalid ticket filename: {path.name}")
    number, ticket_slug = match.groups()
    body = path.read_text(encoding="utf-8")
    title_match = TITLE_RE.search(body)
    if not title_match:
        raise DispatchError(f"{path}: missing '# NN: title' heading")
    if title_match.group(1) != number:
        raise DispatchError(f"{path}: filename number {number} != heading number {title_match.group(1)}")
    values = field_values(body)
    if "blocked by" not in values or "status" not in values:
        raise DispatchError(f"{path}: Blocked by and Status are required")

    raw_status = values["status"].strip().lower()
    status = STATUS_ALIASES.get(raw_status, raw_status)
    if status not in VALID_STATUSES:
        raise DispatchError(f"{path}: unknown Status {values['status']!r}; ask the user")

    blocked_text = values["blocked by"].strip()
    blockers: list[str] = []
    if not re.match(r"^none\b", blocked_text, re.IGNORECASE):
        blockers = BLOCKER_RE.findall(blocked_text)
        if not blockers:
            raise DispatchError(f"{path}: Blocked by has text but no explicit ticket number: {blocked_text!r}")
        if len(blockers) != len(set(blockers)):
            raise DispatchError(f"{path}: duplicate dependency in Blocked by: {blocked_text!r}")
        cross = re.findall(r"\.scratch/([a-z0-9][a-z0-9-]*)/issues/\d{2}", blocked_text)
        if any(slug != feature_slug for slug in cross):
            raise DispatchError(f"{path}: cross-slug dependency is not allowed: {blocked_text!r}")

    acceptance = tuple(item.strip() for _, item in CHECKBOX_RE.findall(body))
    if not acceptance:
        raise DispatchError(f"{path}: at least one acceptance checkbox is required")
    touches_text = values.get("likely touches", "")
    force_serial = bool(re.search(r"不可并行|non[- ]parallel|serialize", touches_text, re.IGNORECASE))
    likely_touches = tuple(
        cleaned
        for item in re.split(r"[,;]", touches_text)
        if (cleaned := re.sub(r"\([^)]*(?:不可并行|non[- ]parallel|serialize)[^)]*\)", "", item, flags=re.IGNORECASE).strip())
    )
    return Ticket(
        number=number,
        slug=ticket_slug,
        path=path,
        title=title_match.group(2).strip(),
        body=body,
        blockers=tuple(blockers),
        status=status,
        acceptance=acceptance,
        likely_touches=likely_touches,
        force_serial=force_serial,
    )


def discover_groups(repo: Path) -> dict[str, list[Path]]:
    scratch = repo / ".scratch"
    groups: dict[str, list[Path]] = {}
    if not scratch.is_dir():
        return groups
    for slug_dir in sorted(scratch.iterdir()):
        if not slug_dir.is_dir() or not SLUG_RE.fullmatch(slug_dir.name):
            continue
        issue_dir = slug_dir / "issues"
        paths = sorted(path for path in issue_dir.glob("*.md") if path.is_file()) if issue_dir.is_dir() else []
        if paths:
            groups[slug_dir.name] = paths
    return groups


def load_tickets(repo: Path, slug: str) -> dict[str, Ticket]:
    if not SLUG_RE.fullmatch(slug):
        raise DispatchError("slug must match [a-z0-9][a-z0-9-]*")
    issue_dir = (repo / ".scratch" / slug / "issues").resolve()
    expected_parent = (repo / ".scratch" / slug).resolve()
    if not contained(issue_dir, expected_parent) or not issue_dir.is_dir():
        raise DispatchError(f"ticket directory not found or escaped containment: {issue_dir}")
    tickets: dict[str, Ticket] = {}
    for path in sorted(issue_dir.glob("*.md")):
        resolved = path.resolve()
        if not contained(resolved, issue_dir):
            raise DispatchError(f"ticket path escapes issue directory: {path}")
        ticket = parse_ticket(resolved, slug)
        if ticket.number in tickets:
            raise DispatchError(f"duplicate ticket number {ticket.number}")
        tickets[ticket.number] = ticket
    if not tickets:
        raise DispatchError(f"no tickets found for slug {slug}")
    validate_graph(tickets)
    return tickets


def validate_graph(tickets: dict[str, Ticket]) -> None:
    for number, ticket in tickets.items():
        for blocker in ticket.blockers:
            if blocker == number:
                raise DispatchError(f"ticket {number} depends on itself")
            if blocker not in tickets:
                raise DispatchError(f"ticket {number} depends on missing ticket {blocker}")

    visiting: list[str] = []
    visited: set[str] = set()

    def visit(number: str) -> None:
        if number in visiting:
            index = visiting.index(number)
            cycle = visiting[index:] + [number]
            raise DispatchError("dependency cycle: " + " -> ".join(cycle))
        if number in visited:
            return
        visiting.append(number)
        for blocker in tickets[number].blockers:
            visit(blocker)
        visiting.pop()
        visited.add(number)

    for number in sorted(tickets):
        visit(number)


def normalize_batch(raw: str) -> list[str]:
    """Parse an explicit batch while preserving the caller's order."""
    values: list[str] = []
    seen: set[str] = set()
    for token in re.split(r"[\s,]+", raw.strip()):
        if not token:
            continue
        match = re.fullmatch(r"[tT]?(\d{1,2})([a-z]?)", token)
        if not match:
            raise DispatchError(f"invalid ticket in --batch: {token!r}")
        number = f"{int(match.group(1)):02d}{match.group(2)}"
        if number in seen:
            raise DispatchError(f"duplicate ticket in --batch: {token!r}")
        seen.add(number)
        values.append(number)
    if not values:
        raise DispatchError("--batch must name at least one ticket")
    return values


def update_ticket(path: Path, status: str, comment: dict[str, Any] | None = None) -> None:
    if status not in VALID_STATUSES:
        raise DispatchError(f"refusing to write invalid ticket status {status}")
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(
        r"^(?P<indent>\s*)(?P<open>\*\*)?Status\s*:\s*(?P<close>\*\*)?.*$",
        re.IGNORECASE | re.MULTILINE,
    )
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise DispatchError(f"{path}: expected exactly one Status line")
    match = matches[0]
    replacement = f"{match.group('indent')}{match.group('open') or ''}Status:"
    if match.group('close'):
        replacement += "**"
    replacement += f" {status}"
    text = text[:match.start()] + replacement + text[match.end():]
    if comment is not None:
        record = "- `" + json.dumps(comment, ensure_ascii=False, separators=(",", ":")) + "`"
        heading = re.search(r"^##\s+Comments\s*$", text, re.IGNORECASE | re.MULTILINE)
        if heading:
            following = re.search(r"^##\s+", text[heading.end():], re.MULTILINE)
            insert = heading.end() + (following.start() if following else len(text[heading.end():]))
            prefix = text[:insert].rstrip()
            suffix = text[insert:]
            text = prefix + "\n" + record + "\n" + suffix.lstrip("\n")
        else:
            text = text.rstrip() + "\n\n## Comments\n\n" + record + "\n"
    atomic_text(path, text)


def repo_context(start: Path, target_assertion: str | None) -> tuple[Path, Path, str, str]:
    root = Path(command(["git", "rev-parse", "--show-toplevel"], cwd=start).stdout.strip()).resolve()
    common_raw = command(["git", "rev-parse", "--git-common-dir"], cwd=root).stdout.strip()
    git_raw = command(["git", "rev-parse", "--git-dir"], cwd=root).stdout.strip()
    common = (root / common_raw).resolve() if not Path(common_raw).is_absolute() else Path(common_raw).resolve()
    git_dir = (root / git_raw).resolve() if not Path(git_raw).is_absolute() else Path(git_raw).resolve()
    if git_dir != common:
        raise DispatchError("dispatcher must start from the repository's main worktree")
    branch = command(["git", "symbolic-ref", "--quiet", "--short", "HEAD"], cwd=root, check=False)
    if branch.returncode != 0 or not branch.stdout.strip():
        raise DispatchError("detached HEAD is not supported")
    branch_name = branch.stdout.strip()
    if target_assertion and target_assertion != branch_name:
        raise DispatchError(f"--target asserted {target_assertion!r}, but current branch is {branch_name!r}")
    head = command(["git", "rev-parse", "HEAD"], cwd=root).stdout.strip()
    return root, common, branch_name, head


def protected_worktree_status(repo: Path, slug: str) -> str:
    if not SLUG_RE.fullmatch(slug):
        raise DispatchError("slug must match [a-z0-9][a-z0-9-]*")
    return command([
        "git", "status", "--porcelain=v1", "--untracked-files=all", "--", ".",
        f":(top,exclude).scratch/{slug}/**",
    ], cwd=repo).stdout


def validate_worktree_ignore(repo: Path) -> None:
    probe = ".worktrees/__herdr_probe__"
    tracked = command(["git", "ls-files", "--", ".worktrees"], cwd=repo).stdout.strip()
    if tracked:
        raise DispatchError(".worktrees contains tracked paths")
    proc = command(["git", "check-ignore", "-v", "--no-index", "--", probe], cwd=repo, check=False)
    if proc.returncode != 0:
        raise DispatchError("repository .gitignore must cover .worktrees/")
    source = proc.stdout.split(":", 1)[0]
    source_path = (repo / source).resolve() if not Path(source).is_absolute() else Path(source).resolve()
    if not contained(source_path, repo) or source_path.name != ".gitignore":
        raise DispatchError(f".worktrees is ignored only by a non-repository source: {source}")


def acquire_lock(common: Path, slug: str) -> tuple[Any, Path]:
    lock_path = common / "herdr-ticket-dispatcher" / slug / "dispatcher.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    stream = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        stream.seek(0)
        holder = stream.read().strip() or "unknown holder"
        stream.close()
        raise DispatchError(f"dispatcher lock is held: {holder}") from exc
    stream.seek(0)
    stream.truncate()
    stream.write(json.dumps({"pid": os.getpid(), "host": os.uname().nodename, "started_at": utc_now()}))
    stream.flush()
    os.fsync(stream.fileno())
    return stream, lock_path


def inspect_cli(run_dir: Path) -> set[str]:
    help_dir = run_dir / "cli-help"
    help_dir.mkdir(parents=True, exist_ok=True)
    outputs: dict[str, str] = {}
    for label, args in {
        "herdr": ["herdr", "--help"],
        "agent": ["herdr", "agent"],
        "tab": ["herdr", "tab"],
        "pane": ["herdr", "pane"],
        "notification": ["herdr", "notification"],
    }.items():
        proc = command(args, check=False)
        outputs[label] = proc.stdout + proc.stderr
        atomic_text(help_dir / f"{label}.txt", outputs[label])
    kinds_match = re.search(r"^\s*kinds:\s*(.+)$", outputs["agent"], re.MULTILINE)
    if not kinds_match:
        raise DispatchError("cannot discover Herdr agent kinds from current CLI help")
    return set(kinds_match.group(1).strip().split("|"))


def agent_list() -> list[dict[str, Any]]:
    payload = json_command(["herdr", "agent", "list"])
    agents = ((payload.get("result") or {}).get("agents") or [])
    return agents if isinstance(agents, list) else []


def parse_pi_catalog(output: str) -> dict[str, list[str]]:
    catalog: dict[str, list[str]] = defaultdict(list)
    for line in output.splitlines():
        columns = line.split()
        if len(columns) < 2 or columns[0].lower() == "provider":
            continue
        provider, model = columns[0], columns[1]
        if SELECTION_RE.fullmatch(provider) and SELECTION_RE.fullmatch(model):
            catalog[provider].append(model)
    return {provider: sorted(set(models)) for provider, models in catalog.items()}


def parse_opencode_catalog(output: str) -> dict[str, list[str]]:
    catalog: dict[str, list[str]] = defaultdict(list)
    for raw in output.splitlines():
        value = raw.strip()
        if "/" not in value:
            continue
        provider, model = value.split("/", 1)
        if SELECTION_RE.fullmatch(provider) and SELECTION_RE.fullmatch(model):
            catalog[provider].append(model)
    return {provider: sorted(set(models)) for provider, models in catalog.items()}


def parse_codex_catalog(output: str) -> dict[str, list[str]]:
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as exc:
        raise DispatchError(f"cannot parse Codex model catalog: {exc}") from exc
    models = []
    for item in payload.get("models") or []:
        slug = item.get("slug")
        if (
            isinstance(slug, str)
            and SELECTION_RE.fullmatch(slug)
            and item.get("visibility", "list") == "list"
            and item.get("supported_in_api", True) is not False
        ):
            models.append(slug)
    if not models:
        raise DispatchError("Codex returned an empty bundled model catalog")
    # Current Codex exposes a provider-independent model catalog. V1 only offers
    # its enumerable built-in OpenAI provider; custom/local providers fail closed.
    return {"openai": sorted(set(models))}


def discover_model_catalog(kind: str, provider_filter: str | None = None) -> dict[str, list[str]]:
    if provider_filter and not SELECTION_RE.fullmatch(provider_filter):
        raise DispatchError("provider contains unsupported characters")
    if kind == "pi":
        args = ["pi", "--list-models"]
        if provider_filter:
            args.append(provider_filter)
        proc = command(args, check=False, timeout=60)
        if proc.returncode != 0:
            raise DispatchError(f"Pi model discovery failed: {proc.stderr.strip()}")
        catalog = parse_pi_catalog(proc.stdout)
    elif kind == "opencode":
        args = ["opencode", "models"]
        if provider_filter:
            args.append(provider_filter)
        proc = command(args, check=False, timeout=60)
        if proc.returncode != 0:
            raise DispatchError(f"OpenCode model discovery failed: {proc.stderr.strip()}")
        catalog = parse_opencode_catalog(proc.stdout)
    elif kind == "codex":
        if provider_filter and provider_filter != "openai":
            raise DispatchError("Codex V1 can enumerate models only for provider 'openai'")
        proc = command(["codex", "debug", "models", "--bundled"], check=False, timeout=60)
        if proc.returncode != 0:
            raise DispatchError(f"Codex model discovery failed: {proc.stderr.strip()}")
        catalog = parse_codex_catalog(proc.stdout)
    else:
        raise DispatchError(f"unsupported worker kind: {kind}")
    if provider_filter:
        catalog = {provider_filter: catalog.get(provider_filter, [])}
    if not catalog or (provider_filter and not catalog.get(provider_filter)):
        scope = f" provider {provider_filter!r}" if provider_filter else ""
        raise DispatchError(f"{kind} returned no selectable providers/models for{scope}")
    return catalog


def native_model_args(kind: str, provider: str, model: str, thinking: str) -> list[str]:
    if not SELECTION_RE.fullmatch(provider) or not SELECTION_RE.fullmatch(model):
        raise DispatchError("provider/model contains unsupported characters")
    if thinking not in THINKING_LEVELS:
        raise DispatchError(f"unsupported thinking level: {thinking}")
    if kind == "pi":
        return ["--provider", provider, "--model", model, "--thinking", thinking]
    if kind == "opencode":
        # The opencode TUI (root command) does not support --variant; the model
        # variant is applied through the worktree's opencode.json instead
        # (see ensure_opencode_variant_config).
        return ["--model", f"{provider}/{model}"]
    if kind == "codex":
        return [
            "-c", f'model_provider="{provider}"',
            "-c", f'model_reasoning_effort="{thinking}"',
            "--model", model,
        ]
    raise DispatchError(f"unsupported worker kind: {kind}")


def ensure_opencode_variant_config(worktree: str, provider: str, model: str, thinking: str) -> None:
    """Apply the confirmed thinking level as the opencode TUI default variant.

    The opencode TUI root command does not accept --variant, but its config
    schema supports agent.build.{model,variant}. Write (or merge into) the
    worktree-local opencode.json and keep it out of the worker's git status
    via the worktree-private exclude file.
    """
    root = Path(worktree)
    config_path = root / "opencode.json"
    payload: dict[str, Any] = {}
    if config_path.is_file():
        try:
            loaded = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise DispatchError(f"{config_path}: existing opencode config is not valid JSON: {exc}") from exc
        if not isinstance(loaded, dict):
            raise DispatchError(f"{config_path}: existing opencode config is not a JSON object")
        payload = loaded
    agent_section = payload.get("agent")
    if agent_section is None:
        agent_section = {}
    if not isinstance(agent_section, dict):
        raise DispatchError(f"{config_path}: 'agent' must be an object")
    build = agent_section.get("build")
    if build is None:
        build = {}
    if not isinstance(build, dict):
        raise DispatchError(f"{config_path}: 'agent.build' must be an object")
    build["model"] = f"{provider}/{model}"
    build["variant"] = thinking
    agent_section["build"] = build
    payload["agent"] = agent_section
    # Workers run unattended; auto-approve tool permissions so a batch never
    # stalls on the TUI approval prompt (explicit "deny" rules still apply).
    payload["permission"] = "allow"
    payload.setdefault("$schema", "https://opencode.ai/config.json")
    atomic_text(config_path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    git_dir = command(["git", "rev-parse", "--git-dir"], cwd=root).stdout.strip()
    exclude_dir = Path(git_dir) / "info"
    exclude_dir.mkdir(parents=True, exist_ok=True)
    exclude = exclude_dir / "exclude"
    existing = exclude.read_text(encoding="utf-8") if exclude.is_file() else ""
    if not any(line.strip() == "/opencode.json" for line in existing.splitlines()):
        with exclude.open("a", encoding="utf-8") as stream:
            stream.write("/opencode.json\n")


def validate_selection(kind: str, provider: str, model: str, installed: set[str]) -> None:
    if kind not in SUPPORTED_KINDS:
        raise DispatchError(f"unsupported context adapter kind: {kind}")
    if kind not in installed:
        raise DispatchError(f"Herdr reports kind {kind!r} is unsupported; no fallback is allowed")
    providers = discover_model_catalog(kind)
    if provider not in providers:
        raise DispatchError(f"provider {provider!r} is not selectable for kind {kind}; discovered: {', '.join(sorted(providers))}")
    catalog = discover_model_catalog(kind, provider)
    if model not in catalog[provider]:
        raise DispatchError(f"model {model!r} is not listed under {kind}/{provider}")
    if kind == "opencode":
        if not (Path.home() / ".local/share/opencode/opencode.db").is_file():
            raise DispatchError("opencode adapter requires its readable SQLite database")
        if sqlite_unavailable():
            raise DispatchError("Python sqlite3 support is required for opencode")


def sqlite_unavailable() -> bool:
    try:
        import sqlite3  # noqa: F401
        return False
    except ImportError:
        return True


def create_state(
    run_id: str,
    batch_number: int,
    slug: str,
    repo: Path,
    common: Path,
    branch: str,
    base_head: str,
    protected_status: str,
    kind: str,
    provider: str,
    model: str,
    thinking: str,
    jobs: int,
    fail_fast: bool,
    context_window: int | None,
    batch_timeout_minutes: int | None,
    tickets: dict[str, Ticket],
    skipped: set[str],
) -> dict[str, Any]:
    created = utc_now()
    return {
        "version": 4,
        "run_id": run_id,
        "batch_number": batch_number,
        "batch_tickets": list(tickets),
        "slug": slug,
        "repo_root": str(repo),
        "git_common_dir": str(common),
        "target_branch": branch,
        "base_head": base_head,
        "protected_status": protected_status,
        "kind": kind,
        "provider": provider,
        "model": model,
        "thinking": thinking,
        "jobs": jobs,
        "fail_fast": fail_fast,
        "context_window": context_window,
        "batch_timeout_minutes": batch_timeout_minutes,
        "phase": "preflight",
        "created_at": created,
        "updated_at": created,
        "tickets": {
            number: {
                "path": str(ticket.path),
                "status": "skipped" if number in skipped else ticket.status,
                "attempts": [],
            }
            for number, ticket in tickets.items()
        },
    }


def save_state(path: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = utc_now()
    atomic_json(path, state)


def summarize_groups(repo: Path, groups: dict[str, list[Path]]) -> None:
    print("Available ticket groups:")
    for slug, paths in groups.items():
        counts = defaultdict(int)
        for path in paths:
            try:
                counts[parse_ticket(path, slug).status] += 1
            except DispatchError:
                counts["invalid"] += 1
        print(f"  {slug}: " + ", ".join(f"{key}={counts[key]}" for key in sorted(counts)))


def previous_claimed_attempt(common: Path, feature_slug: str, ticket: Ticket) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    run_root = common / "herdr-ticket-dispatcher" / feature_slug / "runs"
    for state_path in sorted(run_root.glob("*/state.json"), reverse=True):
        try:
            old_state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if old_state.get("slug") != feature_slug:
            continue
        ticket_state = (old_state.get("tickets") or {}).get(ticket.number) or {}
        if ticket_state.get("status") != "claimed":
            continue
        for attempt in reversed(ticket_state.get("attempts") or []):
            if attempt.get("state") not in {"allocating", "prompting", "running", "stopping", "needs-input"}:
                continue
            if all(attempt.get(key) for key in ("attempt", "agent", "pane", "branch", "worktree")):
                return state_path, old_state, attempt
    raise DispatchError(f"claimed ticket {ticket.number} has no verifiable previous attempt state")


def registered_worktrees(repo: Path) -> dict[Path, str | None]:
    output = command(["git", "worktree", "list", "--porcelain"], cwd=repo).stdout
    records: dict[Path, str | None] = {}
    path: Path | None = None
    branch: str | None = None
    for line in output.splitlines() + [""]:
        if line.startswith("worktree "):
            path = Path(line.removeprefix("worktree ")).resolve()
            branch = None
        elif line.startswith("branch "):
            branch = line.removeprefix("branch refs/heads/")
        elif not line and path is not None:
            records[path] = branch
            path = None
            branch = None
    return records


def abandon_claimed_attempt(repo: Path, common: Path, feature_slug: str, ticket: Ticket) -> None:
    state_path, old_state, attempt = previous_claimed_attempt(common, feature_slug, ticket)
    attempt_no = int(attempt["attempt"])
    expected_branch = f"ticket/{ticket.number}-{ticket.slug}/a{attempt_no}"
    expected_worktree = (repo / ".worktrees" / feature_slug / f"{ticket.number}-{ticket.slug}" / f"a{attempt_no}").resolve()
    if attempt["branch"] != expected_branch or Path(attempt["worktree"]).resolve() != expected_worktree:
        raise DispatchError(f"claimed ticket {ticket.number} previous branch/worktree failed containment verification")

    worktrees = registered_worktrees(repo)
    if expected_worktree in worktrees and worktrees[expected_worktree] != expected_branch:
        raise DispatchError(f"claimed ticket {ticket.number} worktree is attached to an unexpected branch")
    if expected_worktree.exists() and expected_worktree not in worktrees:
        raise DispatchError(f"claimed ticket {ticket.number} path exists but is not the recorded Git worktree")

    get = command(["herdr", "agent", "get", attempt["agent"]], check=False)
    if get.returncode == 0:
        try:
            live = json.loads(get.stdout)
        except json.JSONDecodeError as exc:
            raise DispatchError(f"cannot verify old worker {attempt['agent']}: invalid Herdr JSON") from exc
        live_text = json.dumps(live)
        if attempt["pane"] not in live_text:
            raise DispatchError(f"old worker {attempt['agent']} is live in an unexpected pane")
        expected_ref = attempt.get("context_ref")
        live_ref = find_agent_session(live)
        if expected_ref and live_ref != expected_ref:
            raise DispatchError(f"old worker {attempt['agent']} has an unexpected session reference")
        if not stop_agent(attempt):
            raise DispatchError(f"old worker {attempt['agent']} could not be stopped")

    if attempt.get("tab"):
        command(["herdr", "tab", "close", attempt["tab"]], check=False)
    else:
        # Compatibility for attempts created before worker-per-tab became mandatory.
        command(["herdr", "pane", "close", attempt["pane"]], check=False)
    if expected_worktree in worktrees:
        removed = command(["git", "worktree", "remove", "--force", "--", str(expected_worktree)], cwd=repo, check=False)
        if removed.returncode != 0:
            raise DispatchError(f"cannot remove abandoned worktree for ticket {ticket.number}: {removed.stderr.strip()}")
    branch_exists = command(["git", "show-ref", "--verify", "--quiet", f"refs/heads/{expected_branch}"], cwd=repo, check=False).returncode == 0
    if branch_exists:
        deleted = command(["git", "branch", "-D", "--", expected_branch], cwd=repo, check=False)
        if deleted.returncode != 0:
            raise DispatchError(f"cannot delete abandoned branch for ticket {ticket.number}: {deleted.stderr.strip()}")

    attempt["state"] = "abandoned"
    attempt["reason"] = "dispatcher-restart"
    old_state["tickets"][ticket.number]["status"] = "ready-for-agent"
    old_state["updated_at"] = utc_now()
    atomic_json(state_path, old_state)
    update_ticket(ticket.path, "ready-for-agent", {
        "run_id": old_state.get("run_id"),
        "attempt": attempt_no,
        "reason": "abandoned-after-dispatcher-restart",
        "branch_deleted": expected_branch,
        "at": utc_now(),
    })


def resolve_claimed(
    repo: Path,
    common: Path,
    feature_slug: str,
    tickets: dict[str, Ticket],
    skip_claimed: bool,
) -> set[str]:
    claimed = [number for number, ticket in tickets.items() if ticket.status == "claimed"]
    if skip_claimed:
        return set(claimed)
    for number in claimed:
        abandon_claimed_attempt(repo, common, feature_slug, tickets[number])
    return set()


def short_agent_name(ticket: Ticket, attempt: int, live_names: set[str], rollover: int = 0) -> str:
    base_slug = re.sub(r"[^a-z0-9]", "", ticket.slug.lower()) or "ticket"
    suffix = f"-a{attempt}" + (f"-r{rollover}" if rollover else "")
    prefix = f"t{ticket.number}-{base_slug}"
    base = prefix[:32 - len(suffix)] + suffix
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", base):
        raise DispatchError(f"cannot normalize worker name for ticket {ticket.number}")
    if base in live_names:
        raise DispatchError(f"live Herdr agent name collision: {base}")
    return base


def extract_tab_and_root_pane(payload: dict[str, Any]) -> tuple[str, str]:
    result = payload.get("result") or {}
    tab = result.get("tab")
    pane = result.get("root_pane")
    tab_id = tab if isinstance(tab, str) else (tab or {}).get("tab_id")
    pane_id = pane if isinstance(pane, str) else (pane or {}).get("pane_id")
    if not isinstance(tab_id, str) or not isinstance(pane_id, str):
        raise DispatchError("tab create response has no result.tab/result.root_pane IDs")
    return tab_id, pane_id


def find_agent_session(payload: dict[str, Any]) -> str | None:
    def walk(value: Any) -> str | None:
        if isinstance(value, dict):
            session = value.get("agent_session")
            if isinstance(session, dict) and isinstance(session.get("value"), str):
                return session["value"]
            for child in value.values():
                found = walk(child)
                if found:
                    return found
        elif isinstance(value, list):
            for child in value:
                found = walk(child)
                if found:
                    return found
        return None

    return walk(payload)


def wait_agent_session(agent: str, *, attempts: int = 40, delay: float = 0.5) -> str | None:
    """Poll for the agent session id; freshly started agents register it asynchronously."""
    for index in range(attempts):
        try:
            get = json_command(["herdr", "agent", "get", agent])
        except DispatchError:
            get = {}
        context_ref = find_agent_session(get)
        if context_ref:
            return context_ref
        listed = next(
            (
                item
                for item in agent_list()
                if item.get("agent") == agent or item.get("agent_name") == agent or item.get("name") == agent
            ),
            None,
        )
        context_ref = find_agent_session(listed or {})
        if context_ref:
            return context_ref
        if index + 1 < attempts:
            time.sleep(delay)
    return None


def ensure_context_ref(state_path: Path, state: dict[str, Any], attempt: dict[str, Any]) -> None:
    """Backfill the worker session id; opencode only creates it after the first prompt."""
    if attempt.get("context_ref"):
        return
    ref = wait_agent_session(attempt["agent"], attempts=30, delay=1.0)
    if ref:
        attempt["context_ref"] = ref
        save_state(state_path, state)

def historical_attempt_max(common: Path, feature_slug: str, ticket_number: str) -> int:
    maximum = 0
    run_root = common / "herdr-ticket-dispatcher" / feature_slug / "runs"
    for state_path in run_root.glob("*/state.json"):
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        ticket_state = (state.get("tickets") or {}).get(ticket_number) or {}
        for attempt in ticket_state.get("attempts") or []:
            try:
                maximum = max(maximum, int(attempt.get("attempt", 0)))
            except (TypeError, ValueError):
                continue
    return maximum


def next_attempt(
    repo: Path,
    common: Path,
    worktree_root: Path,
    feature_slug: str,
    ticket: Ticket,
    attempts: list[dict[str, Any]],
) -> int:
    number = max(
        historical_attempt_max(common, feature_slug, ticket.number),
        max([int(item.get("attempt", 0)) for item in attempts] + [0]),
    ) + 1
    while True:
        branch = f"ticket/{ticket.number}-{ticket.slug}/a{number}"
        worktree = worktree_root / feature_slug / f"{ticket.number}-{ticket.slug}" / f"a{number}"
        branch_exists = command(["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], cwd=repo, check=False).returncode == 0
        if not branch_exists and not worktree.exists():
            return number
        number += 1


def render_worker_prompt(template: str, ticket: Ticket, attempt: dict[str, Any]) -> str:
    values = {
        "AGENT_NAME": attempt["agent"],
        "TICKET_ID": ticket.number,
        "TICKET_TITLE": ticket.title,
        "BRANCH": attempt["branch"],
        "KIND": attempt["kind"],
        "PROVIDER": attempt["provider"],
        "MODEL": attempt["model"],
        "THINKING": attempt["thinking"],
        "RESULT_PATH": attempt["result_path"],
        "TICKET_BASE_SHA": attempt["ticket_base_sha"],
        "CONTROL_PATH": f".scratch/{ticket.path.parent.parent.name}/",
        "HANDOFF_DIR": attempt["handoff_dir"],
        "TICKET_BODY": ticket.body,
    }
    for key, value in values.items():
        template = template.replace("{{" + key + "}}", str(value))
    return template


def handoff_directory(state: dict[str, Any], ticket: Ticket, attempt_no: int) -> Path:
    root = (Path(tempfile.gettempdir()) / "herdr-ticket-dispatcher").resolve()
    directory = (root / state["run_id"] / f"{ticket.number}-a{attempt_no}").resolve()
    if not contained(directory, root):
        raise DispatchError("computed handoff directory escaped the OS temporary directory")
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)
    return directory


def prepare_handoff(
    state: dict[str, Any],
    ticket: Ticket,
    attempt: dict[str, Any],
    sample: dict[str, Any],
    reason: str,
) -> tuple[Path, str]:
    sequence = len(attempt.get("handoffs") or []) + 1
    directory = Path(attempt["handoff_dir"]).resolve()
    expected = handoff_directory(state, ticket, int(attempt["attempt"]))
    if directory != expected:
        raise DispatchError("attempt handoff directory failed containment verification")
    path = directory / f"handoff-{sequence}.md"
    path.unlink(missing_ok=True)
    attempt["pending_handoff"] = {
        "sequence": sequence,
        "path": str(path),
        "reason": reason,
        "sample": sample,
        "requested_at": utc_now(),
        "correction_sent": False,
    }
    attempt["state"] = "handing-off"
    prompt = (
        f"/handoff Continue ticket {ticket.number}: {ticket.title} in a fresh worker. "
        f"Save the handoff document exactly to {path}. Include current progress, decisions, "
        "commits and uncommitted work, validation already run, and the precise remaining work. "
        "Stop business edits while producing the handoff."
    )
    return path, prompt


def validate_handoff(path: Path, attempt: dict[str, Any]) -> str:
    directory = Path(attempt["handoff_dir"]).resolve()
    resolved = path.resolve()
    if not contained(resolved, directory) or resolved.suffix != ".md":
        raise DispatchError("handoff path failed containment verification")
    try:
        text = resolved.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise DispatchError("handoff skill did not write the requested document") from exc
    if len(text.strip()) < 80:
        raise DispatchError("handoff document is empty or too small to continue safely")
    return text


def continuation_prompt(template: str, ticket: Ticket, attempt: dict[str, Any], handoff_path: Path) -> str:
    contract = render_worker_prompt(template, ticket, attempt)
    return (
        "A previous worker reached the dispatcher context threshold and ran the handoff skill.\n"
        f"Read `{handoff_path}` in full before doing anything else. Treat it as progress context; "
        "the ticket, spec, and worker contract below remain authoritative. Continue in the existing "
        "worktree and branch, preserving committed and uncommitted work.\n\n"
        + contract
    )


def start_continuation(
    state_path: Path,
    state: dict[str, Any],
    ticket: Ticket,
    attempt: dict[str, Any],
    live_names: set[str],
    template: str,
) -> str:
    pending = attempt.get("pending_handoff")
    if not isinstance(pending, dict) or not pending.get("path"):
        raise DispatchError("continuation has no pending handoff")
    handoff_path = Path(pending["path"])
    validate_handoff(handoff_path, attempt)

    rollover = int(attempt.get("rollover_count", 0)) + 1
    agent = short_agent_name(ticket, int(attempt["attempt"]), live_names, rollover)
    if attempt["kind"] == "opencode":
        ensure_opencode_variant_config(
            str(attempt["worktree"]), attempt["provider"], attempt["model"], attempt["thinking"]
        )
    workspace = os.environ.get("HERDR_WORKSPACE_ID")
    if not workspace:
        raise DispatchError("HERDR_WORKSPACE_ID is missing")
    created = json_command([
        "herdr", "tab", "create", "--workspace", workspace,
        "--cwd", attempt["worktree"], "--label", agent, "--no-focus",
    ])
    tab, pane = extract_tab_and_root_pane(created)
    new_session = {**attempt, "agent": agent, "tab": tab, "pane": pane}
    try:
        start_args = [
            "herdr", "agent", "start", agent, "--kind", attempt["kind"], "--pane", pane, "--",
            *native_model_args(attempt["kind"], attempt["provider"], attempt["model"], attempt["thinking"]),
        ]
        started = start_agent_when_shell_ready(start_args)
        if started.returncode != 0:
            evidence = inspect_worker(new_session)
            if herdr_error_code(started) != "agent_not_ready":
                raise DispatchError(f"continuation start failed for {agent}: {started.stderr.strip()}\n{evidence}")
            prompt_user_for_worker(new_session, evidence)
        if not wait_agent_ready(new_session):
            raise DispatchError(
                f"continuation agent {agent} never reached an interactive state; refusing to submit the prompt"
            )
        context_ref = wait_agent_session(agent, attempts=8, delay=0.5) or ""
    except Exception:
        command(["herdr", "agent", "send-keys", agent, "ctrl+c"], check=False)
        command(["herdr", "tab", "close", tab], check=False)
        raise

    previous = {
        "agent": attempt["agent"],
        "tab": attempt["tab"],
        "pane": attempt["pane"],
        "context_ref": attempt["context_ref"],
    }
    record = {**pending, **previous, "continued_by": agent, "continued_at": utc_now()}
    attempt.setdefault("handoffs", []).append(record)
    attempt.pop("pending_handoff", None)
    attempt.update({
        "rollover_count": rollover,
        "agent": agent,
        "tab": tab,
        "pane": pane,
        "context_ref": context_ref,
        "state": "prompting",
        "correction_sent": False,
        "wait_timeouts": 0,
    })
    save_state(state_path, state)
    command(["herdr", "tab", "close", previous["tab"]], check=False)
    return continuation_prompt(template, ticket, attempt, handoff_path)


def read_worker_declaration(result_path: Path, attempt: dict[str, Any], repo: Path) -> dict[str, Any]:
    """Read protocol facts needed for the batch summary; perform no quality gate."""
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DispatchError("worker did not write a result file") from exc
    except json.JSONDecodeError as exc:
        raise DispatchError(f"worker result is invalid JSON: {exc}") from exc
    if not isinstance(result, dict):
        raise DispatchError("worker result must be a JSON object")
    status = result.get("status")
    if status not in {"completed", "failed", "needs-input"}:
        raise DispatchError(f"worker result has unknown status: {status!r}")
    if status in {"failed", "needs-input"}:
        reason = result.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise DispatchError(f"worker {status} declaration has no reason")
        return result

    declared_head = result.get("head_sha")
    if not isinstance(declared_head, str) or not SHA_RE.fullmatch(declared_head):
        raise DispatchError("completed declaration has no valid head_sha")
    actual_head = command(["git", "rev-parse", attempt["branch"]], cwd=repo).stdout.strip()
    if not SHA_RE.fullmatch(actual_head):
        raise DispatchError(f"cannot resolve branch HEAD for {attempt['branch']}")
    if declared_head != actual_head:
        raise DispatchError(f"declared head_sha {declared_head} != branch HEAD {actual_head}")
    return result


def verify_target(repo: Path, state: dict[str, Any]) -> None:
    branch = command(["git", "symbolic-ref", "--quiet", "--short", "HEAD"], cwd=repo, check=False).stdout.strip()
    head = command(["git", "rev-parse", "HEAD"], cwd=repo).stdout.strip()
    status = protected_worktree_status(repo, state["slug"])
    if branch != state["target_branch"] or head != state["base_head"]:
        raise DispatchError(f"target drift: expected {state['target_branch']}@{state['base_head']}, got {branch}@{head}")
    if status != state["protected_status"]:
        raise DispatchError(f"target worktree changed outside .scratch/{state['slug']}")


def context_snapshot(kind: str, ref: str, window: int | None = None) -> dict[str, Any]:
    helper = Path(__file__).with_name("get_context.py")
    args = [sys.executable, str(helper), kind, ref, "--json"]
    if window:
        args.extend(["--window", str(window)])
    proc = command(args, check=False, timeout=45)
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"error": proc.stderr.strip() or "invalid context adapter output"}


def archive_attempt(repo: Path, run_dir: Path, ticket: Ticket, attempt: dict[str, Any], reason: str) -> Path:
    directory = run_dir / "artifacts" / f"{ticket.number}-a{attempt['attempt']}"
    directory.mkdir(parents=True, exist_ok=True)
    worktree = Path(attempt["worktree"])
    records = {
        "status.txt": command(["git", "status", "--porcelain=v1"], cwd=worktree, check=False).stdout,
        "diff.patch": command(["git", "diff", "--binary"], cwd=worktree, check=False).stdout,
        "diff-cached.patch": command(["git", "diff", "--cached", "--binary"], cwd=worktree, check=False).stdout,
        "commits.txt": command(
            ["git", "log", "--oneline", f"{attempt['ticket_base_sha']}..{attempt['branch']}"],
            cwd=repo,
            check=False,
        ).stdout,
    }
    for name, content in records.items():
        atomic_text(directory / name, content)
    untracked_raw = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        cwd=worktree,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    ).stdout
    untracked = [item.decode("utf-8", "surrogateescape") for item in untracked_raw.split(b"\0") if item]
    if untracked:
        with tarfile.open(directory / "untracked.tar", "w") as archive:
            for relative in untracked:
                source = (worktree / relative).resolve()
                if contained(source, worktree) and source.is_file():
                    archive.add(source, arcname=relative, recursive=False)
    read = command(
        ["herdr", "agent", "read", attempt["agent"], "--source", "recent-unwrapped", "--lines", "200"],
        check=False,
    )
    atomic_text(directory / "transcript.txt", read.stdout + read.stderr)
    snapshot = context_snapshot(attempt["kind"], attempt["context_ref"], attempt.get("context_window")) if attempt.get("context_ref") else {"error": "no context ref"}
    atomic_json(directory / "context.json", snapshot)
    atomic_json(directory / "reason.json", {"reason": reason, "at": utc_now(), "attempt": attempt})
    return directory


SETTLE_POLLS = 20
SETTLE_POLL_DELAY = 0.25
SETTLE_GET_RETRIES = 3


def agent_status(attempt: dict[str, Any]) -> tuple[int, str | None]:
    """Return (returncode, status) for the worker's current herdr agent state."""
    current = command(["herdr", "agent", "get", attempt["agent"]], check=False)
    return current.returncode, watcher_status(current.stdout)


def wait_agent_ready(attempt: dict[str, Any]) -> bool:
    """Wait until the herdr agent reports a real interactive state.

    Process detection alone is not enough: an opencode TUI keeps bootstrapping
    for tens of seconds after its process appears, and a prompt pasted before
    the TUI input is wired up is silently dropped, which herdr then reports as
    agent_prompt_stalled. Only idle/done/working/blocked mean the TUI can
    accept and consume submitted text.
    """
    for index in range(READY_POLL_ATTEMPTS):
        rc, status = agent_status(attempt)
        if rc == 0 and status in {"idle", "done", "working", "blocked"}:
            return True
        if index + 1 < READY_POLL_ATTEMPTS:
            time.sleep(READY_POLL_DELAY)
    return False


def interrupt_keys(kind: str | None) -> tuple[str, ...]:
    """Keys that stop a running worker without destroying its reachability.

    The opencode TUI exits entirely when it receives ctrl+c during a run
    (abort followed by quit), which unregisters the agent from herdr and makes
    the worker unreachable for the handoff prompt. A double escape interrupts
    the run while keeping the TUI alive. Other kinds keep ctrl+c.
    """
    if kind == "opencode":
        return ("escape", "escape")
    return ("ctrl+c",)


def stop_agent(attempt: dict[str, Any]) -> bool:
    command(["herdr", "agent", "send-keys", attempt["agent"], "ctrl+c"], check=False)
    for _ in range(20):
        proc = command(["herdr", "agent", "get", attempt["agent"]], check=False)
        if proc.returncode != 0:
            return True
        try:
            payload = json.loads(proc.stdout)
            text = json.dumps(payload)
        except json.JSONDecodeError:
            text = proc.stdout
        if '"agent_status":"working"' not in text and '"status":"working"' not in text:
            return True
        time.sleep(0.25)
    return False


def settle_agent_for_handoff(attempt: dict[str, Any]) -> bool:
    rc, status = agent_status(attempt)
    retries = 0
    while rc != 0 or status not in {"idle", "done", "working", "blocked"}:
        retries += 1
        if retries >= SETTLE_GET_RETRIES:
            return False
        time.sleep(SETTLE_POLL_DELAY)
        rc, status = agent_status(attempt)
    if status in {"idle", "done"}:
        return True
    command(["herdr", "agent", "send-keys", attempt["agent"], *interrupt_keys(attempt.get("kind"))], check=False)
    unresolved = 0
    for _ in range(SETTLE_POLLS):
        rc, status = agent_status(attempt)
        if rc == 0 and status in {"idle", "done"}:
            return True
        if rc != 0:
            unresolved += 1
            if unresolved >= SETTLE_GET_RETRIES:
                return False
        time.sleep(SETTLE_POLL_DELAY)
    return False


def close_attempt(repo: Path, attempt: dict[str, Any], *, remove_branch: bool) -> None:
    command(["herdr", "tab", "close", attempt["tab"]], check=False)
    command(["git", "worktree", "remove", "--force", "--", attempt["worktree"]], cwd=repo, check=False)
    if remove_branch:
        command(["git", "branch", "-d", "--", attempt["branch"]], cwd=repo, check=False)


def fail_attempt(
    repo: Path,
    run_dir: Path,
    state_path: Path,
    state: dict[str, Any],
    ticket: Ticket,
    attempt: dict[str, Any],
    reason: str,
) -> bool:
    attempt["state"] = "stopping"
    state["tickets"][ticket.number]["status"] = "claimed"
    save_state(state_path, state)
    if not stop_agent(attempt):
        attempt["state"] = "needs-input"
        attempt["reason"] = "worker could not be stopped; manual intervention required"
        state["phase"] = "stalled"
        save_state(state_path, state)
        return False
    artifact = archive_attempt(repo, run_dir, ticket, attempt, reason)
    close_attempt(repo, attempt, remove_branch=False)
    attempt["state"] = "failed"
    attempt["reason"] = reason
    snapshot = json.loads((artifact / "context.json").read_text(encoding="utf-8"))
    comment = {
        "run_id": state["run_id"],
        "attempt": attempt["attempt"],
        "reason": reason,
        "total": snapshot.get("total"),
        "window": snapshot.get("window"),
        "pct": snapshot.get("pct"),
        "source": snapshot.get("source"),
        "freshness": snapshot.get("freshness"),
        "branch": attempt["branch"],
        "artifact_path": str(artifact),
    }
    update_ticket(ticket.path, "failed", comment)
    state["tickets"][ticket.number]["status"] = "failed"
    save_state(state_path, state)
    return True


def complete_attempt(
    repo: Path,
    state_path: Path,
    state: dict[str, Any],
    ticket: Ticket,
    attempt: dict[str, Any],
    result: dict[str, Any],
) -> None:
    """Record a worker's completed declaration and preserve its branch for merge."""
    ensure_context_ref(state_path, state, attempt)
    snapshot = context_snapshot(attempt["kind"], attempt["context_ref"], attempt.get("context_window"))
    attempt["context_snapshot"] = snapshot
    attempt["state"] = "completed"
    update_ticket(ticket.path, "completed", {
        "run_id": state["run_id"],
        "batch": state["batch_number"],
        "attempt": attempt["attempt"],
        "worker_head_sha": result["head_sha"],
        "branch": attempt["branch"],
        "result_path": attempt["result_path"],
        "worker_session": attempt["context_ref"],
        "context_snapshot": snapshot,
    })
    state["tickets"][ticket.number]["status"] = "completed"
    save_state(state_path, state)
    stop_agent(attempt)
    close_attempt(repo, attempt, remove_branch=False)


def watcher_status(output: str) -> str | None:
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return None
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
    for status in ("blocked", "done", "idle", "unknown", "working"):
        if status in found:
            return status
    return None


def start_watcher(attempt: dict[str, Any]) -> subprocess.Popen[str]:
    proc = subprocess.Popen(
        ["herdr", "agent", "wait", attempt["agent"], "--timeout", str(WAIT_TIMEOUT_MS)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    attempt["watcher_pid"] = proc.pid
    return proc


def start_prompt_watcher(attempt: dict[str, Any], prompt: str) -> subprocess.Popen[str]:
    proc = subprocess.Popen(
        [
            "herdr", "agent", "prompt", attempt["agent"], prompt,
            "--wait", "--timeout", str(WAIT_TIMEOUT_MS),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    attempt["watcher_pid"] = proc.pid
    return proc


def inspect_worker(attempt: dict[str, Any]) -> str:
    get = command(["herdr", "agent", "get", attempt["agent"]], check=False)
    read = command(
        ["herdr", "agent", "read", attempt["agent"], "--source", "recent-unwrapped", "--lines", "120"],
        check=False,
    )
    return get.stdout + get.stderr + "\n" + read.stdout + read.stderr


def prompt_user_for_worker(attempt: dict[str, Any], evidence: str) -> None:
    print(f"\nWorker {attempt['agent']} needs input:\n{evidence}")
    if not sys.stdin.isatty():
        raise DispatchError("worker needs user input but dispatcher stdin is not interactive")
    answer = input("Your answer (blank leaves run stalled): ").strip()
    if not answer:
        raise DispatchError("user left blocked worker unanswered")
    current = command(["herdr", "agent", "get", attempt["agent"]], check=False)
    if '"agent_status":"blocked"' in current.stdout or '"status":"blocked"' in current.stdout:
        command(["herdr", "pane", "send-text", attempt["pane"], answer], check=True, timeout=30)
        command(["herdr", "agent", "send-keys", attempt["agent"], "enter"], check=True, timeout=30)
    else:
        command(["herdr", "agent", "prompt", attempt["agent"], answer], check=True, timeout=30)


def launch_attempt(
    repo: Path,
    worktree_root: Path,
    run_dir: Path,
    state_path: Path,
    state: dict[str, Any],
    ticket: Ticket,
    live_names: set[str],
    template: str,
) -> dict[str, Any]:
    ticket_state = state["tickets"][ticket.number]
    attempt_no = next_attempt(
        repo,
        Path(state["git_common_dir"]),
        worktree_root,
        state["slug"],
        ticket,
        ticket_state["attempts"],
    )
    branch = f"ticket/{ticket.number}-{ticket.slug}/a{attempt_no}"
    worktree = (worktree_root / state["slug"] / f"{ticket.number}-{ticket.slug}" / f"a{attempt_no}").resolve()
    if not contained(worktree, worktree_root):
        raise DispatchError("computed worktree escaped .worktrees root")
    result_path = (run_dir / "results" / f"{ticket.number}-a{attempt_no}.json").resolve()
    handoff_dir = handoff_directory(state, ticket, attempt_no)
    agent = short_agent_name(ticket, attempt_no, live_names)
    attempt = {
        "attempt": attempt_no,
        "state": "allocating",
        "kind": state["kind"],
        "provider": state["provider"],
        "model": state["model"],
        "thinking": state["thinking"],
        "agent": agent,
        "tab": "",
        "pane": "",
        "context_ref": "",
        "context_window": state.get("context_window"),
        "branch": branch,
        "worktree": str(worktree),
        "ticket_base_sha": state["base_head"],
        "result_path": str(result_path),
        "handoff_dir": str(handoff_dir),
        "handoffs": [],
        "rollover_count": 0,
        "correction_sent": False,
        "wait_timeouts": 0,
    }
    ticket_state["attempts"].append(attempt)
    save_state(state_path, state)

    try:
        validate_worktree_ignore(repo)
        worktree.parent.mkdir(parents=True, exist_ok=True)
        command(["git", "worktree", "add", "-b", branch, "--", str(worktree), attempt["ticket_base_sha"]], cwd=repo)
        if state["kind"] == "opencode":
            ensure_opencode_variant_config(str(worktree), state["provider"], state["model"], state["thinking"])
        workspace = os.environ.get("HERDR_WORKSPACE_ID")
        if not workspace:
            raise DispatchError("HERDR_WORKSPACE_ID is missing")
        created = json_command([
            "herdr", "tab", "create", "--workspace", workspace,
            "--cwd", str(worktree), "--label", agent, "--no-focus",
        ])
        attempt["tab"], attempt["pane"] = extract_tab_and_root_pane(created)
        save_state(state_path, state)

        start_args = [
            "herdr", "agent", "start", agent, "--kind", state["kind"], "--pane", attempt["pane"], "--",
            *native_model_args(state["kind"], state["provider"], state["model"], state["thinking"]),
        ]
        start = start_agent_when_shell_ready(start_args)
        if start.returncode != 0:
            # A blocked startup remains inspectable; every other failure is terminal.
            evidence = inspect_worker(attempt)
            if herdr_error_code(start) != "agent_not_ready":
                raise DispatchError(f"agent start failed for {agent}: {start.stderr.strip()}\n{evidence}")
            prompt_user_for_worker(attempt, evidence)

        # A fresh opencode TUI bootstraps for tens of seconds after its
        # process is detected; prompting before the input is wired up drops
        # the entire worker contract and herdr reports agent_prompt_stalled.
        if not wait_agent_ready(attempt):
            raise DispatchError(
                f"agent {agent} never reached an interactive state; refusing to submit the worker prompt"
            )

        # opencode registers its session id only after the first prompt lands;
        # the ref is backfilled lazily by ensure_context_ref().
        context_ref = wait_agent_session(agent, attempts=8, delay=0.5) or ""
        attempt["context_ref"] = context_ref
        attempt["state"] = "prompting"
        save_state(state_path, state)
        return attempt
    except Exception:
        attempt["state"] = "failed"
        attempt["reason"] = "launch-failed"
        if attempt.get("pane"):
            command(["herdr", "agent", "send-keys", agent, "ctrl+c"], check=False)
        if attempt.get("tab"):
            command(["herdr", "tab", "close", attempt["tab"]], check=False)
        if worktree.exists():
            launch_artifact = run_dir / "artifacts" / f"{ticket.number}-a{attempt_no}-launch"
            launch_artifact.mkdir(parents=True, exist_ok=True)
            diff = command(["git", "diff", "--binary"], cwd=worktree, check=False).stdout
            atomic_text(launch_artifact / "diff.patch", diff)
            command(["git", "worktree", "remove", "--force", "--", str(worktree)], cwd=repo, check=False)
        save_state(state_path, state)
        raise


def print_batch(tickets: dict[str, Ticket], state: dict[str, Any]) -> None:
    print(f"\nBatch {state['batch_number']} preflight (base {state['base_head']}):")
    for number, ticket in tickets.items():
        print(f"  {number} [{state['tickets'][number]['status']}] {ticket.title}")


def build_batch_summary(repo: Path, state: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    workers: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for number in state["batch_tickets"]:
        ticket_state = state["tickets"][number]
        attempts = ticket_state.get("attempts") or []
        attempt = attempts[-1] if attempts else {}
        result_path = Path(attempt["result_path"]) if attempt.get("result_path") else None
        declaration: dict[str, Any] | None = None
        if result_path and result_path.is_file():
            try:
                parsed = json.loads(result_path.read_text(encoding="utf-8"))
                declaration = parsed if isinstance(parsed, dict) else None
            except (OSError, json.JSONDecodeError):
                declaration = None

        declared_status = declaration.get("status") if declaration else None
        attempt_state = attempt.get("state")
        if declared_status == "completed" and attempt_state == "completed":
            status = "completed"
        elif declared_status in {"failed", "needs-input"}:
            status = declared_status
        else:
            status = ticket_state["status"]
        record: dict[str, Any] = {
            "ticket": number,
            "attempt": attempt.get("attempt"),
            "status": status,
            "head_sha": None,
            "branch": attempt.get("branch"),
            "result_path": str(result_path) if result_path else None,
            "handoffs": [item.get("path") for item in attempt.get("handoffs", []) if item.get("path")],
        }
        branch = attempt.get("branch")
        if branch:
            resolved = command(["git", "rev-parse", "--verify", branch], cwd=repo, check=False)
            if resolved.returncode == 0 and SHA_RE.fullmatch(resolved.stdout.strip()):
                record["head_sha"] = resolved.stdout.strip()
        if status != "completed":
            reason = declaration.get("reason") if declaration else attempt.get("reason")
            record["reason"] = reason or "worker did not produce a completed declaration"
            failed.append({"ticket": number, "status": status, "reason": record["reason"]})
        workers.append(record)

    return {
        "batch": state["batch_number"],
        "base_head": state["base_head"],
        "tickets": list(state["batch_tickets"]),
        "workers": workers,
        "failed": failed,
        "run_id": state["run_id"],
        "artifacts": str(run_dir),
    }


def write_batch_summary(repo: Path, state: dict[str, Any], run_dir: Path) -> tuple[dict[str, Any], Path]:
    summary = build_batch_summary(repo, state, run_dir)
    path = run_dir / "results" / f"batch-{state['batch_number']}-summary.json"
    atomic_json(path, summary)
    print("\nBatch summary:")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"Summary path: {path}")
    return summary, path


def run_dispatch(
    repo: Path,
    tickets: dict[str, Ticket],
    skipped: set[str],
    run_dir: Path,
    state_path: Path,
    state: dict[str, Any],
) -> None:
    worktree_root = (repo / ".worktrees").resolve()
    template = (Path(__file__).parent.parent / "prompts/worker.md").read_text(encoding="utf-8")
    pending = [number for number in state["batch_tickets"] if number not in skipped]
    running: dict[str, dict[str, Any]] = {}
    watchers: dict[str, subprocess.Popen[str]] = {}
    watcher_kinds: dict[str, str] = {}
    watcher_prompts: dict[str, str] = {}
    selector = selectors.DefaultSelector()
    monitor: subprocess.Popen[str] | None = None
    monitor_log = (run_dir / "monitor.stderr.log").open("w", encoding="utf-8")
    batch_timeout_minutes = state.get("batch_timeout_minutes")
    deadline = time.monotonic() + batch_timeout_minutes * 60 if batch_timeout_minutes else None
    state["phase"] = "running"
    save_state(state_path, state)

    def register_watcher(number: str, attempt: dict[str, Any], prompt: str | None = None) -> None:
        proc = start_prompt_watcher(attempt, prompt) if prompt is not None else start_watcher(attempt)
        watchers[number] = proc
        watcher_kinds[number] = "prompt" if prompt is not None else "wait"
        if prompt is not None:
            watcher_prompts[number] = prompt
        assert proc.stdout is not None
        selector.register(proc.stdout, selectors.EVENT_READ, ("watcher", number))
        save_state(state_path, state)

    def remove_watcher(number: str) -> tuple[subprocess.Popen[str] | None, str | None, str | None]:
        proc = watchers.pop(number, None)
        kind = watcher_kinds.pop(number, None)
        prompt = watcher_prompts.pop(number, None)
        if proc and proc.stdout:
            try:
                selector.unregister(proc.stdout)
            except Exception:
                pass
        return proc, kind, prompt

    def begin_handoff(number: str, sample: dict[str, Any], reason: str) -> bool:
        attempt = running[number]
        watcher, _, _ = remove_watcher(number)
        if watcher and watcher.poll() is None:
            watcher.terminate()
        try:
            if not settle_agent_for_handoff(attempt):
                raise DispatchError("worker could not be settled for handoff")
            Path(attempt["result_path"]).unlink(missing_ok=True)
            _, prompt = prepare_handoff(state, tickets[number], attempt, sample, reason)
            save_state(state_path, state)
            register_watcher(number, attempt, prompt)
            print(
                f"Context rollover: ticket {number} worker {attempt['agent']} is writing "
                f"{attempt['pending_handoff']['path']}"
            )
            return True
        except Exception as exc:
            fail_attempt(
                repo, run_dir, state_path, state, tickets[number], attempt,
                f"context-handoff-failed: {exc}",
            )
            running.pop(number, None)
            if state["fail_fast"]:
                raise DispatchError(f"--fail-fast: context handoff failed for ticket {number}") from exc
            return False

    try:
        while pending or running:
            verify_target(repo, state)
            new_attempts: list[tuple[str, dict[str, Any]]] = []
            live_names = {str(item.get("name") or item.get("agent_name") or "") for item in agent_list()}
            while pending and len(running) < state["jobs"]:
                number = pending.pop(0)
                try:
                    attempt = launch_attempt(
                        repo, worktree_root, run_dir, state_path, state, tickets[number], live_names, template
                    )
                    live_names.add(attempt["agent"])
                    running[number] = attempt
                    new_attempts.append((number, attempt))
                except Exception as exc:
                    print(f"launch failed for ticket {number}: {exc}", file=sys.stderr)
                    state["tickets"][number]["status"] = "failed"
                    update_ticket(tickets[number].path, "failed", {
                        "run_id": state["run_id"], "batch": state["batch_number"], "reason": f"launch-failed: {exc}"
                    })
                    save_state(state_path, state)
                    if state["fail_fast"]:
                        raise DispatchError(f"--fail-fast: launch failed for ticket {number}") from exc

            # Launch every available batch slot before observing a completion.
            for number, attempt in new_attempts:
                prompt = render_worker_prompt(template, tickets[number], attempt)
                try:
                    register_watcher(number, attempt, prompt)
                except OSError as exc:
                    fail_attempt(repo, run_dir, state_path, state, tickets[number], attempt, f"prompt-failed: {exc}")
                    running.pop(number, None)
                    if state["fail_fast"]:
                        raise DispatchError(f"--fail-fast: prompt failed for ticket {number}") from exc
                    continue
                attempt["state"] = "running"
                state["tickets"][number]["status"] = "claimed"
                update_ticket(tickets[number].path, "claimed")
                save_state(state_path, state)
                ensure_context_ref(state_path, state, attempt)

            if running and monitor is None:
                monitor = subprocess.Popen(
                    [sys.executable, str(Path(__file__).with_name("monitor.py")), "--state", str(state_path), "--notify"],
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=monitor_log,
                )
                assert monitor.stdout is not None
                selector.register(monitor.stdout, selectors.EVENT_READ, ("monitor", None))

            if not running:
                continue
            if deadline is None:
                select_timeout = 300.0
            else:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    for timeout_number, timeout_attempt in list(running.items()):
                        timeout_watcher, _, _ = remove_watcher(timeout_number)
                        if timeout_watcher and timeout_watcher.poll() is None:
                            timeout_watcher.terminate()
                        if fail_attempt(
                            repo, run_dir, state_path, state, tickets[timeout_number], timeout_attempt,
                            f"batch-timeout-exceeded: batch budget of {batch_timeout_minutes} minutes elapsed",
                        ):
                            running.pop(timeout_number, None)
                    raise DispatchError(f"batch timeout exceeded: {batch_timeout_minutes} minutes")
                select_timeout = min(300.0, remaining)
            events = selector.select(timeout=select_timeout)
            if not events:
                continue
            for key, _ in events:
                kind, number = key.data
                if kind == "monitor":
                    line = key.fileobj.readline()
                    if not line:
                        state["phase"] = "stalled"
                        save_state(state_path, state)
                        raise DispatchError("context monitor exited or event channel closed")
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if event.get("type") == "context-trip":
                        target = str(event.get("ticket"))
                        attempt = running.get(target)
                        if (
                            attempt
                            and attempt.get("state") == "running"
                            and int(event.get("attempt", -1)) == attempt["attempt"]
                        ):
                            result_path = Path(attempt["result_path"])
                            try:
                                settled = read_worker_declaration(result_path, attempt, repo)
                            except DispatchError:
                                settled = None
                            if settled and settled["status"] == "completed":
                                watcher, _, _ = remove_watcher(target)
                                if watcher and watcher.poll() is None:
                                    watcher.terminate()
                                complete_attempt(repo, state_path, state, tickets[target], attempt, settled)
                                running.pop(target, None)
                            else:
                                begin_handoff(target, event.get("sample") or {}, "context-limit")
                    elif event.get("type") == "monitor-error":
                        evidence = json.dumps(event, indent=2, ensure_ascii=False)
                        if not sys.stdin.isatty():
                            raise DispatchError("monitor needs user decision: " + evidence)
                        print(evidence)
                        decision = input("Monitor error: [r]etry, [s]top ticket, [e]nd run: ").strip().lower()
                        target = str(event.get("ticket"))
                        if decision == "s" and target in running:
                            watcher, _, _ = remove_watcher(target)
                            if watcher:
                                watcher.terminate()
                            fail_attempt(repo, run_dir, state_path, state, tickets[target], running[target], "monitor-error")
                            running.pop(target, None)
                        elif decision == "e":
                            raise DispatchError("user ended run after monitor error")
                    continue

                assert number is not None
                proc, watcher_kind, watcher_prompt = remove_watcher(number)
                if proc is None:
                    continue
                stdout, stderr = proc.communicate()
                attempt = running.get(number)
                if not attempt:
                    continue

                if watcher_kind == "prompt" and proc.returncode != 0:
                    code = error_code_from_output(stdout, stderr)
                    if code != "timeout":
                        retries = int(attempt.get("prompt_retries", 0)) + 1
                        if retries <= PROMPT_RETRIES and code != "agent_blocked":
                            attempt["prompt_retries"] = retries
                            save_state(state_path, state)
                            print(
                                f"Prompt submission failed for ticket {number} "
                                f"(worker {attempt['agent']}, code {code or 'unknown'}); "
                                f"retry {retries}/{PROMPT_RETRIES}"
                            )
                            time.sleep(PROMPT_RETRY_BASE_DELAY * retries)
                            wait_agent_ready(attempt)
                            register_watcher(number, attempt, watcher_prompt)
                            continue
                        fail_attempt(
                            repo, run_dir, state_path, state, tickets[number], attempt,
                            f"prompt-submission-failed: code {code or 'unknown'} after "
                            f"{int(attempt.get('prompt_retries', 0))} retries: "
                            f"{(stderr.strip() or stdout.strip())[:300]}",
                        )
                        running.pop(number, None)
                        if state["fail_fast"]:
                            raise DispatchError(f"--fail-fast: prompt submission failed for ticket {number}")
                        continue
                    # A >1h turn exhausted the prompt watcher's wait budget while
                    # the submission itself was accepted; keep waiting.
                    attempt["wait_timeouts"] = int(attempt.get("wait_timeouts", 0)) + 1
                    ensure_context_ref(state_path, state, attempt)
                    sample = context_snapshot(state["kind"], attempt["context_ref"], attempt.get("context_window"))
                    if attempt["wait_timeouts"] >= 2 and isinstance(sample.get("pct"), (int, float)) and sample["pct"] >= 0.70:
                        begin_handoff(number, sample, "repeated-wait-timeout-near-context-limit")
                    else:
                        register_watcher(number, attempt)
                    continue
                if watcher_kind == "prompt":
                    attempt["prompt_retries"] = 0

                if attempt.get("state") == "handing-off":
                    pending_handoff = attempt.get("pending_handoff") or {}
                    handoff_path = Path(pending_handoff.get("path", ""))
                    try:
                        validate_handoff(handoff_path, attempt)
                    except DispatchError as exc:
                        if not pending_handoff.get("correction_sent"):
                            pending_handoff["correction_sent"] = True
                            correction = (
                                f"/handoff The required handoff file was not created ({exc}). "
                                f"Write the complete handoff exactly to {handoff_path}; include progress and remaining work."
                            )
                            save_state(state_path, state)
                            register_watcher(number, attempt, correction)
                            continue
                        fail_attempt(
                            repo, run_dir, state_path, state, tickets[number], attempt,
                            f"context-handoff-failed: {exc}",
                        )
                        running.pop(number, None)
                        if state["fail_fast"]:
                            raise DispatchError(f"--fail-fast: context handoff failed for ticket {number}") from exc
                        continue

                    try:
                        live_names = {str(item.get("name") or item.get("agent_name") or "") for item in agent_list()}
                        prompt = start_continuation(
                            state_path, state, tickets[number], attempt, live_names, template
                        )
                        register_watcher(number, attempt, prompt)
                    except Exception as exc:
                        fail_attempt(
                            repo, run_dir, state_path, state, tickets[number], attempt,
                            f"continuation-launch-failed: {exc}",
                        )
                        running.pop(number, None)
                        if state["fail_fast"]:
                            raise DispatchError(f"--fail-fast: continuation launch failed for ticket {number}") from exc
                        continue
                    attempt["state"] = "running"
                    save_state(state_path, state)
                    ensure_context_ref(state_path, state, attempt)
                    print(
                        f"Context rollover complete: ticket {number} continues in {attempt['agent']} "
                        f"from {handoff_path}"
                    )
                    continue

                result_path = Path(attempt["result_path"])
                try:
                    result = read_worker_declaration(result_path, attempt, repo)
                except DispatchError as exc:
                    result = None
                    declaration_error = str(exc)
                else:
                    declaration_error = ""

                if result and result["status"] == "completed":
                    complete_attempt(repo, state_path, state, tickets[number], attempt, result)
                    running.pop(number, None)
                    continue
                if result and result["status"] == "failed":
                    fail_attempt(repo, run_dir, state_path, state, tickets[number], attempt, f"worker-declared-failed: {result['reason']}")
                    running.pop(number, None)
                    if state["fail_fast"]:
                        raise DispatchError("--fail-fast: worker declared failed")
                    continue
                if result and result["status"] == "needs-input":
                    if sys.stdin.isatty():
                        prompt_user_for_worker(attempt, result["reason"])
                        result_path.unlink(missing_ok=True)
                        attempt["state"] = "running"
                        register_watcher(number, attempt)
                    else:
                        fail_attempt(repo, run_dir, state_path, state, tickets[number], attempt, f"worker-needs-input: {result['reason']}")
                        running.pop(number, None)
                    continue

                status = watcher_status(stdout)
                if proc.returncode != 0:
                    attempt["wait_timeouts"] = int(attempt.get("wait_timeouts", 0)) + 1
                    ensure_context_ref(state_path, state, attempt)
                    sample = context_snapshot(state["kind"], attempt["context_ref"], attempt.get("context_window"))
                    if attempt["wait_timeouts"] >= 2 and isinstance(sample.get("pct"), (int, float)) and sample["pct"] >= 0.70:
                        begin_handoff(number, sample, "repeated-wait-timeout-near-context-limit")
                    else:
                        register_watcher(number, attempt)
                    continue
                attempt["wait_timeouts"] = 0
                evidence = inspect_worker(attempt)
                if status == "blocked":
                    prompt_user_for_worker(attempt, evidence)
                    register_watcher(number, attempt)
                elif not attempt.get("correction_sent"):
                    attempt["correction_sent"] = True
                    correction = (
                        f"Your turn settled without a usable result ({declaration_error or 'missing result'}). "
                        f"Write the protocol JSON atomically to {attempt['result_path']}."
                    )
                    register_watcher(number, attempt, correction)
                else:
                    fail_attempt(repo, run_dir, state_path, state, tickets[number], attempt, "missing-or-invalid-result-after-correction")
                    running.pop(number, None)

        statuses = [state["tickets"][number]["status"] for number in state["batch_tickets"]]
        state["phase"] = "completed" if statuses and all(value == "completed" for value in statuses) else "failed"
        save_state(state_path, state)
    finally:
        if monitor is not None and monitor.poll() is None:
            monitor.terminate()
            try:
                monitor.wait(timeout=5)
            except subprocess.TimeoutExpired:
                monitor.kill()
        for proc in watchers.values():
            if proc.poll() is None:
                proc.terminate()
        if running:
            state["phase"] = "stopping"
            save_state(state_path, state)
            for number, attempt in list(running.items()):
                try:
                    fail_attempt(repo, run_dir, state_path, state, tickets[number], attempt, "dispatcher-stopped")
                except Exception as exc:
                    print(f"cleanup failed for ticket {number}: {exc}", file=sys.stderr)
            state["phase"] = "failed"
            save_state(state_path, state)
        monitor_log.close()
        selector.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slug")
    parser.add_argument("--target")
    parser.add_argument("--expect-target-head", help="refuse to start unless target HEAD equals this SHA")
    parser.add_argument("--batch", help="explicit ticket IDs for this batch, e.g. '01 02 07b' or 't1 t2'")
    parser.add_argument("--batch-number", type=int, default=1, help="plan batch number used in the summary filename")
    parser.add_argument("--kind", choices=sorted(SUPPORTED_KINDS))
    parser.add_argument("--provider")
    parser.add_argument("--model")
    parser.add_argument("--thinking", choices=THINKING_LEVELS)
    parser.add_argument("--catalog", choices=("providers", "models"), help="discover selectable values without starting a run")
    parser.add_argument("--jobs", type=int, default=3)
    parser.add_argument("--window", type=int, help="explicit model context window; observations become estimated")
    parser.add_argument("--batch-timeout", type=int, help="per-batch wall-clock budget in minutes; minimum 120")
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--skip-claimed", action="store_true", help="leave stale claimed tickets untouched instead of abandoning them")
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be at least 1")
    if args.batch_number < 1:
        parser.error("--batch-number must be at least 1")
    if args.window is not None and args.window <= 0:
        parser.error("--window must be positive")
    if args.batch_timeout is not None and args.batch_timeout < MIN_BATCH_TIMEOUT_MINUTES:
        parser.error(f"--batch-timeout must be at least {MIN_BATCH_TIMEOUT_MINUTES} minutes")
    if args.expect_target_head and not SHA_RE.fullmatch(args.expect_target_head):
        parser.error("--expect-target-head must be a full 40-64 character lowercase SHA")
    return args


def main() -> int:
    args = parse_args()
    if os.environ.get("HERDR_ENV") != "1":
        print("herdr-ticket-dispatcher must run inside Herdr (HERDR_ENV=1)", file=sys.stderr)
        return 2
    for dependency in ("git", "python3", "herdr"):
        if shutil.which(dependency) is None:
            print(f"missing dependency: {dependency}", file=sys.stderr)
            return 2
    try:
        if args.catalog:
            if not args.kind:
                raise DispatchError("--catalog requires --kind")
            if shutil.which(args.kind) is None:
                raise DispatchError(f"{args.kind} executable is not installed")
            if args.catalog == "providers":
                catalog = discover_model_catalog(args.kind)
                print("\n".join(sorted(catalog)))
                return 0
            if not args.provider:
                raise DispatchError("--catalog models requires --provider")
            catalog = discover_model_catalog(args.kind, args.provider)
            print("\n".join(catalog[args.provider]))
            return 0

        if not args.batch:
            raise DispatchError("dispatch requires --batch; create and confirm the execution plan first")
        batch_numbers = normalize_batch(args.batch)
        if not args.kind or not args.provider or not args.model or not args.thinking:
            raise DispatchError("dispatch requires explicit --kind, --provider, --model, and --thinking selections")
        if shutil.which(args.kind) is None:
            raise DispatchError(f"{args.kind} executable is not installed")

        repo, common, branch, head = repo_context(Path.cwd(), args.target)
        if args.expect_target_head and args.expect_target_head != head:
            raise DispatchError(f"target drift before batch: expected {args.expect_target_head}, got {head}")
        groups = discover_groups(repo)
        if not args.slug:
            summarize_groups(repo, groups)
            print("Rerun with --slug <feature-slug> and the confirmed --batch.")
            return 3
        if args.slug not in groups:
            raise DispatchError(f"unknown ticket slug: {args.slug}")

        lock, _ = acquire_lock(common, args.slug)
        try:
            protected_status = protected_worktree_status(repo, args.slug)
            validate_worktree_ignore(repo)
            all_tickets = load_tickets(repo, args.slug)
            unknown = [number for number in batch_numbers if number not in all_tickets]
            if unknown:
                raise DispatchError("--batch names unknown tickets: " + ", ".join(unknown))
            tickets = {number: all_tickets[number] for number in batch_numbers}
            already_completed = [number for number, ticket in tickets.items() if ticket.status == "completed"]
            if already_completed:
                raise DispatchError("--batch includes tickets already declared completed: " + ", ".join(already_completed))

            run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"-{os.getpid()}-{uuid.uuid4().hex[:6]}"
            run_dir = common / "herdr-ticket-dispatcher" / args.slug / "runs" / run_id
            run_dir.mkdir(parents=True, exist_ok=False)
            installed = inspect_cli(run_dir)
            validate_selection(args.kind, args.provider, args.model, installed)

            claimed_numbers = {number for number, ticket in tickets.items() if ticket.status == "claimed"}
            skipped = resolve_claimed(repo, common, args.slug, tickets, args.skip_claimed)
            if claimed_numbers and not skipped:
                refreshed = load_tickets(repo, args.slug)
                tickets = {number: refreshed[number] for number in batch_numbers}
            state = create_state(
                run_id, args.batch_number, args.slug, repo, common, branch, head, protected_status,
                args.kind, args.provider, args.model, args.thinking, args.jobs, args.fail_fast,
                args.window, args.batch_timeout, tickets, skipped,
            )
            state_path = run_dir / "state.json"
            save_state(state_path, state)
            print_batch(tickets, state)
            if skipped:
                print("Skipped claimed tickets:", ", ".join(sorted(skipped)))

            run_error: Exception | None = None
            try:
                run_dispatch(repo, tickets, skipped, run_dir, state_path, state)
            except (DispatchError, OSError, subprocess.TimeoutExpired) as exc:
                run_error = exc
            summary, _ = write_batch_summary(repo, state, run_dir)
            if run_error:
                raise run_error
            return 0 if not summary["failed"] and all(
                worker["status"] == "completed" and worker["head_sha"] for worker in summary["workers"]
            ) else 1
        finally:
            lock.close()
    except (DispatchError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"dispatcher stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
