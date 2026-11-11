"""Which running task is changing which project file, and the shared worker slots.

Several agents can work on one project at once (ADR 0036). Each works in its own
staged change, so nothing is overwritten on disk, but two changes to the same file
could never both be accepted. So a task claims each file it stages, and a write
to a file another task has claimed is refused with a plain tool error. Claims last
until the user decides the change (or it is dropped).

``joinery.lock`` is shared bookkeeping, not anyone's work: it is never claimed,
and :mod:`joinery.rebase` merges it instead. Folders are never claimed either, nor
the empty ``.gdignore`` markers that every change making a source folder writes.

Variants of one request (ADR 0042) are alternatives, not rivals: tasks in one
*variant group* may claim the same files, since at most one of them is ever
accepted. Against every other task their claims are as exclusive as any.

Game features are one claim for the whole project (:data:`FEATURES`): a change
that adds or removes a feature rewrites the carried game manifest, so only one
change at a time may. ``joinery feature add|remove`` takes the same claim from
its own process (:class:`FeatureLock`).
"""

from __future__ import annotations

import asyncio
import contextlib
import tomllib
from collections.abc import AsyncIterator, Callable, Iterable
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

from joinery import tools as tool_modules
from joinery.platform_support import lock_file, unlock_file

#: The task id of the chat's own request (the lead).
MAIN = "main"
MAIN_TITLE = "the main request"
#: Files every change may touch; merged on accept rather than claimed.
UNCLAIMED = frozenset({"joinery.lock"})
#: Godot's "don't import this folder" marker: empty, the same in every change.
MARKER = ".gdignore"
#: Concurrent turns per project, the chat's own included, unless joinery.toml says.
DEFAULT_PARALLEL = 3
MAX_PARALLEL = 6
#: Apps whose document operators can't run side by side in one worker image.
SERIAL_TOOLS = frozenset({"blender", "krita", "material-maker"})
#: The game manifest a feature change rewrites: one claim for every game feature.
FEATURES = ".joinery/game-manifest.json"
_FEATURE_LOCK = ".joinery/features.lock"
_FEATURE_HOLDER = ".joinery/features.holder"


def refusal(path: str, title: str) -> str:
    if path == FEATURES:
        return features_refusal(title)
    return f"{path} is being changed by another task: {title}. Work on something else or wait."


def live_refusal(title: str) -> str:
    return (
        f"The scene on screen is being changed live by another task: {title}. "
        "Change a scene that isn't open, or wait until that change is decided."
    )


#: A task asked to change the scene on screen while the main request works.
LEAD_BUSY = (
    "The main request is working, so it keeps the scene on screen. Change a scene "
    "that isn't open, or leave this to the main request."
)


def features_refusal(title: str) -> str:
    return (
        f"Game features are being changed by another task: {title}. "
        "Wait until that change is decided, then try again."
    )


