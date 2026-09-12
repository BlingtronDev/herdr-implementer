# Preview Validation

## Load the intended workflow

Explicitly load [SKILL.md](SKILL.md) from this directory, independently of the legacy root workflow. In Pi, use `--skill <absolute-path>/docs/plan-management/SKILL.md`; the preview skill name is `herdr-plan-manager-preview`. Loading it does not migrate the official entry or remove legacy implementation.

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
