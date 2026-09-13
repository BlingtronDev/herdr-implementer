# 09: Post-migration end-to-end smoke

Manual driver for the remaining real-runtime acceptance after the `herdr-plan-manager` migration. It adapts the ticket 07 fixture and capture style; ticket 07 evidence is untouched. The coordinator runs the phases, performs the merges, and records the outcome. The Pi run's real captures are recorded under `logs/real-pi/`, including the first cleanup field-assertion failure; the OpenCode run was still in flight when this revision was prepared.

## What it establishes

- Explicit configuration per runtime, automatic handoff with runtime-produced configuration, handoff-document read, and write-ordering proof, with the unchanged worktree and branch.
- Per-run concurrency of 2 inside the authorized aggregate cap: a third `start` is refused while A and B are active without registering a worker or changing the slot count. Rerun `quota-check` while `status-a` shows `handing-off` to record the replacement slot.
- `wait`/`ack` with delivery that does not move the target; `start-c` from the integration SHA while B is still working.
- Dirty A: the valid delivery retains its tab with `release.reason = uncommitted-content`; `cleanup` reports the `uncommitted-content` blocker before an archive and then archives the note with `--archive-uncommitted`, retaining the branch and result.
- Clean C and B: automatic tab release closes their tabs while worktrees, results, and branches remain.
- Full goal closeout: all four outputs integrated with exact contents and no active workers.
- Offline completeness: `verify` re-checks A's handoff, B's and C's provider/model/thinking/cwd from runtime-produced session evidence, each worker's terminal session/release state, and the raw `last_archive` cleanup record; `verify-cleanup-a` accepts an existing successful cleanup without rerunning it.

## Before running

- `HERDR_ENV=1` with a populated `HERDR_WORKSPACE_ID`.
- The user's confirmed provider, model, thinking, and aggregate concurrency cap; export:
  `HPM_SMOKE_PROVIDER`, `HPM_SMOKE_MODEL`, `HPM_SMOKE_THINKING` (optional `HPM_SMOKE_HANDOFF_TOKENS`, default 35000).
- Inherited Git identity for `git commit`; the driver creates disposable repositories under `/tmp/opencode/` and never writes repository-local Git configuration.
- No other ticket 09 run in flight; run one runtime at a time to keep the aggregate cap unambiguous.

## Phases

```bash
python3 smoke.py setup                       # disposable pi/opencode repos under /tmp/opencode; writes location.json once
for KIND in pi opencode; do
  python3 smoke.py "$KIND" init
  python3 smoke.py "$KIND" start-a           # A crosses the handoff threshold mid-ticket
  python3 smoke.py "$KIND" start-b           # B waits at the checkpoint while remaining working
  python3 smoke.py "$KIND" quota-check       # third start refused; no extra worker or slot
  python3 smoke.py "$KIND" wait              # first delivery (A)
  python3 smoke.py "$KIND" ack-a             # records the decision, target unchanged
  python3 smoke.py "$KIND" merge-a           # coordinator merge; records delivery -> integration
  python3 smoke.py "$KIND" capture-a         # runtime config, handoff document, tool ordering
  python3 smoke.py "$KIND" start-c           # from the integrated A SHA, B still working
  python3 smoke.py "$KIND" release-b         # checkpoint file releases B
  python3 smoke.py "$KIND" wait-final        # bounded observation window for B and C
  python3 smoke.py "$KIND" merge-final       # coordinator merges C and B
  python3 smoke.py "$KIND" capture-final     # refreshed states, runtime configs, release facts, disk preservation
  python3 smoke.py "$KIND" finish            # exact combined verification and goal closeout
  python3 smoke.py "$KIND" stop-a            # business_stopped must be true
  python3 smoke.py "$KIND" cleanup-a         # blocker, archive, refreshed cleanup state
  python3 smoke.py "$KIND" verify-cleanup-a  # optional read-only recovery after a completed cleanup
done
python3 smoke.py verify [pi|opencode]        # offline re-check of every captured fact; defaults to both
python3 validate_smoke.py --replay           # copy the captured tree to scratch and re-verify it
```

If a phase is rerun, its capture gets a numeric suffix; `verify` reads the newest one by numeric order.

