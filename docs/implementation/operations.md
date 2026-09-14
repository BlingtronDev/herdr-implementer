# Operations Reference

Use this reference when executing the [herdr-implementer workflow](../../SKILL.md). The product entry is `bin/implementer.py`; consult `<operation> --help` for current parameters. This tool handles lifecycle mechanics, not scheduling.

Shell variables below are placeholders for resolved values, never defaults inferred from historical smoke tests. Resolve `HI` to the absolute path of `../../bin/implementer.py` relative to this document. Set `REPO` to the absolute target checkout path.

## Prepare and register

Reuse confirmed configuration or an unambiguous, verifiable persistent source. Resolve missing or conflicting values before registration; use `pi --list-models` or `opencode models <provider> --verbose` only when selection information is missing or validation fails. Never silently select another model/provider.

The tool validates model-catalog membership at registration and launch. Pi validation checks the global thinking-level names and the model's thinking capability flag; OpenCode checks the exact model's advertised variant. It does not prove Pi's per-level behavior, provider credentials, service availability, or the effective runtime configuration. Retain reliable runtime documentation/configuration evidence for the requested Pi level; if unavailable, resolve that gap before launch rather than treating the global list as proof. Verify effective configuration from runtime-produced evidence when execution begins.

`start` checks `HERDR_ENV=1` and a populated `HERDR_WORKSPACE_ID`, plus repository, base commit, and material inputs. It does not preflight the installed Herdr/runtime integration or the context helper dependencies. Reuse current installation evidence; when missing or after an installation change, inspect `herdr --help` and `herdr agent --help` for the selected runtime's official integration, and verify its installation and context-helper dependencies (including Node for Pi). Registration alone is not environment readiness. Report unavailable prerequisites or runtime errors as blockers, not grounds to substitute an execution protocol.

```bash
python3 "$HI" init-run --repo "$REPO" --run-id "$RUN" \
  --kind "$KIND" --provider "$PROVIDER" --model "$MODEL" \
  --thinking "$THINKING"
```

Save a reference to the returned run and its resolved configuration, including the saved concurrency limit. Registration does not launch a worker or overwrite an existing run. For an explicit limit, ID constraints, and other options, use `init-run --help`.

**Configuration and quota scope.** One run binds one confirmed configuration. `start` inherits it; explicitly repeated fields must match. Replacement sessions keep the same configuration. A confirmed configuration change requires a new run and a recorded reason. The tool enforces concurrency per run, not globally across runs or nested agents: the coordinator must honor the user's aggregate resource constraints and explicit time/cost budgets. Creating another run cannot bypass those constraints; there is no global scheduler. Existing runs keep their saved positive integer limit; a missing or invalid saved limit blocks startup rather than acquiring today's default. Separate sequential runs are suitable for testing both runtimes.

**OpenCode configuration.** Follow the entry's [Permissions](../../SKILL.md#permissions). The adapter injects model and reasoning selection through the new pane's `OPENCODE_CONFIG_CONTENT`, without editing repository configuration.

**Management directory.** The default is `herdr-implementer/` under the target repository's Git common directory. To override it, pass `--state-root <absolute-path>` consistently on every operation for that execution. Examples below use the default.

## Dispatch

First establish that dependencies are satisfied. Resolve the intended target baseline to its full commit SHA and use that value as `BASE`.

```bash
python3 "$HI" start --repo "$REPO" --run "$RUN" \
  --ticket-id "$TICKET" --title "$TITLE" --base "$BASE" \
  --material "$SPEC" --material "$ISSUE" \
  --instructions "$TASK_CONTEXT"
```

Supply enough task context to identify one ticket's goal, scope, acceptance, and dependencies. Repeat `--material` for additional files or directories. The tool creates controlled read-only snapshots and records original paths in a manifest, so ignored or uncommitted main-checkout inputs remain accessible from isolated worktrees. Include required linked references explicitly: copying Markdown does not recursively collect everything it links to. Exclude secrets and unrelated material.

Save the returned `worker_id` and management-record reference in the execution record; resolve branch, resource paths and launch facts there rather than copying them. The tool creates an isolated worktree and starts supervision; the coordinator does not assemble background shell processes.

On launch failure, inspect registration and preserved resources before deciding on another attempt. Uncertain delivery must be investigated, not blindly resent. Finite mechanical retries belong to the tool; a new business attempt requires a coordinator decision.

If launch delivery is uncertain or context observation/handoff needs diagnosis or tuning, read [Troubleshooting](troubleshooting.md). Normal dispatch uses the tool's policy without threshold tuning.

## Observe and handle items

