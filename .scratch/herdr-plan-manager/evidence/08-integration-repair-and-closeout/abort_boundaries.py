#!/usr/bin/env python3
"""Real Git fault injection for the documented preservation decisions, no agents.

Leaves the disposable conflicted scene and injected index lock intact as evidence.
This is a procedure exercise, not an automatic production abort implementation.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
OUT = HERE / "logs/abort-boundaries.json"
assert not OUT.exists(), "preserve prior evidence"
repo = Path(tempfile.mkdtemp(prefix="hpm08-abort-", dir="/tmp/opencode"))
commands = []


def git(*args, expected=0):
    p = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True)
    commands.append(dict(argv=["git", *args], cwd=str(repo), exit_code=p.returncode,
                         stdout=p.stdout, stderr=p.stderr))
    assert p.returncode == expected, commands[-1]
    return p.stdout.strip()


def scene():
    return {name: hashlib.sha256((repo / name).read_bytes()).hexdigest()
            for name in ("feature.txt", ".git/index", ".git/MERGE_HEAD")}


git("init", "-b", "main")
(repo / "feature.txt").write_text("seed\n")
git("add", "feature.txt")
git("commit", "-m", "seed abort boundary fixture")
git("switch", "-c", "incoming")
(repo / "feature.txt").write_text("incoming\n")
git("add", "feature.txt")
git("commit", "-m", "incoming fixture change")
incoming = git("rev-parse", "HEAD")
git("switch", "main")
(repo / "feature.txt").write_text("target\n")
git("add", "feature.txt")
git("commit", "-m", "target fixture change")
target = git("rev-parse", "HEAD")
assert git("status", "--porcelain") == ""

# Unknown user work: preserve it and defer initiating a merge.
(repo / "user-note.txt").write_text("unclassified user work (fixture)\n")
user_hash = hashlib.sha256((repo / "user-note.txt").read_bytes()).hexdigest()
dirty = git("status", "--porcelain")
assert "user-note.txt" in dirty
dirty_decision = "defer merge; preserve and clarify user work"
assert git("rev-parse", "HEAD") == target
# For the next independent exercise, declare the fixture note explicitly owned
# and commit it. No discarded content and no forced cleanup.
git("add", "user-note.txt")
git("commit", "-m", "preserve identified fixture note")
target = git("rev-parse", "HEAD")
assert git("status", "--porcelain") == ""
git("merge", "--no-edit", incoming, expected=1)
before = scene()

# An observer with no initiating-merge record takes no abort action.
unowned_decision = "retain operation and report unknown ownership; no abort"
assert scene() == before
assert git("rev-parse", "MERGE_HEAD") == incoming
assert git("rev-parse", "HEAD") == target

# The actual initiator has clean preconditions. Inject an exclusive index lock
# to force a mechanical abort failure without modifying business files.
(repo / ".git/index.lock").write_text("fault injection: preserve this lock and scene\n")
git("merge", "--abort", expected=128)
assert scene() == before
assert git("rev-parse", "HEAD") == target
assert hashlib.sha256((repo / "user-note.txt").read_bytes()).hexdigest() == user_hash
assert (repo / ".git/index.lock").exists()
result = dict(repo=str(repo), target=target, incoming=incoming, commands=commands,
              user_work_decision=dirty_decision, unowned_decision=unowned_decision,
              abort_failure_decision="blocked; preserve scene and lock, report; no reset or clean",
              before=before, after=scene(), preserved=True,
              scope="real Git plus controlled fault injection; coordinator procedure, no runtime worker")
OUT.write_text(json.dumps(result, indent=2) + "\n")
print(f"PASS: user work, unowned merge and failed abort preserved; evidence {OUT}; scene {repo}")
