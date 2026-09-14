#!/usr/bin/env python3
"""Compatibility CLI for existing Herdr Plan Manager executions.

New executions should use implementer.py. Keep this entry and the installation
path available until every old worker/supervisor has stopped.
"""

from pathlib import Path

import implementer


def main() -> int:
    # Preserve old defaults and the executable used by background supervision.
    # State paths and JSON fields are deliberately not migrated or rewritten.
    implementer.DEFAULT_STATE_DIR = "herdr-plan-manager"
    implementer.DEFAULT_BRANCH_PREFIX = "hpm"
    implementer.IMPLEMENTER_PATH = Path(__file__).resolve()
    return implementer.main()


if __name__ == "__main__":
    raise SystemExit(main())
