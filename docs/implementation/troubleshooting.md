# Troubleshooting

Load only for the matching exception or deliberate tuning. Use `python3 "$HI" <operation> --help` for current options; variables are defined in [Operations](operations.md).

## Launch uncertainty

If `start` fails or its response is lost, inspect run status and preserved worker records before retrying. Determine whether a worker was registered or launched; uncertain delivery is not permission to resend. Resolve configuration errors against the selected runtime's catalog and installation evidence described in [Prepare and register](operations.md#prepare-and-register). Report unavailable prerequisites as blockers, without changing execution protocol. A new business attempt needs a coordinator decision and confirmed stopping of the previous writer.

## Observation or handoff exceptions

Read the pending item's `kind` and `code`, worker status, and `read --worker "$WORKER" --lines 120`. `blocked` can be a runtime error rather than a permission request; some Pi blocking interfaces still show `working`. Terminal labels alone cannot establish progress or a valid result. The tool attempts one result-report correction for a settled worker without a valid result; handle a resulting protocol exception rather than accepting terminal idleness.

Persistent context-observation failures, missing supervision, or failed handoffs require a recorded decision: resolve the prerequisite, preserve and stop the attempt, or report a blocker. A failed replacement keeps the scene and durable handoff document. A handoff response confirms a request, not completion: inspect subsequent status. Keep the same worker's worktree single-writer; do not improvise continuation prompts or revive a delivered worker.

## Deliberate context tuning

Consult `start --help` for `--handoff-tokens`, `--handoff-pct`, and `--context-window`, including current defaults. Keep thresholds above the fresh session's seed context to avoid immediate repeated handoffs. Token thresholds are operational policy, not universal degradation boundaries. Override the context window only with reliable size evidence, never to conceal failed observation. Successful handoff remains the same worker, not a new business attempt.

## Stop or cleanup blockers

Inspect `status --worker` and its `release`, `sessions[].release` (handoff), or `cleanup` reason. For `stop-incomplete`, retain the scene until stopping can be confirmed; no replacement writer or cleanup may assume ownership is free. A retained terminal does not invalidate a delivery, and a released terminal does not prove integration.

Resolve the reported condition (for example, uncertain session exit or resource ownership) without forcibly closing tabs or deleting files. Dirty worktrees block disk cleanup, not terminal release. After resolving it, use `stop` again and inspect the returned facts. If ownership or stopping remains uncertain, preserve resources and report the blocker.

Before cleanup, protect committed work by integration or a retained branch and copy accepted non-code artifacts to a durable destination. For dirty content, `cleanup --archive-uncommitted` saves an archive before removal; verify its saved location. `--discard-uncommitted` requires authorization to discard that content. For branch deletion, consult `cleanup --help`: `--force-branch` also requires `--delete-branch` and an explicit deletion decision (such as a verified squash mapping), never an automatic fallback after refusal. Keep failed scenes until a specific disposition exists.

Save remaining blockers, retained resources, and archive references in the coordinator's record, not a parallel lifecycle state table.
