# Operations Reference

Use this reference when executing the [herdr-plan-manager workflow](../../SKILL.md). The product entry is `bin/plan_manager.py`; consult `<operation> --help` for current parameters. This tool handles lifecycle mechanics, not scheduling.

Shell variables below are placeholders for confirmed values, never defaults inferred from historical smoke tests. Resolve `HPM` to the absolute path of `../../bin/plan_manager.py` relative to this document. Set `REPO` to the absolute target checkout path.

## Prepare and register

Verify `HERDR_ENV=1` and a populated `HERDR_WORKSPACE_ID`. Discover the installed CLI through `herdr --help` and `herdr agent --help`; use the installed help to inspect the selected runtime's official integration. If the environment is unavailable, report the blocker rather than substituting an execution protocol.

Use `pi --list-models` or `opencode models --verbose` to assist selection. Validate the provider/model pair and the selected model's ability to express the requested thinking level. A global list of reasoning levels is not proof that every model supports them. The tool validates at registration and launch.

```bash
python3 "$HPM" init-run --repo "$REPO" --run-id "$RUN" \
  --kind "$KIND" --provider "$PROVIDER" --model "$MODEL" \
  --thinking "$THINKING" --max-workers "$MAX_WORKERS"
```

Save the returned run ID and configuration. Registration does not launch a worker and does not overwrite an existing run. `RUN` accepts up to 64 lowercase letters, digits, or hyphens, beginning with a letter or digit.

**Configuration and quota scope.** One run binds one confirmed configuration. `start` inherits it; explicitly repeated fields must match. Replacement sessions keep the same configuration. A confirmed configuration change requires a new run and a recorded reason. The tool enforces concurrency per run: the coordinator must enforce the user's total cap across related runs. Creating another run cannot bypass that cap. Separate sequential runs are suitable for testing both runtimes.

**OpenCode permissions.** The adapter injects model and reasoning selection through the new pane's `OPENCODE_CONFIG_CONTENT`, without editing repository configuration. It currently starts OpenCode with `--auto`: permissions not explicitly denied are automatically approved; explicit denies remain. Confirm authorization for this behavior before launch. The manager has no per-launch switch to disable it.

**Management directory.** The default is `herdr-plan-manager/` under the target repository's Git common directory. To override it, pass `--management-root <absolute-path>` consistently on every operation for that execution. Examples below use the default.

## Dispatch

First establish that dependencies are satisfied. Resolve the intended target baseline to its full commit SHA and use that value as `BASE`.

```bash
python3 "$HPM" start --repo "$REPO" --run "$RUN" \
  --ticket-id "$TICKET" --title "$TITLE" --base "$BASE" \
  --material "$SPEC" --material "$ISSUE" \
  --instructions "$TASK_CONTEXT"
```

`TICKET` is a coordinator-assigned protocol ID: up to 64 letters, digits, dots, underscores, or hyphens, beginning with a letter or digit. This restriction does not constrain source filenames, languages, headings, or dependency notation.

Supply enough task context to identify one ticket's goal, scope, acceptance, and dependencies. Repeat `--material` for additional files or directories. The tool creates controlled read-only snapshots and records original paths in a manifest, so ignored or uncommitted main-checkout inputs remain accessible from isolated worktrees. Include required linked references explicitly: copying Markdown does not recursively collect everything it links to. Exclude secrets and unrelated material.

Save returned `worker_id`, branch, worktree, management and result paths, and launch facts as references in the execution record. The tool creates an isolated worktree and starts supervision; the coordinator does not assemble background shell processes.

On launch failure, inspect registration and preserved resources before deciding on another attempt. Uncertain delivery must be investigated, not blindly resent. Finite mechanical retries belong to the tool; a new business attempt requires a coordinator decision.

**Context thresholds.** Defaults are 300000 tokens or 0.8 window occupancy. Override with `--handoff-tokens` and `--handoff-pct` only when appropriate; keep thresholds above a fresh session's seed context. The token threshold is an operational policy, not a universal model-degradation boundary. Use `--context-window` only with reliable window-size evidence, never to conceal failed observation.

## Observe and handle items

```bash
python3 "$HPM" status --repo "$REPO" --run "$RUN"
python3 "$HPM" status --repo "$REPO" --worker "$WORKER"
python3 "$HPM" wait --repo "$REPO" --run "$RUN"
```

Run status exposes active workers and pending items. Worker status exposes results, sessions, handoff progress, supervision, and cleanup facts. A worker in handoff still occupies one slot. Retaining a delivered scene after business writes have stopped does not occupy an execution slot.

`wait` returns existing pending items immediately and otherwise waits indefinitely by default. Its `items` array may contain several entries; handle them independently. If the caller needs bounded tool calls, use a positive window such as `--timeout 30`. An empty array with `timed_out: true` means only that the window elapsed. Neither wait windows nor protocol-response timeouts are task execution budgets.

Read each item's `kind` and `code`, then inspect its worker's result and evidence. Terminal `idle` or `done` is not acceptance. `blocked` can mean a runtime error as well as an approval request. If a settled worker has no valid result, the tool permits one result-report correction before raising a protocol exception.

Persistent context-observation failures, failed handoffs, and missing supervision require coordinator decisions. Some Pi blocking interfaces may still report `working`; inspect the scene if progress is doubtful:

