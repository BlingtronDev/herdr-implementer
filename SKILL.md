---
name: herdr-plan-manager
description: "Manage a confirmed plan or spec through independent Pi or OpenCode workers in Herdr: dynamic dispatch, automatic handoff, wait/ack, delivery, integration, repair, and cleanup. Requires HERDR_ENV=1."
disable-model-invocation: true
---

# Herdr Plan Manager

The coordinator makes decisions. Workers implement individual tickets. Tools make execution mechanically reliable.

This skill is the entry for the plan-management workflow; `bin/plan_manager.py` is the product entry and the operation references live under `docs/plan-management/`. Resolve relative paths from this file's directory. The manager requires `HERDR_ENV=1` and a populated `HERDR_WORKSPACE_ID`; verify both before the first launch.

## Establish the execution contract

1. Read the target repository's conventions, the spec or plan, and the **complete initial ticket set**. Look under `.scratch/<slug>/spec.md` and `.scratch/<slug>/issues/` unless the user supplies other paths. Resolve missing materials before dispatch.
2. Identify explicit and semantic dependencies, the target branch, and unresolved requirements that would change implementation direction. Assign stable ticket IDs without imposing a Markdown format or a fixed batch plan.
3. Confirm the goal, scope, authorization boundaries, and worker configuration together: `kind` (`pi` or `opencode`), `provider`, `model`, `thinking`, and `max_workers`. Reuse information already confirmed; ask only about omissions or contradictions. Keep task-duration and cost budgets out of the execution contract.
4. Read [Prepare and register](docs/plan-management/operations.md#prepare-and-register) before the first launch. Verify Herdr and the runtime integrations, validate the exact configuration, and register the run. Unsupported or unverifiable selections require an explicit adjustment, not a silent fallback. OpenCode's current `--auto` behavior must be covered by the authorization.
5. Create a coordinator-owned [execution record](docs/plan-management/execution-record.md) containing the input references, authorization, run, target baseline, and complete ticket index.

**Ready to dispatch:** the inputs are complete and accessible, direction-setting questions are resolved, and the authorized configuration and concurrency limit are registered.

## Advance dynamically

Repeat while useful work remains:

1. **Select eligible work.** Use integrated results and available worker slots to choose the next tickets. A code dependency is satisfied only after the required result enters the target branch. For investigation or verification dependencies, explicitly accept the artifact as usable. Delivery, terminal idleness, and acknowledgement are not integration.
2. **Dispatch one ticket per worker.** Follow [Dispatch](docs/plan-management/operations.md#dispatch). Supply its goal, scope, acceptance criteria, dependency context, plan references, and an explicit base commit. Record the returned worker, branch, and resource paths. The tool snapshots materials, renders the [worker contract](docs/plan-management/plan-worker.md), and manages background supervision.
3. **Observe without a batch barrier.** Do available management work; otherwise [wait for pending items](docs/plan-management/operations.md#observe-and-handle-items). Process any returned delivery or exception without waiting for unrelated workers. A successful automatic handoff needs no coordinator approval and remains the same worker occupying one slot. A wait-window expiry is not a task result.
4. **Decide the disposition.** Inspect the declaration, acceptance evidence, HEAD or artifacts, and remaining work. Reuse worker verification by default; inspect suspicious results or assign supplementary checks when justified. Handle ordinary failures within the authorization: investigate, retry, or replace a worker. Confirm the previous attempt has stopped business writes before replacement. Repeated lack of progress calls for a different strategy or a blocker report.
5. **Integrate or accept artifacts.** Follow the integration rules below for code. Record acceptance and durable locations for non-code results. Once the disposition is recorded, acknowledge the item. If acknowledgement precedes a merge, keep an explicit **pending integration** entry. Reassess eligibility immediately.

You may add investigation, repair, or verification tickets, split oversized work, and adjust dependencies within the authorized goal. Record the reason, relation to the goal, and affected work when making the change. Workers report unexpected findings; the coordinator updates shared plans and ticket indexes as the single writer. Tools neither select the next ticket nor decide business retries.

Ask the user when a requirement trade-off cannot be inferred, the goal would expand, or an authorization constraint must change. Routine execution choices do not require renewed approval.

## Integrate and preserve results

Before the first merge, read [Capture and release a conflicted target](docs/plan-management/repair-and-closeout.md#capture-and-release-a-conflicted-target) for the evidence to retain **before** an operation starts. Before each merge, verify the intended target branch and delivered HEAD and save the target SHA, tracked/untracked status, and absence of unfinished Git operations. Preserve and clarify user changes that cannot safely be distinguished.

Perform normal merges **serially**, following repository policy rather than imposing a universal ff, no-ff, or squash rule. Record the ticket, delivered SHA, and actual integration SHA before unlocking dependents. For squash or other non-ancestry integration, retain the explicit mapping and evidence.

You may read code and run checks to support judgment. Assign implementation, conflict resolution, and behavioral repairs to workers. **On a merge conflict, behavioral failure, or target change during repair**, read [Integration repair](docs/plan-management/repair-and-closeout.md) before acting. Capture both inputs and requirements, abort only your own merge with known-safe preconditions, and dispatch a new isolated worker with a [repair brief](docs/plan-management/repair-brief.md). Keep affected dependencies blocked until the repair is integrated with compatibility evidence.

Delivery ends automatic handoff; a valid delivery with a clean worktree also releases the worker terminal, while the result and scene remain available. Before releasing other resources, read [Stop and clean up](docs/plan-management/operations.md#stop-and-clean-up); `status --worker` records a retained tab and its reason under `release`. Require confirmed stopping, an explicit integration or disposition decision, and a safe destination for uncommitted work. Failed scenes and unintegrated results are not automatically destroyed.

## Close the goal

At closeout, follow [Decide overall completion](docs/plan-management/repair-and-closeout.md#decide-overall-completion) to map every plan-level goal to integrated results, applicable acceptance evidence, and remaining gaps. Assign a new worker for necessary cross-module verification; reuse sufficient existing evidence instead of requiring a fixed final-review role.

Report completion only when the overall goal is met, not merely when every worker has delivered. Include the main integration results, verification summary, unresolved items, and retained resource locations.

When validating this workflow rather than executing a user plan, follow [Validation](docs/plan-management/validation.md) and record the results in [Final acceptance](docs/plan-management/final-acceptance.md).

This version relies on an active coordinator. Durable records support reliable item collection and scene inspection; they do not promise coordinator handoff, crash recovery, or automatic takeover.
