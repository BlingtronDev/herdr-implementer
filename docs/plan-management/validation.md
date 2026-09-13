# Validation

## Load the intended workflow

Load the [herdr-plan-manager entry](../../SKILL.md). In Pi, use `--skill <SKILL_DIR>/SKILL.md`. The manager is `bin/plan_manager.py`; this workflow supersedes the removed fixed-batch dispatcher. The [final acceptance map](final-acceptance.md) records which of the twelve plan scenarios this validation covers, from which evidence, and with which limits.

Keep two evidence levels separate:

- **Deterministic integration tests** exercise the real manager and Git against simulated runtimes.
- **Real-runtime smoke tests** establish that installed runtimes execute the worker lifecycle under coordinator control.

Simulation cannot substitute for real-runtime evidence. Neither layer guarantees that a model will always follow the coordinator instructions. After rewriting prompts, identify which checks were rerun; historical smoke results remain evidence for the revision actually exercised.

## Run deterministic checks

From the project root:

```bash
python3 -m pytest tests/test_plan_management_workflow.py -v
python3 -m pytest tests/ -q
```

The workflow test runs separately for Pi and OpenCode. It registers a run, keeps B active, triggers A's automatic handoff and delivery, handles wait and acknowledgement, merges A, and starts C from the integrated baseline. It then exercises stopping and uncommitted-work archival.

The test uses temporary Git repositories and simulated Herdr, worker behavior, and context observations. It does not contact a real provider. Test watchdogs protect the test process; they are not product task budgets.

## Run a real A/B/C experiment

### Establish the fixture and authorization

Record the coordinator environment, tool and runtime versions, and the explicitly confirmed provider, model, and thinking for each runtime. Confirm a total concurrency limit of at least two and authorization for OpenCode `--auto`. Ask about missing values rather than treating a historical test configuration as current authorization.

Use disposable repositories with a complete local plan and initial ticket set stored in an ignored main-checkout directory:

- **A:** produce and commit `api.txt`, with meaningful work remaining after a required automatic handoff.
- **B:** produce an independent result and remain active long enough to observe C's launch.
- **C:** read A's integrated `api.txt`, derive `consumer.txt`, and verify the required relationship. Its only code dependency is A.

Give each ticket concrete acceptance conditions. A coordinator-controlled observation checkpoint may keep B active for a repeatable experiment; document it as test instrumentation, not a production scheduling barrier. A wait-window expiry must never become B's failure condition.

Create an [execution record](execution-record.md). Run the same experiment for each runtime, preferably sequentially in separate repositories so the aggregate concurrency bound is unambiguous.

### Observe handoff and dynamic integration

1. Follow [Operations](operations.md) to register a run and launch A and B from the same explicit baseline. Save commands, responses, worker contracts, and material manifests.
2. Set A's controlled handoff threshold above its fresh-session seed context. Provide enough context-producing work to cross it before delivery, and leave clear continuation work. Avoid thresholds that cause immediate repeated handoffs after every restart.
3. Observe an automatic handoff without coordinator approval. Retain the incremented session ID, unchanged worktree and branch, readable durable handoff document, and evidence that the new session actually read it. Verify the old session stopped business writes before the new session began them.
4. Verify provider, model, thinking, and working directory using runtime-produced evidence from both sessions. Launch arguments alone are insufficient proof of effective configuration.
5. While B is still working, receive A's valid delivery through `wait`. Record that the target SHA has not yet changed and C has not started. If acknowledging first, retain **pending integration** in the execution record.
6. Merge A normally and record its delivery-to-integration SHA mapping. Start C from that integrated baseline. Capture evidence that B is still active and the total active-worker count stays within the authorized cap.
7. Handle subsequent items dynamically. Confirm that C consumed A's inherited result, integrate accepted outputs, and assess overall coverage and necessary combined-behavior checks. A known combined failure prevents completion even if every ticket has delivered.

If A delivers too early or B finishes before C starts, record that the ordering scenario was not covered. Adjust the fixture and run a new attempt; preserve the original evidence rather than rewriting its outcome.

### Preserve and report

Record results, actual commits, verification evidence, coordinator decisions, and retained resource locations. Stop workers selected for cleanup and require `business_stopped: true`. Supply an explicit cleanup decision; preserve or archive uncommitted content before removal, retain branches by default, and verify that only registered resources are touched. The end of a test is not permission to force-delete its workers or artifacts.

Publish an evidence index with commands, run and worker IDs, initial and final states, integration mappings, verification results, and scene locations. Distinguish passed, failed, uncovered, and unexecuted checks. Separate the smoke repository's integration commits from the implementation project's delivery or integration status.

## Run repair and closeout experiments

Use [Integration repair](repair-and-closeout.md) and its brief for both cases below. Establish current runtime authorization and complete initial inputs as above. Use disposable repositories; real workers implement original tickets and new workers implement repairs. Fixture setup may prepare seed files and an unrelated branch before the experiment, but the coordinator only observes, records, dispatches, and performs normal target merges once workers start.

### Text conflict

1. Start A and B at the same seed SHA. Give them independent changes to the same function: A normalizes whitespace; B adds a greeting. Specify the final combined intent in the plan, as well as the intermediate per-ticket assertions.
2. Receive both actual deliveries. Acknowledge with pending-integration records and demonstrate that target HEAD is unchanged. Merge A, then attempt B and capture the actual conflict, both SHAs, unmerged index, requirements, and clean preconditions.
3. Abort your own merge and prove HEAD, clean status, and operation state match the pre-merge snapshot. Leave conflict resolution to a new R worker from the restored target, supplied with B's pinned incoming SHA and both intents.
4. While R is active, integrate an unrelated prebuilt documentation change into the target. On R's delivery, compare the new target with R's base and record why disjoint evidence remains applicable. If actual changes affect behavior, use a new worker for compatibility instead of assuming the old evidence still applies.
5. Check that R reproduced the conflict in its assigned worktree, resolved and committed there, and verified both intents. Integrate R, record ancestry or equivalent mappings, and release affected dependencies only after compatibility is supported.

