# Handoff 001 — ticket A: post-migration smoke A with handoff and dirty worktree

- Worker: `w09-opencode-a` (opencode, provider `opencode-go`, model `deepseek-v4.1-flash`, thinking `max`)
- Branch: `hpm/w09-opencode-a`
- Worktree: `/tmp/opencode/hpm09-migration-lawoi34f/opencode/.git/herdr-plan-manager/worktrees/w09-opencode-a`
- Base SHA: `70a7e2d20cf5f97cab559ca5b5bb04c05c8164bc`
- Management dir: `/tmp/opencode/hpm09-migration-lawoi34f/opencode/.git/herdr-plan-manager/workers/w09-opencode-a`
- Contract: `/tmp/opencode/hpm09-migration-lawoi34f/opencode/.git/herdr-plan-manager/workers/w09-opencode-a/contract.md`
- Materials (read-only): `materials/01-spec.md`, `materials/02-A.md`, `materials/03-corpus.txt` under the management dir
- Handoff reason: context threshold reached (35085 / 1000000 tokens, 4%) at the controlled initial-session checkpoint. This is an expected automatic handoff, not a failure or a decision request.

## Progress

- Confirmed this is the INITIAL session (no previous handoff; `state.json` showed `session_index: 1`, empty handoff history, branch at base).
- Read corpus rows into model context in contiguous chunks of at most 400 lines, from `materials/03-corpus.txt`:
  - chunk 1: lines 1–242 (rows 0000–0241)
  - chunk 2: lines 243–484 (rows 0242–0483)
  - chunk 3: lines 485–726 (rows 0484–0725)
  - chunk 4: lines 727–968 (rows 0726–0967)
  - chunk 5: lines 969–1210 (rows 0968–1209)
  - chunk 6: lines 1211–1452 (rows 1210–1451)
  - chunk 7: lines 1453–1694 (rows 1452–1693)
  - Boundaries are gapless and non-overlapping; each read was capped near 50 KB by the Read tool, which is why chunks are ~242 lines even though `limit=400` was requested.
- Stopped corpus reading when the lifecycle tool requested handoff (ticket allows stopping at handoff even before the 2400-row cap). Rows 1694–2399 were intentionally NOT read.
- Wrote uncommitted `seed-note.txt` in the worktree containing exactly `seed prepared before handoff\n`.
- Did NOT create `api.txt` or `resumed.txt`, did NOT commit anything, and did NOT write `result.json` in this session (as required for the initial session).

## Decisions

- Treat the handoff request as the controlled checkpoint defined by ticket A; do not resume corpus reading.
- Next session must NOT reread the corpus (`materials/03-corpus.txt`); the handoff plus already-saved seed note are sufficient.
- Commit ONLY `api.txt` and `resumed.txt` in the continuation session; `seed-note.txt` must stay uncommitted on purpose (dirty-worktree retention is part of the acceptance).
- Use the exact file contents required by the spec: `HPM09_API_V1\n` for `api.txt` and `HPM09_RESUMED_OK\n` for `resumed.txt` (single trailing newline, no extra bytes).
- Publish the final result with the lifecycle tool's delivered-result JSON at the contract's result path.

## Verification

Checks already run in this session (all from the worktree unless noted):

- `wc -l .../materials/03-corpus.txt` → `2400` (corpus is exactly 2400 rows).
- Read tool chunks: 7 contiguous chunks covering lines 1–1694 with no gaps (see Progress); contents are corpus rows, not summaries.
- `git status --porcelain=v1 -b` → `## hpm/w09-opencode-a` plus `?? seed-note.txt` only.
- `git log --oneline -3` → single commit `70a7e2d initialize smoke fixture`.
- `git rev-parse HEAD` → `70a7e2d20cf5f97cab559ca5b5bb04c05c8164bc` (no commits on top of base yet).
- Read of `seed-note.txt` → exactly `seed prepared before handoff` (one line).
- Not yet run (must be done next session): exact-content assertions for `api.txt`/`resumed.txt`, staging/commit of exactly those two files, post-commit status/HEAD checks, and writing `result.json`.

## Commits

- None. Branch HEAD is still at the base SHA `70a7e2d20cf5f97cab559ca5b5bb04c05c8164bc` (`70a7e2d initialize smoke fixture`).

## Uncommitted work

- Worktree: `seed-note.txt` (untracked), content `seed prepared before handoff\n` — intentionally left uncommitted; must remain uncommitted after the continuation commit.
- Management dir: this handoff document at `handoffs/handoff-001.md`.
- No other modified, staged, or untracked files.

## Next steps

Continuation session — do these immediately, do not reread the corpus (do NOT open `materials/03-corpus.txt`), and do not repeat the initial-session sleep/checkpoint:

1. Create `api.txt` in the worktree with exactly `HPM09_API_V1` plus a newline.
2. Create `resumed.txt` in the worktree with exactly `HPM09_RESUMED_OK` plus a newline (this is proof of reading this handoff; the exact token `HPM09_RESUMED_OK` must appear).
3. Assert exact contents, e.g. `test "$(cat api.txt)" = "HPM09_API_V1"` and `test "$(cat resumed.txt)" = "HPM09_RESUMED_OK"`, and confirm byte-exactness if convenient (each file is the token plus one `\n`).
4. Stage and commit ONLY `api.txt` and `resumed.txt` (e.g. `git add api.txt resumed.txt` then commit). Do not stage `seed-note.txt`; do not use `git add .`/`git add -A`.
5. Verify afterwards: `git status --porcelain=v1 -b` still shows `?? seed-note.txt`; `git log --oneline` shows the new commit; capture the full commit SHA via `git rev-parse HEAD`.
6. Publish the delivered result JSON at `/tmp/opencode/hpm09-migration-lawoi34f/opencode/.git/herdr-plan-manager/workers/w09-opencode-a/result.json` (write a temp sibling, then rename over it). Required shape per the contract: `ticket_id` `A`, `worker_id` `w09-opencode-a`, `status` `delivered`, summary, an acceptance entry for every ticket criterion, a non-empty verification list with the exact commands/exit codes actually observed, `head` = the new full commit SHA, `artifacts` = the produced files (worktree-relative `api.txt`, `resumed.txt`), and `remaining` explicitly reporting that `seed-note.txt` was left uncommitted on purpose plus the unread corpus remainder if relevant.
7. Stop business writes after publishing; only correct the result if the lifecycle tool requests it.

Acceptance reminders for the continuation session: deliver only after `api.txt` and `resumed.txt` are committed with exact contents, `seed-note.txt` is still uncommitted, and `result.json` reflects the actual HEAD and evidence.

## Suggested skills

- None required for the continuation. Follow ticket A / the spec plus this handoff and the worker contract directly.
