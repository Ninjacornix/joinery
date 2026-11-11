"""What a staged change wrote since the last look, without hashing the whole stage.

While several agents work on one project (ADR 0036) each one's staged files are
claimed after every tool call. Hashing the whole stage each time grows with the
project, so a :class:`StageWatch` keeps a stat snapshot instead: every folder's
mtime and every file's size and mtime. After a call it looks only at the files the
call named and at the folders whose listing changed (a file made, removed or
replaced by a rename, which is how most apps save). Only those files are hashed.
A file rewritten in place that no call named is found by the full hash at submit.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

from joinery.transactions import EXCLUDED


class StageWatch:
    """A stat snapshot of one transaction's stage."""

    def __init__(self, root: Path, transaction_id: str) -> None:
        self.root = root
        self.id = transaction_id
        #: Folder ("" for the stage itself) -> mtime in ns.
        self.folders: dict[str, int] = {}
        #: File -> (size, mtime in ns).
        self.files: dict[str, tuple[int, int]] = {}
        self._walk("", set())

    def changes(self, named: Iterable[str] = ()) -> set[str]:
        """Files made, removed or rewritten since the last look; the snapshot moves on.

        ``named`` are project-relative paths a tool call named: a file is compared
        by its size and mtime, a folder has its listing read again.
        """
        found: set[str] = set()
        moved = [
            folder
            for folder, stamp in list(self.folders.items())
            if self._stamp(folder) != stamp
        ]
        for path in named:
            if path in self.folders:
                moved.append(path)
            elif path not in found:
                self._compare(path, found)
        for folder in moved:
            if folder in self.folders:
                self._relist(folder, found)
        return found

    # --- Looking ----------------------------------------------------------------------

    def _path(self, relative: str) -> Path:
        return self.root / relative if relative else self.root

    def _stamp(self, folder: str) -> int:
        try:
            return self._path(folder).stat().st_mtime_ns
        except OSError:
            return -1

    def _compare(self, path: str, found: set[str]) -> None:
        """Note ``path`` when it differs from the snapshot."""
        target = self._path(path)
        try:
            if target.is_symlink() or not target.is_file():
                if self.files.pop(path, None) is not None:
                    found.add(path)
                return
            stat = target.stat()
        except OSError:
            return
        now = (stat.st_size, stat.st_mtime_ns)
        if self.files.get(path) != now:
            self.files[path] = now
            found.add(path)

    def _relist(self, folder: str, found: set[str]) -> None:
        """Read ``folder``'s listing again: new, removed and rewritten files in it."""
        target = self._path(folder)
        stamp = self._stamp(folder)
        if stamp < 0:
            self._forget(folder, found)
            return
        self.folders[folder] = stamp
        prefix = folder + "/" if folder else ""
        seen: set[str] = set()
        try:
            entries = list(os.scandir(target))
        except OSError:
            return
        for entry in entries:
            name = prefix + entry.name
            if entry.is_symlink():
                continue
            if entry.is_dir():
                if entry.name in EXCLUDED:
                    continue
                seen.add(name)
                if name not in self.folders:
                    self._walk(name, found)
            elif entry.is_file():
                seen.add(name)
                self._compare(name, found)
        for gone in [
            child
            for child in [*self.files, *self.folders]
            if child != folder
            and child.startswith(prefix)
            and "/" not in child[len(prefix) :]
            and child not in seen
        ]:
            if gone in self.folders:
                self._forget(gone, found)
            elif self.files.pop(gone, None) is not None:
                found.add(gone)

    def _forget(self, folder: str, found: set[str]) -> None:
        """``folder`` is gone: every file the snapshot had below it was removed."""
        prefix = folder + "/"
        for path in [p for p in self.files if p.startswith(prefix)]:
            del self.files[path]
            found.add(path)
        for path in [p for p in self.folders if p == folder or p.startswith(prefix)]:
            del self.folders[path]

    def _walk(self, folder: str, found: set[str]) -> None:
        """Record ``folder`` and everything below it; its files are new to ``found``."""
        stack = [folder]
        while stack:
            current = stack.pop()
            target = self._path(current)
            try:
                self.folders[current] = target.stat().st_mtime_ns
                entries = list(os.scandir(target))
            except OSError:
                self.folders.pop(current, None)
                continue
            prefix = current + "/" if current else ""
            for entry in entries:
                name = prefix + entry.name
                if entry.is_symlink():
                    continue
                if entry.is_dir():
                    if entry.name not in EXCLUDED:
                        stack.append(name)
                elif entry.is_file():
                    try:
                        stat = entry.stat()
                    except OSError:
                        continue
                    self.files[name] = (stat.st_size, stat.st_mtime_ns)
                    found.add(name)
