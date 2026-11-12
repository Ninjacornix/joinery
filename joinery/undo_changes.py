"""Undo several accepted changes as one reviewed change (ADR 0080).

Rewinding a chat leaves the project alone. When the user also asks to undo the
changes made after the message they rewound to, this stages one ordinary
Joinery change that puts every file those changes touched back as it was
before the first of them. Nothing in the project changes until the user accepts
it in Review, like a Timeline restore, and the history is never rewritten: the
later changes stay in the Timeline, followed by this one.

It uses each change's restore data. A change too old to have it, or a file that
something else changed since, is refused with words that say where to go
instead (the Timeline tab puts single files back from git).
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from joinery.transactions import DIRECTORY, FileTransaction, inventory

TOO_OLD = (
    '"{summary}" is too old to undo here. The Timeline tab can still put its '
    "files back one by one."
)
CHANGED_SINCE = (
    "{path} changed after those changes. Undo it by hand, or put it back from the "
    "Timeline tab."
)


def _accepted(project: Path, ids: list[str]) -> list[FileTransaction]:
    found: list[FileTransaction] = []
    for identifier in dict.fromkeys(ids):
        try:
            tx = FileTransaction.load(project, identifier)
        except (OSError, ValueError):
            continue
        if tx.state == "accepted":
            found.append(tx)
    return sorted(found, key=lambda tx: (tx.decided, tx.created))


def plan(project: Path, ids: list[str]) -> dict[str, tuple[FileTransaction, str]]:
    """For each file the accepted changes in ``ids`` touched: the change that first
    touched it and the file's hash before ("" for a file it created). Raises
    ValueError when they can't be undone together."""
    project = project.resolve()
    changes = _accepted(project, ids)
    if not changes:
        raise ValueError("None of those changes is still accepted; nothing to undo.")
    first: dict[str, tuple[FileTransaction, str]] = {}
    last: dict[str, str] = {}
    for tx in changes:
        if not tx.revertible or not tx.snapshot.is_dir():
            raise ValueError(TOO_OLD.format(summary=tx.summary or "A change"))
        for change in [*tx.changes, *tx.carried]:
            path = change["path"]
            first.setdefault(path, (tx, change["before"]))
            last[path] = change["after"]
    current = inventory(project, include_dirs=True)
    for path, after in last.items():
        if current.get(path, "") != after:
            raise ValueError(CHANGED_SINCE.format(path=path))
    return first


def stage_undo(project: Path, ids: list[str], summary: str = "") -> dict[str, Any]:
    """Stage the reviewed change that undoes the accepted changes in ``ids``.

    Returns {transaction_id, summary, state, changes, undone}."""
    from joinery import game_setup  # Late: game_setup imports transactions.

    project = project.resolve()
    files = plan(project, ids)
    undone = len({tx.id for tx, _ in files.values()})
    title = summary or (
        f"Undo the {undone} changes made after an earlier message"
        if undone > 1
        else "Undo the change made after an earlier message"
    )

    def edit(stage: Path) -> None:
        made: list[Path] = []
        for path, (tx, before) in sorted(files.items()):
            target = stage / path
            if before == DIRECTORY:
                target.mkdir(parents=True, exist_ok=True)
            elif before:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(tx.snapshot / path, target)
            elif target.is_dir():
                made.append(target)
            else:
                target.unlink(missing_ok=True)
        # Folders the changes made go too, deepest first, once they are empty.
        for folder in sorted(made, key=lambda p: -len(p.parts)):
            if folder.is_dir() and not any(folder.iterdir()):
                folder.rmdir()

    try:
        tx = game_setup.stage_change(project, title, edit)
    except ValueError as exc:
        if "no project changes" in str(exc):
            raise ValueError("The files are already as they were then.") from None
        raise
    return {
        "transaction_id": tx.id,
        "summary": tx.summary,
        "state": tx.state,
        "changes": tx.changes,
        "undone": undone,
    }
