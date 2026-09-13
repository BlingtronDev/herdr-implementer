# Execution Record Template

Create one coordinator-owned Markdown record associated with the target repository, for example `.scratch/<slug>/execution.md`. Use this template to record decisions and integration evidence, not to mirror tool state or define a machine-parsed business state model.

Keep one authoritative location for each fact. If an existing ticket index owns ticket status or unexpected findings, link to it instead of maintaining another copy. The coordinator is the sole writer of the shared record.

## Goal and authorization

- Goal, scope, and exclusions:
- Plan path and revision; complete initial ticket index:
- Target repository, target branch, and initial target SHA:
- Confirmed kind, provider, model, thinking, and max_workers:
- Source of the user's authorization:
- Authorization for OpenCode `--auto`, if applicable:
- Run ID and management directory:
- Additional runs, their reasons, and the aggregate concurrency constraint:

## Tickets and integration

Replace the example rows with the complete initial ticket set. Preserve links to every attempt rather than overwriting failed-attempt history.

| Ticket ID and source | Dependency and evidence required to unlock | Worker and result reference | Delivery declaration and SHA or artifact | Integration SHA or non-code acceptance evidence | Decision and next action |
| --- | --- | --- | --- | --- | --- |
| A | None | Not dispatched | No delivery | Not integrated | Dispatch when eligible |
| B | None | Not dispatched | No delivery | Not integrated | Dispatch when eligible |
| C | A integrated into the target branch | Not dispatched | No delivery | Not integrated | Wait for A's integration |

`delivered` is the worker's declaration. Record actual integration evidence before unlocking code dependents; acknowledgement is not that evidence. For investigation or verification tickets, record accepted durable artifacts instead of manufacturing empty commits.

## Decisions and unexpected findings

Append an entry when a decision or finding affects execution:

- Time, affected tickets, and pending-item IDs:
- Observed facts and evidence references:
- Decision, rationale, and relation to the authorized goal:
- Added, split, or adjusted tickets; dependency changes and affected work:
- Existing authorization or the unresolved question requiring the user:
- Owner and next action:
- Acknowledgement status; explicitly note **pending integration** if applicable:

If the repository has a designated unexpected-findings log, put the full entry there and retain only its reference here.

## Integration and verification

- Ticket and worker delivery SHA -> pre-merge target SHA -> actual integration SHA:
- Repository merge policy used; mapping evidence for squash or other non-ancestry integration:
- Worker acceptance evidence reused:
- Additional checks or verification workers and their conclusions:
- Goal coverage and combined-behavior evidence:
- Remaining gaps, blocked dependencies, and follow-up tickets:

For a conflict or behavioral failure, use [Integration repair](repair-and-closeout.md) and record:

- Pre-merge target and incoming SHAs, original ticket requirements, attempted command and failure evidence:
- Ownership/precondition evidence, abort command/result, restored HEAD/status or retained blocked scene:
- Repair ticket/worker and chosen base; previous attempts and incoming inputs:
- Target changes during repair, compatibility decision and supporting evidence:
- Original delivery -> repair delivery -> actual integration SHA; affected dependency release decision:

At closeout, account for every plan-level goal (reuse the existing acceptance index if it owns this mapping):

| Goal / acceptance requirement | Integrated SHA or accepted artifact | Applicable verification evidence | Result, gap, and next ticket |
| --- | --- | --- | --- |
| <goal> | <actual integrated result> | <worker, command/artifact, checked baseline> | <met / executing / blocked and reason> |

## Resources and closeout

- Retained workers, worktrees, branches, results, handoff documents, and archives; locations and reasons:
- Explicit cleanup decisions and cleanup-record references:
- Cleanup refusals, residual resources, and next actions:
- Overall conclusion: complete, executing, or blocked; supporting evidence:
- Final report: principal integration results, verification summary, unresolved items, and retained resource locations.
