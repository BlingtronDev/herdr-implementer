# Lifecycle implementation notes

For maintainers changing lifecycle safety checks, not coordinators executing a plan. The authority is `bin/implementer.py`; use [Validation](validation.md) for deterministic and real-runtime evidence. Operational exception handling lives in [Troubleshooting](../implementation/troubleshooting.md).

## Ownership and stopping

Concurrent starts serialize quota admission; limits belong to a run, not a global scheduler. During handoff the same worker retains its slot and worktree. Replacement business writes must wait for the previous session to stop.

A live supervisor owns worker state until it exits, even after recording a terminal result: release can still be in progress. `stop` does not take over while the recorded supervisor PID is alive. Concurrent stops serialize on a per-worker lock so release decisions are not duplicated or overwritten. Stopping covers all registered business sessions and handoff competition; incomplete confirmation must preserve the scene.

## Delivered-terminal release

A valid, durably recorded delivered result can release registered tabs only after all registered sessions are confirmed exited, Git status is readable and clean (including untracked content), and occupancy establishes exclusive ownership. An early result file does not close a working session. For agent queries, only explicit `agent_not_found` establishes absence; unreadable responses do not.

Occupancy validation checks registered pane/tab associations and rejects malformed listings, conflicting tab assignments, unregistered panes, or other agents in a registered tab. Each tab close rechecks session exit, worktree cleanliness, and occupancy. Herdr has no atomic conditional close, so the final check-to-close race is narrowed, not eliminated. Preserve this limitation in safety claims and regression tests.

Release retains the branch, worktree, result, logs, and materials; it is independent of acknowledgement and integration. Retention reasons are persisted under `release`. A failed close neither changes delivery nor retries indefinitely.

`stop` reuses release after confirmed stopping, including for delivered records created before automatic release existed. Repeated stops and already-closed tabs are idempotent. Cleanup remains a separate, explicitly authorized operation with dirty-content and committed-result protection.

## Change validation

Keep the existing tests for concurrent admission, handoff single-writer ordering, live-supervisor stopping, unreadable agent/Git/occupancy responses, foreign panes/agents, close failures, repeated stop, and dirty cleanup. Documentation layering is not grounds to remove program checks, change state version, or add terminal-worker recovery. See the lifecycle scenarios in [Validation](validation.md#lifecycle-coverage-and-acceptance).
