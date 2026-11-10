# Timeline

Accepted: 2026-11-11.

## Context

Accepted changes keep restore data for a while (ADR 0026), bounded by the
project's history policy, so older ones can no longer be reverted. Users asked
to "see changes through time" from the editor's bottom bar. Godot has no git
view built in (its VCS plugin is a separate download), and its **History** dock
is the session's undo history, a different thing. Joinery projects already
start with git LFS rules (`joinery.vcs`).

## Decision

Each accepted Journeyman change, and each revert, becomes one git commit in
the project's own repository, and a **Timeline** bottom-panel tab reads that
history through `joinery timeline list|show|restore` (`joinery.timeline`,
plain `git` subprocesses, no new dependency).

- **Only the change's files.** `FileTransaction.accept` and `revert` commit
  exactly the paths they wrote (`git commit --only`), including Joinery's
  carried game record, so the user's other edits and staged work stay as
  they were. Hooks and signing are skipped for these commits so they never
  prompt. A failed commit never fails the change; the sidebar notice says why.
- **The user is the author.** Author and committer are the user's git
  identity, or `Joinery user <joinery-user@localhost>` when there is none.
  Identity variables in the environment are replaced, so an agent process
  can't lend its own. Messages are the request summary plus a plain
  "Accepted in Joinery" or "Reverted in Joinery" line, no trailers.
- **Git only where asked.** `joinery new`, and the wizard's `game apply` and
  `setup` steps on a folder with no work in it yet, run `git init` and commit
  "Start <name>"; the next creation steps (`brief`, `bible`) fold into that
  first commit while it is newest and a few minutes old. An existing project
  gets git only from the Timeline tab's **Start keeping a timeline**. A
  project inside another repository is never committed to. Missing git is a
  plain message, not an error.
- **Ignored.** `.gitignore` gains `.godot/` and `.joinery/*`, except the game
  record and the art approval, so restore data, caches and attachments stay
  out of history.
- **Restore is a reviewed change.** Restoring a file stages its old bytes as
  a normal transaction that waits in the Review tab; history is never reset
  or rewritten. The agent hook denies `joinery timeline restore` and `start`
  for every agent; they are the user's clicks.
- **Live scene edits** are not files until the scene is saved, so the tab
  commits them on the editor's `scene_saved` with their summaries.
- **Switchable.** `[timeline] auto_commit` in joinery.toml (default true),
  shown as **Timeline** in Journeyman settings.

## Consequences

- History outlives the restore-data window and travels with the project.
- Restoring an old version is as safe as any other change: review first.
- Projects nested in a bigger repository, and users without git, keep
  working exactly as before, without a timeline.
- The addon runs on stock Godot: the tab uses only bottom-panel APIs.
