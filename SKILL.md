---
name: herdr-ticket-dispatcher
description: "Dispatch a user-confirmed local ticket plan in explicit Herdr worker batches, then merge each completed branch from the calling agent session. Requires HERDR_ENV=1."
disable-model-invocation: true
---

# Herdr Ticket Dispatcher

Plan and execute one local `.scratch/<slug>/issues/` ticket set. The calling agent owns dependency analysis, batch order, failure decisions, and merges. `bin/dispatcher.py` is only a batch executor: it launches isolated Herdr workers, waits at the batch barrier, rolls workers over through the `handoff` skill at the context threshold, cleans stale attempts, and reports worker declarations.

`SKILL_DIR` is the directory containing this `SKILL.md`.

## Process

1. Run `test "${HERDR_ENV:-}" = 1`. If it fails, tell the user this skill must run inside Herdr and stop.
2. Resolve the worker runtime through exactly four sequential questions. Ask one question per turn; each answer gates discovery for the next:
   1. Ask for **kind**: `opencode`, `codex`, or `pi`. Do not query or mention providers yet.
   2. Run `python3 "${SKILL_DIR}/bin/dispatcher.py" --catalog providers --kind <kind>`. Show every returned provider and ask the user to choose one. Do not query models yet.
   3. Run `python3 "${SKILL_DIR}/bin/dispatcher.py" --catalog models --kind <kind> --provider <provider>`. Show every returned model and ask the user to choose one. Do not ask about thinking yet.
   4. Ask for **thinking level**: `off`, `minimal`, `low`, `medium`, `high`, `xhigh`, or `max`.
3. Keep all four confirmed values verbatim. Never infer a default, move a model between providers, or change the thinking level.
4. Select the feature slug. Inspect `.scratch/*/issues/*.md`; if more than one group exists, show them and ask the user to choose. Do not start a worker yet.
5. Build the execution plan:
   1. Read `.scratch/<slug>/spec.md` and every `.scratch/<slug>/issues/*.md` in full.
   2. Derive dependencies from both spec semantics and explicit `Blocked by:` fields. Treat the fields as evidence, not as a complete graph.
   3. Partition tickets into ordered parallel batches. Every ticket in a batch must depend only on work already merged before that batch. Tickets with a dependency on one another belong in different batches because every worker in one batch starts from the same target HEAD.
   4. Capture the current branch and full HEAD. Write `.scratch/<slug>/plan-<run-id>.json` with `created_at`, `based_on_head`, ordered `batches`, and `notes`. Each batch contains `batch`, `tickets`, and later receives `base` and `merged_sha`.
   5. Show the complete batch plan and rationale to the user. Obtain one explicit confirmation before dispatching the first worker. If the plan changes later, show the revision and obtain confirmation again.
6. For each confirmed batch, record the current full target HEAD as that batch's `base`, then run one foreground command:

   ```bash
   python3 "${SKILL_DIR}/bin/dispatcher.py" \
     --kind <kind> --provider <provider> --model <model> --thinking <thinking-level> \
     --slug <feature-slug> --batch-number <N> --batch "<ticket-id> <ticket-id> ..." \
     --target <current-branch> --expect-target-head <batch-base-head> \
     [--jobs N] [--window TOKENS] [--batch-timeout MINUTES] [--fail-fast]
   ```

   `--batch-timeout` is optional; when set it is the per-batch wall-clock budget in minutes and must be at least 120.

   `--batch` is mandatory. A command executes only those tickets and returns after all of them reach a terminal declaration. Keep the command in the foreground; timing, polling, wait-any, context rollover, and the batch barrier belong to the program. Batch execution may legitimately take hours: never impose a per-batch timeout shorter than 120 minutes, do not kill or background the foreground command early, and do not poll for completion outside the summary file. At 300K observed tokens (or the configured percentage safety threshold), the executor interrupts that worker, sends `/handoff` to save a Markdown continuation under the OS temporary directory, then starts a fresh worker in the same worktree and branch. A successful rollover is not a failed attempt; repeated rollovers remain within the same attempt and are recorded in run state.
7. Read `results/batch-<N>-summary.json` from the emitted run directory. A worker declaration is final: `completed` means completed. `workers[].handoffs` lists any rollover documents. Do not inspect its diff, acceptance evidence, test output, commit messages, worktree cleanliness, or `.scratch/` changes as a second acceptance gate. Protocol/merge-input errors reported by the executor are failures, not quality judgments.
8. Complete the batch before starting the next one:
   1. **Disposition:** for every `failed` or `needs-input` entry, show the exact reason and choose with the user whether to retry it in a new higher-numbered attempt, pause, or leave it failed. Never let a failed prerequisite silently unblock a later batch.
   2. **Merge:** for each `completed` worker, from the target worktree run `git merge --ff-only <branch>`. If fast-forward is impossible, run `git merge --no-ff --no-edit <branch>`. Merge branches one at a time without squashing.
   3. **Conflict resolution:** if a merge conflicts, inspect `git status`, every conflicted path, both sides of the conflict, `.scratch/<slug>/spec.md`, and the relevant incoming and already-merged tickets. Resolve the conflict autonomously in the target worktree so the combined result satisfies the spec and every non-superseded ticket requirement. Stage only deliberate resolution changes, require `git diff --name-only --diff-filter=U` to be empty, run focused checks for the reconciled behavior, finish the merge with `git commit --no-edit`, and continue with the remaining branches and batches. Preserve both sides' compatible intent rather than choosing one side wholesale merely to clear the conflict. Ask the user only when the source documents impose genuinely incompatible requirements and no precedence can be inferred; preserve the in-progress merge while awaiting that decision.
   4. **Record:** after all selected completed branches merge, write the new full target HEAD to that batch's `merged_sha` in the plan file. Use it as the next batch's expected base.
9. Repeat steps 6–8 until every confirmed batch has a disposition. Report each batch summary path, every merged branch/SHA, all retained failed branches/artifacts, and the final plan path. Partial completion is not success.

## Trust and ownership

- The worker owns its completion claim. The executor only checks that result JSON and branch/HEAD merge inputs are readable; it performs no acceptance or quality review.
- The calling agent alone runs `git merge`. The Python executor never merges, advances the target HEAD, resolves conflicts, or deletes completed branches.
- `.scratch/<slug>/` is mutable control data. Workers treat it as read-only; executor status updates may remain in the target worktree.
- Stale `claimed` attempts are abandoned by default after ownership verification: stop worker, close its tab, delete its worktree and branch, then allocate a higher attempt. Use `--skip-claimed` only when the user explicitly wants them untouched. If ownership cannot be verified, stop for manual handling.
- Answer worker approval/question UIs only with the user's decision. A non-interactive `needs-input` declaration is returned in the batch summary for the calling agent to handle.
- Preserve failed branches and artifacts. Abandoned stale attempts are the deliberate exception.
- Context-limit rollover preserves the attempt worktree and branch. Stop only the old session after its handoff document is readable; launch the replacement with the same pinned runtime and require it to read that document before continuing. If handoff generation or replacement launch fails after one handoff correction, preserve the branch/artifacts and report a real failure.
- Preserve the target worktree outside `.scratch/<slug>/`. Resolve merge conflicts there through the spec-and-ticket reconciliation in step 8; never reset the target, close an unregistered tab, or clean ignored files.

The worker contract is in [`prompts/worker.md`](prompts/worker.md). Runtime state and result contracts are in [`schema/`](schema/); consult them only when diagnosing stale cleanup or protocol failures.
