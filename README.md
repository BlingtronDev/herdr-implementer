# Herdr Implementer

Plan and spec implementation skill for Herdr: the calling agent (the coordinator) reads a confirmed spec or plan with its complete initial ticket set, dispatches one isolated Pi or OpenCode worker per ticket, handles deliveries and exceptions as they arrive, performs normal merges, dispatches repair or verification workers when needed, and reports completion only when the overall goal is met.

[SKILL.md](SKILL.md) is the user-invoked five-step workflow: read/register, select/dispatch, handle outcomes, integrate, and check/report/optionally clean up. `bin/implementer.py` is the lifecycle tool; it creates worktrees, renders the worker contract, starts background supervision, observes context, performs standard worker handoff, records results durably, and manages stops and cleanup. Details live in `docs/implementation/`:

| Reference | Use |
| --- | --- |
| [operations.md](docs/implementation/operations.md) | Register, dispatch, observe, wait/ack, handoff, stop, cleanup |
| [execution-record.md](docs/implementation/execution-record.md) | Coordinator-owned record template |
| [plan-worker.md](docs/implementation/plan-worker.md) | Worker contract template rendered by the lifecycle tool |
| [repair-and-closeout.md](docs/implementation/repair-and-closeout.md) | Merge conflicts, behavioral failures, or target changes during repair; normal closeout stays in the entry/record |
| [repair-brief.md](docs/implementation/repair-brief.md) | Task-specific inputs for repair or related verification |
| [troubleshooting.md](docs/implementation/troubleshooting.md) | Launch uncertainty, observation/handoff exceptions, deliberate context tuning, or stop/cleanup blockers |

## Responsibility boundaries

- **Coordinator:** interpret confirmed requirements, select eligible tickets, inspect evidence, integrate results, and decide whether the overall goal is met.
- **Workers:** implement, investigate, verify, and repair individual tickets in isolated worktrees.
- **Lifecycle tool:** enforce registered configuration and per-run concurrency, snapshot inputs, supervise sessions and handoffs, collect durable results, and safely stop or clean up owned resources.

The product delivers the implementation, not merely an updated plan or a collection of finished workers. Scheduling remains a coordinator decision; acceptance and integration are not inferred from terminal status or acknowledgement.

## Requirements

