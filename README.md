# Herdr Plan Manager

Plan management skill for Herdr: the calling agent (the coordinator) reads a confirmed spec or plan with its complete initial ticket set, dispatches one isolated Pi or OpenCode worker per ticket, handles deliveries and exceptions as they arrive, performs normal merges, dispatches repair or verification workers when needed, and reports completion only when the overall goal is met.

`SKILL.md` is the user-invoked workflow entry. `bin/plan_manager.py` is the lifecycle tool; it creates worktrees, renders the worker contract, starts background supervision, observes context, performs standard worker handoff, records results durably, and manages stops and cleanup. Details live in `docs/plan-management/`:

| Reference | Use |
| --- | --- |
| [operations.md](docs/plan-management/operations.md) | Register, dispatch, observe, wait/ack, handoff, stop, cleanup |
| [execution-record.md](docs/plan-management/execution-record.md) | Coordinator-owned record template |
| [plan-worker.md](docs/plan-management/plan-worker.md) | Worker contract template rendered by the lifecycle tool |
| [repair-and-closeout.md](docs/plan-management/repair-and-closeout.md) | Conflict/behavior repair and overall completion |
| [repair-brief.md](docs/plan-management/repair-brief.md) | Repair or verification ticket template |
| [validation.md](docs/plan-management/validation.md) | Source-checkout tests and repeatable real-runtime validation |

## Requirements

- Run inside Herdr with `HERDR_ENV=1` and a populated `HERDR_WORKSPACE_ID`.
- Pi and/or OpenCode installed, with the current Herdr integration for each runtime used.
- Python 3 for `bin/plan_manager.py`; `node` for the retained Pi context helper (`bin/pi_context.mjs`).
- One explicit configuration per run: `kind` (`pi` or `opencode`), `provider`, `model`, `thinking`, and `max_workers`. The tool validates the pair and rejects silent fallback.

## Usage

```bash
HPM="<skill-dir>/bin/plan_manager.py"   # absolute path to this repository's bin/plan_manager.py
REPO="<target repository>"

python3 "$HPM" init-run --repo "$REPO" --run-id plan-01 \
  --kind pi --provider <provider> --model <model> --thinking <thinking> --max-workers 3

python3 "$HPM" start --repo "$REPO" --run plan-01 --ticket-id 05 \
  --base <full-base-sha> --material <ticket.md> --material <spec.md> --instructions "<task context>"

python3 "$HPM" status --repo "$REPO" --run plan-01
python3 "$HPM" wait --repo "$REPO" --run plan-01            # default waits until an item appears
python3 "$HPM" ack --repo "$REPO" --item <worker-id>/<item-id> --note "<decision>"
python3 "$HPM" stop --repo "$REPO" --worker <worker-id>
python3 "$HPM" cleanup --repo "$REPO" --worker <worker-id> --integrated <integration-sha>
```

The coordinator merges delivered branches itself, following repository policy. Delivery, acknowledgement, and terminal idleness are not integration.

## Records and workspaces

Read templates and tools from the installed skill. Write plans, tickets, execution records, and acceptance reports in the target repository, for example `<target-repo>/.scratch/<slug>/`. Resolve these paths against the target repository and pass absolute paths to the CLI. Installed templates remain read-only.

Tool state lives in the target repository's Git common directory by default: `<common-dir>/herdr-plan-manager/` with `runs/<run-id>/run.json` and `workers/<worker-id>/` (state, result, contract, material snapshots, handoffs, archives). Override with `--management-root`, consistently on every operation of that run.

Worker branches default to `hpm/<worker-id>`. Worktree location is selected in this order:

1. An explicit `start --worktree <absolute-path>`.
2. `<management-root>/worktrees/<worker-id>` when `--management-root` is supplied.
3. `<common-dir>/herdr-plan-manager/worktrees/<worker-id>` otherwise, normally `<target-repo>/.git/herdr-plan-manager/worktrees/<worker-id>`.

Linked worktrees share their repository's Git common directory. To use `<target-repo>/.worktrees/<worker-id>`, supply `--worktree` and ignore `.worktrees/` in that project; this changes the worker checkout location independently of tool state.

## First-version boundaries

- Pi and OpenCode only; no Codex support.
- No fixed batches, batch barriers, four-round questionnaires, or task time/cost budgets. The coordinator schedules dynamically within the confirmed concurrency cap.
- No coordinator handoff, crash recovery, or automatic takeover of lost executions; durable records support inspection only.
- No conversion layer for the removed fixed-batch dispatcher: old run records, branches, and worktrees are neither interpreted as new state nor automatically adopted or deleted.
- Cleanup requires an explicit coordinator decision; branches are retained by default and uncommitted content is archived or discarded only on request.

## Distribution and development

Keep the source checkout separate from the installed skill when a runtime-only installation is desired. From a committed release revision, export with `git archive --format=tar --prefix=herdr-plan-manager/ <revision> -o <absolute-output.tar>`. The export attributes omit development history, tests, and source-only configuration; the archive retains the root skill entry, README, tools, and runtime references/templates. `git archive` exports committed content, so commit the intended release changes before packaging.

Install the exported directory in the runtime's skill location and start a fresh agent session to discover it. The source checkout retains tests and the skill's own development evidence under `.scratch/`, including the historical migration acceptance. Those records describe development of this skill; each managed project owns its own execution records.
