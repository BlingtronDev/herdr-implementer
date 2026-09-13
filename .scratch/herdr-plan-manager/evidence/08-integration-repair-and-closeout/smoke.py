#!/usr/bin/env python3
"""Explicit coordinator actions for disposable real-worker repair experiments.

No scheduling: invoke one named action only after inspecting the prior evidence.
Usage: smoke.py setup | <pi|opencode> <init|start-A|start-B|wait|inputs|start-R|move-target|integrate|retain|capture>
Checks: checks (full suite), checks-local (reuse supplemental worker suite), checks-docs.
Supplemental evidence: capture-test-repair; stop-test-fakes stops only experiment test processes.
"""
import json
import ast
import os
from pathlib import Path
import re
import signal
import shutil
import sqlite3
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[3]
HPM = PROJECT / "bin/plan_manager.py"
META = HERE / "location.json"


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def call(argv, cwd=None):
    p = subprocess.run(list(map(str, argv)), cwd=cwd, text=True, capture_output=True)
    return dict(argv=list(map(str, argv)), cwd=str(cwd) if cwd else None,
                exit_code=p.returncode, stdout=p.stdout, stderr=p.stderr)


def run(label, argv, cwd=None, expected=0):
    result = call(argv, cwd)
    path = LOG / f"{label}.json"
    n = 2
    while path.exists():
        path = LOG / f"{label}-{n}.json"
        n += 1
    write(path, result)
    print(f"{label}: exit={result['exit_code']} evidence={path.relative_to(HERE)}")
    if result["exit_code"] != expected:
        print(json.dumps(result, ensure_ascii=False))
    assert result["exit_code"] == expected, result
    return result["stdout"].strip()


def git(label, *args, expected=0):
    return run(label, ["git", *args], REPO, expected)


def pm(label, action, *args):
    return json.loads(run(label, [sys.executable, HPM, action, "--repo", REPO, *args]))


def state(role):
    return pm(f"status-{role}", "status", "--worker", f"w08-{KIND}-{role.lower()}")


def snapshot_target(label):
    facts = {"branch": git(label + "-branch", "branch", "--show-current"),
             "head": git(label + "-head", "rev-parse", "HEAD"),
             "status": git(label + "-status", "status", "--porcelain"),
             "unmerged": git(label + "-index", "ls-files", "-u")}
    git_dir = Path(git(label + "-git-dir", "rev-parse", "--absolute-git-dir"))
    facts["operations"] = [p for p in ("MERGE_HEAD", "rebase-merge", "rebase-apply", "CHERRY_PICK_HEAD", "REVERT_HEAD") if (git_dir / p).exists()]
    write(LOG / f"{label}.json", facts)
    return facts


if sys.argv[1] == "stop-test-fakes":
    sources = {
        str(PROJECT / "tests/fakes/scenario.py"),
        "/tmp/opencode/hpm08-test-repair/.git/herdr-plan-manager/worktrees/w08-stale-test-fix/tests/fakes/scenario.py",
        "/tmp/hpm08-mutation/tests/fakes/scenario.py",
    }
    rows = subprocess.check_output(["ps", "-eo", "pid=,ppid=,args="], text=True).splitlines()
    stopped = []
    for row in rows:
        fields = row.split(None, 2)
        if len(fields) != 3:
            continue
        pid, parent, command = fields
        argv = command.split()
        if len(argv) == 4 and argv[1] in sources and argv[3].startswith(("/tmp/pytest-of-blingtron/", "/tmp/hpm08-diag/")):
            os.kill(int(pid), signal.SIGTERM)
            stopped.append({"pid": int(pid), "parent": int(parent), "argv": argv})
    write(HERE / "logs/checks/stopped-test-fakes.json", stopped)
    print(f"Stopped {len(stopped)} fake scenario processes from this project's completed tests; no real agent or worktree removed")
    sys.exit(0)

