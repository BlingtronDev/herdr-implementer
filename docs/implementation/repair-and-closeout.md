# Integration Repair and Goal Closeout

Use this procedure when a merge conflicts, integrated behavior fails, the target moves during repair, or the plan is ready for a completion decision. The coordinator owns the decision and normal merge; a **new worker** owns each repair or necessary combined verification. Use ordinary `start --run` dispatch with the [repair brief](repair-brief.md); no separate repair runtime or automatic merger is needed.

## Capture and release a conflicted target

Before initiating a normal merge, record the intended target branch, full target HEAD, incoming delivered HEAD, clean tracked/untracked status, and absence of an unfinished merge, rebase, cherry-pick, or revert. Follow repository merge policy. If user changes cannot be separated safely, preserve them and defer the merge.

If your merge conflicts:

1. Save the attempted command and exit status, pre-merge target SHA, incoming SHA, `git status --short`, `git diff --name-only --diff-filter=U`, and `git ls-files -u` in the durable execution evidence. Link both original tickets and their acceptance requirements. This identifies both sides and the conflicted paths without editing them.
2. Verify this is still your merge: `HEAD` is the saved target, `MERGE_HEAD` identifies the intended incoming commit, and no other writer or unexplained user work appeared. Run `git merge --abort` only with these known-safe preconditions.
3. Verify abort succeeded: target HEAD is unchanged, no unmerged index entries or `MERGE_HEAD` remain, and the recorded clean status is restored. Retain the incoming branch and worker scene.

**Abort unavailable or unsafe:** retain the worktree, index, operation state, and evidence; record a blocker and report it. Do not force-reset, clean, or overwrite that scene. Independent work can continue in separately owned worktrees. A new worker may investigate a safely captured reproduction, but resuming merges into the target requires clarified ownership and a safe restoration decision.

**Ready for isolated repair:** both immutable inputs and their requirements are identified, and the target is either verified restored or explicitly blocked and preserved.

## Dispatch and integrate a repair

1. Add a stable repair ticket linked to the original goal and affected tickets. Record the failure and reason for adding work. Keep affected dependents blocked on **repair integration and compatibility evidence**; continue unrelated work when eligible.
2. Resolve the chosen target baseline to a full SHA. Fill the [repair brief](repair-brief.md) with both sides, requirements, reproduction, expected behavior, and verification. Include all required source materials via `--material`; references are not copied recursively. For incoming commits outside this repository, first establish an accessible ref or controlled patch and record provenance.
3. Dispatch through [Operations / Dispatch](operations.md#dispatch) using `--base` equal to that baseline. The lifecycle tool creates the worker's isolated branch/worktree. For a **text conflict**, the worker merges the pinned incoming SHA inside that assigned branch, reproduces the conflict, resolves both intents, verifies, and commits. For behavior repair, investigation, or verification, it follows the brief's reproduction and acceptance requirements. In every case it keeps the assigned branch and leaves the target checkout untouched.
4. Inspect the delivered result, verification, actual branch HEAD, and uncommitted-content report. For a merge repair, check that both pinned inputs are represented by the delivered ancestry; where repository policy uses squash or patches, retain an explicit mapping and verification instead. Delivery or acknowledgement leaves the repair **pending integration**.
5. Compare the current target with the repair baseline before merging. If unchanged, perform a normal serial merge following repository policy. Record original delivery -> repair delivery -> actual integration SHA and compatibility evidence, then reassess dependents.

### Target changed during repair

Record the new target SHA and intervening changes. Judge whether the worker's evidence still establishes compatibility: a disjoint documentation-only change may permit a normal merge with a recorded rationale. Changes touching the repaired interfaces or uncertain interactions require a **new repair or verification worker** from the updated target, supplied with the previous repair SHA and changed requirements. A new textual conflict re-enters the capture/abort procedure above.

Keep affected dependencies blocked until evidence covers the actual integrated combination. Stale-baseline delivery is useful evidence, not automatic permission to unlock work. Avoid repeated conflicts by choosing to stabilize the target briefly when useful; the tool makes no such scheduling decision.

## Repair behavior without text conflicts

A clean merge can still violate the plan. Record the failing command, exit code, expected/actual behavior, integrated target SHA, and implicated ticket results. Keep the failure visible even if all initial tickets have delivered and all pending items have been acknowledged.

Create a repair ticket from that integrated SHA. Supply the reproduction and original intent; the new worker diagnoses, repairs, and runs a regression check plus relevant individual acceptance checks. If the intended behavior is genuinely ambiguous, ask the user for that requirement decision. If diagnosis is unclear, dispatch investigation rather than prescribing an unsupported fix.

Integrate the accepted repair using the same baseline comparison and evidence mapping above. A verification-only ticket may return a durable findings artifact without a new commit. Required combined validation is worker work; the coordinator may inspect or run checks to support judgment and reuse sufficient existing evidence.

## Decide overall completion

Compare each plan-level goal with its actual integrated result and applicable evidence. Follow [Important decisions and closeout](execution-record.md#important-decisions-and-closeout), referencing the authoritative goal acceptance index when one exists. Evidence from an earlier baseline is reusable only when the later changes do not invalidate it; state the reason when relevant.

- An unmet goal or known combined failure means **executing** if actionable follow-up exists, or **blocked** if a decision or external condition prevents progress. Add the required repair, investigation, or verification ticket and keep the goal open.
- **Complete** requires every in-scope goal supported, required results integrated or non-code artifacts explicitly accepted, and no unresolved failure of acceptance. Remaining optional follow-ups must be clearly distinguished from unmet requirements.

Report the overall conclusion, principal integration mappings, verification evidence and its limits, unresolved issues, and retained worker/worktree/branch/result/archive locations. Resource disposal follows [Stop and clean up](operations.md#stop-and-clean-up); goal completion does not itself authorize deleting scenes.
