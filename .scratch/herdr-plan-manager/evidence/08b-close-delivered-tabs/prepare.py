#!/usr/bin/env python3
"""Snapshot the current implementation into a disposable repo and dispatch 08-R2.

The snapshot includes the uncommitted 08-R1 fix and new operation references;
it does not claim integration into the implementation project's main branch.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[3]
META = HERE / "location.json"
assert os.environ.get("HERDR_ENV") == "1"
assert not META.exists(), "preserve existing attempt"
root = Path(tempfile.mkdtemp(prefix="hpm08-tabs-", dir="/tmp/opencode"))
repo = root / "source"
logs = HERE / "logs"
logs.mkdir(exist_ok=True)


def run(name, argv, cwd=None, input=None):
    p = subprocess.run(list(map(str, argv)), cwd=cwd, input=input, text=True, capture_output=True)
    (logs / f"{name}.json").write_text(json.dumps(dict(argv=list(map(str, argv)), cwd=str(cwd) if cwd else None,
                                                     exit_code=p.returncode, stdout=p.stdout, stderr=p.stderr), indent=2))
    print(f"{name}: exit={p.returncode}", flush=True)
    assert p.returncode == 0, p.stderr
    return p.stdout.strip()


run("clone", ["git", "clone", "--no-hardlinks", PROJECT, repo])
original = run("project-head", ["git", "rev-parse", "HEAD"], PROJECT)
patch = run("snapshot-diff", ["git", "diff", "--binary", "HEAD", "--", "bin", "tests", "docs", "prompts"], PROJECT)
if patch:
    run("apply-snapshot", ["git", "apply", "-"], repo, input=patch + "\n")
for relative in ("docs/plan-management/repair-and-closeout.md", "docs/plan-management/repair-brief.md"):
    shutil.copy2(PROJECT / relative, repo / relative)
run("snapshot-stage", ["git", "add", "bin", "tests", "docs", "prompts"], repo)
run("snapshot-commit", ["git", "commit", "-m", "snapshot current issue08 delivery for isolated tab release repair"], repo)
base = run("snapshot-base", ["git", "rev-parse", "HEAD"], repo)
(HERE / "location.json").write_text(json.dumps({"root": str(root), "repo": str(repo), "original_head": original,
                                               "base": base, "worker": "w08-tab-release", "run": "issue08-tab-release"}, indent=2))
hpm = PROJECT / "bin/plan_manager.py"
run("init", [sys.executable, hpm, "init-run", "--repo", repo, "--run-id", "issue08-tab-release", "--kind", "pi",
             "--provider", "opencode-go", "--model", "deepseek-v4.1-flash", "--thinking", "max", "--max-workers", "3"])
run("start", [sys.executable, hpm, "start", "--repo", repo, "--run", "issue08-tab-release", "--ticket-id", "08-R2",
              "--worker-id", "w08-tab-release", "--base", base,
              "--material", PROJECT / ".scratch/herdr-plan-manager/issues/08b-close-delivered-tabs.md",
              "--material", PROJECT / ".scratch/herdr-plan-manager/项目重建方案.md",
              "--instructions", "Implement only 08-R2 in your assigned isolated worktree. The snapshot already includes the 08-R1 stale-test fix and current docs. Follow the rebuild plan, not the legacy root dispatcher SKILL. Use your own tools; no subagents or real extra workers. Add meaningful deterministic regression coverage, reuse runtime adapters, preserve result semantics and foreign pane ownership. For old delivered records reuse stop to trigger the same safe release. Avoid new policy framework. Run focused tests while developing and the full suite once at the end. Commit only bin/tests/prompts/docs changes and publish result. Coordinator owns real runtime smoke and shared issue/evidence records."])
print(repo)
