#!/usr/bin/env python3
"""Manual smoke actions. No scheduler: master chooses each action and integration.

Usage: smoke.py setup | <pi|opencode> <init|start-b|start-a|wait|status|merge-a|start-c|release-b|finish|retain>
Commands and responses are retained under logs/real-<kind>.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[3]
HPM = PROJECT / "bin/plan_manager.py"
META = HERE / "smoke-location.json"


def call(argv, cwd=None):
    proc = subprocess.run([str(v) for v in argv], cwd=cwd, text=True, capture_output=True)
    return {"argv": [str(v) for v in argv], "cwd": str(cwd) if cwd else None,
            "exit_code": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}


def save_call(label, argv, cwd=None):
    result = call(argv, cwd)
    path = LOG / f"{label}.json"
    if path.exists():
        index = 2
        while (LOG / f"{label}-{index}.json").exists():
            index += 1
        path = LOG / f"{label}-{index}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["exit_code"]:
        raise SystemExit(result["exit_code"])
    return result["stdout"]


def pm(label, *args):
    return json.loads(save_call(label, [sys.executable, HPM, args[0], "--repo", REPO, *args[1:]]))


def git(*args):
    result = call(["git", *args], REPO)
    if result["exit_code"]:
        raise RuntimeError(result)
    return result["stdout"].strip()


if sys.argv[1] == "setup":
    assert os.environ.get("HERDR_ENV") == "1"
    assert not META.exists(), "preserve prior smoke location"
    root = Path(tempfile.mkdtemp(prefix="hpm07-real-"))
    META.write_text(json.dumps({"root": str(root)}, indent=2))
    for kind in ("pi", "opencode"):
        repo = root / kind
        repo.mkdir()
        for argv in (["git", "init", "-b", "main"], ["git", "config", "user.name", "HPM Smoke"],
                     ["git", "config", "user.email", "hpm-smoke@example.invalid"]):
            assert call(argv, repo)["exit_code"] == 0
        (repo / ".gitignore").write_text(".scratch/\n")
        (repo / "README.md").write_text("# Issue 07 isolated smoke\nNormal integration policy: fast-forward when possible, otherwise a normal merge.\n")
        assert call(["git", "add", "."], repo)["exit_code"] == 0
        assert call(["git", "commit", "-m", "initialize smoke fixture"], repo)["exit_code"] == 0
        material = repo / ".scratch"
        material.mkdir()
        (material / "spec.md").write_text("""# Dynamic integration smoke
Complete A, B and C with independent workers. A and B have no code dependency; C depends only on A integrated into main.
A creates api.txt containing exactly `HPM07_API_V1\n` after an automatic session handoff.
B produces independent b.txt containing exactly `HPM07_B_OK\n` after the observer releases its acceptance checkpoint.
C reads the integrated api.txt and writes consumer.txt containing exactly `consumer:HPM07_API_V1\n`.
Each worker commits only its own business files, runs exact content assertions and records evidence.
Whole goal: all three outputs integrated into main with exact contents, automatic handoff and dynamic ordering proven.
Do not spawn subagents. This is a tiny fixture; use your own tools. Do not implement a scheduler.
""")
        gate = repo / ".git" / "release-b"
        (material / "B.md").write_text(f"""# Ticket B (no dependency)
Create b.txt with HPM07_B_OK plus newline and test the exact contents; commit it.
Acceptance includes observing the master checkpoint at absolute path `{gate}` before declaring delivered.
This is a controlled concurrency experiment, not a user permission prompt. Remain working while that file is absent.
Use a foreground Python loop that only reads gate existence and sleeps 1 second, with no total task deadline.
If your tool window expires, continue checking; only the actual file releases this checkpoint.
When present, verify b.txt again and write your contract result. Never create or change the gate yourself.
""")
        (material / "A.md").write_text("""# Ticket A (no dependency)
This ticket's acceptance specifically tests automatic context handoff with meaningful remaining work.
INITIAL SESSION ONLY: read corpus.txt from the materials snapshot in chunks of at most 400 lines to bring corpus rows into your model context (not a summary/hash). Stop after 2400 rows or when the tool requests handoff, whichever comes first.
After at least one chunk, save uncommitted seed-note.txt saying `seed prepared before handoff` in your worktree.
Do NOT create api.txt or declare delivered in the initial session. After reading, keep a foreground Python sleep loop running until the lifecycle tool interrupts it and asks for handoff. This pause is the controlled handoff acceptance checkpoint, not a need for user input.
Follow the tool's handoff instructions. In the handoff Next steps, include the exact token HPM07_RESUMED_OK and say the next session must NOT reread corpus, must create api.txt with HPM07_API_V1 plus newline, and must create resumed.txt with HPM07_RESUMED_OK plus newline as proof of reading this handoff.
CONTINUATION SESSION (when given a previous-session handoff by the tool): immediately perform those Next steps, assert exact api.txt/resumed.txt contents, commit ONLY api.txt and resumed.txt, leave seed-note.txt uncommitted intentionally, and deliver. Report the uncommitted note in remaining. Do not return to the initial-session corpus/checkpoint.
""")
        (material / "C.md").write_text("""# Ticket C (depends on A integrated)