```bash
python3 "$HI" status --repo "$REPO" --run "$RUN"
python3 "$HI" status --repo "$REPO" --worker "$WORKER"
python3 "$HI" wait --repo "$REPO" --run "$RUN"
```

Run status exposes active workers and pending items. Worker status exposes results, sessions, handoff progress, supervision, and cleanup facts. A worker in handoff still occupies one slot. Retaining a delivered scene after business writes have stopped does not occupy an execution slot.

Handle every entry in the returned `items` array independently. For a bounded call, use `wait --timeout 30`; an empty array with `timed_out: true` means only that the window elapsed, not task failure. Wait windows are not task execution budgets. See `wait --help` for waiting options.

Read each item's `kind` and `code`, then inspect its worker's result and evidence. Terminal `idle` or `done` is not acceptance. For a reported exception or doubtful progress, inspect the scene and follow [Troubleshooting](troubleshooting.md#observation-or-handoff-exceptions):

```bash
python3 "$HI" read --repo "$REPO" --worker "$WORKER" --lines 120
```

Automatic handoff is tool-owned. For a deliberate handoff, request the same standard process:

```bash
python3 "$HI" handoff --repo "$REPO" --worker "$WORKER" --reason "$REASON"
```

The response confirms a request, not successful replacement; inspect subsequent status. A successful handoff keeps the ticket, worktree, branch, and configuration. Results end eligibility for further handoff. A failed replacement preserves the scene and durable handoff document for disposition. Do not send ad hoc continuation prompts or create a second writer in that worktree.

For each outcome, follow [Outcome handling](execution-record.md#outcome-handling): read the result and evidence, save their references, the decision and unfinished actions, then ack. Do not transcribe worker facts. If saving fails, do not ack items whose follow-up depends on that record. For other items, save the handling decision and any next action. Then acknowledge the returned `item_id` (`<worker-id>/<item-id>`):

```bash
python3 "$HI" ack --repo "$REPO" --item "$ITEM" --note "$DECISION"
```

Repeat `--item` to acknowledge several items. Acknowledged items stop appearing in wait results; new items remain discoverable. Acknowledgement neither stops a worker nor changes quality or integration conclusions. When acknowledging before a merge, preserve **pending integration** with its next action in the authoritative record. After merging, update the actual integration mapping before unlocking code dependencies. An empty pending-item list is not proof of completion.

For merge conflicts, behavior failures, or a target that changed during repair, follow [Integration repair](repair-and-closeout.md). Repairs and combined verification use the same `start --run` interface with a [repair brief](repair-brief.md) and explicit base; integration decisions remain in the coordinator's record.

## Stop and clean up

```bash
python3 "$HI" stop --repo "$REPO" --worker "$WORKER" --reason "$REASON"
```

Require `business_stopped: true` before treating write ownership as free. Stop preserves worktrees, branches, results, and handoff documents. If stopping is incomplete, preserve the scene and follow [Stop or cleanup blockers](troubleshooting.md#stop-or-cleanup-blockers).

**Automatic release after delivery.** A valid delivery releases the registered terminal when the tool's safety conditions are met; the branch, worktree, result, logs, and materials remain. Release does not wait for ack or integration. If retained, inspect `status --worker` under `release` and follow [Stop or cleanup blockers](troubleshooting.md#stop-or-cleanup-blockers), rather than forcing a close. Disk cleanup is a separate decision.

After confirming stopping and resource ownership, supply an explicit cleanup decision:

```bash
# Use the actual integration SHA recorded by the coordinator.
python3 "$HI" cleanup --repo "$REPO" --worker "$WORKER" --integrated "$INTEGRATION_SHA"

# Use a specific disposition for accepted non-code work or explicitly abandoned work.
python3 "$HI" cleanup --repo "$REPO" --worker "$WORKER" --disposition "$DISPOSITION"
```

`--integrated` records the coordinator's conclusion; it does not ask the tool to merge or verify the spec. Preserve durable copies of non-code artifacts before removing their worktree.

Uncommitted content blocks deletion; branches are retained unless explicitly selected for deletion. If archival, authorized discard, or branch deletion is needed, consult `cleanup --help` and [Stop or cleanup blockers](troubleshooting.md#stop-or-cleanup-blockers). Operate only on registered, owned resources with confirmed stopping; tool refusal is not permission to bypass its checks.

Keep failed scenes until a specific disposition exists. Preserve committed results through integration or a retained branch; a vague disposition must not destroy the only copy of completed work.

After cleanup, inspect `status --worker` and its `cleanup` field. Record what was removed, remaining blockers or resources, and archive locations. Durable management records, results, and handoff materials remain available for traceability.