`wait-final` observes B and C inside a wall-clock window (`HPM_SMOKE_FINAL_WINDOW` seconds, default 180). It returns as soon as both are delivered, returns early with `wait-final-partial.json` when an unacknowledged item is pending, and records `wait-final-no-result.json` when the window expires. A window expiry is not a task failure; acknowledge or merge the pending item and rerun the phase.

## Recovery after a completed cleanup

`cleanup-a` already removed A's worktree, so rerunning it would change the scene and duplicate logs. When cleanup succeeded but a later driver step failed, validate the recorded result read-only instead:

```bash
python3 smoke.py pi verify-cleanup-a
python3 validate_smoke.py --replay
```

`verify-cleanup-a` only calls `status --worker` and reads the raw `cleanup.json`; the `--replay` mode copies `logs/real-pi` under `/tmp/opencode/` before running `smoke.py verify pi`, so the original captures stay byte-identical. Both keep the first failure's scene for the record.

## Evidence map

| Path | Content |
| --- | --- |
| `location.json` | disposable smoke root |
| `logs/real-<kind>/init.json` | registered run configuration and per-run cap |
| `logs/real-<kind>/start-a.json`, `start-b.json`, `start-c.json` | launch facts, including C's integrated base |
| `logs/real-<kind>/quota-before.json`, `quota-refused.json`, `quota-after.json`, `quota-check.json` | cap refusal without a registered third worker; recorded A lifecycle |
| `logs/real-<kind>/wait*.json`, `ack-*.json` | pending items and acknowledgement without integration |
| `logs/real-<kind>/integration-*.json` | delivery -> pre-merge target -> actual integration SHA |
| `logs/real-<kind>/captured/<worker>/` | contract, result, state, handoff document, materials manifest, cleanup state |
| `logs/real-<kind>/runtime-config-<role>.json` | native provider/model/thinking and cwd per session |
| `logs/real-<kind>/tool-activity-a.json` | start/completion tool timestamps proving the old session stopped before the new one wrote |
| `logs/real-<kind>/release-facts.json` | retained-dirty A and closed-clean B/C |
| `logs/real-<kind>/disk-preserved-*.json` | worktree, result, branch, and HEAD retention after release |
| `logs/real-<kind>/cleanup-a-refused.json`, `cleanup-a-refused-blockers.json`, `cleanup-a-archived.json`, `cleanup-a-state.json` | blocker payload, archive decision, refreshed cleanup state plus the raw `last_archive` record |
| `logs/real-<kind>/cleanup-a-verified.json` | read-only recovery result for an already-successful cleanup |
| `logs/real-<kind>/combined-verification.json`, `goal-closeout.json` | exact contents and completion evidence |
| `logs/verify.json` | offline verification summary for the replayed runtimes, with runtime-config provenance |
| `logs/verify/*.txt`, `logs/verify/replay-real-pi*.txt` | raw offline check and replay outputs |

## Driver validation

`python3 validate_smoke.py` runs focused offline checks: numeric `latest` selection, refusal-payload parsing from either stream, the pure `check_handoff`/`check_runtime` verifiers for both runtime schemas, `cleanup_evidence_failures` against the captured `last_archive` schema, the wait-final window semantics, `opencode_messages` against the real read-only database, and a setup sandbox proving no repository-local Git identity is written and the disposable root stays under `/tmp/opencode`. Last run: 38/38 passed.

`python3 validate_smoke.py --replay` re-runs `smoke.py verify` over the captured real runs in a scratch copy; it currently replays the completed Pi run and recovers B/C runtime configuration from the recorded session references.

## Limits

- The driver only runs real runtimes; the deterministic layer is `python3 -m pytest tests/ -q`.
- It does not induce handoff failure, repair conflict behavior, or a behavior-affecting target move; those remain covered by the ticket 04/08 evidence listed in [final acceptance](final-acceptance.md).
- It does not verify model compliance beyond the recorded tool activity and committed outputs. A model may not follow the fixture instructions exactly; record deviations instead of editing the historical logs. On a genuine fixture or product failure, keep the scene and report it.
- `verify` needs the disposable smoke root and the runtime session logs referenced by the captured states; replay degrades to a failure if those paths no longer exist.