### Behavior conflict and goal closeout

1. Start A and B independently: A changes a producer from dollars to cents with explicit unit metadata; B formats legacy dollar packets in a different consumer file. Require the final plan to render a produced 12 dollars as `$12.00` and retain legacy dollar input support.
2. Receive both deliveries and merge both without text conflict. Run the combined check and retain the failure (`$1200.00` rather than `$12.00`). Record **executing**, even though all initial tickets delivered and were integrated.
3. Add and dispatch a new R ticket from the failing integrated target. R reproduces, fixes the mismatch, and commits a regression check covering both producer and legacy input. The coordinator does not edit the repair.
4. Inspect worker acceptance, compare target/base, and integrate R. Reuse R's combination evidence where applicable; dispatch a verification worker when a remaining plan-level gap requires it. Report complete only with goal coverage, actual integration mappings, evidence, unresolved items, and preserved resource locations.

Capture runtime-produced tool calls (including repair edits/merge commands and verification) and effective configuration/cwd to attribute the fix to the new worker. Preserve per-action coordinator command logs, contract/material snapshots, result files, and before/after SHAs. Neither simulated workers nor prebuilt repair commits establish actual worker repair.

Also exercise or explicitly label unexecuted branches: unsafe/unowned abort, abort failure, and behavior-affecting target movement requiring a new compatibility worker. A document walkthrough is evidence for the prescribed decision, not evidence of a real runtime failure. Retain failed attempts and distinguish fixture failures from product defects.

## Post-migration acceptance smoke

The migration renamed the skill entry to `herdr-plan-manager`, fixed the live references, and removed the fixed-batch dispatcher, Codex adapter, strict ticket parser, and superseded schemas and tests. It did not change `bin/plan_manager.py`, `prompts/plan-worker.md`, the runtime adapters, or the on-disk state format. Reuse the [final acceptance map](final-acceptance.md) to see which prior real-runtime evidence still applies, then rerun this smoke against the migrated entry before treating the migrated product as usable.

Prerequisites: `HERDR_ENV=1`, a populated `HERDR_WORKSPACE_ID`, the confirmed `kind`/`provider`/`model`/`thinking` per runtime, an explicit total concurrency cap, and authorization covering OpenCode `--auto`. Use a disposable repository with a complete local plan and initial ticket set, one run per runtime, and keep the aggregate active-worker count within the authorized cap.

One run per runtime covers automatic handoff, quota, wait/ack, stop/cleanup, and automatic tab release:

1. `init-run` with the confirmed configuration and `--max-workers 2` (the per-run cap; keep the total across concurrent runs within the authorized aggregate cap); save the returned run ID.
2. `start` A and B from the same explicit base SHA. Confirm `status --run` reports both active and that a third `start` is refused at the cap.
3. Give A a controlled `--handoff-tokens` threshold above its fresh-session seed context and enough context-producing work to cross it. Confirm the automatic handoff: the session index increments, worktree/branch/configuration stay the same, the durable handoff document is readable, the replacement session reads it, and the old session's last write precedes the new session's first write.
4. While B is still `working`, receive A's valid delivery through `wait`; acknowledge it and confirm the target HEAD has not moved. Delivery and acknowledgement are not integration.
5. Merge A following repository policy, record the delivery-to-integration SHA mapping, and `start` C from that integrated SHA. Confirm B is still active and the active-worker count stayed within the cap.
6. Receive C's delivery, integrate it, and run the combined check the plan requires.
7. Confirm automatic release after each valid delivery with a clean worktree: `status --worker` shows the release state and the registered tab is closed while the branch, worktree, result, logs, and materials remain. Confirm a delivery with uncommitted content retains its tab.
8. `stop` every unfinished worker and require `business_stopped: true`. Exercise `cleanup` with an explicit decision: without `--archive-uncommitted` it must refuse while uncommitted content exists; with the archive flag it must save the content, remove only that worker's registered worktree resources, close only its registered tabs, and retain the branch and evidence. Repeat `stop` and `cleanup` to check idempotence. When a cleanup already succeeded, validate it read-only with `verify-cleanup-a` instead of rerunning the refusal or archive steps, and keep the first failure's captures.
9. Record commands, responses, run/worker IDs, runtime-produced configuration and session evidence, integration SHAs, release/cleanup facts, and scene locations. Keep real smoke evidence separate from deterministic results, and state which prior checks were not rerun.

A ready-to-run manual driver for this sequence is [`.scratch/herdr-plan-manager/evidence/09-end-to-end-and-migration/smoke.py`](../../.scratch/herdr-plan-manager/evidence/09-end-to-end-and-migration/smoke.py), with usage and the evidence map in its [DRIVER.md](../../.scratch/herdr-plan-manager/evidence/09-end-to-end-and-migration/DRIVER.md). It adds an explicit quota-refusal phase while A and B are active, observes B and C inside a bounded `wait-final` window, offers the read-only `verify-cleanup-a` recovery phase for a cleanup that already ran, and lets `verify` re-check every captured fact offline, including B/C runtime configuration and the raw `last_archive` cleanup record.
