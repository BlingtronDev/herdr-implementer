# Validation

## Load the intended workflow

Load the [herdr-implementer entry](../../SKILL.md). In Pi, use `--skill <SKILL_DIR>/SKILL.md`. The lifecycle tool is `bin/implementer.py`. Record the tested skill revision, environment, scenarios, evidence, and limits in a fresh acceptance report in the validation project's record directory, for example `<validation-repo>/.scratch/<slug>/acceptance.md`.

Keep two evidence levels separate:

- **Deterministic integration tests** exercise the real lifecycle tool and Git against simulated runtimes.
- **Real-runtime smoke tests** establish that installed runtimes execute the worker lifecycle under coordinator control.

Simulation cannot substitute for real-runtime evidence. Neither layer guarantees that a model will always follow the coordinator instructions. After rewriting prompts, identify which checks were rerun; historical smoke results remain evidence for the revision actually exercised.

## Run deterministic checks

From this skill's source checkout (tests are development assets, omitted from the runtime distribution):

```bash
python3 -m pytest tests/test_implementation_workflow.py -v
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

Create an [execution record](execution-record.md) in the validation project's record directory. Run the same experiment for each runtime, preferably sequentially in separate repositories so the aggregate concurrency bound is unambiguous.

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

## Lifecycle coverage and acceptance

During the A/B/C experiment, also check the following lifecycle behavior. Use the current [Operations](operations.md) interface and preserve raw responses in the validation project's evidence directory.

- **Quota:** with A and B active and the run cap set to two, a third launch is refused without registering a worker.
- **Terminal release:** valid delivery with a clean worktree releases the registered terminal while retaining branch, worktree, result, logs, and materials. Uncommitted content retains the terminal with a reason under `status --worker`.
- **Cleanup:** after `stop` confirms `business_stopped: true`, cleanup without an uncommitted-content decision refuses a dirty worktree. Explicit `--archive-uncommitted` preserves the content before removing registered resources; branches and evidence remain. Repeated stop and cleanup are idempotent. If cleanup already succeeded, inspect the saved cleanup result and archive instead of trying to reproduce a refusal on a removed worktree.
- **Outcome semantics:** an idle worker without a valid result is not delivered. An investigation-only ticket produces an existing findings artifact without an empty commit.

For each scenario, record the tested revision, exact commands and exit codes, run/worker IDs, configuration and session evidence, integration SHAs, retained resource paths, and a result of passed, failed, or unexecuted. Link prior evidence only when its tested behavior remains applicable. The acceptance report belongs to this validation run; a historical report is not a current completion decision.