class FeatureLock:
    """The game-features claim across processes: the chat host's agents and
    ``joinery feature add|remove`` take it in turn. An OS lock, so a crashed
    holder never keeps it."""

    def __init__(self, project: Path) -> None:
        self.project = project
        self._file: BinaryIO | None = None

    def take(self, title: str) -> str:
        """Hold the claim for ``title``; returns the holder's title when it is taken."""
        if self._file is not None:
            return ""
        path = self.project / _FEATURE_LOCK
        path.parent.mkdir(parents=True, exist_ok=True)
        stream = path.open("a+b")
        try:
            lock_file(stream)
        except (BlockingIOError, PermissionError, OSError):
            stream.close()
            return self.holder(self.project) or "another change"
        self._file = stream
        with contextlib.suppress(OSError):
            (self.project / _FEATURE_HOLDER).write_text(title, encoding="utf-8")
        return ""

    def release(self) -> None:
        stream, self._file = self._file, None
        if stream is None:
            return
        with contextlib.suppress(OSError):
            (self.project / _FEATURE_HOLDER).unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            unlock_file(stream)
        stream.close()

    @staticmethod
    def holder(project: Path) -> str:
        """Who holds the claim now ("" when no one does)."""
        path = project / _FEATURE_LOCK
        if not path.is_file():
            return ""
        try:
            with path.open("a+b") as stream:
                try:
                    lock_file(stream)
                except (BlockingIOError, PermissionError):
                    pass
                else:
                    unlock_file(stream)
                    return ""
        except OSError:
            return ""
        try:
            text = (project / _FEATURE_HOLDER).read_text(encoding="utf-8").strip()
        except OSError:
            text = ""
        return text[:120] or "another change"


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

    def __init__(self, project: Path | None = None) -> None:
        #: The project, for the game-features claim other processes see.
        self.project = project
        self._features: FeatureLock | None = None
        #: Each task working now: {task id: title}.
        self.titles: dict[str, str] = {}
        #: Claimed file -> task id.
        self.owners: dict[str, str] = {}
        #: Claimed file -> the other tasks of the owner's variant group holding it too.
        self.sharers: dict[str, set[str]] = {}
        #: Task id -> its variant group, for tasks that are variants of one request.
        self.groups: dict[str, str] = {}
        #: Bumped on every accepted change, so a stage knows the project moved.
        self.generation = 0
        #: The one task changing the scene on screen live ("" for no one); held
        #: from its first live edit until the user decides the preview.
        self.live = ""

    def register(self, task: str, title: str) -> None:
        self.titles[task] = title

    def unregister(self, task: str) -> None:
        self.release(task)
        self.titles.pop(task, None)
        self.groups.pop(task, None)

    def join(self, task: str, group: str) -> None:
        """``task`` is one variant of ``group``: it shares files with the others."""
        self.groups[task] = group

    def leave(self, task: str) -> None:
        """``task`` is no longer a variant: its claims are exclusive again."""
        self.groups.pop(task, None)

    def alternatives(self, one: str, other: str) -> bool:
        """``one`` and ``other`` are variants of the same request."""
        group = self.groups.get(one, "")
        return bool(group) and group == self.groups.get(other, "")

    def contended(self) -> bool:
        """More than one task works now, so staged files must be claimed."""
        return len(self.titles) > 1

    def holder(self, path: str, task: str) -> str:
        """The title of the other task holding ``path`` ("" when free or ours)."""
        owner = self.owners.get(path, "")
        if not owner or owner == task or self.alternatives(owner, task):
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
            if path in UNCLAIMED or PurePosixPath(path).name == MARKER:
                continue
            title = self.holder(path, task)
            owner = self.owners.get(path, "")
            if title:
                refused[path] = title
            elif owner and owner != task:
                # Another variant of the same request holds it: both may.
                self.sharers.setdefault(path, set()).add(task)
            else:
                self.owners[path] = task
        return refused

    def held(self, task: str) -> list[str]:
        return sorted(
            p
            for p, owner in self.owners.items()
            if owner == task or task in self.sharers.get(p, ())
        )

    def release(self, task: str) -> None:
        for path in self.held(task):
            sharers = self.sharers.get(path, set())
            if self.owners.get(path) == task and sharers:
                # The file stays claimed by the variants still holding it.
                self.owners[path] = sharers.pop()
            elif self.owners.get(path) == task:
                del self.owners[path]
            else:
                sharers.discard(task)
            if not sharers:
                self.sharers.pop(path, None)
        if self.features_holder() == "" and self._features is not None:
            self._features.release()
            self._features = None

    def claim_live(self, task: str) -> str:
        """Make ``task`` the scene's live owner; the holder's title if another is."""
        if self.live in {"", task}:
            self.live = task
            return ""
        return self.titles.get(self.live, "another task")

    def release_live(self) -> str:
        """The live preview was decided or dropped; returns who held the scene."""
        owner, self.live = self.live, ""
        return owner

    def features_holder(self) -> str:
        """The task holding the game-features claim in this process ("" for none)."""
        return self.owners.get(FEATURES, "")

    def claim_features(self, task: str) -> str:
        """Take the game-features claim for ``task``.

        Returns the holder's title when another task, or ``joinery feature`` in
        another process, has it; "" once ``task`` holds it.
        """
        owner = self.features_holder()
        if owner == task:
            return ""
        if owner:
            return self.titles.get(owner, "another task")
        if self.project is not None:
            lock = FeatureLock(self.project)
            held_by = lock.take(self.titles.get(task, "another task"))
            if held_by:
                return held_by
            self._features = lock
        self.owners[FEATURES] = task
        return ""


class WorkerSlots:
    """One document operator at a time per worker image for apps that can't share one.

    Blender, Krita and Material Maker keep a document open in the worker while an
    operator runs; two tasks driving them at once would fight over it, so their
    calls queue. An operation that runs several apps (a Krita paint loaded into a
    Blender model) holds every slot it needs for the whole call, taken in one
    global order so two such operations never wait on each other.
    """

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        #: The apps the call holding each slot runs.
        self._holders: dict[str, tuple[str, ...]] = {}

    @staticmethod
    def key(tool: str) -> str:
        if tool not in SERIAL_TOOLS:
            return ""
        module = tool_modules.REGISTRY.get(tool)
        return (module.worker_image if module is not None else "") or tool

    @staticmethod
    def app_name(tool: str) -> str:
        module = tool_modules.REGISTRY.get(tool)
        return (
            module.name if module is not None else tool.replace("-", " ").capitalize()
        )

    @asynccontextmanager
    async def hold(
        self,
        tools: str | Iterable[str],
        waiting: Callable[[str], None] = lambda _app: None,
    ) -> AsyncIterator[None]:
        """Hold the slot of every app in `tools`; `waiting` names the app waited for."""
        wanted: dict[str, list[str]] = {}
        for tool in sorted({tools} if isinstance(tools, str) else set(tools)):
            key = self.key(tool)
            if key:
                wanted.setdefault(key, []).append(tool)
        async with AsyncExitStack() as stack:
            for key in sorted(wanted):
                apps = wanted[key]
                lock = self._locks.setdefault(key, asyncio.Lock())
                if lock.locked():
                    held = self._holders.get(key, ())
                    waiting(
                        self.app_name(next((a for a in apps if a in held), apps[0]))
                    )
                await stack.enter_async_context(lock)
                self._holders[key] = tuple(apps)
                stack.callback(self._holders.pop, key, None)
            yield