if sys.argv[1] == "capture-test-repair":
    REPO = Path("/tmp/opencode/hpm08-test-repair")
    LOG = HERE / "logs/test-repair"
    facts = pm("status", "status", "--worker", "w08-stale-test-fix")
    assert facts["result"]["status"] == "delivered"
    worker = Path(facts["paths"]["management"])
    captured = LOG / "captured"
    captured.mkdir(exist_ok=True)
    for name in ("state.json", "result.json", "contract.md", "supervisor.log", "ack.json"):
        if (worker / name).exists():
            shutil.copy2(worker / name, captured / name)
    patch = git("worker-diff", "diff", facts["repo"]["base"], facts["result"]["head"], "--", "tests/test_plan_manager.py")
    assert git("worker-changed-files", "diff", "--name-only", facts["repo"]["base"], facts["result"]["head"]) == "tests/test_plan_manager.py"
    (LOG / "worker-fix.patch").write_text(patch + "\n")
    for item in facts["pending_items"]:
        pm("ack", "ack", "--item", item["item_id"], "--note", "test fix accepted; patch to be incorporated in issue08 working-tree delivery, no project integration claimed")
    assert pm("stop", "stop", "--worker", "w08-stale-test-fix", "--reason", "repair accepted; retain worktree and branch")["business_stopped"]
    for name in ("state.json", "ack.json"):
        shutil.copy2(worker / name, captured / name)
    # Preserve runtime configuration plus tools/results for this supplemental Pi worker.
    ref = facts["sessions"][0]["context_ref"]
    events = [json.loads(line) for line in Path(ref).read_text().splitlines() if line.strip()]
    config = [e for e in events if e.get("type") in ("session", "model_change", "thinking_level_change")]
    write(captured / "runtime-config.json", config)
    for e in config:
        if e["type"] == "session":
            assert e["cwd"] == facts["worktree"]
        elif e["type"] == "model_change":
            assert (e["provider"], e["modelId"]) == ("opencode-go", "deepseek-v4.1-flash")
        elif e["type"] == "thinking_level_change":
            assert e["thinkingLevel"] == "max"
    write(captured / "tool-results.json", [e for e in events if e.get("message", {}).get("role") == "toolResult"])
    sys.exit(0)

if sys.argv[1] in ("checks", "checks-local", "checks-docs"):
    LOG = HERE / "logs/checks"
    for path in HERE.glob("*.py"):
        ast.parse(path.read_text(), filename=str(path))
    links = 0
    docs = list((PROJECT / "docs/plan-management").glob("*.md")) + [HERE / "README.md"]
    docs += list((PROJECT / ".scratch/herdr-plan-manager/issues").glob("08*.md"))
    for path in docs:
        for target in re.findall(r"\]\(([^)]+)\)", path.read_text()):
            if "://" in target or target.startswith("#"):
                continue
            assert (path.parent / target.split("#")[0]).exists(), (path, target)
            links += 1
    write(LOG / "docs-syntax.json", {"python_syntax": "passed", "existing_local_doc_links": links})
    if sys.argv[1] == "checks-docs":
        print(run("diff-check", ["git", "diff", "--check"], PROJECT))
        print(f"PASS Python syntax and {links} local document links")
        sys.exit(0)
    print(run("verify-evidence", [sys.executable, HERE / "verify_evidence.py"], PROJECT))
    if sys.argv[1] == "checks-local":
        repair = json.loads((HERE / "logs/test-repair/captured/state.json").read_text())
        assert (PROJECT / "tests/test_plan_manager.py").read_bytes() == (Path(repair["worktree"]) / "tests/test_plan_manager.py").read_bytes()
        changed = run("code-changes", ["git", "diff", "--name-only", repair["repo"]["base"], "--", "bin", "tests", "prompts"], PROJECT)
        assert changed == "tests/test_plan_manager.py"
        print(run("test-fixed-local", [sys.executable, "-m", "pytest", "tests/test_plan_manager.py::test_stale_context_sample_never_triggers_a_handoff", "-q"], PROJECT))
        write(LOG / "worker-suite-reuse.json", {"base": repair["repo"]["base"], "worker_head": repair["result"]["head"], "test_file_byte_identical": True, "other_bin_tests_prompts_unchanged": True, "reason": "reuse delivered worker full-suite evidence for identical implementation; local focused regression confirms patch transfer"})
    else:
        print(run("test-all", [sys.executable, "-m", "pytest", "tests/", "-q"], PROJECT))
    print(run("diff-check", ["git", "diff", "--check"], PROJECT))
    sys.exit(0)