Read api.txt inherited from your base; assert it equals HPM07_API_V1 plus newline.
Write consumer.txt as `consumer:` plus the content of api.txt; assert exact consumer:HPM07_API_V1 plus newline.
Commit consumer.txt only and deliver with verification evidence. Do not modify api.txt or fabricate it if missing.
""")
        (material / "corpus.txt").write_text("".join(
            f"row {i:04d}: record={i*7919:08x} test-vector {i*37:08d} alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike november oscar papa quebec romeo sierra tango uniform victor whiskey xray yankee zulu\n"
            for i in range(2400)))
    print(root)
    raise SystemExit(0)

KIND, ACTION = sys.argv[1:3]
assert KIND in ("pi", "opencode")
REPO = Path(json.loads(META.read_text())["root"]) / KIND
LOG = HERE / "logs" / f"real-{KIND}"
LOG.mkdir(parents=True, exist_ok=True)
RUN = f"issue07-{KIND}"
MATERIAL = REPO / ".scratch"

if ACTION == "init":
    pm("init", "init-run", "--run-id", RUN, "--kind", KIND, "--provider", "opencode-go",
       "--model", "deepseek-v4.1-flash", "--thinking", "max", "--max-workers", "3")
elif ACTION.startswith("start-"):
    role = ACTION[-1].upper()
    args = ["start", "--run", RUN, "--ticket-id", role, "--worker-id", f"w07-{KIND}-{role.lower()}",
            "--title", f"real dynamic smoke {role}", "--base", git("rev-parse", "HEAD"),
            "--material", str(MATERIAL / "spec.md"), "--material", str(MATERIAL / f"{role}.md"),
            "--instructions", "Read the single ticket and spec. Follow the staged acceptance exactly. Do not spawn subagents."]
    if role == "A":
        args += ["--material", str(MATERIAL / "corpus.txt"), "--handoff-tokens", "35000"]
    pm(ACTION, *args)
elif ACTION == "status":
    pm("status-run", "status", "--run", RUN)
    for role in ("b", "a", "c"):
        if (REPO / ".git/herdr-plan-manager/workers" / f"w07-{KIND}-{role}").exists():
            pm(f"status-{role}", "status", "--worker", f"w07-{KIND}-{role}")
elif ACTION == "wait":
    pm("wait", "wait", "--run", RUN, "--timeout", "30")
elif ACTION == "merge-a":
    facts = pm("a-before-merge", "status", "--worker", f"w07-{KIND}-a")
    assert facts["result"]["status"] == "delivered"
    assert git("status", "--porcelain") == ""
    before = git("rev-parse", "HEAD")
    for item in facts["pending_items"]:
        pm("ack-a", "ack", "--item", item["item_id"], "--note", "A accepted; integration pending")
    assert git("rev-parse", "HEAD") == before
    assert not (REPO / "api.txt").exists()
    save_call("merge-a", ["git", "merge", "--ff-only", facts["result"]["head"]], REPO)
    (LOG / "integration-a.json").write_text(json.dumps({"before": before, "delivery": facts["result"]["head"], "integrated": git("rev-parse", "HEAD")}, indent=2))
elif ACTION == "release-b":
    assert (LOG / "start-c.json").exists()
    (REPO / ".git/release-b").write_text("C started from integrated A; B may deliver.\n")
    save_call("b-at-release", [sys.executable, HPM, "status", "--repo", REPO, "--worker", f"w07-{KIND}-b"])
elif ACTION == "finish":
    for role in ("c", "b"):
        facts = pm(f"{role}-before-merge", "status", "--worker", f"w07-{KIND}-{role}")
        assert facts["result"]["status"] == "delivered"
        assert git("status", "--porcelain") == ""
        before = git("rev-parse", "HEAD")
        save_call(f"merge-{role}", ["git", "merge", "--no-edit", facts["result"]["head"]], REPO)
        (LOG / f"integration-{role}.json").write_text(json.dumps({"before": before, "delivery": facts["result"]["head"], "integrated": git("rev-parse", "HEAD")}, indent=2))
        for item in facts["pending_items"]:
            pm(f"ack-{role}", "ack", "--item", item["item_id"], "--note", f"{role.upper()} integrated")
    save_call("combined-verification", [sys.executable, "-c", "from pathlib import Path; expected={'api.txt':'HPM07_API_V1\\n','resumed.txt':'HPM07_RESUMED_OK\\n','consumer.txt':'consumer:HPM07_API_V1\\n','b.txt':'HPM07_B_OK\\n'}; [None if Path(p).read_text()==v else (_ for _ in ()).throw(AssertionError(p)) for p,v in expected.items()]; print('all four integrated outputs match')"], REPO)
    save_call("git-log", ["git", "log", "--oneline", "--graph", "--all"], REPO)
    pm("final-run", "status", "--run", RUN)
elif ACTION == "retain":
    for role in ("a", "b", "c"):
        facts = pm(f"stop-{role}", "stop", "--worker", f"w07-{KIND}-{role}", "--reason", "smoke integrated; preserve scene pending explicit cleanup")
        assert facts["business_stopped"]
else:
    raise SystemExit("unknown manual action")
