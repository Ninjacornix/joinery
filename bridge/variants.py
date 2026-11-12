"""Variants side by side: one request made a few different ways at once (ADR 0042).

``/variants N <request>``, "Try 3 ways" in the composer and "Try other ways" on a
change card start a *variant group*: N parallel tasks (bridge.crew) on the same
request and targets, each told to take its own direction ("chunky and worn",
"slim and clean"). Each stages its own change in its own lane; the group's claims
are shared between its variants (joinery.claims) and exclusive against everyone
else. Each variant is checked and read by the reviewer like any change.

The user picks one: that variant is accepted (one Timeline commit) and every other
is discarded or stopped. Mix starts one more variant from notes on the others
("door shape from 2, colour from 3"); Discard all ends the group. Variants beyond
the project's parallel limit wait their turn.

"Try other ways" keeps the change that is already in review as the group's first
way ("As made"); picking another discards it, and accepting it ends the group.

Nothing here knows which provider runs the tasks.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from bridge.crew import FINAL, IN_REVIEW, Crew, Task
from bridge.instructions import VARIANT_MIX_PROMPT, VARIANT_PROMPT
from bridge.protocol import Prompt
from joinery.claims import MAIN

#: Ways at once; Mix may add a few more.
MIN_WAYS, MAX_WAYS, DEFAULT_WAYS = 2, 4, 3
MAX_MEMBERS = 6
#: The id a group gives the change already in review that it started from.
LEAD = "lead"
#: How the group's own event names the lead's change.
AS_MADE = "As made"
#: Decides the chat's own change: (transaction id, accept) -> the decision event.
LeadDecide = Callable[[str, bool], Awaitable[dict[str, Any]]]

#: Joinery's own directions when nobody gave any, by what the request is about.
DIRECTIONS: dict[str, tuple[str, ...]] = {
    "sound": (
        "short and punchy",
        "soft and airy",
        "deep and heavy",
        "bright and crisp",
    ),
    "music": (
        "calm and sparse",
        "driving and upbeat",
        "dark and moody",
        "playful and bouncy",
    ),
    "code": (
        "the simplest thing that works",
        "more forgiving for the player",
        "snappier and tighter",
        "more dramatic",
    ),
    "scene": (
        "open and spacious",
        "dense and cosy",
        "symmetrical and formal",
        "loose and natural",
    ),
    "look": (
        "chunky and worn",
        "slim and clean",
        "ornate and detailed",
        "bold and simple",
    ),
}
_KINDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("music", ("music", "song", "melody", "soundtrack", r"\.mid", r"\.ogg")),
    ("sound", ("sound", "sfx", "noise", "footsteps?", "creak", r"\.wav")),
    ("code", ("script", "code", "jump", "speed", "movement", r"\.gd")),
    (
        "look",
        ("colou?rs?", "restyle", "style", "texture", "material", "model", "looks?"),
    ),
    ("scene", ("level", "layout", "room", "arrange", r"\.tscn")),
)


def _whole(hint: str) -> str:
    """A word starts at a word's start; a file extension anywhere."""
    return hint if hint.startswith("\\") else "(?<![a-z])" + hint


def kind_of(request: str, targets: list[str] | tuple[str, ...] = ()) -> str:
    """What a request is mostly about, for Joinery's own directions. A visual change
    of a scene or a label is a look; only whole words count ("theme" isn't music)."""
    words = " ".join([request, *targets]).lower()
    for kind, hints in _KINDS:
        if any(re.search(_whole(hint) + "(?![a-z])", words) for hint in hints):
            return kind
    return "look"


def directions_for(
    request: str,
    count: int,
    targets: list[str] | tuple[str, ...] = (),
    given: list[str] | tuple[str, ...] = (),
) -> list[str]:
    """``count`` short, different directions: the ones given first, then Joinery's."""
    chosen = [" ".join(d.split())[:80] for d in given if d.strip()][:count]
    for extra in DIRECTIONS[kind_of(request, targets)]:
        if len(chosen) >= count:
            break
        if extra not in chosen:
            chosen.append(extra)
    return chosen