if sys.argv[1] == "setup":
    assert os.environ.get("HERDR_ENV") == "1"
    assert not META.exists(), "preserve prior experiment"
    root = Path(tempfile.mkdtemp(prefix="hpm08-real-", dir="/tmp/opencode"))
    write(META, {"root": str(root), "authorization": "User confirmed Pi/OpenCode opencode-go/deepseek-v4.1-flash/max, aggregate max 3, OpenCode --auto; disposable worktrees and worker commits"})
    for KIND in ("pi", "opencode"):
        REPO = root / KIND
        REPO.mkdir()
        LOG = HERE / "logs" / KIND
        git("init-repo", "init", "-b", "main")
        (REPO / ".gitignore").write_text(".scratch/\n__pycache__/\n")
        (REPO / "README.md").write_text("# Issue 08 disposable fixture\nPolicy: fast-forward when possible, otherwise normal merge.\n")
        if KIND == "pi":
            (REPO / "formatter.py").write_text("def format_user(name):\n    return name\n")
            spec = """# Text-conflict goal
Initial tickets: A and B, independent from the same seed commit.
A adds whitespace normalization. B adds the greeting `Hello, <name>!`.
Final combined goal: format_user(' Ada ') == 'Hello, Ada!' and format_user('Bob') == 'Hello, Bob!'.
Intermediate per-ticket outputs differ by design; preserve both feature intents in the final combination.
Dependent consumer work stays blocked until both features are integrated and compatible.
"""
            a = "Add whitespace normalization in formatter.py: format_user(' Ada ') == 'Ada' and format_user('Bob') == 'Bob' at this baseline. Commit only formatter.py; run both assertions."
            b = "Add greeting in formatter.py: format_user('Ada') == 'Hello, Ada!' and format_user('Bob') == 'Hello, Bob!' at this baseline. Commit only formatter.py; run both assertions."
        else:
            (REPO / "producer.py").write_text("def produce(dollars):\n    return {'amount': dollars, 'unit': 'dollars'}\n")
            (REPO / "consumer.py").write_text("def render(packet):\n    return str(packet['amount'])\n")
            spec = """# Behavior-conflict goal
Initial tickets: A and B, independent from the same seed commit.
A changes producer output to cents with explicit unit metadata. B adds currency formatting to the legacy dollar consumer.
Final combined goal: render(produce(12)) == '$12.00', render(produce(0)) == '$0.00', and the legacy dollar packet remains supported.
Both original tickets may pass individually while combined behavior fails; that must trigger a new repair ticket and keep the goal open.
"""
            a = "Change only producer.py so produce(12) == {'amount': 1200, 'unit': 'cents'} and produce(0) == {'amount': 0, 'unit': 'cents'}. Verify, commit producer.py only."
            b = "Change only consumer.py to format legacy dollar packets with currency sign and two decimals: render({'amount': 12, 'unit': 'dollars'}) == '$12.00', likewise 0 -> '$0.00'. Verify, commit consumer.py only."
        git("seed-add", "add", ".")
        git("seed-commit", "commit", "-m", "initialize repair experiment")
        seed = git("seed-head", "rev-parse", "HEAD")
        mat = REPO / ".scratch"
        mat.mkdir()
        (mat / "spec.md").write_text(spec + "\nUse your own tools; do not spawn subagents. The coordinator owns integration and shared records.\n")
        (mat / "A.md").write_text("# A\n" + a + "\n")
        (mat / "B.md").write_text("# B\n" + b + "\n")
        # A prebuilt unrelated documentation commit, integrated only during repair.
        git("docs-branch", "switch", "-c", "fixture-docs")
        (REPO / "NOTICE.md").write_text("Unrelated fixture documentation; no runtime behavior.\n")
        git("docs-add", "add", "NOTICE.md")
        git("docs-commit", "commit", "-m", "document isolated experiment")
        docs = git("docs-head", "rev-parse", "HEAD")
        git("return-main", "switch", "main")
        write(LOG / "fixture.json", {"seed": seed, "docs": docs, "repo": str(REPO)})
    print(root)
    sys.exit(0)

KIND, ACTION = sys.argv[1:3]
assert KIND in ("pi", "opencode")
REPO = Path(json.loads(META.read_text())["root"]) / KIND
LOG = HERE / "logs" / KIND
MAT = REPO / ".scratch"
RUN = f"issue08-{KIND}"
fixture = json.loads((LOG / "fixture.json").read_text())
check = ("from formatter import format_user; assert format_user(' Ada ') == 'Hello, Ada!'; assert format_user('Bob') == 'Hello, Bob!'; print('combined greeting passed')" if KIND == "pi" else "from producer import produce; from consumer import render; assert produce(12)=={'amount':1200,'unit':'cents'}; assert render(produce(12))=='$12.00', render(produce(12)); assert render(produce(0))=='$0.00'; assert render({'amount':12,'unit':'dollars'})=='$12.00'; print('combined and legacy currency passed')")

if ACTION == "init":
    pm("init-run", "init-run", "--run-id", RUN, "--kind", KIND, "--provider", "opencode-go",
       "--model", "deepseek-v4.1-flash", "--thinking", "max", "--max-workers", "3")
