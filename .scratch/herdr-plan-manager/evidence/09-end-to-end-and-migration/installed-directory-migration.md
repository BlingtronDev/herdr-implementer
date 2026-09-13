# Installed-directory migration (historical)

This note preserves the migration-time installation guidance formerly in the root README. Its observations describe the 2026-09-13 development snapshot, not the current checkout.

The product was renamed from `herdr-ticket-dispatcher` to `herdr-plan-manager`. At acceptance, the verified installation was `~/.agents/skills/herdr-plan-manager`; a fresh OpenCode process discovered the new entry, and the main checkout held the pre-migration baseline with the implementation incorporated without a project commit. See [Final acceptance](final-acceptance.md) for the recorded SHAs and evidence.

The migration procedure was to relocate the skill directory, retain exactly one root `SKILL.md`, start a fresh session to check discovery, and update per-skill links or clone remotes when applicable. Existing run evidence, management records, branches, and worktrees were preserved independently of the skill rename.

The migration-time smoke driver and its recovery phases remain in [DRIVER.md](DRIVER.md) and `smoke.py` beside the captured evidence. They reproduce that development fixture; current runtime validation uses the self-contained [validation procedure](../../../../docs/plan-management/validation.md) and writes fresh results in the validation project.