def parse_command(text: str) -> tuple[int, str, list[str]]:
    """``/variants [N] <request> [| direction | direction…]`` -> (N, request, directions).

    The leading command word is optional; N defaults to 3."""
    body = text.strip()
    body = re.sub(r"^/variants\b", "", body, flags=re.IGNORECASE).strip()
    count = DEFAULT_WAYS
    match = re.match(r"^(\d+)\b\s*", body)
    if match:
        count = int(match.group(1))
        body = body[match.end() :]
    request, *given = [part.strip() for part in body.split("|")]
    return count, request, [g for g in given if g]


@dataclass
class Group:
    id: str
    title: str
    request: str
    origin: Literal["user", "lead"]
    targets: list[str]
    #: The variants' task ids, in the order they show (way 1, 2…).
    members: list[str] = field(default_factory=list)
    #: The change already in review the group started from ("" for none).
    lead_tx: str = ""
    lead_summary: str = ""
    lead_state: str = ""
    #: "working" until the user picks one ("picked") or discards them all.
    state: str = "working"
    #: The task id picked, or LEAD.
    picked: str = ""
    #: Deciding now: its ways' changes don't announce the group until it is done.
    deciding: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class Variants:
    """The variant groups of one chat host."""

    def __init__(self, crew: Crew, lead_decide: LeadDecide | None = None) -> None:
        self.crew = crew
        self._lead_decide = lead_decide
        self.groups: dict[str, Group] = {}
        crew.observers.append(self._observe)

    # --- Asking -------------------------------------------------------------------------

    def group_of(self, task_id: str) -> Group | None:
        task = self.crew.tasks.get(task_id)
        return self.groups.get(task.group) if task is not None and task.group else None

    def member(self, transaction_id: str) -> Task | None:
        """The variant whose change ``transaction_id`` is in review."""
        task = self.crew.owner(transaction_id)
        return task if task is not None and task.group in self.groups else None

    def joined(self, transaction_id: str) -> Group | None:
        """The open group the chat's own change ``transaction_id`` is a way of."""
        return next(
            (
                g
                for g in self.groups.values()
                if transaction_id
                and g.lead_tx == transaction_id
                and g.state == "working"
            ),
            None,
        )

    def event(self, group: Group) -> dict[str, Any]:
        ways: list[dict[str, Any]] = []
        if group.lead_tx:
            ways.append(
                {
                    "task_id": LEAD,
                    "variant": 0,
                    "direction": AS_MADE,
                    "state": group.lead_state or IN_REVIEW,
                    "transaction_id": group.lead_tx,
                    "summary": group.lead_summary,
                }
            )
        for task_id in group.members:
            task = self.crew.tasks[task_id]
            ways.append(
                {
                    "task_id": task.id,
                    "variant": task.variant,
                    "direction": task.direction,
                    "state": task.state,
                    "transaction_id": task.transaction_id,
                    "summary": task.summary,
                    "message": task.message,
                }
            )
        return {
            "kind": "variants",
            "group_id": group.id,
            "title": group.title,
            "request": group.request,
            "origin": group.origin,
            "state": group.state,
            "picked": group.picked,
            "ways": ways,
        }

    # --- Starting -----------------------------------------------------------------------

    async def start(
        self,
        request: str,
        *,
        prompt: Prompt,
        count: int = DEFAULT_WAYS,
        directions: list[str] | tuple[str, ...] = (),
        targets: list[str] | tuple[str, ...] = (),
        origin: Literal["user", "lead"] = "user",
        title: str = "",
        lead_tx: str = "",
        lead_summary: str = "",
    ) -> Group:
        """Start ``count`` variants of ``request``; those over the limit wait their turn."""
        request = request.strip()
        if not request:
            raise ValueError("Say what to try, for example: /variants 3 a wooden door.")
        if not MIN_WAYS <= count <= MAX_WAYS:
            raise ValueError(f"Try {MIN_WAYS} to {MAX_WAYS} ways at once.")
        if self.crew.limit <= 1:
            raise ValueError(
                "Parallel tasks are off for this project ([agents] parallel = 1 in "
                "joinery.toml), so Joinery can't try several ways at once."
            )
        if lead_tx and self.joined(lead_tx) is not None:
            raise ValueError("Other ways of this change are already being tried.")
        notes = directions_for(request, count, list(targets), list(directions))
        group = Group(
            id=uuid.uuid4().hex,
            title=(title or _title(request)),
            request=request,
            origin=origin,
            targets=list(targets),
            lead_tx=lead_tx,
            lead_summary=lead_summary,
            lead_state=IN_REVIEW if lead_tx else "",
        )
        claims = self.crew.gateway.claims
        if lead_tx:
            # The change in review is one of the ways: the others share its files.
            claims.join(MAIN, group.id)
        self.groups[group.id] = group
        base = _staged_view(prompt)
        try:
            for index, note in enumerate(notes, 1):
                others = [n for n in notes if n != note]
                if lead_tx:
                    others.append("the version already made (" + lead_summary + ")")
                self._add(
                    group,
                    base,
                    index,
                    note,
                    VARIANT_PROMPT.fill(
                        index=index,
                        count=len(notes) + (1 if lead_tx else 0),
                        direction=note,
                        others="; ".join(others) or "none",
                        request=request,
                    ),
                )
        except ValueError:
            await self.discard_all(group.id)
            raise
        self._announce(group)
        self.crew.pump()
        return group

    async def mix(self, group_id: str, note: str, prompt: Prompt) -> Task:
        """One more variant, combining the others as ``note`` says."""
        group = self._open(group_id)
        note = " ".join(note.split())
        if not note:
            raise ValueError(
                "Say what to combine, for example: door shape from 2, colour from 3."
            )
        if len(group.members) + (1 if group.lead_tx else 0) >= MAX_MEMBERS:
            raise ValueError(
                f"{MAX_MEMBERS} ways is the most at once. Pick one or discard some first."
            )
        ways = []
        if group.lead_tx:
            ways.append(f"0. {AS_MADE}: {group.lead_summary or 'the first version'}")
        for task_id in group.members:
            task = self.crew.tasks[task_id]
            made = task.summary or task.message or "not finished"
            ways.append(f"{task.variant}. {task.direction}: {made}")
        index = max((self.crew.tasks[t].variant for t in group.members), default=0) + 1
        task = self._add(
            group,
            _staged_view(prompt),
            index,
            "Mix: " + note[:72],
            VARIANT_MIX_PROMPT.fill(
                note=note, ways="\n".join(ways), request=group.request
            ),
        )
        self._announce(group)
        self.crew.pump()
        return task

    def _add(
        self,
        group: Group,
        prompt: Prompt,
        index: int,
        note: str,
        parts: dict[str, str],
    ) -> Task:
        task = self.crew.add(
            f"{group.title} · {index}",
            group.request,
            origin=group.origin,
            prompt=prompt,
            text=parts["text"],
            rules=parts["rules"],
            targets=group.targets,
            group=group.id,
            direction=note,
            variant=index,
        )
        group.members.append(task.id)
        return task

    # --- Deciding -----------------------------------------------------------------------

    async def pick(self, group_id: str, choice: str) -> list[dict[str, Any]]:
        """Accept the way ``choice`` (a task id, or LEAD) and drop every other.

        Atomic for the user: if the accept fails nothing else is touched and the
        group stays open; once it is accepted, every other way is discarded or
        stopped before the group says it was picked. Returns the decision events.
        """
        group = self._open(group_id)
        async with group.lock:
            if group.state != "working":
                raise ValueError("A way was already picked for this request.")
            decisions: list[dict[str, Any]] = []
            group.deciding = True
            try:
                decision = await self._accept(group, choice)
            finally:
                group.deciding = False
            if not decision.get("accepted"):
                return [decision]
            decisions.append(decision)
            group.state, group.picked = "picked", choice
            decisions += await self._drop(group, keep=choice)
            self._close(group)
            return decisions

    async def _accept(self, group: Group, choice: str) -> dict[str, Any]:
        """Accept one way: the chat's own change (LEAD) or a variant's."""
        if choice == LEAD:
            if not group.lead_tx or self._lead_decide is None:
                raise ValueError("That version is no longer waiting.")
            decision = await self._lead_decide(group.lead_tx, True)
            group.lead_state = "accepted"
            return decision
        task = self.crew.tasks.get(choice)
        if task is None or task.group != group.id:
            raise ValueError("That way isn't part of this request.")
        if self.crew.owner(task.transaction_id) is not task:
            raise ValueError("That way has no change waiting yet.")
        return await self.crew.decide(task.transaction_id, True)

    async def discard_all(self, group_id: str) -> list[dict[str, Any]]:
        """Discard every way's change and stop those still working."""
        group = self.groups.get(group_id)
        if group is None:
            raise ValueError("Those variants are no longer open.")
        async with group.lock:
            if group.state != "working":
                return []
            group.state = "discarded"
            decisions = await self._drop(group, keep="")
            self._close(group)
            return decisions

    async def decided(self, task: Task, accepted: bool) -> list[dict[str, Any]]:
        """A way was decided outside the strip (its own Accept in the Review tab):
        accepting it is a pick."""
        if not accepted or not task.group:
            return []
        group = self.groups.get(task.group)
        if group is None or group.state != "working":
            return []
        async with group.lock:
            group.state, group.picked = "picked", task.id
            decisions = await self._drop(group, keep=task.id)
            self._close(group)
            return decisions

    async def lead_decided(
        self, transaction_id: str, accepted: bool
    ) -> list[dict[str, Any]]:
        """The chat's own change, one of a group's ways, was decided on its card."""
        group = self.joined(transaction_id)
        if group is None:
            return []
        group.lead_state = "accepted" if accepted else "discarded"
        self.crew.gateway.claims.leave(MAIN)
        if not accepted:
            self._announce(group)
            return []
        async with group.lock:
            group.state, group.picked = "picked", LEAD
            decisions = await self._drop(group, keep=LEAD)
            self._close(group)
            return decisions

    async def _drop(self, group: Group, *, keep: str) -> list[dict[str, Any]]:
        """Discard or stop every way but ``keep``; returns the discards' events.
        The group is announced once, after all of them (:meth:`_close`)."""
        group.deciding = True
        decisions: list[dict[str, Any]] = []
        for task_id in group.members:
            task = self.crew.tasks[task_id]
            if task_id == keep or task.state in FINAL:
                continue
            owned = self.crew.owner(task.transaction_id)
            if owned is task:
                try:
                    decisions.append(await self.crew.decide(task.transaction_id, False))
                except (OSError, ValueError):
                    await self.crew.stop(task.id)
            else:
                await self.crew.stop(task.id)
        if keep != LEAD and group.lead_tx and group.lead_state == IN_REVIEW:
            if self._lead_decide is not None:
                with contextlib.suppress(OSError, ValueError, RuntimeError):
                    decisions.append(await self._lead_decide(group.lead_tx, False))
            group.lead_state = "discarded"
        return decisions

    def _close(self, group: Group) -> None:
        group.deciding = False
        if group.lead_tx:
            self.crew.gateway.claims.leave(MAIN)
        self._announce(group)

    def _open(self, group_id: str) -> Group:
        group = self.groups.get(group_id)
        if group is None or group.state != "working":
            raise ValueError("Those variants are no longer open.")
        return group

    # --- Following ----------------------------------------------------------------------

    def _observe(self, task: Task) -> None:
        group = self.groups.get(task.group) if task.group else None
        if group is not None and task.id in group.members and not group.deciding:
            self._announce(group)

    def _announce(self, group: Group) -> None:
        self.crew.emit(self.event(group))


def _title(request: str) -> str:
    words = " ".join(request.split())
    return words if len(words) <= 48 else words[:47].rstrip() + "…"


def _staged_view(prompt: Prompt) -> Prompt:
    """Variants change the scene on screen as a staged file, each in its own stage:
    only one agent can change it live. A scene with unsaved edits stays live (and so
    out of the variants' reach), since its saved file isn't what the user sees."""
    context = dict(prompt.editor_context)
    scene = context.get("scene_path", "")
    if scene and scene not in context.get("unsaved_scenes", ""):
        context.pop("scene_path", None)
        context["scene_on_screen"] = scene
    return prompt.model_copy(update={"editor_context": context})
