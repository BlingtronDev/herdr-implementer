# Integration Repair

Read for a merge conflict, integrated behavior failure, or target movement during repair. Normal goal closeout uses [Important decisions and closeout](execution-record.md#important-decisions-and-closeout), not this procedure. The coordinator owns decisions and serial target merges; implementation repairs use an ordinary new ticket/worker in an isolated worktree. Simple supplementary checks may run directly; dispatch independent or substantial verification only when needed.

## Capture and release a conflicted target

Use the pre-merge branch, full target/incoming SHAs, clean tracked/untracked status and absence of unfinished Git operations saved under [Integrate serially and unlock dependencies](../../SKILL.md#4-integrate-serially-and-unlock-dependencies).

If your merge conflicts:

1. Save the attempted command and exit status, `git status --short`, `git diff --name-only --diff-filter=U`, and `git ls-files -u` as durable evidence. Reference the pre-merge snapshot and both original requirements instead of copying them into separate reports.
2. Confirm ownership: the target branch and `HEAD` match the snapshot, `MERGE_HEAD` identifies the intended incoming SHA, and no other writer or unexplained user work appeared. Run `git merge --abort` only with these known-safe preconditions.
3. Verify restoration: abort succeeded, target branch and HEAD are unchanged, no unmerged index entries or `MERGE_HEAD` remain, and the recorded clean status is restored. Retain the incoming branch and worker scene.

**Abort unavailable, failed, or unsafe:** preserve the worktree, index, operation state and evidence; record and report a blocker. Do not force-reset, clean or overwrite the scene. Independent work may continue in separately owned worktrees; target merges wait for clarified ownership and a safe restoration decision.

**Ready for isolated repair:** both immutable inputs and requirements are accessible, and the target is verified restored or explicitly blocked and preserved.

## Dispatch and integrate a repair

1. Add a stable repair ticket linked to the goal, affected tickets and failure evidence. Keep affected dependencies blocked until **actual integration and compatibility evidence** are both available.
2. Fill the [repair brief](repair-brief.md). Resolve the chosen target baseline to a full SHA; make outside commits available through a controlled ref or patch with provenance. Follow [Dispatch](operations.md#dispatch) with that SHA as `--base` and all required materials. The worker reproduces and repairs in the tool-created isolated worktree under the injected contract.
3. Handle the result through [Outcome handling](execution-record.md#outcome-handling). For text repair, check that both pinned inputs are represented by delivered ancestry; squash or patch integration needs an explicit mapping and verification instead. Reference original delivery -> repair delivery -> actual integration SHA in the authoritative record. Acceptance or ack alone leaves code pending integration.
4. Compare the current target with the repair baseline. If unchanged, integrate serially under repository policy; otherwise apply the baseline decision below. Unlock affected dependencies only when evidence covers the actual integrated combination.

### Target changed during repair

Save the new target SHA and intervening diff reference. Reuse earlier evidence only when those changes do not invalidate it, with a rationale where relevant. A disjoint documentation change may permit normal integration. Changed interfaces or uncertain interactions require fresh compatibility evidence: the coordinator may run simple supplementary checks; independent or substantial verification gets a new ticket/worker from the updated target with the previous repair SHA. Further implementation needs a new repair worker. A new text conflict re-enters capture/abort above.

Until the actual combination is supported, keep affected dependencies blocked. If repeated movement prevents a reliable check, stabilize the target briefly rather than treating stale delivery as permission to proceed.

## Repair behavior without text conflicts

Save the failing command, exit code, expected/actual behavior, integrated target SHA and implicated result references. A clean merge and all workers delivered do not close a failing goal.

Create a repair ticket from that integrated SHA, with reproduction, original intent and regression checks for the combined case and both original behaviors. Ask the user for genuinely ambiguous requirements; use investigation when diagnosis is unclear instead of prescribing an unsupported fix. Integrate through the same baseline and compatibility decision above, then return to the execution record's goal closeout.

Verification-only work can yield a durable findings artifact without a new commit. Apply the record's explicit acceptance and artifact-preservation rules before cleanup.
