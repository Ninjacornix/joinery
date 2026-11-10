# 0026: Change history storage

Status: accepted, 2026-11-10.

## Context
Every request runs as a file transaction (snapshot, review, accept or reject). The
first version copied the whole project twice per request, an `original` and a
`stage`, and kept both forever. Recipe preimages (`recipes/inputs/<uuid>.blend`)
were full copies per edit, and nothing was ever pruned, so a project's history grew
by two project-sized copies per change.

## Decision
- **Cloned stage.** `begin()` makes one stage tree with copy-on-write clones:
  `clonefile(2)` on macOS (APFS), the `FICLONE` ioctl on Linux, and a plain
  `shutil.copy2` anywhere cloning fails, Windows included. `JOINERY_NO_CLONE=1`
  turns cloning off. The base inventory is taken from the stage, so a hand edit to
  the project during staging is caught on accept, not reviewed as the tool's change.
- **Originals hold only changed files.** `review()` saves just the files the change
  replaces or deletes, after checking each one's hash; `accept()` checks again
  before its first write.
- **The stage goes after the decision.** It is deleted after accept, reject, revert
  and recovery. Revert and recovery work from the journal's recorded changes plus
  the saved originals, and check their hashes before restoring. Journals are
  version 2 and still load version 1.
- **Retention.** `joinery.toml` `[history]` sets `keep` (default 50 accepted
  changes) and `max_mb` (default 2048). Pruning runs under a lock after each
  decision and never fails the decision. A revert holds the same lock and re-reads
  its journal under it, so pruning or Compact never removes restore data mid-revert
  and a stale copy cannot revert twice. The newest accepted change always stays
  revertible; older ones keep their journal marked `revertible: false`, and
  `joinery transactions` lists that flag.
- **Content-addressed preimages.** `recipe.store_preimage()` names each preimage by
  its sha256, so identical ones are stored once.
- **Commands.** `joinery history usage [--json]` reports the bytes kept
  (`total_bytes` and its parts). `joinery history compact` makes the current sources
  the new base: it renames preimages still in use to their hash, rewrites whole
  references in current recipes, then drops superseded recipes, unused preimages
  and all restore data. It refuses while a change waits for review or a recipe
  cannot be read, and each step leaves replay working if interrupted. The
  Journeyman settings page shows History with its size and a Compact button.

## Consequences
- Disk use per change is roughly the size of the files it changed, not the project.
- Byte counts are file sizes as listed, so a cloned file counts in full even when it
  shares blocks; the settings page can show more than is really used.
- After Compact, older changes can no longer be reverted; their cards say so.
- Compact edits the working project directly, not as a reviewed transaction,
  because a transaction would itself keep copies of what it deletes.

## Addendum, 2026-11-11: choosing the policy
- **Where.** The new-project wizard (Features and apps: **Keep older versions**),
  Project apps (`/setup`, **Older versions**, saved with Save) and Settings
  (**Older versions** under History, next to Compact). Presets: last 50 changes up
  to 2 GB (`keep-50`, the default), last 10 up to 1 GB (`keep-10`), only the newest
  (`latest`, keep 1), or Custom numbers (Project apps and Settings).
- **CLI.** `joinery history policy [--keep N] [--max-mb M] [--dry-run] [--json]`
  reads or writes `[history]`, replacing only that table in `joinery.toml`, then
  runs retention at once and reports `pruned` and `freed_bytes`; `--dry-run`
  reports the same without changing anything. `joinery new`, `game apply` and
  `setup` take `--history PRESET` or `--history-keep/--history-max-mb`. The
  wizard passes its choice to `game apply`, or to `setup` without a starting point.
- **Asking first.** Settings applies a choice that removes nothing at once; one that
  removes restore data says how many changes and how much space, and applies only
  on **Keep fewer**. Project apps says the same under the choice before Save.
- **What it covers.** The policy bounds transaction restore data: the older
  versions of models, textures and sounds that accepted changes replaced.
  `recipes/inputs` documents that current recipes replay from cannot be dropped
  without breaking `joinery rebuild`, and `recipes/history` holds replaced recipes
  (small text); both stay until Compact, which removes replaced recipes and the
  starting documents no current recipe needs. The newest accepted change always
  stays revertible, whatever the policy.
