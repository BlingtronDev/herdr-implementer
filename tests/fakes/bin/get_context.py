#!/usr/bin/env python3
"""Deterministic context adapter for implementer tests.

Environment:
- HI_FAKE_DIR               marker/counter directory (required for counters)
- HI_FAKE_CONTEXT_COUNTER   counter filename, shared across sessions (default context_calls)
- HI_FAKE_CONTEXT_MODE      ok (default) | stale | error
- HI_FAKE_CONTEXT_TOTAL     baseline current-context tokens (default 10)
- HI_FAKE_CONTEXT_WINDOW    model context window (default 1000)
- HI_FAKE_CONTEXT_SPIKE_TOTAL   token value for the first spike calls
- HI_FAKE_CONTEXT_SPIKE_CALLS   number of calls that report the spike value
- HI_FAKE_CONTEXT_ERROR     error text used in error mode
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def arg_value(flag: str) -> str | None:
    if flag in sys.argv:
        index = sys.argv.index(flag)
        if index + 1 < len(sys.argv):
            return sys.argv[index + 1]
    return None


def main() -> int:
    kind = sys.argv[1] if len(sys.argv) > 1 else "pi"
    ref = sys.argv[2] if len(sys.argv) > 2 else ""
    mode = os.environ.get("HI_FAKE_CONTEXT_MODE", "ok")
    if mode == "error":
        payload = {
            "kind": kind,
            "context_ref": ref,
            "error": os.environ.get("HI_FAKE_CONTEXT_ERROR", "fake: no completed assistant usage"),
            "observed_at": now(),
        }
        print(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
        print(payload["error"], file=sys.stderr)
        return 1

    calls = 0
    root = os.environ.get("HI_FAKE_DIR")
    if root:
        counter = Path(root) / os.environ.get("HI_FAKE_CONTEXT_COUNTER", "context_calls")
        calls = int(counter.read_text()) + 1 if counter.is_file() else 1
        counter.write_text(str(calls))

    base_total = int(os.environ.get("HI_FAKE_CONTEXT_TOTAL", "10"))
    spike_total = int(os.environ.get("HI_FAKE_CONTEXT_SPIKE_TOTAL", str(base_total)))
    spike_calls = int(os.environ.get("HI_FAKE_CONTEXT_SPIKE_CALLS", "0"))
    total = spike_total if calls and calls <= spike_calls else base_total

    override = arg_value("--window")
    window = int(override) if override else int(os.environ.get("HI_FAKE_CONTEXT_WINDOW", "1000"))
    payload = {
        "kind": kind,
        "context_ref": ref,
        "total": total,
        "window": window,
        "pct": total / window if window else 0.0,
        "source": "estimated" if override else "precise",
        "freshness": "stale-model-change" if mode == "stale" else "completed-call",
        "observed_at": now(),
    }
    print(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