elif ACTION.startswith("start-"):
    role = ACTION[-1]
    assert role in ("A", "B", "R")
    base = fixture["seed"] if role != "R" else json.loads((LOG / "repair-inputs.json").read_text())["base"]
    pm(ACTION, "start", "--run", RUN, "--ticket-id", role, "--title", f"issue08 {KIND} {role}",
       "--worker-id", f"w08-{KIND}-{role.lower()}", "--base", base,
       "--material", MAT / "spec.md", "--material", MAT / f"{role}.md",
       "--instructions", "Read spec and your one ticket; implement and verify in your assigned worktree. Commit code and publish your contract result. Do not spawn subagents.")
elif ACTION == "wait":
    pm("wait", "wait", "--run", RUN, "--timeout", "30")
elif ACTION == "inputs":
    a, b = state("A"), state("B")
    assert a["result"]["status"] == b["result"]["status"] == "delivered"
    pre = snapshot_target("before-inputs")
    assert pre["head"] == fixture["seed"] and pre["branch"] == "main" and not pre["status"] and not pre["operations"]
    for s in (a, b):
        for item in s["pending_items"]:
            pm("ack-input", "ack", "--item", item["item_id"], "--note", "accepted individual delivery; pending integration, combined goal not complete")
    assert git("after-ack-head", "rev-parse", "HEAD") == fixture["seed"]
    git("merge-A", "merge", "--ff-only", a["result"]["head"])
    before = snapshot_target("before-B")
    git("merge-B", "merge", "--no-edit", b["result"]["head"], expected=1 if KIND == "pi" else 0)
    if KIND == "pi":
        git("conflict-paths", "diff", "--name-only", "--diff-filter=U")
        git("conflict-index", "ls-files", "-u")
        git("conflict-status", "status", "--short")
        assert git("owned-merge-head", "rev-parse", "MERGE_HEAD") == b["result"]["head"]
        assert git("owned-target-head", "rev-parse", "HEAD") == before["head"]
        git("abort", "merge", "--abort")
        after = snapshot_target("after-abort")
        assert after == before
        reproduction = f"Merge incoming {b['result']['head']} with git merge --no-edit inside your assigned branch, capture the actual conflict, and resolve formatter.py preserving normalization and greeting intents."
    else:
        run("combined-failure", [sys.executable, "-c", check], REPO, expected=1)
        reproduction = "Reproduce the failing combined command below before editing. Diagnose and repair the unit mismatch while preserving producer cents metadata and legacy dollar rendering."
    base = git("repair-base", "rev-parse", "HEAD")
    inputs = {"base": base, "a": a["result"]["head"], "b": b["result"]["head"], "goal": "executing", "affected_dependents": "blocked until compatible repair integrated", "reason": "text conflict" if KIND == "pi" else "all initial deliveries accepted and merged, but combined verification failed"}
    write(LOG / "repair-inputs.json", inputs)
    (MAT / "R.md").write_text(f"""# R: isolated integration repair
Reason: {inputs['reason']}. The goal remains executing; affected dependents remain blocked.
Target main baseline / assigned base: {base}
A delivered SHA: {inputs['a']}; B incoming delivered SHA: {inputs['b']}. Both are accessible in this Git repository.
Original requirements: read spec.md, A.md and B.md below.
A: {(MAT / 'A.md').read_text()}
B: {(MAT / 'B.md').read_text()}
Coordinator target status: {'verified clean after coordinator-owned merge abort' if KIND == 'pi' else 'both deliveries integrated, clean, combined check fails'}.
{reproduction}
Required combined verification: python3 -c {__import__('shlex').quote(check)}
Preserve both original intents, add a small committed regression test file, run it and the combined command, and report reproduction and passing verification with actual exit codes.
Stay on the assigned branch/worktree, commit the completed repair there. The coordinator alone merges into main and decides completion.
""")
elif ACTION == "move-target":
    assert KIND == "pi"
    s = state("R")
    assert s["lifecycle"]["state"] == "running", "capture an actual target move during active repair"
    before = snapshot_target("before-target-move")
    assert not before["status"] and not before["operations"]
    git("merge-unrelated-docs", "merge", "--no-edit", fixture["docs"])
    snapshot_target("after-target-move")
