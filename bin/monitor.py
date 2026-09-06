#!/usr/bin/env python3
"""Deterministic context monitor for herdr-ticket-dispatcher."""

from __future__ import annotations

import argparse
import datetime as dt
import errno
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def load_json(path: Path, default: Any = None) -> Any:
    try:
        with path.open(encoding="utf-8") as stream:
            return json.load(stream)
    except FileNotFoundError:
        return default


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--fifo", type=Path)
    parser.add_argument("--interval", type=float, default=float(os.environ.get("MONITOR_INTERVAL", "60")))
    parser.add_argument("--abs-limit", type=int, default=int(os.environ.get("ABS_LIMIT", "300000")))
    parser.add_argument("--pct-limit", type=float, default=float(os.environ.get("PCT_LIMIT", "0.80")))
    parser.add_argument("--error-limit", type=int, default=3)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--notify", action="store_true")
    args = parser.parse_args()
    if args.interval <= 0 or args.abs_limit <= 0 or not 0 < args.pct_limit <= 1 or args.error_limit <= 0:
        parser.error("invalid monitor threshold")
    return args


def running_attempts(state: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    result = []
    for ticket_id, ticket in (state.get("tickets") or {}).items():
        for attempt in ticket.get("attempts") or []:
            if attempt.get("state") == "running" and attempt.get("context_ref"):
                result.append((ticket_id, attempt))
    return result


def send_event(fifo: Path | None, event: dict[str, Any]) -> None:
    line = (json.dumps(event, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
    if fifo is None:
        print(line.decode().rstrip(), flush=True)
        return
    try:
        fd = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
    except OSError as exc:
        if exc.errno in (errno.ENXIO, errno.ENOENT):
            print(f"monitor event channel unavailable: {exc}", file=sys.stderr)
            return
        raise
    try:
        os.write(fd, line)
    finally:
        os.close(fd)


def notify(title: str, body: str) -> None:
    subprocess.run(
        ["herdr", "notification", "show", title, "--body", body, "--sound", "request"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def observe(helper: Path, kind: str, ref: str, window: int | None) -> tuple[int, dict[str, Any]]:
    command = [sys.executable, str(helper), kind, ref, "--json"]
    if window:
        command.extend(["--window", str(window)])
    proc = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        payload = {"error": f"adapter emitted invalid JSON; stderr={proc.stderr.strip()}", "observed_at": now()}
    return proc.returncode, payload


def advance_counter(
    counter: dict[str, Any],
    code: int,
    sample: dict[str, Any],
    *,
    abs_limit: int,
    pct_limit: float,
    error_limit: int,
) -> str | None:
    """Apply one sample and return trip/error when an event becomes due."""
    counter["last_sample"] = sample
    counter["sampled_at"] = now()
    if code != 0 or sample.get("error"):
        counter["errors"] = int(counter.get("errors", 0)) + 1
        if counter["errors"] >= error_limit and not counter.get("error_reported"):
            counter["error_reported"] = True
            return "error"
        return None

    counter["errors"] = 0
    counter["error_reported"] = False
    if sample.get("freshness") == "stale-model-change" or not sample.get("window"):
        return None
    over = int(sample["total"]) >= abs_limit or float(sample["pct"]) >= pct_limit
    counter["over"] = int(counter.get("over", 0)) + 1 if over else 0
    if counter["over"] == 1:
        return "trip"
    return None


def main() -> int:
    args = parse_args()
    run_dir = args.state.parent
    counters_path = run_dir / "monitor-state.json"
    sentinels = run_dir / "sentinels"
    helper = Path(__file__).with_name("get_context.py")
    counters = load_json(counters_path, {}) or {}

    while True:
        state = load_json(args.state)
        if not isinstance(state, dict):
            send_event(args.fifo, {"type": "monitor-exit", "reason": "state-unreadable", "at": now()})
            return 2
        active_keys: set[str] = set()
        for ticket_id, attempt in running_attempts(state):
            attempt_no = int(attempt["attempt"])
            key = f"{state.get('run_id')}:{ticket_id}:a{attempt_no}"
            active_keys.add(key)
            counter = counters.setdefault(key, {"over": 0, "errors": 0, "error_reported": False})
            code, sample = observe(
                helper,
                state.get("kind") or attempt.get("kind"),
                attempt["context_ref"],
                attempt.get("context_window"),
            )
            action = advance_counter(
                counter,
                code,
                sample,
                abs_limit=args.abs_limit,
                pct_limit=args.pct_limit,
                error_limit=args.error_limit,
            )
            if action == "error":
                event = {
                    "type": "monitor-error",
                    "run_id": state.get("run_id"),
                    "ticket": ticket_id,
                    "attempt": attempt_no,
                    "sample": sample,
                    "at": now(),
                }
                send_event(args.fifo, event)
                if args.notify:
                    notify("Ticket monitor error", f"{ticket_id} attempt {attempt_no}: {sample.get('error')}")
            elif action == "trip":
                sentinel = {
                    "type": "context-trip",
                    "run_id": state.get("run_id"),
                    "ticket": ticket_id,
                    "attempt": attempt_no,
                    "sample": sample,
                    "at": now(),
                }
                atomic_json(sentinels / f"{ticket_id}-a{attempt_no}.json", sentinel)
                send_event(args.fifo, sentinel)
                if args.notify:
                    notify("Ticket context limit", f"{ticket_id} attempt {attempt_no} reached {sample['pct']:.1%}")

        counters = {key: value for key, value in counters.items() if key in active_keys}
        atomic_json(counters_path, counters)
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
