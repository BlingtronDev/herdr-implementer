#!/usr/bin/env python3
"""Manual real-runtime verification and explicit old-worker tab release.

Run after the repaired implementation is incorporated in the project worktree.
Actions: setup | <pi|opencode> start|wait|verify | old-before|old-close.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[3]
HPM = PROJECT / "bin/plan_manager.py"
ROOT = Path(json.loads((HERE / "location.json").read_text())["root"])
LOG = HERE / "logs"


def save(name, value):
    path = LOG / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    i = 2
    while path.exists():
        path = LOG / f"{name}-{i}.json"
        i += 1
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def call(name, argv, cwd=None, expected=0):
    p = subprocess.run(list(map(str, argv)), cwd=cwd, text=True, capture_output=True)
    save(name, dict(argv=list(map(str, argv)), cwd=str(cwd) if cwd else None,
                    exit_code=p.returncode, stdout=p.stdout, stderr=p.stderr))
    print(f"{name}: exit={p.returncode}", flush=True)
    assert p.returncode == expected, p.stderr
    return p.stdout.strip()


def pm(name, repo, action, *args):
    return json.loads(call(name, [sys.executable, HPM, action, "--repo", repo, *args]))


def git(name, repo, *args):
    return call(name, ["git", *args], repo)


def wait_tab_closed(name, tab):
    deadline = time.monotonic() + 120  # Test protocol observation window, not ticket duration.
    observations = []
    while time.monotonic() < deadline:
        p = subprocess.run(["herdr", "tab", "get", tab], text=True, capture_output=True)
        observations.append({"at": time.time(), "exit_code": p.returncode,
                             "stdout": p.stdout, "stderr": p.stderr})
        if p.returncode:
            raw = p.stdout + p.stderr
            if "tab_not_found" in raw:
                save(name, observations)
                return
            save(name, observations)
            raise AssertionError(raw)
        time.sleep(1)
    save(name, observations)
    raise AssertionError(f"registered tab {tab} remained present during the close observation window")


def disk_facts(repo, worker):
    path = repo / ".git/herdr-plan-manager/workers" / worker
    state = json.loads((path / "state.json").read_text())
    result = path / "result.json"
    worktree = Path(state["worktree"])
    facts = {"worker": worker, "worktree": str(worktree), "branch": state["branch"],
             "head": git(f"{worker}-head", repo, "rev-parse", state["branch"]),
             "status": git(f"{worker}-dirty", worktree, "status", "--porcelain"),
             "result_sha256": hashlib.sha256(result.read_bytes()).hexdigest(),
             "tabs": [s["tab"] for s in state["sessions"]]}
    assert worktree.is_dir() and result.is_file()
    return facts


def old_workers():
    previous = json.loads((HERE.parent / "08-integration-repair-and-closeout/location.json").read_text())
    for kind in ("pi", "opencode"):
        for role in ("a", "b", "r"):
            yield Path(previous["root"]) / kind, f"w08-{kind}-{role}"
    yield Path("/tmp/opencode/hpm08-test-repair"), "w08-stale-test-fix"


action = sys.argv[1]
assert os.environ.get("HERDR_ENV") == "1"
if action == "setup":
    for kind in ("pi", "opencode"):
        repo = ROOT / f"{kind}-smoke"
        repo.mkdir()
        git(f"{kind}/git-init", repo, "init", "-b", "main")
        (repo / ".gitignore").write_text(".scratch/\n")
        (repo / "README.md").write_text("# Automatic delivered-tab release fixture\n")
        git(f"{kind}/seed-add", repo, "add", ".")
        git(f"{kind}/seed-commit", repo, "commit", "-m", "initialize delivered tab fixture")
        (repo / ".scratch").mkdir()
        (repo / ".scratch/spec.md").write_text("# Goal and complete initial ticket set\nOne ticket T: create answer.txt containing exactly HPM08_TAB_RELEASE_OK plus newline, assert its content, commit only that file, publish a valid delivered result. Do not spawn subagents. Coordinator verifies automatic terminal release and retained worktree/branch/result.\n")
        pm(f"{kind}/init", repo, "init-run", "--run-id", f"tabs-{kind}", "--kind", kind,
           "--provider", "opencode-go", "--model", "deepseek-v4.1-flash", "--thinking", "max", "--max-workers", "3")
elif action in ("pi", "opencode"):
    kind, step = sys.argv[1:3]
    repo = ROOT / f"{kind}-smoke"
    worker = f"w08-tabs-{kind}"
    if step == "start":
        base = git(f"{kind}/baseline", repo, "rev-parse", "HEAD")
        pm(f"{kind}/start", repo, "start", "--run", f"tabs-{kind}", "--ticket-id", "T", "--worker-id", worker,
           "--base", base, "--material", repo / ".scratch/spec.md",
           "--instructions", "Complete the one tiny ticket yourself, verify and commit it, then publish the result and end your turn. Do not spawn subagents or close your own tab; the lifecycle supervisor will release the completed terminal.")
    elif step == "wait":
        pm(f"{kind}/wait", repo, "wait", "--run", f"tabs-{kind}", "--timeout", "30")
    elif step == "verify":
        status = pm(f"{kind}/delivered", repo, "status", "--worker", worker)
        assert status["result"]["status"] == "delivered"
        before = disk_facts(repo, worker)
        assert not before["status"]
        for tab in before["tabs"]:
            wait_tab_closed(f"{kind}/tab-closed", tab)
        after = disk_facts(repo, worker)
        assert before == after
        assert (Path(after["worktree"]) / "answer.txt").read_text() == "HPM08_TAB_RELEASE_OK\n"
        final = pm(f"{kind}/final-status", repo, "status", "--worker", worker)
        assert final["lifecycle"]["state"] == "delivered"
        assert not final["herdr"]["agent_present"]
        assert not final["supervisor"]["warnings"]
        assert all(item["kind"] == "delivery" for item in final["items"])
        for item in final["pending_items"]:
            pm(f"{kind}/ack", repo, "ack", "--item", item["item_id"], "--note", "automatic tab release verified; artifacts retained, fixture branch not integrated")
        save(f"{kind}/preserved-disk", after)
    else:
        raise SystemExit("unknown per-runtime action")
elif action == "old-before":
    entries = []
    for repo, worker in old_workers():
        status = pm(f"old/{worker}-before", repo, "status", "--worker", worker)
        assert status["result"]["status"] == "delivered" and not status["supervisor"]["alive"]
        facts = disk_facts(repo, worker)
        assert not facts["status"]
        for tab in facts["tabs"]:
            call(f"old/{worker}-tab-before", ["herdr", "tab", "get", tab])
        entries.append(facts)
    save("old/before", entries)
elif action == "old-close":
    before = json.loads((LOG / "old/before.json").read_text())
    for repo, worker in old_workers():
        recorded = next(f for f in before if f["worker"] == worker)
        assert disk_facts(repo, worker) == recorded
        stopped = pm(f"old/{worker}-stop", repo, "stop", "--worker", worker, "--reason", "user confirmed closing normally delivered tabs; retain all disk resources")
        assert stopped["business_stopped"]
        for tab in recorded["tabs"]:
            wait_tab_closed(f"old/{worker}-tab-closed", tab)
        assert disk_facts(repo, worker) == recorded
        pm(f"old/{worker}-stop-again", repo, "stop", "--worker", worker, "--reason", "verify idempotent completed-tab release")
        pm(f"old/{worker}-after", repo, "status", "--worker", worker)
    save("old/verified", {"count": len(before), "disk_unchanged": True, "tabs_closed": True})
elif action == "finish-worker":
    meta = json.loads((HERE / "location.json").read_text())
    repo, worker = Path(meta["repo"]), meta["worker"]
    before = disk_facts(repo, worker)
    status = pm("repair-worker/before", repo, "status", "--worker", worker)
    assert status["result"]["status"] == "delivered"
    for item in status["pending_items"]:
        pm("repair-worker/ack", repo, "ack", "--item", item["item_id"], "--note", "accepted exact patch and real dual-runtime closure evidence; project working-tree delivery pending commit")
    stopped = pm("repair-worker/stop", repo, "stop", "--worker", worker, "--reason", "repair verified; release completed tab and preserve all disk artifacts")
    assert stopped["business_stopped"]
    for tab in before["tabs"]:
        wait_tab_closed("repair-worker/tab-closed", tab)
    assert before == disk_facts(repo, worker)
    pm("repair-worker/after", repo, "status", "--worker", worker)
else:
    raise SystemExit("unknown action")