elif ACTION == "integrate":
    s = state("R")
    assert s["result"]["status"] == "delivered"
    inputs = json.loads((LOG / "repair-inputs.json").read_text())
    before = snapshot_target("before-repair-integration")
    assert not before["status"] and not before["operations"]
    delta = git("target-delta", "diff", "--name-only", inputs["base"], before["head"])
    assert delta in ("", "NOTICE.md"), "requires a fresh compatibility decision/worker"
    for sha in (inputs["a"], inputs["b"]):
        git("repair-ancestry", "merge-base", "--is-ancestor", sha, s["result"]["head"])
    for item in s["pending_items"]:
        pm("ack-repair", "ack", "--item", item["item_id"], "--note", "repair accepted, pending integration; related work remains blocked")
    assert git("ack-repair-target", "rev-parse", "HEAD") == before["head"]
    git("merge-repair", "merge", "--no-edit", s["result"]["head"])
    run("combined-success", [sys.executable, "-c", check], REPO)
    write(LOG / "integration.json", {**inputs, "repair": s["result"]["head"], "pre_merge": before["head"], "integrated": git("integrated-head", "rev-parse", "HEAD"), "target_delta": delta, "compatibility_decision": "reuse worker regression evidence; intervening change only adds NOTICE.md and cannot change Python behavior" if delta else "target unchanged since worker baseline", "goal": "complete", "unresolved": []})
    git("final-log", "log", "--oneline", "--graph", "--all")
elif ACTION == "retain":
    for role in ("A", "B", "R"):
        s = pm(f"stop-{role}", "stop", "--worker", f"w08-{KIND}-{role.lower()}", "--reason", "experiment complete; retain all scenes and branches as evidence")
        assert s["business_stopped"]
    pm("retained-run", "status", "--run", RUN)
elif ACTION == "capture":
    out = LOG / "captured"
    out.mkdir(exist_ok=True)
    configs, calls, repair_outputs = [], [], []
    for worker in sorted((REPO / ".git/herdr-plan-manager/workers").iterdir()):
        s = json.loads((worker / "state.json").read_text())
        dest = out / worker.name
        dest.mkdir(exist_ok=True)
        for name in ("state.json", "result.json", "contract.md", "supervisor.log", "ack.json"):
            if (worker / name).exists():
                shutil.copy2(worker / name, dest / name)
        shutil.copy2(worker / "materials/manifest.json", dest / "materials-manifest.json")
        for session in s["sessions"]:
            ref = session.get("context_ref")
            if not ref:
                continue
            ident = {"worker": worker.name, "session": session["index"], "ref": ref}
            if KIND == "pi":
                events = [json.loads(line) for line in Path(ref).read_text().splitlines() if line.strip()]
                configs.append({**ident, "events": [e for e in events if e.get("type") in ("session", "model_change", "thinking_level_change")]})
                for e in events:
                    message = e.get("message", {})
                    if worker.name.endswith("-r") and message.get("role") == "toolResult":
                        repair_outputs.append({**ident, "timestamp": e.get("timestamp"), "tool": message.get("toolName"), "tool_call_id": message.get("toolCallId"), "is_error": message.get("isError"), "content": message.get("content")})
                    content = e.get("message", {}).get("content", [])
                    if isinstance(content, list):
                        for p in content:
                            if p.get("type") == "toolCall":
                                calls.append({**ident, "timestamp": e.get("timestamp"), "tool": p.get("name"), "tool_call_id": p.get("id"), "input": p.get("arguments")})
            else:
                db = Path.home() / ".local/share/opencode/opencode.db"
                with sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True) as conn:
                    messages = [json.loads(r[0]) for r in conn.execute("SELECT data FROM message WHERE session_id=? ORDER BY time_created,id", (ref,))]
                    parts = [json.loads(r[0]) for r in conn.execute("SELECT data FROM part WHERE session_id=? ORDER BY time_created,id", (ref,))]
                configs.append({**ident, "messages": [{k: m.get(k) for k in ("role", "providerID", "modelID", "variant", "path")} for m in messages if m.get("role") == "assistant"]})
                calls.extend({**ident, "tool": p.get("tool"), "status": p.get("state", {}).get("status"), "input": p.get("state", {}).get("input"), "time": p.get("state", {}).get("time")} for p in parts if p.get("type") == "tool")
                if worker.name.endswith("-r"):
                    repair_outputs.extend({**ident, "tool": p.get("tool"), "input": p.get("state", {}).get("input"), "status": p.get("state", {}).get("status"), "output": p.get("state", {}).get("output"), "error": p.get("state", {}).get("error"), "metadata": p.get("state", {}).get("metadata"), "time": p.get("state", {}).get("time")} for p in parts if p.get("type") == "tool" and p.get("tool") in ("bash", "write", "edit", "apply_patch"))
    write(out / "runtime-config.json", configs)
    write(out / "tool-calls.json", calls)
    write(out / "repair-tool-results.json", repair_outputs)
    for path in MAT.glob("*.md"):
        shutil.copy2(path, out / path.name)
    run("final-diff", ["git", "diff", fixture["seed"], "HEAD"], REPO)
    print(f"Captured {len(configs)} sessions and {len(calls)} tool calls")
else:
    raise SystemExit("unknown explicit action")
