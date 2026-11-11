"""Which running task is changing which project file, and the shared worker slots.

Several agents can work on one project at once (ADR 0036). Each works in its own
staged change, so nothing is overwritten on disk, but two changes to the same file
could never both be accepted. So a task claims each file it stages, and a write
to a file another task has claimed is refused with a plain tool error. Claims last
until the user decides the change (or it is dropped).

``joinery.lock`` is shared bookkeeping, not anyone's work: it is never claimed,
and :mod:`joinery.rebase` merges it instead. Folders are never claimed either.
"""

from __future__ import annotations

import asyncio
import tomllib
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path, PurePosixPath
from typing import Any

from joinery import tools as tool_modules

#: The task id of the chat's own request (the lead).
MAIN = "main"
MAIN_TITLE = "the main request"
#: Files every change may touch; merged on accept rather than claimed.
UNCLAIMED = frozenset({"joinery.lock"})
#: Concurrent turns per project, the chat's own included, unless joinery.toml says.
DEFAULT_PARALLEL = 3
MAX_PARALLEL = 6
#: Apps whose document operators can't run side by side in one worker image.
SERIAL_TOOLS = frozenset({"blender", "krita"})


def refusal(path: str, title: str) -> str:
    return f"{path} is being changed by another task: {title}. Work on something else or wait."


def parallel_limit(project: Path) -> int:
    """``[agents] parallel`` in joinery.toml: turns at once (1 turns tasks off)."""
    try:
        raw = tomllib.loads((project / "joinery.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return DEFAULT_PARALLEL
    agents = raw.get("agents", {})
    value = agents.get("parallel", DEFAULT_PARALLEL) if isinstance(agents, dict) else 0
    if not isinstance(value, int) or isinstance(value, bool):
        return DEFAULT_PARALLEL
    return max(1, min(value, MAX_PARALLEL))


def _relative(text: str, roots: tuple[Path, ...]) -> str:
    value = text.strip().replace("\\", "/")
    if not value or len(value) > 400 or any(c.isspace() for c in value):
        return ""
    if value.startswith("res://"):
        return value.removeprefix("res://").strip("/")
    if value.startswith("/") or (len(value) > 2 and value[1] == ":"):
        for root in roots:
            try:
                return Path(value).relative_to(root).as_posix()
            except ValueError:
                continue
        return ""
    path = PurePosixPath(value.removeprefix("./"))
    if ".." in path.parts or not (path.suffix or len(path.parts) > 1):
        return ""
    return path.as_posix()


def project_paths(arguments: Any, *roots: Path) -> set[str]:
    """Every project-relative path a tool call's arguments seem to name.

    Generous on purpose: the names are only compared with other tasks' claims,
    so a string that only looks like a path costs nothing.
    """
    found: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, str):
            relative = _relative(value, roots)
            if relative:
                found.add(relative)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list | tuple):
            for item in value:
                walk(item)

    walk(arguments)
    return found


class PathClaims:
    """Claims of every task working on one project, shared by their gateways."""

    def __init__(self) -> None:
        #: Each task working now: {task id: title}.
        self.titles: dict[str, str] = {}
        #: Claimed file -> task id.
        self.owners: dict[str, str] = {}
        #: Bumped on every accepted change, so a stage knows the project moved.
        self.generation = 0

    def register(self, task: str, title: str) -> None:
        self.titles[task] = title

    def unregister(self, task: str) -> None:
        self.release(task)
        self.titles.pop(task, None)

    def contended(self) -> bool:
        """More than one task works now, so staged files must be claimed."""
        return len(self.titles) > 1

    def holder(self, path: str, task: str) -> str:
        """The title of the other task holding ``path`` ("" when free or ours)."""
        owner = self.owners.get(path, "")
        if not owner or owner == task:
            return ""
        return self.titles.get(owner, "another task")

    def first_conflict(self, paths: set[str], task: str) -> tuple[str, str]:
        """The first of ``paths`` another task holds, with its title, or ("", "")."""
        for path in sorted(paths):
            title = self.holder(path, task)
            if title:
                return path, title
        return "", ""

    def claim(self, task: str, paths: list[str] | set[str]) -> dict[str, str]:
        """Claim ``paths`` for ``task``; returns the ones refused, with their holders."""
        refused: dict[str, str] = {}
        for path in sorted(paths):
            if path in UNCLAIMED:
                continue
            title = self.holder(path, task)
            if title:
                refused[path] = title
            else:
                self.owners[path] = task
        return refused

    def held(self, task: str) -> list[str]:
        return sorted(p for p, owner in self.owners.items() if owner == task)

    def release(self, task: str) -> None:
        for path in self.held(task):
            del self.owners[path]


class WorkerSlots:
    """One document operator at a time per worker image for apps that can't share one.

    Blender and Krita keep a document open in the worker while an operator runs;
    two tasks driving them at once would fight over it, so their calls queue.
    """

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}

    @staticmethod
    def key(tool: str) -> str:
        if tool not in SERIAL_TOOLS:
            return ""
        module = tool_modules.REGISTRY.get(tool)
        return (module.worker_image if module is not None else "") or tool

    @asynccontextmanager
    async def hold(
        self, tool: str, waiting: Callable[[], None] = lambda: None
    ) -> AsyncIterator[None]:
        key = self.key(tool)
        if not key:
            yield
            return
        lock = self._locks.setdefault(key, asyncio.Lock())
        if lock.locked():
            waiting()
        async with lock:
            yield
