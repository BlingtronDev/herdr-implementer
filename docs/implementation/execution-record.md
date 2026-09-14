# Execution Record Template

Create one coordinator-owned Markdown record in the target repository, for example `<target-repo>/.scratch/<slug>/execution.md`. Keep this installed template read-only. Resolve links against this template's original directory before copying them. The coordinator is the sole writer of the shared record; artifact references grant no additional worker write permissions.

Worker result JSON and its evidence are the authoritative source for completed work, acceptance, verification, deviations, and remaining work. Record references and coordinator decisions, not transcriptions or a second worker Markdown report. Tool records own configuration, resource paths, and lifecycle facts. Existing records need no migration.

## Execution entry

- Goal, scope, input paths and revisions; complete authoritative ticket index (including dependencies):
- Target repository, target branch, and initial target SHA:
- Run and management-record references; additional runs and reasons:
- Authorization and constraints not already expressed by those sources, including the user's aggregate resource cap and explicit time/cost budgets:

Reference registered configuration and worker paths rather than copying their fields. This template does not change configuration or launch authorization requirements.

## Task decisions and integration

If authoritative ticket records already hold these decisions, link to them instead of maintaining a second status table. Otherwise use the following table as outcomes arrive; the complete ticket set remains in the entry's index. Preserve every attempt's result reference and failed history.

| Ticket / attempt | Worker / result | Decision and necessary rationale | Integration SHA / accepted artifact | Next action |
| --- | --- | --- | --- | --- |
| A / 1 | worker reference; durable result JSON | Accepted | Actual integration SHA; mapping evidence | None |
| B / 1 | worker reference; durable result JSON | Accepted; **pending integration** | — | Coordinator: merge into target branch |
| C / 1 | worker reference; durable result JSON | Repair required; failure evidence reference | — | Dispatch R; retain this attempt |

### Outcome handling

For each `delivered`, `failed`, or `needs-decision` outcome:

1. Read the result and evidence against the assigned requirements; decide acceptance and disposition. Tool validation checks structure, not whether every departure was disclosed.
2. Save the ticket/attempt, worker and durable result reference, decision, and unfinished actions with an owner or follow-up ticket. For multiple deviations with different dispositions, cite each result entry (for example `plan_deviations[0]`: accepted under authorization D1; `plan_deviations[1]`: pending user decision, coordinator to ask). Cover every deviation without copying its contents. `plan_deviations: []` needs no repeated Markdown declaration. Missing or invalid deviation information is not “no deviations”; obtain clarification before acceptance, including for older results.
3. Ack the corresponding item only after saving. If saving fails, do not ack an item whose unfinished actions depend on that record. The lifecycle tool neither generates nor verifies this record.
4. After integration, update the actual integration mapping before unlocking code dependencies. Merging before ack is also allowed; otherwise keep **pending integration** and its next action discoverable in the authoritative record even after the item disappears from `wait`.

`delivered` is a worker declaration; acceptance is the coordinator's judgment; ack removes an item from the pending queue; integration puts the result into the target branch. These are distinct. Acceptance and ack alone do not unlock code dependencies.

For code, retain ticket/attempt and result `head` -> pre-merge target SHA -> actual integration SHA, with mapping and verification evidence for squash or other non-ancestry integration. Reference the delivery SHA in the result instead of transcribing its other fields. For non-code work, record explicit acceptance and a durable artifact location; preserve and verify an accessible copy before worktree cleanup. An empty commit is unnecessary.

## Important decisions and closeout

- Scope or dependency changes, unexpected findings, and authorization decisions: necessary rationale, evidence references, affected tickets, owner and next action. Link an existing decision/findings log instead of duplicating it.
- Unresolved issues, blocked dependencies, and follow-up actions: link the authoritative ticket decisions above.
- Overall goal conclusion (complete, executing, or blocked) and evidence: account for every goal through integrated results or accepted artifacts and applicable verification. Reuse an existing goal acceptance index; otherwise record each goal's result, evidence, gap and next action here. Evidence from an earlier baseline is reusable only when later changes do not invalidate it; record the rationale where relevant. **Complete** requires every in-scope goal supported, required code integrated or non-code artifacts explicitly accepted, and no unresolved acceptance failure. An unmet goal or combined failure stays **executing** when actionable follow-up exists, or **blocked** when a decision or external condition prevents progress; identify the gap and next action. Optional follow-ups are distinct from unmet requirements. All workers delivered does not imply overall completion.
- Retained resources needing attention, durable artifact/archive locations, cleanup decisions and tool-record references, blockers and next actions. Protect uncommitted and unintegrated work before cleanup.

For conflicts, behavioral failures, or target changes during repair, follow [Integration repair](repair-and-closeout.md). Keep durable references to its input, scene, repair and compatibility evidence; preserve original delivery -> repair delivery -> actual integration mapping without copying evidence into multiple reports.

Final reporting cites the main integration results, verification, unresolved items, and retained resource locations from these authoritative sources.
