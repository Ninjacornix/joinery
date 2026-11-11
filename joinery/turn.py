"""One agent's turn, owned by Joinery rather than by the provider.

Every provider (Codex, Claude Code, Gemini CLI, opencode) runs its own model
loop, but the turn around it is the same: the agent works on one staged change,
hands it over with ``submit_for_review``, Joinery checks it, and the user
decides. The states here are that lifecycle; nothing in this module knows which
provider is running.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from joinery.gateway import ProjectGateway


class TurnState(StrEnum):
    IDLE = "idle"
    WORKING = "working"
    SUBMITTING = "submitting"
    IN_REVIEW = "in_review"
    DECIDED = "decided"
    COMPACTING = "compacting"


#: Submit attempts per turn, the agent's and Joinery's together. The last
#: failure is final and drops the change.
MAX_SUBMITS = 3
#: Times Joinery asks the agent to fix a change it submitted on its behalf.
MAX_CONTINUATIONS = 1
#: Share of the context window at which Joinery summarises the session.
COMPACT_AT = 0.8

_ALLOWED: dict[TurnState, frozenset[TurnState]] = {
    TurnState.IDLE: frozenset({TurnState.WORKING, TurnState.COMPACTING}),
    # A read-only turn that ran while a review was open ends back in review.
    TurnState.WORKING: frozenset(
        {TurnState.SUBMITTING, TurnState.IDLE, TurnState.IN_REVIEW}
    ),
    TurnState.SUBMITTING: frozenset(
        {TurnState.IN_REVIEW, TurnState.WORKING, TurnState.IDLE}
    ),
    TurnState.IN_REVIEW: frozenset(
        {TurnState.DECIDED, TurnState.WORKING, TurnState.COMPACTING}
    ),
    TurnState.DECIDED: frozenset({TurnState.IDLE}),
    TurnState.COMPACTING: frozenset({TurnState.IDLE, TurnState.IN_REVIEW}),
}


def allowed(source: TurnState, target: TurnState) -> bool:
    return target in _ALLOWED[source]


def _res(path: str) -> str:
    return "res://" + path.removeprefix("res://") if path else ""


def scene_route(
    scene_path: str, editor_context: Mapping[str, str]
) -> Literal["live", "staged"]:
    """How a change to ``scene_path`` is made: in the live editor or as a staged file.

    A scene open with unsaved edits, or one that has no saved file yet (an empty
    path), is edited live; its saved file can't be accepted while the editor
    holds newer edits. Every other scene is a staged file change.
    """
    if not scene_path.removeprefix("res://"):
        return "live"
    try:
        unsaved = json.loads(editor_context.get("unsaved_scenes", "[]") or "[]")
    except ValueError:
        unsaved = []
    if not isinstance(unsaved, list):
        unsaved = []
    return "live" if _res(scene_path) in {_res(str(s)) for s in unsaved} else "staged"


class ContextMeter:
    """How full the provider's context is, from its Usage reports."""

    def __init__(self) -> None:
        self.used = 0
        self.window = 0

    def update(self, used: int, window: int) -> None:
        if used > 0:
            self.used = used
        if window > 0:
            self.window = window

    def reset(self) -> None:
        self.used = 0

    def needs_compaction(self) -> bool:
        """Near the window's end. An unknown window never triggers; the provider's own
        compaction stays the backstop there."""
        return self.window > 0 and self.used >= COMPACT_AT * self.window


@dataclass
class Problem:
    """One thing a submitted change fails, in words the agent can act on."""

    check: str
    message: str
    paths: list[str] = field(default_factory=list)
    #: False when no fix in this turn can pass it (protected files, a live conflict).
    fixable: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {"check": self.check, "message": self.message, "paths": self.paths}


