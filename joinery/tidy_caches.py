"""Joinery's own clutter in a project: caches it can make again, and old records.

Not the game and not history: nothing here is reviewed, and the Timeline and the
restore data of changes (``joinery history``) are never touched. Cleared:

* ``.joinery/render_cache``: review renders, made again when a change is reviewed;
* ``.joinery/map``: the project map, rebuilt from the files;
* ``.joinery/parts``: part reports of native sources, read again on demand;
* ``.joinery/kits/previews``: kit preview pictures;
* ``.joinery/previews``: review previews older than a week;
* ``.joinery/results``: long tool results agents read in pages, older than a week;
* ``.joinery/metrics``: change metrics beyond the newest 500 (``joinery.metrics.KEEP``);
* ``.joinery/playtests``: playtest runs beyond the newest 20 (heatmaps read 10 at most).
"""

from __future__ import annotations

import shutil
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from joinery.tidy import CacheFolder

WEEK = 7 * 24 * 3600
#: Playtest runs kept: the heatmaps and the design check read the newest ten.
PLAYTESTS_KEPT = 20


@dataclass(frozen=True)
class Rule:
    path: str
    what: str
    #: Which entries of the folder go: all, older than an age, or beyond the newest N.
    older_than: float = 0.0
    keep_newest: int = 0
    pattern: str = "*"


RULES: tuple[Rule, ...] = (
    Rule(".joinery/render_cache", "review renders"),
    Rule(".joinery/map", "project maps"),
    Rule(".joinery/parts", "part reports"),
    Rule(".joinery/kits/previews", "kit previews"),
    Rule(".joinery/previews", "review previews older than a week", older_than=WEEK),
    Rule(".joinery/results", "tool results older than a week", older_than=WEEK),
    Rule(
        ".joinery/metrics",
        "change metrics beyond the newest 500",
        keep_newest=500,
        pattern="*.jsonl",
    ),
    Rule(
        ".joinery/playtests",
        "playtest runs beyond the newest 20",
        keep_newest=PLAYTESTS_KEPT,
    ),
)


def _bytes(path: Path) -> tuple[int, int]:
    if path.is_symlink():
        return 0, 0
    if path.is_file():
        return path.stat().st_size, 1
    total = files = 0
    for child in path.rglob("*"):
        if child.is_file() and not child.is_symlink():
            total += child.stat().st_size
            files += 1
    return total, files


def _entries(project: Path, rule: Rule, now: float) -> Iterator[Path]:
    folder = project / rule.path
    if not folder.is_dir() or folder.is_symlink():
        return
    children = [c for c in folder.glob(rule.pattern) if not c.is_symlink()]
    if rule.keep_newest:
        children.sort(key=lambda c: c.stat().st_mtime, reverse=True)
        yield from children[rule.keep_newest :]
        return
    for child in children:
        if rule.older_than and now - child.stat().st_mtime < rule.older_than:
            continue
        yield child


def caches(project: Path, now: float | None = None) -> list[CacheFolder]:
    """What clearing would free, per folder (only folders with something to clear)."""
    when = time.time() if now is None else now
    found: list[CacheFolder] = []
    for rule in RULES:
        size = files = 0
        for entry in _entries(project, rule, when):
            more, count = _bytes(entry)
            size += more
            files += count
        if files:
            found.append(
                CacheFolder(path=rule.path, what=rule.what, size=size, files=files)
            )
    return found


def clear(
    project: Path,
    now: float | None = None,
    remove: Callable[[Path], None] | None = None,
) -> list[CacheFolder]:
    """Clear the caches; returns what was cleared, folder by folder."""
    when = time.time() if now is None else now
    cleared: list[CacheFolder] = []
    root = (project / ".joinery").resolve()
    for rule in RULES:
        size = files = 0
        for entry in list(_entries(project, rule, when)):
            if not entry.resolve().is_relative_to(root):
                continue
            more, count = _bytes(entry)
            try:
                if remove is not None:
                    remove(entry)
                elif entry.is_dir():
                    shutil.rmtree(entry)
                else:
                    entry.unlink()
            except OSError:
                continue
            size += more
            files += count
        if files:
            cleared.append(
                CacheFolder(path=rule.path, what=rule.what, size=size, files=files)
            )
    return cleared