- Run inside Herdr with `HERDR_ENV=1` and a populated `HERDR_WORKSPACE_ID`.
- Pi and/or OpenCode installed, with the current Herdr integration for each runtime used.
- Python 3 for `bin/implementer.py`; `node` for the retained Pi context helper (`bin/pi_context.mjs`).
- One resolved configuration per run: `kind` (`pi` or `opencode`), `provider`, `model`, and `thinking`. Reuse confirmed values or unambiguous, verifiable persistent configuration; unresolved or conflicting selections need clarification, never a silent model/provider fallback. Registration validates catalog membership and thinking support within the limits described in [Prepare and register](docs/implementation/operations.md#prepare-and-register).
- Optional `--max-workers` defaults to 4; a positive integer overrides it. The run saves the resolved limit. Existing runs retain their valid saved limit and fail closed at startup if it is missing or invalid. The cap is per run, not global across runs or nested agents; user-specified aggregate constraints still apply.

## Usage

For OpenCode's fixed automatic permission mode, explicit-deny preservation, and user-restriction conflicts, read [Permissions](SKILL.md#permissions) at the use entry.

```bash
HI="<skill-dir>/bin/implementer.py"   # absolute path to this repository's bin/implementer.py
REPO="<target repository>"

python3 "$HI" init-run --repo "$REPO" --run-id plan-01 \
  --kind pi --provider <provider> --model <model> --thinking <thinking>

python3 "$HI" start --repo "$REPO" --run plan-01 --ticket-id 05 \
  --base <full-base-sha> --material <ticket.md> --material <spec.md> --instructions "<task context>"

python3 "$HI" status --repo "$REPO" --run plan-01
python3 "$HI" wait --repo "$REPO" --run plan-01            # default waits until an item appears
python3 "$HI" ack --repo "$REPO" --item <worker-id>/<item-id> --note "<decision>"
python3 "$HI" stop --repo "$REPO" --worker <worker-id>
python3 "$HI" cleanup --repo "$REPO" --worker <worker-id> --integrated <integration-sha>
```

The coordinator merges delivered branches itself, following repository policy. Delivery, acknowledgement, and terminal idleness are not integration.

## Records and workspaces

Read templates and tools from the installed skill. Write plans, tickets, execution records, and acceptance reports in the target repository, for example `<target-repo>/.scratch/<slug>/`. Resolve these paths against the target repository and pass absolute paths to the CLI. Installed templates remain read-only.

Tool state lives in the target repository's Git common directory by default: `<common-dir>/herdr-implementer/` with `runs/<run-id>/run.json` and `workers/<worker-id>/` (state, result, contract, material snapshots, handoffs, archives). Override with `--state-root`, consistently on every operation of that run.

Worker branches default to `hi/<worker-id>`. Worktree location is selected in this order:

1. An explicit `start --worktree <absolute-path>`.
2. `<state-root>/worktrees/<worker-id>` when `--state-root` is supplied.
3. `<common-dir>/herdr-implementer/worktrees/<worker-id>` otherwise, normally `<target-repo>/.git/herdr-implementer/worktrees/<worker-id>`.

Linked worktrees share their repository's Git common directory. To use `<target-repo>/.worktrees/<worker-id>`, supply `--worktree` and ignore `.worktrees/` in that project; this changes the worker checkout location independently of tool state.

## First-version boundaries

- Pi and OpenCode only; no Codex support.
- No fixed batches, batch barriers, or mandatory budget questionnaires. The coordinator schedules dynamically within the saved concurrency cap and honors explicit time, cost, and aggregate resource constraints.
- No coordinator handoff, crash recovery, or automatic takeover of lost executions; durable records support inspection only.
- No conversion layer for the removed fixed-batch dispatcher: old run records, branches, and worktrees are neither interpreted as new state nor automatically adopted or deleted.
- Cleanup requires an explicit coordinator decision; branches are retained by default and uncommitted content is archived or discarded only on request.

## Rename and existing executions

See [Rename compatibility](docs/migrations/rename-compatibility.md) before upgrading an installation with existing runs. New executions use `bin/implementer.py`, `HI_*` settings, the `herdr-implementer/` state directory, and `hi/` branches. Existing state and worktrees are never moved automatically.

## Distribution and development

For workflow development or validation, read [Validation](docs/development/validation.md); for lifecycle safety changes, read [Lifecycle implementation notes](docs/development/lifecycle.md). These are not prerequisites for executing a user plan. Upgrade guidance is linked separately above.

**Reading paths:** normal execution uses `SKILL.md`, the relevant Operations sections, and the execution record; the tool injects the worker contract without a coordinator template read. Merge conflicts, behavioral failures, or target changes during repair open the repair reference; ordinary integration and closeout stay in the entry/record. Exceptions or deliberate tuning open only the matching Troubleshooting section. Development and migration references stay outside that normal path. Moving an already-unloaded file does not itself save tokens; assess the documents actually read and injected, not directory names or line counts.

Keep the source checkout separate from the installed skill when a runtime-only installation is desired. From a committed release revision, export with `git archive --format=tar --prefix=herdr-implementer/ <revision> -o <absolute-output.tar>`. The export attributes omit development history, tests, and source-only configuration; the archive retains the root skill entry, README, tools, and all linked references/templates, including on-demand development and migration guides (but not the test suite). Keeping those guides distributed makes README links usable without making them runtime reading requirements. `git archive` exports committed content, so commit the intended release changes before packaging.

Install the exported directory in the runtime's skill location and start a fresh agent session to discover it. The source checkout retains `tests/`; the skill's own development evidence, including the historical migration acceptance, lives in the untracked `.scratch/` directory, so a fresh clone does not carry it. Older revisions still do, and `git show <revision>:.scratch/...` reads those records. Those records describe development of this skill; each managed project owns its own execution records.

For a machine that only runs the skill and never develops it, keep the clone and trim its working tree to the runtime files:

```bash
git sparse-checkout set --no-cone '/SKILL.md' '/README.md' '/bin' '/docs'
```

The working tree then holds those entries only. The skipped development asset (`tests/`) remains in the local object store, readable with `git show HEAD:<path>`, and later `git pull` operations keep honoring the selection; a plain deletion would be restored by the next checkout instead. The untracked `.scratch/` directory sits outside this selection and stays untouched. The leading slashes and `--no-cone` are required, because cone mode cannot select an individual file at the repository root. Do not apply this on a machine that develops the skill: it hides the test suite there too.