```bash
python3 "$HPM" read --repo "$REPO" --worker "$WORKER" --lines 120
```

Automatic handoff is tool-owned. For a deliberate handoff, request the same standard process:

```bash
python3 "$HPM" handoff --repo "$REPO" --worker "$WORKER" --reason "$REASON"
```

The response confirms a request, not successful replacement; inspect subsequent status. A successful handoff keeps the ticket, worktree, branch, and configuration. Results end eligibility for further handoff. A failed replacement preserves the scene and durable handoff document for disposition. Do not send ad hoc continuation prompts or create a second writer in that worktree.

For each outcome, first complete [outcome and plan-deviation backfill](execution-record.md#outcome-and-plan-deviation-backfill) in the shared Markdown record. For other items, record how they were handled. Then acknowledge the returned `item_id` (`<worker-id>/<item-id>`):

```bash
python3 "$HPM" ack --repo "$REPO" --item "$ITEM" --note "$DECISION"
```

Repeat `--item` to acknowledge several items. Acknowledged items stop appearing in wait results; new items remain discoverable. Acknowledgement neither stops a worker nor changes quality or integration conclusions. Preserve a **pending integration** entry when acknowledging before a merge. An empty pending-item list is not proof of completion.

For merge conflicts, behavior failures, or a target that changed during repair, follow [Integration repair](repair-and-closeout.md). Repairs and combined verification use the same `start --run` interface with a [repair brief](repair-brief.md) and explicit base; integration decisions remain in the coordinator's record.

## Stop and clean up

```bash
python3 "$HPM" stop --repo "$REPO" --worker "$WORKER" --reason "$REASON"
```

Require `business_stopped: true`. If stopping all registered business sessions cannot be confirmed, the tool reports `stop-incomplete`; preserve and investigate the scene rather than assuming write ownership is free. Stop coordinates supervision and in-progress handoff without deleting worktrees, branches, results, or handoff documents. A live supervisor owns the worker until it exits: `stop` never takes over while its recorded PID is alive, and concurrent stops for the same worker serialize on one per-worker lock.

**Automatic release after delivery.** A valid `delivered` result releases the worker's registered tabs once the result is durably recorded, every registered session is confirmed exited, the worktree has no uncommitted or untracked content and its `git status` is readable, and each tab holds only that worker's panes and agents. Release exits the TUI safely and closes only registered tabs; it does not wait for acknowledgement, integration, or cleanup and keeps the branch, worktree, result, logs, and materials.

The tab is retained, with the reason recorded in `status --worker` under `release`, when the result is absent or invalid, an agent query is unreadable (only an explicit `agent_not_found` proves exit), a session is still active or cannot be exited, the worktree is dirty or its status is unconfirmable, an occupancy listing is malformed or a registered pane is reported under a conflicting tab, a registered tab contains an unregistered pane or another agent, or the close itself fails. A close failure never changes the delivery or retries indefinitely. An early result file does not close a session that is still working.

Herdr exposes no conditional close that re-checks ownership in the same operation, so the manager re-verifies session state, worktree cleanliness, and tab occupancy immediately before **each** tab close. That narrows, but cannot eliminate, the race in which a foreign pane or agent appears between the final check and the close call; a later stop re-runs the same path and retains again if the condition persists. Retained-tab reasons are authoritative — inspect the scene instead of forcing a close.

`stop` reuses the same release path, so a delivered worker whose supervisor already exited — including records created before automatic release existed — is released by stopping it again. Repeated `stop` calls and already-closed tabs are idempotent. Disk cleanup still requires an explicit decision.

After confirming stopping and resource ownership, supply an explicit cleanup decision:

```bash
# Use the actual integration SHA recorded by the coordinator.
python3 "$HPM" cleanup --repo "$REPO" --worker "$WORKER" --integrated "$INTEGRATION_SHA"

# Use a specific disposition for accepted non-code work or explicitly abandoned work.
python3 "$HPM" cleanup --repo "$REPO" --worker "$WORKER" --disposition "$DISPOSITION"
```

`--integrated` records the coordinator's conclusion; it does not ask the tool to merge or verify the spec. Preserve durable copies of non-code artifacts before removing their worktree.

| Resource | Default | Explicit alternative |
| --- | --- | --- |
| Uncommitted content | Refuse deletion and retain it | `--archive-uncommitted` saves it before removal. Verify the archive location. Use the mutually exclusive `--discard-uncommitted` only when discarding that content is authorized. |
| Worker branch | Retain it | `--delete-branch` uses Git's safe deletion checks. `--force-branch` additionally requires that flag and an explicit deletion decision, such as after a verified squash mapping. It is not an automatic fallback. |
| Terminal resources | Operate only on registered, owned resources with no active business writers | Close only registered tabs; never take over unrelated terminals or resources outside this run's registration. |

Keep failed scenes until a specific disposition exists. Preserve committed results through integration or a retained branch; a vague disposition must not destroy the only copy of completed work.

After cleanup, inspect `status --worker` and its `cleanup` field. Record what was removed, remaining blockers or resources, and archive locations. Durable management records, results, and handoff materials remain available for traceability.

## Validate dynamic execution

When validating rather than executing a user plan, read [Validation](validation.md). Its A/B/C experiment demonstrates a coordinator merging A and launching its dependent C while independent B is still active. The experiment is not a batch template or a scheduler to embed in the product.
