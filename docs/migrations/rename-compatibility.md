# Rename compatibility

Use this reference when upgrading an existing Herdr Plan Manager installation or locating an older execution. The product is now **Herdr Implementer**: it implements confirmed plans and specs through verification and integration, rather than merely maintaining their status.

## New interface

| Surface | Current name | Compatibility |
| --- | --- | --- |
| Skill | `herdr-implementer` | Only the root `SKILL.md` is discoverable; reload skill discovery after upgrading. |
| CLI | `bin/implementer.py` | `bin/plan_manager.py` remains a compatibility CLI with old defaults. |
| Runtime references | `docs/implementation/` | Historical evidence retains its original citations; tests explicitly map renamed references. |
| Default state directory | `<common-dir>/herdr-implementer/` | The compatibility CLI defaults to `<common-dir>/herdr-plan-manager/`. |
| Default new worker branch | `hi/<worker-id>` | The compatibility CLI retains `hpm/<worker-id>`. Existing registered branches are unchanged. |
| State override | `--state-root` | `--management-root` remains an alias on every operation, including supervision. |
| Environment settings | `HI_*` | Corresponding `HPM_*` runtime settings remain fallbacks. An explicitly set `HI_*` value takes precedence, even if empty. |

The on-disk state version remains 3. JSON fields such as `management_root`, `management_dir`, and `paths.management`, the `MANAGEMENT_DIR` template placeholder, and the `manager-error` error code remain unchanged for consumers. These describe protocol/storage details, not product positioning. The old Python module's import API is not preserved; compatibility covers CLI execution.

## Existing executions

1. Keep the old installation path and compatibility script available while any old coordinator, worker, wait command, or supervisor still uses them. A live process can retain absolute script, material, or template paths even after its first read.
2. Finish existing runs with the compatibility CLI, or explicitly select their original state directory on **every** new-CLI command. For example:

   ```bash
   python3 "<skill-dir>/bin/implementer.py" status --repo "<target-repo>" \
     --run "<existing-run-id>" --state-root "<original-absolute-state-directory>"
   ```

3. Use the new defaults for genuinely new executions. The new CLI does not discover, move, merge, or take over old state automatically. An empty new store does not mean old workers have stopped. Enforce the authorized total concurrency across both stores and all related runs.
4. Keep registered worktrees and state where they are. They contain absolute paths and Git worktree registrations; a filesystem rename alone is not a valid migration. Reading retained records is not coordinator recovery or takeover.

After all old executions and referencing processes have finished, rename the **installation directory** to `herdr-implementer`, update installation references, and reload skills. Install only one discoverable copy; an old-directory symlink under a recursively scanned skill root can create duplicate discovery. Renaming the local checkout or skill does not rename a hosted repository. Rename the hosted repository separately before updating its Git remote URL.

Existing `.scratch/` evidence, registered branches, external target repositories, and prior runtime transcripts are historical records, not text-replacement targets. Do not rewrite them to make a naming audit appear clean.
