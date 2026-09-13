# Repair or Verification Brief

The coordinator creates an ordinary ticket in the target repository (for example `<target-repo>/.scratch/<slug>/issues/<ticket-id>.md`) from this read-only template and supplies its absolute path via `--material`. Omit fields that do not apply; keep immutable SHAs and concrete acceptance requirements. The manager's rendered worker contract remains authoritative for workspace, runtime, and result publication.

## Goal and inputs

- Ticket ID, type (text-conflict repair / behavior repair / investigation / verification):
- Authorized goal and reason this work is necessary:
- Original ticket IDs, plan references, and required acceptance behavior from each side:
- Explicit `--base` SHA and intended target branch:
- Incoming branch and pinned SHA; previous repair SHA if applicable:
- Source of each commit or patch; accessible material paths:
- Captured conflict or behavior failure evidence, exact reproduction command, actual exit code, expected/actual result:
- Target status: restored after coordinator-owned abort, or blocked scene retained at location:
- Changes since an earlier repair baseline and compatibility questions:
- Affected dependents and evidence needed to unblock them:

## Worker scope and acceptance

- Reproduce the recorded problem in your assigned isolated worktree. For text conflicts, merge the pinned incoming commit into the manager-assigned branch using repository policy; resolve both documented intents there.
- Implement the repair (or investigate/verify if that is the assigned type). Preserve original acceptance behavior and cover the combined case with concrete checks. Record exact commands, observed exit codes, and evidence, including the initial reproduction.
- Required checks and expected outcomes: <fill with task-specific checks>.
- Report requirement ambiguity or a changed assumption through the normal result protocol. Keep the coordinator's target checkout, shared plan, and other worker scenes read-only.
- Commit code results on the assigned branch, account for uncommitted content, and report the final HEAD and verification under the existing worker contract. A verification-only task produces an artifact without an empty commit.

The coordinator will judge baseline freshness, perform the target merge, update dependencies, and decide overall completion after delivery.
