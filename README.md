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
| [validation.md](docs/plan-management/validation.md) | Deterministic checks, real smoke runs, post-migration smoke |
| [final-acceptance.md](docs/plan-management/final-acceptance.md) | Scenario-to-evidence map and remaining checks |

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

State lives in the target repository's Git common directory by default: `<common-dir>/herdr-plan-manager/` with `runs/<run-id>/run.json`, `workers/<worker-id>/` (state, result, contract, material snapshots, handoffs, archives), and `worktrees/<worker-id>/` for the worker checkouts on branch `hpm/<worker-id>`. Override only with `--management-root`, consistently on every operation of that run.

## First-version boundaries

- Pi and OpenCode only; no Codex support.
- No fixed batches, batch barriers, four-round questionnaires, or task time/cost budgets. The coordinator schedules dynamically within the confirmed concurrency cap.
- No coordinator handoff, crash recovery, or automatic takeover of lost executions; durable records support inspection only.
- No conversion layer for the removed fixed-batch dispatcher: old run records, branches, and worktrees are neither interpreted as new state nor automatically adopted or deleted.
- Cleanup requires an explicit coordinator decision; branches are retained by default and uncommitted content is archived or discarded only on request.

## Installed-directory migration

The product was renamed from `herdr-ticket-dispatcher` to `herdr-plan-manager`. The verified installed location on this machine is `~/.agents/skills/herdr-plan-manager`, and a fresh OpenCode process discovers only the new `herdr-plan-manager` entry. The project's main checkout still holds the pre-migration baseline and stays uncommitted by the user's decision, so the installed tree is usable as it stands; no integration commit is required for usage.

To move an installed checkout:

1. Relocate the skill directory so its root is `~/.agents/skills/herdr-plan-manager` (or the equivalent skill location for the runtime).
2. Keep exactly one `SKILL.md`, at the directory root. Runtimes discover nested `SKILL.md` files as additional skills, so remove any leftover `docs/**/SKILL.md` from the old tree.
3. Start a fresh agent session (skills are discovered at session start) and confirm the `herdr-plan-manager` skill is listed and reachable while the old `herdr-ticket-dispatcher` entry is gone. If the machine also installs the skill through per-skill links, recreate that link against the new directory.
4. Leave existing run evidence and management directories untouched. This migration does not rename, interpret, adopt, or delete `herdr-plan-manager/` or old `herdr-ticket-dispatcher/` records, branches, or worktrees.
5. Update a clone's remote with `git remote set-url` when that clone tracks the moved directory. Do not change global Git configuration, and do not require a merge of a migration commit that does not exist.
