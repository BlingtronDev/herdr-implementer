---
name: herdr-implementer
description: "Implement a confirmed plan or spec through isolated Pi or OpenCode workers in Herdr, delivering verified, integrated results. Requires HERDR_ENV=1."
disable-model-invocation: true
---

# Herdr Implementer

Turn a confirmed plan or spec into verified, integrated results. The **coordinator** selects work, judges evidence, records decisions, and serially integrates. **Workers** implement and verify individual tickets under the tool-injected contract. **Tools** own configuration checks, lifecycle safety, and durable execution facts; they do not decide acceptance or business retries.

Resolve skill resources from this file's directory, and plan, ticket, and record paths from the confirmed target repository. Write execution records and repair inputs there; keep installed templates read-only. Pass absolute paths to `bin/implementer.py`.

## Permissions

OpenCode workers start with `--auto`: permissions not explicitly denied are automatically approved; explicit denies remain. A worktree provides version-control isolation, not a security sandbox. This default does not expand task scope or override user restrictions. If an existing user restriction conflicts with this mode, stop before launch and explain the incompatibility; the tool has no per-launch switch to disable it. No separate approval of this fixed mode is needed when the user's constraints are compatible.

## 1. Read the goal and register

Read the target repository's conventions, the spec or plan, and the **complete initial ticket set**, normally under `.scratch/<slug>/spec.md` and `.scratch/<slug>/issues/` unless other paths are supplied. Establish the goal, target branch, stable ticket IDs, and explicit or semantic dependencies; resolve missing materials and direction-setting requirements.

Resolve worker `kind` (`pi` or `opencode`), `provider`, `model`, and `thinking` from confirmed values or an unambiguous, verifiable persistent configuration. Ask only when a necessary value cannot be resolved, is unavailable, or conflicts with direction or authorization; historical smoke settings are not configuration, and model/provider changes require an explicit decision. `max_workers` is optional: use the tool's default unless constrained. Honor explicit time, cost, and aggregate resource constraints across runs and nested agents without imposing a budget questionnaire.

Follow [Prepare and register](docs/implementation/operations.md#prepare-and-register), including prerequisites the tool does not cover. Let the tool perform its covered checks. Create a coordinator-owned [execution record](docs/implementation/execution-record.md) referencing the inputs, complete ticket index, target branch/initial SHA, run, and otherwise unrecorded constraints.

**Ready:** all initial tickets and required materials are accounted for, direction-setting questions are resolved, and usable configuration is registered with launch prerequisites supported by evidence.

## 2. Select and dispatch from a baseline

Choose eligible tickets using accepted dependencies and available slots. Code dependencies require actual integration into the target branch; investigation or verification dependencies require explicit acceptance of usable artifacts. Follow [Dispatch](docs/implementation/operations.md#dispatch) with one ticket, its required materials, acceptance criteria, dependency context, and a full baseline SHA per worker. Include required linked material explicitly; snapshots do not recursively collect Markdown links.

Reference the returned worker management record instead of copying tool facts. The tool injects the worker contract; dispatch does not require the coordinator to read its template. Each worktree has one business writer; the injected contract governs worker write scope and ends business writes after final result publication. The coordinator alone updates shared plans, ticket indexes, and execution decisions.

**Dispatched:** each launch is traceable to its ticket, baseline, and management record. Investigate uncertain launch delivery through the Dispatch exception pointer before attempting another launch.

## 3. Handle deliveries and exceptions dynamically

Advance eligible work and integration without a batch barrier; otherwise [observe and wait](docs/implementation/operations.md#observe-and-handle-items). Handle each returned item without waiting for unrelated workers. Successful automatic handoff remains the same worker and needs no approval; a wait-window expiry is not task failure. Reported observation/handoff exceptions or doubtful progress trigger that section's Troubleshooting pointer.

For outcomes, follow [Outcome handling](docs/implementation/execution-record.md#outcome-handling): judge evidence against requirements, save result references, decisions and unfinished actions, then ack. Worker facts stay in result JSON; if saving fails, leave items unacknowledged when their follow-up depends on that record. Acceptance is a judgment, ack removes a pending item, and neither is integration. Retain pending integration and its next action after ack; merging before ack is also allowed.

Handle ordinary failures within authorization by investigation, retry, or replacement, using [Stop and clean up](docs/implementation/operations.md#stop-and-clean-up) to confirm the previous attempt has stopped business writes before replacement. Preserve attempt history. Repeated lack of progress calls for a different strategy or a blocker report. Add or split investigation, repair, or verification tickets as needed; record why and which goals/dependencies change. Ask the user for uninferable requirement trade-offs, scope expansion, or authorization changes, not routine scheduling choices.

**Handled:** every collected item has a durable decision and any unfinished action has an owner or follow-up ticket; acknowledged work remains discoverable through that record.

## 4. Integrate serially and unlock dependencies

Before each merge, save the intended target branch, full target and incoming delivered SHAs, clean tracked/untracked status, and absence of unfinished merge, rebase, cherry-pick or revert operations. Establish ownership of the target checkout; preserve unexplained user changes and defer merging until they can be safely separated. These preconditions support safe conflict recovery.

Perform normal merges **serially**, following repository policy. Save the actual integration mapping under [Task decisions and integration](docs/implementation/execution-record.md#task-decisions-and-integration), including evidence for squash or other non-ancestry integration. Non-code results need explicit acceptance at a durable artifact location.

**Merge conflict, behavioral failure, or target change during repair:** read [Integration repair](docs/implementation/repair-and-closeout.md) before recovery or repair decisions. Implementation and repairs go to isolated workers; the coordinator may inspect code and run checks. Keep affected dependencies blocked until actual integration and compatibility evidence are available.

**Integrated:** the accepted result has an actual integration mapping and applicable verification, or an explicitly accepted durable artifact. Reassess dependencies immediately and return to steps 2–4 while useful work remains.

## 5. Check the overall goal, report, and optionally clean up

Follow [Important decisions and closeout](docs/implementation/execution-record.md#important-decisions-and-closeout): account for every in-scope goal through integrated results or accepted artifacts and applicable evidence. Reuse valid evidence; run simple supplementary checks directly and dispatch independent or substantial verification only when needed. All workers delivered is not overall completion; an unmet goal or combined failure keeps the goal executing or blocked with a next action.

Report the goal conclusion, main integration results, verification, unresolved items, and retained resource locations from the authoritative record. Cleanup is optional and independent of goal completion. When releasing resources, follow [Stop and clean up](docs/implementation/operations.md#stop-and-clean-up) for confirmed stopping, ownership, disposition, and protection of uncommitted, unintegrated, or non-code work. The tool governs safe terminal release; retained resources are inspected there rather than forcibly removed.

**Closed:** every goal has an evidence-backed conclusion, remaining actions and resources are locatable, and the report distinguishes completion from blockers. This version requires an active coordinator; durable records support inspection, not coordinator recovery or automatic takeover.
