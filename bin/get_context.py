#!/usr/bin/env python3
"""Normalize current-context observations for Herdr-managed agent sessions."""

from __future__ import annotations

import argparse
from contextlib import closing
import datetime as dt
import glob
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
from typing import Any

KINDS = {"opencode", "codex", "pi"}


class ContextError(RuntimeError):
    pass


def iso_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=sorted(KINDS))
    parser.add_argument("context_ref")
    parser.add_argument("--json", action="store_true", required=True)
    parser.add_argument("--window", type=int)
    parser.add_argument("--opencode-db", type=Path, default=Path.home() / ".local/share/opencode/opencode.db")
    parser.add_argument("--codex-db", type=Path, default=Path.home() / ".codex/state_5.sqlite")
    parser.add_argument("--pi-helper", type=Path, default=Path(__file__).with_name("pi_context.mjs"))
    args = parser.parse_args()
    if args.window is not None and args.window <= 0:
        parser.error("--window must be positive")
    return args


def open_sqlite_ro(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise ContextError(f"SQLite database not found: {path}")
    try:
        return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error as exc:
        raise ContextError(f"cannot open SQLite database read-only: {path}: {exc}") from exc


def decode_model_record(output: str, provider: str, model_id: str) -> dict[str, Any]:
    marker = f"{provider}/{model_id}"
    start = output.find(marker)
    if start < 0:
        raise ContextError(f"opencode model registry has no {marker}")
    brace = output.find("{", start + len(marker))
    if brace < 0:
        raise ContextError(f"opencode model registry returned no metadata for {marker}")
    try:
        value, _ = json.JSONDecoder().raw_decode(output[brace:])
    except json.JSONDecodeError as exc:
        raise ContextError(f"cannot parse opencode model metadata for {marker}: {exc}") from exc
    if not isinstance(value, dict):
        raise ContextError(f"invalid opencode model metadata for {marker}")
    return value


def opencode_window(provider: str, model_id: str, cwd: str | None) -> int:
    try:
        proc = subprocess.run(
            ["opencode", "models", provider, "--verbose"],
            cwd=cwd if cwd and Path(cwd).is_dir() else None,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ContextError(f"cannot query opencode model registry: {exc}") from exc
    if proc.returncode != 0:
        raise ContextError(f"opencode model query failed: {proc.stderr.strip()}")
    record = decode_model_record(proc.stdout, provider, model_id)
    window = record.get("limit", {}).get("context")
    if not isinstance(window, int) or window <= 0:
        raise ContextError(f"opencode model {provider}/{model_id} has no context limit")
    return window


def get_opencode(ref: str, db_path: Path, window_override: int | None) -> dict[str, Any]:
    with closing(open_sqlite_ro(db_path)) as conn:
        rows = conn.execute(
            "SELECT data FROM message WHERE session_id = ? ORDER BY time_created DESC, id DESC",
            (ref,),
        )
        selected: dict[str, Any] | None = None
        newer_models: list[tuple[str, str]] = []
        for (raw,) in rows:
            try:
                data = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                continue
            tokens = data.get("tokens") or {}
            if data.get("role") != "assistant":
                continue
            provider = data.get("providerID")
            model_id = data.get("modelID")
            if isinstance(provider, str) and isinstance(model_id, str):
                newer_models.append((provider, model_id))
            if int(tokens.get("output") or 0) > 0:
                selected = data
                break
    if selected is None:
        raise ContextError("opencode session has no completed assistant message with non-zero output")

    tokens = selected.get("tokens") or {}
    cache = tokens.get("cache") or {}
    fields = [tokens.get("input"), tokens.get("output"), tokens.get("reasoning"), cache.get("read"), cache.get("write")]
    if any(not isinstance(value, (int, float)) for value in fields):
        raise ContextError("opencode assistant token record is incomplete")
    total = int(sum(fields))
    provider = selected.get("providerID")
    model_id = selected.get("modelID")
    if not isinstance(provider, str) or not isinstance(model_id, str):
        raise ContextError("opencode assistant record has no providerID/modelID")
    cwd = (selected.get("path") or {}).get("cwd")
    current_provider, current_model = newer_models[0] if newer_models else (provider, model_id)
    freshness = "stale-model-change" if (current_provider, current_model) != (provider, model_id) else "completed-call"
    window = window_override or opencode_window(current_provider, current_model, cwd)
    completed = (selected.get("time") or {}).get("completed")
    observed = (
        dt.datetime.fromtimestamp(completed / 1000, dt.timezone.utc).isoformat().replace("+00:00", "Z")
        if isinstance(completed, (int, float))
        else iso_now()
    )
    return {
        "total": total,
        "window": window,
        "source": "estimated" if window_override else "precise",
        "freshness": freshness,
        "observed_at": observed,
        "provider": current_provider,
        "model": current_model,
    }


def locate_codex_rollout(ref: str, db_path: Path) -> Path:
    if not re.fullmatch(r"[0-9A-Fa-f]{8}-(?:[0-9A-Fa-f]{4}-){3}[0-9A-Fa-f]{12}", ref):
        raise ContextError("invalid Codex session UUID")
    if db_path.is_file():
        with closing(open_sqlite_ro(db_path)) as conn:
            row = conn.execute("SELECT rollout_path FROM threads WHERE id = ?", (ref,)).fetchone()
        if row:
            path = Path(row[0]).expanduser().resolve()
            if path.is_file():
                return path
    pattern = str(Path.home() / ".codex/sessions" / "*/*/*" / f"rollout-*-{glob.escape(ref)}.jsonl")
    matches = [Path(item).resolve() for item in glob.glob(pattern)]
    if len(matches) != 1:
        raise ContextError(f"expected one Codex rollout for {ref}, found {len(matches)}")
    return matches[0]


def get_codex(ref: str, db_path: Path, window_override: int | None) -> dict[str, Any]:
    path = locate_codex_rollout(ref, db_path)
    selected: dict[str, Any] | None = None
    try:
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                payload = event.get("payload") or {}
                info = payload.get("info") or {}
                usage = info.get("last_token_usage") or {}
                if (
                    event.get("type") == "event_msg"
                    and payload.get("type") == "token_count"
                    and isinstance(usage.get("total_tokens"), (int, float))
                ):
                    selected = event
    except OSError as exc:
        raise ContextError(f"cannot read Codex rollout {path}: {exc}") from exc
    if selected is None:
        raise ContextError("Codex rollout has no completed token_count event")
    info = selected["payload"]["info"]
    total = int(info["last_token_usage"]["total_tokens"])
    recorded_window = info.get("model_context_window")
    window = window_override or recorded_window
    if not isinstance(window, (int, float)) or window <= 0:
        raise ContextError("Codex token_count event has no model_context_window")
    return {
        "total": total,
        "window": int(window),
        "source": "estimated" if window_override else "precise",
        "freshness": "completed-call",
        "observed_at": selected.get("timestamp") or iso_now(),
        "rollout_path": str(path),
    }


def allowed_pi_roots() -> list[Path]:
    roots = [Path.home() / ".pi/agent/sessions"]
    configured = os.environ.get("PI_SESSION_DIR")
    if configured:
        roots.append(Path(configured).expanduser())
    return [root.resolve() for root in roots if root.exists()]


def contained(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def get_pi(ref: str, helper: Path, window_override: int | None) -> dict[str, Any]:
    raw_path = Path(ref).expanduser()
    if raw_path.is_symlink():
        raise ContextError("Pi context ref must not be a symlink")
    path = raw_path.resolve(strict=True)
    if not path.is_file() or not any(contained(path, root) for root in allowed_pi_roots()):
        raise ContextError("Pi context ref is outside the allowed sessions roots")
    try:
        proc = subprocess.run(
            ["node", str(helper), str(path)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ContextError(f"Pi helper failed: {exc}") from exc
    if proc.returncode != 0:
        raise ContextError(f"Pi helper failed: {proc.stderr.strip()}")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ContextError(f"Pi helper returned invalid JSON: {exc}") from exc
    window = window_override or data.get("window")
    if not isinstance(window, (int, float)) or window <= 0:
        raise ContextError("Pi model registry has no context window; provide --window explicitly")
    data["window"] = int(window)
    data["source"] = "estimated" if window_override else "precise"
    return data


def main() -> int:
    args = parse_args()
    try:
        if args.kind == "opencode":
            data = get_opencode(args.context_ref, args.opencode_db, args.window)
        elif args.kind == "codex":
            data = get_codex(args.context_ref, args.codex_db, args.window)
        else:
            data = get_pi(args.context_ref, args.pi_helper, args.window)
        total = data.get("total")
        window = data.get("window")
        if not isinstance(total, (int, float)) or total < 0:
            raise ContextError("adapter returned an invalid total")
        if not isinstance(window, (int, float)) or window <= 0:
            raise ContextError("adapter returned an invalid window")
        result = {
            "kind": args.kind,
            "context_ref": args.context_ref,
            **data,
            "total": int(total),
            "window": int(window),
            "pct": float(total) / float(window),
        }
        print(json.dumps(result, separators=(",", ":"), ensure_ascii=False))
        return 0
    except Exception as exc:  # one stable machine-readable error surface
        print(
            json.dumps(
                {"kind": args.kind, "context_ref": args.context_ref, "error": str(exc), "observed_at": iso_now()},
                separators=(",", ":"),
                ensure_ascii=False,
            )
        )
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
