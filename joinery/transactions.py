"""File transactions: isolated staging, conflict detection and crash recovery."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, BinaryIO

from joinery.platform_support import lock_file, unlock_file

EXCLUDED = {".git", ".godot", ".joinery", "__pycache__", ".pytest_cache", ".venv"}
DIRECTORY = "directory"


class ProjectLease:
    """One authoring host owns recovery/review for a project at a time."""

    def __init__(self, project: Path) -> None:
        path = project / ".joinery/session.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.file: BinaryIO = path.open("a+b")
        try:
            lock_file(self.file)
        except BlockingIOError:
            self.file.close()
            raise ValueError(
                "This project already has an active Joinery chat connection. Close it before reconnecting from another editor."
            ) from None

    def close(self) -> None:
        if not self.file.closed:
            unlock_file(self.file)
            self.file.close()


def inventory(root: Path, *, include_dirs: bool = False) -> dict[str, str]:
    found = {}
    for directory, children, files in os.walk(root, followlinks=False):
        children[:] = [name for name in children if name not in EXCLUDED]
        parent = Path(directory)
        if include_dirs and parent != root:
            found[parent.relative_to(root).as_posix()] = DIRECTORY
        for name in [*children, *files]:
            if (parent / name).is_symlink():
                raise ValueError("Project transactions do not follow symbolic links.")
        for name in files:
            path = parent / name
            found[path.relative_to(root).as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    return found


def _copy(source: Path, target: Path) -> None:
    # Validate before copying so a symlink cannot import files outside the project.
    inventory(source)
    shutil.copytree(
        source, target, ignore=shutil.ignore_patterns(*EXCLUDED), dirs_exist_ok=True
    )


def atomic_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as file:
            json.dump(data, file, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class FileTransaction:
    """No writes to working sources until explicit accept. No git reset needed."""

    def __init__(self, project: Path, identifier: str | None = None) -> None:
        self.project = project.resolve()
        if not (self.project / "project.godot").is_file():
            raise ValueError("Expected a Godot project.")
        self.id = identifier or uuid.uuid4().hex
        if len(self.id) != 32 or any(ch not in "0123456789abcdef" for ch in self.id):
            raise ValueError("Invalid transaction identity.")
        self.directory = self.project / ".joinery/transactions" / self.id
        self.original = self.directory / "original"
        self.stage = self.directory / "stage"
        self.journal = self.directory / "journal.json"
        self.state = "new"
        self.base: dict[str, str] = {}
        self.summary = ""
        self.include_dirs = True

    def begin(self, summary: str) -> None:
        if self.directory.exists():
            raise ValueError("Transaction already exists.")
        self.base = inventory(self.project, include_dirs=self.include_dirs)
        self.directory.mkdir(parents=True, mode=0o700)
        try:
            _copy(self.project, self.original)
            _copy(self.original, self.stage)
            self.summary = summary
            self.state = "staging"
            self._record()
        except BaseException:
            shutil.rmtree(self.directory)
            raise

    def _record(self, **extra: Any) -> None:
        atomic_json(
            self.journal,
            {
                "version": 1,
                "id": self.id,
                "state": self.state,
                "summary": self.summary,
                "base": self.base,
                "include_dirs": self.include_dirs,
                **extra,
            },
        )

    def diff(self) -> list[dict[str, str]]:
        after = inventory(self.stage, include_dirs=self.include_dirs)
        return [
            {
                "path": path,
                "before": self.base.get(path, ""),
                "after": after.get(path, ""),
                "change": "created"
                if path not in self.base
                else "deleted"
                if path not in after
                else "modified",
            }
            for path in sorted(self.base.keys() | after.keys())
            if self.base.get(path) != after.get(path)
        ]

    def review(self) -> list[dict[str, str]]:
        if self.state != "staging":
            raise ValueError("Transaction is not staging.")
        changes = self.diff()
        if not changes:
            raise ValueError("The tool produced no project changes.")
        self.state = "review"
        self._record(changes=changes)
        return changes

    def accept(self) -> list[dict[str, str]]:
        if self.state != "review":
            raise ValueError("Review the transaction before accepting it.")
        changes = self.diff()
        reviewed = json.loads(self.journal.read_text()).get("changes")
        if changes != reviewed:
            raise ValueError(
                "Staged files changed after review. Review the updated transaction before accepting."
            )
        current = inventory(self.project, include_dirs=self.include_dirs)
        for change in changes:
            if (
                change["before"]
                and change["after"]
                and (DIRECTORY in {change["before"], change["after"]})
            ):
                raise ValueError(
                    "Replace files and directories in separate reviewed transactions."
                )
            if current.get(change["path"], "") != change["before"]:
                raise ValueError(
                    f"{change['path']} changed after the snapshot. The working file was preserved."
                )
        # A durable intent journal precedes the first working-tree mutation.
        self.state = "committing"
        self._record(changes=changes)
        applied = []
        try:
            ordered = sorted(
                changes,
                key=lambda change: (
                    0
                    if change["after"] == DIRECTORY
                    else 2
                    if change["before"] == DIRECTORY
                    else 1,
                    -len(Path(change["path"]).parts)
                    if change["before"] == DIRECTORY
                    else len(Path(change["path"]).parts),
                ),
            )
            for change in ordered:
                path = change["path"]
                target = self.project / path
                if change["after"] == DIRECTORY:
                    target.mkdir(parents=True, exist_ok=True)
                elif change["before"] == DIRECTORY:
                    target.rmdir()
                elif change["after"]:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    descriptor, temp = tempfile.mkstemp(
                        prefix=".joinery-", dir=target.parent
                    )
                    os.close(descriptor)
                    try:
                        shutil.copy2(self.stage / path, temp)
                        os.replace(temp, target)
                    finally:
                        if os.path.exists(temp):
                            os.unlink(temp)
                else:
                    target.unlink()
                applied.append(change)
            self.state = "accepted"
            self._record(changes=changes)
        except BaseException:
            self._restore(applied)
            self.state = "failed"
            self._record(changes=changes)
            raise
        return changes

    def _restore(self, changes: list[dict[str, str]]) -> None:
        removing = {change["path"] for change in changes if not change["before"]}
        # Refuse before restoring any file if a newly created folder now contains
        # unrelated work. A late rmdir failure would otherwise leave a partial revert.
        current = inventory(self.project, include_dirs=self.include_dirs)
        for change in changes:
            if (
                change["after"] == DIRECTORY
                and not change["before"]
                and any(
                    path.startswith(change["path"] + "/") and path not in removing
                    for path in current
                )
            ):
                raise ValueError(
                    "Cannot restore a folder containing newer manual edits."
                )
        ordered = sorted(
            changes,
            key=lambda change: (
                0
                if change["before"] == DIRECTORY
                else 2
                if change["after"] == DIRECTORY
                else 1,
                -len(Path(change["path"]).parts)
                if change["after"] == DIRECTORY
                else len(Path(change["path"]).parts),
            ),
        )
        for change in ordered:
            target = self.project / change["path"]
            if change["before"] == DIRECTORY:
                target.mkdir(parents=True, exist_ok=True)
            elif change["after"] == DIRECTORY:
                if target.is_dir():
                    target.rmdir()
            elif change["before"]:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(self.original / change["path"], target)
            elif target.exists():
                target.unlink()

    def reject(self) -> None:
        if self.state not in {"staging", "review"}:
            raise ValueError("This transaction is not pending.")
        self.state = "rejected"
        self._record()
        shutil.rmtree(self.stage, ignore_errors=True)

    def revert(self) -> None:
        if self.state != "accepted":
            raise ValueError("Only an accepted transaction can be reverted.")
        changes = self.diff()
        current = inventory(self.project, include_dirs=self.include_dirs)
        for change in changes:
            if current.get(change["path"], "") != change["after"]:
                raise ValueError("Cannot revert over newer manual edits.")
        self._restore(changes)
        self.state = "reverted"
        self._record(changes=changes)

    @classmethod
    def load(cls, project: Path, identifier: str) -> FileTransaction:
        transaction = cls(project, identifier)
        raw = json.loads(transaction.journal.read_text())
        if raw.get("version") != 1 or raw.get("id") != identifier:
            raise ValueError("Invalid transaction journal.")
        transaction.base = raw["base"]
        transaction.state = raw["state"]
        transaction.summary = raw["summary"]
        transaction.include_dirs = raw.get("include_dirs", False)
        # Never accept journal paths outside this project, even after local tampering.
        for path in transaction.base:
            if Path(path).is_absolute() or ".." in Path(path).parts:
                raise ValueError("Unsafe journal path.")
        return transaction

    def recover(self) -> None:
        if self.state != "committing":
            return
        changes = self.diff()
        current = inventory(self.project, include_dirs=self.include_dirs)
        applied = []
        for change in changes:
            actual = current.get(change["path"], "")
            if actual == change["after"]:
                applied.append(change)
            elif actual != change["before"]:
                raise ValueError(
                    "Recovery found a newer manual edit; automatic restore was stopped."
                )
        self._restore(applied)
        self.state = "recovered"
        self._record(changes=changes)