@dataclass
class SubmitResult:
    status: Literal["in_review", "failed", "needs_user", "nothing_to_review"]
    attempt: int
    attempts_left: int
    final: bool
    transaction_id: str = ""
    changes: list[dict[str, str]] = field(default_factory=list)
    live_edits: int = 0
    problems: list[Problem] = field(default_factory=list)
    next: str = ""

    def as_tool_result(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "attempt": self.attempt,
            "attempts_left": self.attempts_left,
            "final": self.final,
            "transaction_id": self.transaction_id,
            "changes": self.changes,
            "live_edits": self.live_edits,
            "problems": [problem.as_dict() for problem in self.problems],
            "next": self.next,
        }


#: What the agent is told to do next, by outcome.
NEXT_FIX = "Fix each problem, then call submit_for_review again."
NEXT_STOP = "Stop and tell the user plainly what failed. Nothing was changed."
NEXT_REVIEW = (
    "The change is on screen for the user. Say in a sentence or two what changed "
    "and make no more changes this turn."
)
NEXT_NOTHING = (
    "Nothing is staged and no live edit is waiting. Make the change first, or end "
    "your turn if nothing needed changing."
)
NEXT_ART = "Stop here and tell the user: they approve the art direction first (/art)."


class TurnController:
    """One agent's turn: at most one staged change, checked, then the user's decision.

    The controller holds no globals: one controller drives one agent with one
    gateway, so a scheduler can run several side by side. It never talks to a
    provider; the chat host streams the provider and calls :meth:`begin`,
    :meth:`end` and :meth:`decide` around it.
    """

    def __init__(
        self,
        gateway: ProjectGateway,
        emit: Callable[[dict[str, Any]], None],
        agent_id: str = "",
    ) -> None:
        self.gateway = gateway
        self.emit = emit
        self.agent_id = agent_id
        self.state = TurnState.IDLE
        self.turn_id = ""
        self.request = ""
        #: The user's recent requests, newest last, for the session summary.
        self.requests: list[str] = []
        self.attempts = 0
        #: Scenes changed live this turn ("res://…", "" for an unsaved root).
        self.live_scenes: set[str] = set()
        self.live_edits = 0
        #: A change is on screen for the user; nothing new may be made until decided.
        self.review_open = False
        #: The transaction under review ("" for a live edit alone).
        self.review_id = ""
        self.meter = ContextMeter()
        #: The session summary the next prompt carries after a fresh provider session.
        self.primer = ""
        self.compaction: asyncio.Task[Any] | None = None
        gateway.turn = self

    # --- State -----------------------------------------------------------------------

    def _to(self, state: TurnState, reason: str = "") -> None:
        if not allowed(self.state, state):
            raise RuntimeError(f"A turn cannot go from {self.state} to {state}.")
        self.state = state
        event: dict[str, Any] = {
            "kind": "turn_state",
            "turn_id": self.turn_id,
            "state": str(state),
        }
        if self.review_id and state in {TurnState.IN_REVIEW, TurnState.DECIDED}:
            event["transaction_id"] = self.review_id
        if reason:
            event["reason"] = reason
        self.emit(event)

    def begin(self, request: str) -> None:
        """A user request starts a turn. While a change waits for the user the turn
        may only look, not change anything."""
        if self.state not in {TurnState.IDLE, TurnState.IN_REVIEW}:
            raise ValueError("Journeyman is still working on the last request.")
        self.turn_id = uuid.uuid4().hex
        self.attempts = 0
        self.request = request
        self.requests = [*self.requests, request.strip()[:160]][-10:]
        if not self.review_open:
            self.live_scenes.clear()
            self.live_edits = 0
        self._to(TurnState.WORKING)

    def needs_compaction(self) -> bool:
        return self.meter.needs_compaction()

    def begin_compaction(self) -> None:
        self._to(TurnState.COMPACTING)

    def end_compaction(self) -> None:
        self._to(TurnState.IN_REVIEW if self.review_open else TurnState.IDLE)

    # --- Admission -------------------------------------------------------------------

    def admit_mutation(self) -> None:
        """Raise unless this turn may change the project now."""
        if self.state is TurnState.COMPACTING:
            raise ValueError(
                "Joinery is summarising this conversation; make no changes now."
            )
        if self.state is TurnState.SUBMITTING:
            raise ValueError(
                "Joinery is checking your change; wait for submit_for_review to return."
            )
        if self.state is TurnState.IN_REVIEW:
            raise ValueError(
                "Your change is submitted for review; make no more changes this turn."
            )
        if self.state is not TurnState.WORKING:
            raise ValueError("Changes need an active user request.")
        if self.review_open:
            raise ValueError("Accept or reject the change on screen first.")

    def edited_live(self, scene_path: str) -> bool:
        """``scene_path`` was changed live this turn, so it can't also be staged."""
        return _res(scene_path) in self.live_scenes

    def admit_live_edit(self, editor_context: Mapping[str, str]) -> str:
        """Allow one live edit of the open scene, or raise why not. Returns the scene."""
        self.admit_mutation()
        scene = editor_context.get("scene_path", "")
        if scene_route(scene, editor_context) == "staged":
            raise ValueError(
                "This scene is saved; change it as a staged file with the Godot "
                "tools (call_tool on godot), not live with edit_scene."
            )
        tx = self.gateway.transaction
        if (
            scene
            and tx is not None
            and tx.state == "staging"
            and any(c["path"] == scene.removeprefix("res://") for c in tx.diff())
        ):
            raise ValueError(
                "The saved scene is already staged; do not mix saved-file and live "
                "changes to that scene."
            )
        return scene

    def record_live_edit(self, scene: str, ok: bool) -> None:
        if ok:
            self.live_scenes.add(_res(scene))
            self.live_edits += 1

    def live_decided(self) -> None:
        """The user decided the live preview (or it was dropped)."""
        self.live_scenes.clear()
        self.live_edits = 0
        if self.review_open and not self.review_id:
            self.review_open = False
            if self.state is TurnState.IN_REVIEW:
                self._to(TurnState.DECIDED)
                self._to(TurnState.IDLE)

    # --- Submitting --------------------------------------------------------------------

    def _staged(self) -> bool:
        tx = self.gateway.transaction
        return tx is not None and tx.state == "staging" and bool(tx.diff())

    def _result(self, status: Any, final: bool, **extra: Any) -> SubmitResult:
        return SubmitResult(
            status=status,
            attempt=self.attempts,
            attempts_left=max(MAX_SUBMITS - self.attempts, 0),
            final=final,
            live_edits=self.live_edits,
            **extra,
        )

    async def submit(
        self, summary: str, origin: Literal["agent", "joinery"] = "agent"
    ) -> SubmitResult:
        """Check the turn's change and, when it passes, put it in front of the user.

        Fixable problems leave the stage as it is so the agent can fix them; the
        last allowed attempt, or a problem no fix can pass, drops it.
        """
        self.admit_mutation()
        if not self._staged() and not self.live_edits:
            return self._result("nothing_to_review", False, next=NEXT_NOTHING)
        self.attempts += 1
        self._to(TurnState.SUBMITTING)
        gate: dict[str, Any] | None = None
        try:
            async with self.gateway.call_lock:
                checked = await self.gateway.submit_checks(self.live_scenes)
            found, problems, gate = checked.changes, checked.problems, checked.gate
        except (OSError, RuntimeError, ValueError, TimeoutError) as exc:
            tx = self.gateway.transaction
            found = tx.diff() if tx is not None and tx.state == "staging" else []
            problems = [Problem("checks", f"Joinery could not run its checks: {exc}")]
        if self.state is not TurnState.SUBMITTING:
            # Stopped while the checks ran.
            return self._result(
                "failed", True, problems=[Problem("stopped", "The turn was stopped.")]
            )
        changes = [{"path": c["path"], "change": c["change"]} for c in found]
        if gate is not None:
            self.gateway.art_gate = None
            await self._drop("needs_user")
            self.emit(gate)
            return self._result(
                "needs_user",
                True,
                changes=changes,
                problems=[Problem("art_gate", str(gate["message"]), fixable=False)],
                next=NEXT_ART,
            )
        if problems:
            final = self.attempts >= MAX_SUBMITS or any(
                not problem.fixable for problem in problems
            )
            if final:
                await self._drop("failed")
                return self._result(
                    "failed",
                    True,
                    changes=changes,
                    problems=problems,
                    next=NEXT_STOP,
                )
            self._to(TurnState.WORKING)
            self.gateway.progress("Fixing what the checks found…")
            return self._result(
                "failed",
                False,
                changes=changes,
                problems=problems,
                next=NEXT_FIX,
            )
        if not found and not self.live_edits:
            self._to(TurnState.WORKING)
            self.attempts -= 1
            return self._result("nothing_to_review", False, next=NEXT_NOTHING)
        preview: dict[str, Any] | None = None
        if found:
            preview = self.gateway.open_review(summary.strip() or self.request)
            gate, self.gateway.art_gate = self.gateway.art_gate, None
            if gate is not None:
                preview["art_gate"] = {k: v for k, v in gate.items() if k != "kind"}
            preview["origin"] = origin
            self.review_id = str(preview["transaction_id"])
        elif self.gateway.transaction is not None:
            await self.gateway.cancel()  # an empty stage beside a live edit
        self.review_open = True
        if preview is not None:
            self.emit(preview)
        self._to(TurnState.IN_REVIEW)
        self.gateway.start_media(self.emit)
        return self._result(
            "in_review",
            True,
            transaction_id=self.review_id,
            changes=changes,
            next=NEXT_REVIEW,
        )

    async def end(self, ok: bool, *, last: bool = True) -> SubmitResult | None:
        """The provider finished its turn (``Done``).

        A change the agent left without submitting is submitted on its behalf.
        With ``last`` a fixable failure of that submit is final too: no one is
        left to fix it.
        """
        if self.state is not TurnState.WORKING:
            return None  # submitted already, or decided while the agent finished
        if not ok:
            await self._abandon("failed")
            return None
        if self.review_open:
            self._to(TurnState.IN_REVIEW)
            return None
        if self._staged() or self.live_edits:
            result = await self.submit(self.request[:160], "joinery")
            if result.status == "failed" and not result.final and last:
                await self._drop("failed")
                result.final = True
                result.next = NEXT_STOP
            return result
        if self.gateway.transaction is not None:
            await self.gateway.cancel()  # a stage with no changes
        gate, self.gateway.art_gate = self.gateway.art_gate, None
        if gate is not None:
            self.emit(gate)
        self._to(TurnState.IDLE)
        return None

    async def decide(self, transaction_id: str, accept: bool) -> dict[str, Any]:
        """The user's Accept or Discard on the change in review."""
        event = await self.gateway.decide(transaction_id, accept)
        if transaction_id == self.review_id:
            self.review_open = False
            if self.state is TurnState.IN_REVIEW:
                self._to(TurnState.DECIDED)
                self._to(TurnState.IDLE)
            self.review_id = ""
        return event

    async def cancel(self, reason: str = "cancelled") -> None:
        """Stop the turn. A change already in review stays open for the user."""
        if self.state in {TurnState.WORKING, TurnState.SUBMITTING}:
            await self._abandon(reason)

    async def _abandon(self, reason: str) -> None:
        if self.review_open:
            # The change on screen came from an earlier turn; it stays.
            self._to(TurnState.IN_REVIEW, reason)
            return
        await self._drop(reason)

    async def _drop(self, reason: str) -> None:
        """Throw this turn's change away: the stage and the live preview."""
        await self.gateway.cancel()
        self.gateway.art_gate = None
        self.live_scenes.clear()
        self.live_edits = 0
        self.review_open = False
        self.review_id = ""
        self._to(TurnState.IDLE, reason)
