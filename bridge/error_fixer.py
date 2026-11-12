"""Console errors, fixed as reviewed changes: the chat host's side of ADR 0075.

The sidebar sends what the console printed (``errors_seen``: the editor's Output,
the game's log, GUT's output) and the playtester hands over its report's errors.
:mod:`joinery.console_errors` reads and groups them; this module decides what
happens to each group and keeps the sidebar's Errors list (``{kind: "errors"}``)
up to date.

``[agents] fix_errors = "ask"`` offers each new group once ("Godot reported an
error: … Debug / Always fix errors like this / Dismiss"), and Fix or Fix all on the
list starts fixes. ``"auto"`` starts one at once. Either way a fix is a debug task
(bridge.crew): the debugger finds the cause and a worker stages the fix, which the
user accepts or discards like any other change. Nothing is ever accepted here.

The automatic mode keeps to its limits: ``fix_errors_at_once`` fixes at a time and
never more than the project's free turns, ``fix_errors_cooldown`` seconds between
two starts, nothing for an error whose file a change waiting for review holds, no
fix for an error that showed while such a change was open in that same file (that
change brought it), and after two failed tries a group waits for the user.

Nothing here knows which provider runs: the same host serves every agent.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any, Protocol

from bridge.protocol import Prompt
from joinery import console_errors as ce
from joinery.debugging import DebugRequest

Emit = Callable[[dict[str, Any]], None]
#: Changes waiting for review now: {transaction id: project-relative files}.
Pending = Callable[[], Mapping[str, Iterable[str]]]

GAVE_UP = (
    "Joinery tried to fix this twice and it still happens, so it stopped trying on its "
    "own. Fix tries once more."
)
HELD = "Waits: its file is in a change waiting for review."
CAME_WITH = "Came with a change waiting for review; decide that change first."
FINAL = frozenset({"accepted", "discarded", "done", "failed", "stopped"})


class FixCrew(Protocol):
    """What the fixer needs from bridge.crew.Crew."""

    observers: list[Callable[[Any], None]]

    def capacity(self) -> int: ...

    def working(self) -> list[Any]: ...

    async def debug(
        self,
        request: DebugRequest,
        prompt: Prompt,
        *,
        title: str = "",
        at_once: int = 1,
    ) -> Any: ...


class ErrorFixer:
    """The chat host's error groups, offers and fixes."""

    def __init__(
        self,
        project: Path,
        crew: FixCrew,
        emit: Emit,
        prompt: Callable[[], Prompt],
        *,
        pending: Pending = dict,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
    ) -> None:
        self.project = project
        self.crew = crew
        self._emit = emit
        self._prompt = prompt
        self._pending = pending
        self._clock = clock
        self._wall = wall
        self.book = ce.Book.load(project)
        #: Fix tasks: {task id: group id}, and the state each was last seen in.
        self._tasks: dict[str, str] = {}
        self._states: dict[str, str] = {}
        self._task_of: dict[str, Any] = {}
        self._last_start: float | None = None
        self._offered: set[str] = set()
        self._settings: ce.Settings | None = None
        self._settings_stamp: float = -1.0
        self._pumping = False
        crew.observers.append(self._observe)

    # --- settings -----------------------------------------------------------------------

    def settings(self) -> ce.Settings:
        """joinery.toml's choices, read again whenever the file changed."""
        try:
            stamp = (self.project / "joinery.toml").stat().st_mtime
        except OSError:
            stamp = 0.0
        if self._settings is None or stamp != self._settings_stamp:
            self._settings = ce.settings(self.project)
            self._settings_stamp = stamp
        return self._settings

    async def set_mode(self, mode: str, group_id: str = "") -> None:
        """The Settings switch or "Always fix errors like this" on an offer: save the
        mode, then fix ``group_id`` (the offer's error) and any other open group."""
        ce.set_mode(self.project, mode)
        self._settings = None
        if group_id:
            await self.fix([group_id])
            return
        await self.pump()
        self.publish()

    # --- taking errors ------------------------------------------------------------------

    async def take_text(self, source: str, text: str) -> None:
        """Console text from the sidebar (``errors_seen``)."""
        source = source if source in ce.SOURCES else "output"
        await self.take(ce.parse(text[-200_000:], source))

    async def take_entries(self, entries: Iterable[ce.Entry]) -> None:
        await self.take(list(entries))

    async def take_playtest(self, items: list[dict[str, Any]]) -> None:
        """A playtest report's items: its script and engine errors."""
        await self.take(ce.from_playtest(items))

    async def take(self, entries: list[ce.Entry]) -> None:
        settings = self.settings()
        if settings.mode == "off":
            return
        now = self._wall()
        changed = False
        offer: ce.Group | None = None
        held = self._held()
        for entry in entries:
            if entry.kind == "warning" and not settings.warnings:
                continue
            if ce.ignored(entry, settings.ignore):
                continue
            group, fresh = self.book.add(entry, now)
            changed = True
            fixing = self._task_of.get(group.task_id)
            if group.status == ce.FIXING and fixing is not None:
                # Its change, once ready, says how often the error showed.
                with contextlib.suppress(AttributeError):
                    fixing.review_title = group.fixed_title()
            reopened = self._recurred(group)
            if not (fresh or reopened):
                continue
            change = held.get(ce.where_file(group.where), "")
            if change:
                # It showed while a change to its own file waits for review: that
                # change brought it, and Joinery never fixes its own pending work.
                group.status, group.change, group.note = (
                    ce.FROM_CHANGE,
                    change,
                    CAME_WITH,
                )
                continue
            if group.status == ce.OPEN and group.id not in self._offered:
                offer = group
        if not changed:
            return
        # "auto" starts what is new; either mode starts what the user queued.
        await self.pump()
        if settings.mode == "ask" and offer is not None:
            self._offered.add(offer.id)
        self._save()
        self.publish(offer if settings.mode == "ask" else None)

    def _recurred(self, group: ce.Group) -> bool:
        """An accepted fix's error came back: that fix failed; it is open again."""
        if group.status != ce.FIXED:
            return False
        group.declined = False
        self._failed(group, "The error came back after its fix was accepted.")
        return group.status == ce.OPEN

    # --- fixing -------------------------------------------------------------------------

    async def fix(self, group_ids: Iterable[str] | None = None) -> None:
        """Fix (one row), or Fix all (``None``): the groups wait for a free agent and
        start as turns free up. The user asking again clears a stop or a decline."""
        wanted = (
            [g for g in self.book.groups.values() if g.status in (ce.OPEN, ce.STOPPED)]
            if group_ids is None
            else [self.book.groups[i] for i in group_ids if i in self.book.groups]
        )
        for group in wanted:
            if group.status in (ce.OPEN, ce.STOPPED, ce.IGNORED, ce.FIXED):
                group.status, group.declined, group.note = ce.QUEUED, False, ""
        await self.pump(manual=True)
        self._save()
        self.publish()

    def ignore(self, group_id: str, ignore: bool = True) -> None:
        group = self.book.groups.get(group_id)
        if group is None or group.status in (ce.FIXING, ce.IN_REVIEW):
            return
        group.status, group.note = (ce.IGNORED if ignore else ce.OPEN), ""
        self._save()
        self.publish()

    def adopt(self, task: Any, group_id: str) -> None:
        """Debug on an offer (``debug_start`` with the group): its task fixes the group."""
        group = self.book.groups.get(group_id)
        if group is None:
            return
        self._link(group, task)
        self._save()
        self.publish()

    def running(self) -> int:
        return sum(1 for g in self.book.groups.values() if g.status == ce.FIXING)

    async def pump(self, manual: bool = False) -> None:
        """Start what may start now: queued groups first, then, in "auto", open ones
        no one declined, oldest first, within the limits."""
        if self._pumping:
            return
        self._pumping = True
        try:
            await self._pump(manual)
        finally:
            self._pumping = False

    async def _pump(self, manual: bool) -> None:
        settings = self.settings()
        held = self._held()
        candidates = sorted(
            (
                g
                for g in self.book.groups.values()
                if g.status == ce.QUEUED
                or (settings.mode == "auto" and g.status == ce.OPEN and not g.declined)
            ),
            key=lambda g: (g.status != ce.QUEUED, g.first_seen),
        )
        for group in candidates:
            if self.running() >= settings.at_once or self.crew.capacity() <= 0:
                return
            automatic = group.status == ce.OPEN
            if automatic and not manual and self._cooling(settings):
                return
            if ce.where_file(group.where) in held:
                group.note = HELD
                continue
            await self._start(group, settings)

    def _cooling(self, settings: ce.Settings) -> bool:
        if self._last_start is None:
            return False
        return self._clock() - self._last_start < settings.cooldown

    def cooldown_left(self) -> float:
        """Seconds before the next automatic start may happen."""
        if self._last_start is None:
            return 0.0
        return max(0.0, self.settings().cooldown - (self._clock() - self._last_start))

    async def _start(self, group: ce.Group, settings: ce.Settings) -> None:
        request = DebugRequest(
            error=(group.line or group.message)[:300],
            output=(
                group.context
                + f"\n\nSeen {group.count} time(s), in: "
                + ", ".join(ce.SOURCES.get(s, s) for s in group.counts)
                + (f"\nWhere: {group.where}" if group.where else "")
            )[-12_000:],
        )
        try:
            task = await self.crew.debug(
                request,
                self._prompt(),
                title=group.fixing_title(),
                at_once=settings.at_once,
            )
        except ValueError as exc:
            group.note = str(exc)[:200]
            return
        self._last_start = self._clock()
        self.book.totals["fixes"] += 1
        self._link(group, task)

    def _link(self, group: ce.Group, task: Any) -> None:
        group.status, group.task_id, group.note = ce.FIXING, str(task.id), ""
        group.transaction_id = ""
        with contextlib.suppress(AttributeError):
            task.review_title = group.fixed_title()
        self._tasks[str(task.id)] = group.id
        self._task_of[str(task.id)] = task
        self._states[str(task.id)] = "working"

    # --- following fix tasks ------------------------------------------------------------

    def _observe(self, task: Any) -> None:
        group_id = self._tasks.get(str(task.id))
        if group_id is None:
            return
        state = str(task.state)
        if self._states.get(str(task.id)) == state:
            return
        self._states[str(task.id)] = state
        group = self.book.groups.get(group_id)
        if group is None:
            return
        if state == "in_review":
            group.status, group.transaction_id = ce.IN_REVIEW, str(task.transaction_id)
            self.book.totals["ready"] += 1
        elif state == "accepted":
            group.status, group.note = ce.FIXED, ""
            self.book.totals["accepted"] += 1
        elif state in ("discarded", "stopped"):
            # The user said no: it stays listed, and only the user starts it again.
            group.status, group.declined = ce.OPEN, True
            group.note = (
                "Its fix was discarded." if state == "discarded" else "Stopped."
            )
            if state == "discarded":
                self.book.totals["discarded"] += 1
        elif state in ("failed", "done"):
            reason = str(getattr(task, "message", "") or "The fix didn't finish.")
            self._failed(group, reason[:200])
        else:
            return
        if state in FINAL:
            self._tasks.pop(str(task.id), None)
            self._states.pop(str(task.id), None)
            self._task_of.pop(str(task.id), None)
            group.task_id = "" if state != "accepted" else group.task_id
        self._save()
        self.publish()
        with contextlib.suppress(RuntimeError):
            asyncio.get_running_loop().call_soon(self._repump)

    def _repump(self) -> None:
        with contextlib.suppress(RuntimeError):
            asyncio.get_running_loop().create_task(self._pump_and_publish())

    async def _pump_and_publish(self) -> None:
        before = self.running()
        await self.pump()
        if self.running() != before:
            self._save()
            self.publish()

    def _failed(self, group: ce.Group, reason: str) -> None:
        group.failures += 1
        self.book.totals["failed"] += 1
        group.transaction_id = ""
        if group.failures >= ce.MAX_FAILURES:
            group.status, group.note = ce.STOPPED, GAVE_UP
            self.book.totals["stopped"] += 1
            self._emit(
                {
                    "kind": "notice",
                    "level": "warn",
                    "text": f"Couldn't fix {group.label()} after {group.failures} tries."
                    " Joinery won't try it again on its own; it's in the Errors list.",
                }
            )
        else:
            group.status, group.note = ce.OPEN, reason

    def decided(self, decision: Mapping[str, Any]) -> None:
        """A change was accepted or discarded: errors that came with it are settled.

        Discarded: they leave with it. Accepted: they are the project's now, and
        wait for a fix like any other."""
        transaction_id = str(decision.get("transaction_id", ""))
        if not transaction_id:
            return
        accepted = bool(decision.get("accepted"))
        changed = False
        for group in self.book.groups.values():
            if group.status == ce.FROM_CHANGE and group.change == transaction_id:
                group.change, group.note = "", ""
                group.status = ce.OPEN if accepted else ce.FIXED
                changed = True
        if changed:
            self._save()
            self.publish()
            with contextlib.suppress(RuntimeError):
                asyncio.get_running_loop().call_soon(self._repump)

    def _held(self) -> dict[str, str]:
        """Files in changes waiting for review: {file: transaction id}."""
        held: dict[str, str] = {}
        for transaction_id, files in self._pending().items():
            for path in files:
                held[str(path).removeprefix("res://")] = transaction_id
        return held

    # --- telling the sidebar ------------------------------------------------------------

    def event(self, offer: ce.Group | None = None) -> dict[str, Any]:
        settings = self.settings()
        data: dict[str, Any] = {
            "kind": "errors",
            "mode": settings.mode,
            "warnings": settings.warnings,
            "groups": [g.view() for g in self.book.listed()]
            if settings.mode != "off"
            else [],
            "totals": dict(self.book.totals),
        }
        if offer is not None:
            data["offer"] = offer.view()
        return data

    def publish(self, offer: ce.Group | None = None) -> None:
        self._emit(self.event(offer))

    def _save(self) -> None:
        with contextlib.suppress(OSError):
            self.book.save()
