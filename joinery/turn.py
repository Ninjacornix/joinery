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
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal

from joinery import done_when, metrics, reasons
from joinery.claims import LEAD_BUSY, live_refusal
from joinery.peer_review import PeerReview, Reviewer, ReviewPlan, request_for
from joinery.reasons import Reason
from joinery.routing import change_risk
from joinery.transactions import inventory

if TYPE_CHECKING:
    from joinery.gateway import ProjectGateway
    from joinery.submission import Validation


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
#: Times the reviewer agent may send a change back before it opens with notes.
MAX_REVIEW_ROUNDS = 2
#: Times the done-when checks may send a change back before it opens with their results.
MAX_CHECK_ROUNDS = 1
#: Seconds past the reviewer's own time box before Joinery stops waiting for it.
REVIEW_GRACE = 15
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

    The scene on screen is edited live, so the viewport shows the change before
    the user decides. So is a scene open with unsaved edits, or one that has no
    saved file yet (an empty path): its saved file can't be accepted while the
    editor holds newer edits. Every other scene is a staged file change.
    """
    if not scene_path.removeprefix("res://"):
        return "live"
    if _res(scene_path) == _res(editor_context.get("scene_path", "")):
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
    #: The same failure as items of one shape (rule, file, node, measured,
    #: expected, fix); empty means one generic item per path (``reasons()``).
    items: list[Reason] = field(default_factory=list, compare=False)

    def reasons(self) -> list[Reason]:
        return self.items or reasons.generic(self.check, self.message, self.paths)

    def as_dict(self) -> dict[str, Any]:
        return {
            "check": self.check,
            "message": self.message,
            "paths": self.paths,
            "items": [reason.as_dict() for reason in self.reasons()],
        }


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
NEXT_STOP = (
    "Stop. Joinery tells the user in its own line that the change failed its checks "
    "and nothing was changed; don't repeat that. Say in one short sentence what you "
    "would try differently, or nothing."
)
NEXT_REVIEW = (
    "The change is on screen for the user. Say in a sentence or two what you made "
    "and where you placed it (scene and node), plainly and without Joinery's own "
    "steps, and make no more changes this turn."
)
NEXT_NOTHING = (
    "Nothing is staged and no live edit is waiting. Make the change first, or end "
    "your turn if nothing needed changing."
)
NEXT_ART = "Stop here and tell the user: they approve the art direction first (/art)."
NEXT_REVIEWER = (
    "Joinery's reviewer read your change and found the problems listed. Fix each one "
    "in the same change, then call submit_for_review again. If one is wrong, leave it "
    "and say why in one line; the user sees the reviewer's notes with the change."
)
NEXT_PRECHECK_CLEAN = (
    "Joinery's checks pass on the change as it is now. Finish it if anything is "
    "left, then call submit_for_review."
)
NEXT_PRECHECK_FIX = (
    "Fix each item, then call submit_for_review (or precheck again first). This "
    "precheck used no submit attempt."
)
NEXT_PRECHECK_ART = (
    "This change needs the user's approval of the art direction first. Stop here "
    "and tell the user (/art)."
)
NEXT_PRECHECK_ERROR = (
    "Joinery could not run its checks; nothing in the change can fix that. Call "
    "submit_for_review when the change is complete."
)
NEXT_DONE_WHEN = (
    "Joinery played and checked your change against the to-do's done-when items and "
    "these failed. Fix each in the same change, then call submit_for_review again. If "
    "one can't pass (a bot can't make a jump a person can), leave it and say why in one "
    "line; the user sees every item's result with the change."
)
#: A to-do waits for the user's Go: nothing may change until then.
WAITING_FOR_GO = (
    "The user hasn't pressed Go on your to-do yet. End your turn now without changing "
    "anything; Go starts the work as a new request."
)
#: The to-do's independent steps run as parallel tasks (``run_steps = "together"``).
STEPS_RUNNING = (
    "Joinery runs your to-do's independent steps as parallel tasks. End your turn now "
    "without changing anything; you hear back when they are done or one fails."
)
#: The checks a change passed before the reviewer reads it, in plain words.
PASSED = (
    "Joinery's checks passed: protected files, missing files, asset provenance, "
    "dependencies, art bible rules, Godot import"
)


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
        #: Placeholder shapes the live edits added this turn: (parent, shape).
        self.live_shapes: list[tuple[str, Any]] = []
        #: A change is on screen for the user; nothing new may be made until decided.
        self.review_open = False
        #: The transaction under review ("" for a live edit alone).
        self.review_id = ""
        self.meter = ContextMeter()
        #: The session summary the next prompt carries after a fresh provider session.
        self.primer = ""
        #: The saved chat the summary belongs to ("" for none).
        self.primer_chat = ""
        self.compaction: asyncio.Task[Any] | None = None
        #: The last submit's result this turn, the agent's or Joinery's.
        self.last_result: SubmitResult | None = None
        #: Live edits the editor is applying: {request id: (scene, payload)}.
        self._live_requests: dict[str, tuple[str, Mapping[str, Any]]] = {}
        #: The submit checks running now, so a Stop can end them.
        self._checks: asyncio.Task[Validation] | None = None
        #: The stage and live edits as the last fixable failure found them.
        self._checked: tuple[dict[str, str], int] | None = None
        #: What the passing checks covered, for the review's quiet status line.
        self._passed: dict[str, list[str]] = {}
        #: The agent's plain words on what visibly changed, for the review.
        self._facts: list[str] = []
        #: The reviewer agent that reads each submitted change (None: no reviewer).
        self.reviewer: Reviewer | None = None
        #: Times the reviewer may send a change back this turn.
        self.max_review_rounds = MAX_REVIEW_ROUNDS
        #: The reviewer's time box, seconds.
        self.review_seconds = 90
        #: Send-backs so far this turn.
        self.review_rounds = 0
        #: The request's model choice ({mode, model, effort}), which the reviewer shares.
        self.selection: dict[str, Any] = {}
        #: The last review this turn, and the stage it read.
        self._peer: PeerReview | None = None
        self._peer_print: tuple[dict[str, str], int] | None = None
        self._reviewing: asyncio.Task[PeerReview] | None = None
        #: Joinery's own last submit: the reviewer's notes no longer send it back.
        self._last_chance = False
        #: The provider session's token totals as last reported (input, output, cached).
        self.usage_totals: metrics.Totals = (0, 0, 0)
        #: A change sent back to the agent, until it submits again: (since, by whom).
        self._sent_back: tuple[metrics.Clock, str] | None = None
        #: Prechecks this turn (they are not submit attempts).
        self.prechecks = 0
        #: The request's to-do (ADR 0060): its steps and the done-when items checked after
        #: the change passes; None for a request without one.
        self.todo: done_when.Todo | None = None
        #: A to-do proposed and waiting for the user's Go (nothing may change meanwhile).
        self.pending_todo: done_when.Todo | None = None
        #: Steps the harness runs as parallel tasks once the agent ends this prompt
        #: (0-based, ``[agents] run_steps = "together"``); nothing may change meanwhile.
        self.steps_together: list[int] = []
        #: The steps are running as tasks now: the lead's turn takes no turn of its own.
        self.stepping = False
        #: The chat host can run steps as parallel tasks (a task's own turn can't).
        self.runs_steps = False
        #: Times the done-when checks sent the change back this turn.
        self.check_rounds = 0
        #: Checks the to-do's items against the change (default: done_when.verify).
        self.verifier: (
            Callable[[TurnController], Awaitable[list[done_when.Result]]] | None
        ) = None
        #: The last done-when results this turn, and the stage they checked.
        self._done: list[done_when.Result] = []
        self._done_print: tuple[dict[str, str], int] | None = None
        self._verifying: asyncio.Task[list[done_when.Result]] | None = None
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

    def begin(self, request: str, todo: done_when.Todo | None = None) -> None:
        """A user request starts a turn. While a change waits for the user the turn
        may only look, not change anything. ``todo`` is the to-do the user pressed Go on;
        any other request drops a to-do still waiting for Go."""
        if self.state not in {TurnState.IDLE, TurnState.IN_REVIEW}:
            raise ValueError("Journeyman is still working on the last request.")
        waiting, self.pending_todo = self.pending_todo, None
        if waiting is not None and (todo is None or todo.id != waiting.id):
            self.emit({"kind": "todo", "todo_id": waiting.id, "state": "dropped"})
        self.todo = todo
        self.steps_together = []
        self.stepping = False
        self.check_rounds = 0
        self._done, self._done_print = [], None
        self.turn_id = uuid.uuid4().hex
        self.attempts = 0
        self.last_result = None
        self._checked = None
        self._facts = []
        self.review_rounds = 0
        self._peer = self._peer_print = None
        self._sent_back = None
        self.prechecks = 0
        self.request = request
        self.requests = [*self.requests, request.strip()[:160]][-10:]
        if not self.review_open:
            self.live_scenes.clear()
            self.live_edits = 0
            self.live_shapes = []
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
        if self.pending_todo is not None:
            raise ValueError(WAITING_FOR_GO)
        if self.steps_together or self.stepping:
            raise ValueError(STEPS_RUNNING)

    # --- The to-do -----------------------------------------------------------------------

    def propose_todo(self, params: dict[str, Any]) -> dict[str, Any]:
        """The agent's to-do for a request with several steps (``propose_todo``).

        It shows in the sidebar at once. When the project's ``[agents] todo_go`` asks for
        the user's Go (by default, two steps or more) and nothing has changed yet, the
        agent ends its turn and Go starts the work as a new request; otherwise it carries
        on and the done-when items are checked after it submits.
        """
        if self.state is not TurnState.WORKING:
            raise ValueError("Propose a to-do while working on a user request.")
        if self.pending_todo is not None:
            raise ValueError(WAITING_FOR_GO)
        if self.todo is not None:
            raise ValueError(
                "This request has its to-do already; work through it and submit."
            )
        todo = done_when.proposal(params, self.request)
        wait = done_when.waits_for_go(
            todo, done_when.go_mode(self.gateway.project)
        ) and not (self._staged() or self.live_edits)
        together = (
            []
            if wait or not self.runs_steps or self._staged() or self.live_edits
            else done_when.runs_together(
                todo, done_when.run_steps_mode(self.gateway.project)
            )
        )
        if wait:
            self.pending_todo = todo
        else:
            self.todo = todo
            self.steps_together = together
        self.emit(
            {"kind": "todo", **todo.as_dict(), "state": "waiting" if wait else "going"}
        )
        if wait:
            return {
                "status": "waiting_for_go",
                "todo_id": todo.id,
                "next": "The user sees your to-do and may edit it. " + WAITING_FOR_GO,
            }
        if together:
            return {
                "status": "running_steps",
                "todo_id": todo.id,
                "steps": [i + 1 for i in together],
                "next": STEPS_RUNNING,
            }
        return {
            "status": "go",
            "todo_id": todo.id,
            "next": (
                "Work through the steps as one change and submit it. Joinery checks each "
                "done-when item after the change passes its checks."
            ),
        }

    def drop_todo(self, todo_id: str) -> bool:
        """The user cancelled the to-do waiting for Go."""
        waiting = self.pending_todo
        if waiting is None or waiting.id != todo_id:
            return False
        self.pending_todo = None
        self.emit({"kind": "todo", "todo_id": todo_id, "state": "cancelled"})
        return True

    def todo_event(self, state: str, **extra: Any) -> None:
        if self.todo is not None:
            self.emit(
                {"kind": "todo", "todo_id": self.todo.id, "state": state, **extra}
            )

    async def _check_done(self, changes: list[dict[str, str]]) -> SubmitResult | None:
        """Check the to-do's done-when items; a Fail sends the change back once.

        Returns the send-back, or None when the change goes on to the reviewer (with the
        results kept for the review). A stage unchanged since the last check keeps it.
        """
        todo = self.todo
        if todo is None or not todo.done_when:
            return None
        printed = self._fingerprint()
        if not self._done or printed is None or printed != self._done_print:
            self.todo_event("checking")
            self.gateway.progress("Checking the done-when items…")
            job = asyncio.ensure_future((self.verifier or _verify)(self))
            self._verifying = job
            try:
                await asyncio.wait({job})
            finally:
                if self._verifying is job:
                    self._verifying = None
            if job.cancelled():
                return self._stopped()
            error = job.exception()
            if error is not None:
                if not isinstance(error, (OSError, RuntimeError, ValueError)):
                    raise error
                results = [
                    done_when.Result(
                        c.id,
                        c.text,
                        "inconclusive",
                        f"Joinery couldn't check it: {error}",
                    )
                    for c in todo.done_when
                ]
            else:
                results = job.result()
            self._done, self._done_print = results, printed
            self.todo_event(
                "checked",
                results=[r.as_dict() for r in results],
                summary=done_when.counts_line(results),
            )
        if self.state is not TurnState.SUBMITTING:
            return self._stopped()
        bad = done_when.failed(self._done)
        if not bad or self.check_rounds >= MAX_CHECK_ROUNDS or self._last_chance:
            return None
        # A send-back costs no submit attempt: its rounds are bounded on their own.
        self.check_rounds += 1
        self.attempts -= 1
        self._to(TurnState.WORKING)
        self._send_back("done_when")
        self.todo_event("fixing")
        self.gateway.progress("Fixing what the done-when checks found…")
        return self._result(
            "failed",
            False,
            changes=changes,
            problems=[Problem("done_when", f"{r.text}: {r.evidence}") for r in bad],
            next=NEXT_DONE_WHEN,
        )

    def edited_live(self, scene_path: str) -> bool:
        """``scene_path`` was changed live this turn, so it can't also be staged."""
        return _res(scene_path) in self.live_scenes

    def idle(self) -> bool:
        """Nothing running and nothing on screen for the user."""
        return (
            self.state is TurnState.IDLE
            and not self.review_open
            and not (self.live_edits)
        )

    def admit_live_edit(self, editor_context: Mapping[str, str]) -> str:
        """Allow one live edit of the scene on screen, or raise why not. Returns the
        scene. That scene always routes live (:func:`scene_route`), saved or not.

        One agent changes the scene on screen at a time (ADR 0036): the first live
        edit makes this turn its owner until the user decides the preview. A task
        may only once the main request is idle.
        """
        self.admit_mutation()
        if self.gateway.is_lane and not self.gateway.lead_idle():
            raise ValueError(LEAD_BUSY)
        holder = self.gateway.claims.claim_live(self.gateway.task_id)
        if holder:
            raise ValueError(live_refusal(holder))
        scene = editor_context.get("scene_path", "")
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

    def record_live_edit(
        self, scene: str, ok: bool, payload: Mapping[str, Any] | None = None
    ) -> None:
        if ok:
            from joinery.complex_models import live_shapes

            self.live_scenes.add(_res(scene))
            self.live_edits += 1
            self.live_shapes += live_shapes(payload or {})
        elif not self.live_edits:
            # Nothing on screen after all: the scene is free again.
            claims = self.gateway.claims
            if claims.live == self.gateway.task_id:
                claims.release_live()

    def expect_live(
        self, request_id: str, scene: str, payload: Mapping[str, Any] | None = None
    ) -> None:
        """A live edit was sent to the editor; :meth:`live_result` records its answer."""
        self._live_requests[request_id] = (scene, payload or {})

    def live_result(self, request_id: str, ok: bool) -> None:
        sent = self._live_requests.pop(request_id, None)
        if sent is not None:
            self.record_live_edit(sent[0], ok, sent[1])

    def live_decided(self) -> None:
        """The user decided the live preview (or it was dropped)."""
        self.live_scenes.clear()
        self.live_edits = 0
        self.live_shapes = []
        if self.review_open and not self.review_id:
            self.review_open = False
            if self.state is TurnState.IN_REVIEW:
                self._to(TurnState.DECIDED)
                self._to(TurnState.IDLE)

    # --- Submitting --------------------------------------------------------------------

    def _send_back(self, by: str) -> None:
        """A change went back to the agent to fix; its round is timed until it
        submits again (or the turn ends)."""
        self.close_send_back()
        self._sent_back = (metrics.Clock(), by)

    def close_send_back(self) -> None:
        sent, self._sent_back = self._sent_back, None
        timing = getattr(self.gateway, "timing", None)
        if sent is None or timing is None:
            return
        clock, by = sent
        timing.trace.add(
            metrics.SEND_BACK,
            "send_back " + by,
            clock.wall,
            clock.end(),
            {
                metrics.SENT_BACK_BY: by,
                metrics.ROUND: self.review_rounds
                if by == "reviewer"
                else self.check_rounds
                if by == "done_when"
                else self.attempts,
            },
        )

    def _record_findings(self, by: str, problems: list[Problem], final: bool) -> None:
        """Which rules a failed submit (or a precheck) found, for ``joinery metrics
        --rules``: rule ids only, never the messages."""
        timing = getattr(self.gateway, "timing", None)
        if timing is None or not problems:
            return
        now = metrics.Clock().wall
        timing.trace.add(
            metrics.FINDINGS,
            "findings " + by,
            now,
            now,
            {
                metrics.FOUND_BY: by,
                metrics.FINAL: final,
                metrics.RULES: ",".join(reasons.rules_of(problems)),
            },
        )

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
        self,
        summary: str,
        origin: Literal["agent", "joinery"] = "agent",
        facts: list[str] | None = None,
    ) -> SubmitResult:
        """Check the turn's change and, when it passes, put it in front of the user.

        Fixable problems leave the stage as it is so the agent can fix them; the
        last allowed attempt, or a problem no fix can pass, drops it. A submit
        whose turn was stopped while its checks ran returns "stopped" and leaves
        the turn that runs now alone. ``facts`` are the agent's few plain words on
        what visibly changed ("door 20% wider"); the review shows them in one line.
        """
        turn = self.turn_id
        if facts is not None or origin == "agent":
            self._facts = [f.strip()[:40] for f in facts or [] if f.strip()][:3]
        result = await self._submit(summary, origin)
        if self.turn_id == turn:
            self.last_result = result
            self._checked = self._fingerprint() if not result.final else None
        return result

    def _fingerprint(self) -> tuple[dict[str, str], int] | None:
        """What the stage and the live edits are now, to tell an unchanged change."""
        tx = self.gateway.transaction
        if tx is None or tx.state != "staging":
            return ({}, self.live_edits)
        try:
            return (inventory(tx.stage), self.live_edits)
        except (OSError, ValueError):
            return None

    def _live_shape_problems(self, *, consume: bool = True) -> list[Problem]:
        """Objects the live edits built from placeholder shapes (shared core).

        Said once: the shapes the author removes live can't be told apart by name,
        so the next submit judges only shapes added after this one. A precheck
        (``consume`` False) says them and leaves them for the submit."""
        from joinery.complex_models import check_live

        if not self.live_shapes:
            return []
        found = check_live(self.gateway.project, self.live_shapes)
        if found is None:
            return []
        if consume:
            self.live_shapes = []
        return [found]

    async def _run_checks(self) -> Validation:
        async with self.gateway.call_lock:
            return await self.gateway.submit_checks(self.live_scenes)

    async def _stop_checks(self) -> None:
        """Stop checks still running, before the stage they read is thrown away."""
        checks, self._checks = self._checks, None
        if checks is not None and not checks.done():
            checks.cancel()
            await asyncio.wait({checks})
        reviewing, self._reviewing = self._reviewing, None
        if reviewing is not None and not reviewing.done():
            reviewing.cancel()
            await asyncio.wait({reviewing})
        verifying, self._verifying = self._verifying, None
        if verifying is not None and not verifying.done():
            verifying.cancel()
            await asyncio.wait({verifying})

    def _stopped(self) -> SubmitResult:
        return self._result(
            "failed", True, problems=[Problem("stopped", "The turn was stopped.")]
        )

    async def _submit(self, summary: str, origin: str) -> SubmitResult:
        self.admit_mutation()
        if not self._staged() and not self.live_edits:
            return self._result("nothing_to_review", False, next=NEXT_NOTHING)
        turn = self.turn_id
        self.attempts += 1
        self.close_send_back()
        self._to(TurnState.SUBMITTING)
        checks = asyncio.create_task(self._run_checks())
        self._checks = checks
        try:
            # The checks run in their own task, so a Stop can end them (cancel)
            # whichever task this submit runs in.
            await asyncio.wait({checks})
        except asyncio.CancelledError:
            await self._stop_checks()
            raise
        if checks.cancelled() or self.turn_id != turn:
            return self._stopped()
        if self._checks is checks:
            self._checks = None
        if self.state is not TurnState.SUBMITTING:
            return self._stopped()
        gate: dict[str, Any] | None = None
        skipped: list[str] = []
        error = checks.exception()
        if error is None:
            checked = checks.result()
            found, problems, gate = checked.changes, checked.problems, checked.gate
            problems = problems + self._live_shape_problems()
            self._passed = {"ran": checked.ran, "skipped": checked.skipped}
            skipped = checked.skipped
        elif isinstance(error, (OSError, RuntimeError, ValueError, TimeoutError)):
            tx = self.gateway.transaction
            found = tx.diff() if tx is not None and tx.state == "staging" else []
            # Joinery's own failure: no change the agent makes can pass it.
            problems = [
                Problem(
                    "checks",
                    f"Joinery could not run its checks: {error}",
                    fixable=False,
                )
            ]
        else:
            await self._drop("failed")
            raise error
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
            # What the checks found, for logs and smoke runs: the editor shows
            # only the status line, the agent gets the same list as its result.
            self.emit(
                {
                    "kind": "check_problems",
                    "attempt": self.attempts,
                    "final": final,
                    "problems": [problem.as_dict() for problem in problems],
                }
            )
            self._record_findings("checks", problems, final)
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
            self._send_back("checks")
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
        sent = await self._check_done(changes)
        if sent is not None or self.turn_id != turn:
            return (
                sent if self.turn_id == turn and sent is not None else self._stopped()
            )
        peer = await self._peer_review(summary, found, skipped)
        if self.turn_id != turn or self.state is not TurnState.SUBMITTING:
            return self._stopped()
        blocks = peer.blocking() if peer is not None else []
        if (
            blocks
            and self.review_rounds < self.max_review_rounds
            and not (self._last_chance)
        ):
            # A send-back costs no submit attempt: the reviewer's rounds are bounded.
            self.review_rounds += 1
            self.attempts -= 1
            self._to(TurnState.WORKING)
            self._send_back("reviewer")
            self.gateway.progress("Fixing what the reviewer found…")
            objections = [
                Problem(
                    "reviewer",
                    note.text,
                    [note.file] if note.file else [],
                    items=[reasons.reviewer(note.text, note.file, note.rule)],
                )
                for note in blocks
            ]
            self._record_findings("reviewer", objections, False)
            return self._result(
                "failed",
                False,
                changes=changes,
                problems=objections,
                next=NEXT_REVIEWER,
            )
        try:
            preview = await self._open(summary, found, origin, peer)
        except Exception as exc:  # noqa: BLE001 - never leave the turn submitting.
            await self._drop("failed")
            return self._result(
                "failed",
                True,
                changes=changes,
                problems=[
                    Problem(
                        "review",
                        f"Joinery could not open the review: {exc}",
                        fixable=False,
                    )
                ],
                next=NEXT_STOP,
            )
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

    async def precheck(self) -> dict[str, Any]:
        """Run the submit's deterministic checks on the change as it is, read-only.

        The same checks and the same findings a submit would return now, without
        the reviewer, without a submit attempt and without a send-back: the turn
        stays where it is and the stage is not dropped, whatever is found. Nothing
        the agent made is changed (``ProjectGateway.precheck_checks``).
        """
        self.admit_mutation()
        if not self._staged() and not self.live_edits:
            return {
                "status": "nothing_to_check",
                "changes": [],
                "problems": [],
                "ran": [],
                "skipped": [],
                "notes": [],
                "attempts_left": max(MAX_SUBMITS - self.attempts, 0),
                "next": NEXT_NOTHING,
            }
        self.prechecks += 1
        notes: list[str] = []
        with self.gateway.span(
            "precheck", "precheck", {metrics.ROUND: self.prechecks}
        ) as attrs:
            try:
                async with self.gateway.call_lock:
                    checked, notes = await self.gateway.precheck_checks(
                        self.live_scenes
                    )
            except (OSError, RuntimeError, ValueError, TimeoutError) as error:
                problem = Problem(
                    "checks",
                    f"Joinery could not run its checks: {error}",
                    fixable=False,
                )
                attrs[metrics.OUTCOME] = "error"
                return {
                    "status": "error",
                    "changes": [],
                    "problems": [problem.as_dict()],
                    "ran": [],
                    "skipped": [],
                    "notes": notes,
                    "attempts_left": max(MAX_SUBMITS - self.attempts, 0),
                    "next": NEXT_PRECHECK_ERROR,
                }
            changes = [
                {"path": c["path"], "change": c["change"]} for c in checked.changes
            ]
            problems = checked.problems + self._live_shape_problems(consume=False)
            if checked.gate is not None:
                status, after = "needs_user", NEXT_PRECHECK_ART
                problems = [
                    Problem("art_gate", str(checked.gate["message"]), fixable=False)
                ]
            elif problems:
                status, after = "problems", NEXT_PRECHECK_FIX
            else:
                status, after = "clean", NEXT_PRECHECK_CLEAN
            attrs[metrics.OUTCOME] = status
        self._record_findings("precheck", problems, False)
        return {
            "status": status,
            "changes": changes,
            "problems": [problem.as_dict() for problem in problems],
            "ran": checked.ran,
            "skipped": checked.skipped,
            "notes": notes,
            "attempts_left": max(MAX_SUBMITS - self.attempts, 0),
            "next": after,
        }

    async def _peer_review(
        self, summary: str, found: list[dict[str, str]], skipped: list[str]
    ) -> PeerReview | None:
        """The reviewer agent's notes on the staged change (None: no reviewer).

        A stage unchanged since the last review keeps that review. The reviewer
        never fails the submit: on a timeout or an error the change opens without
        its notes, and the review says so.
        """
        tx = self.gateway.transaction
        if self.reviewer is None or not found or tx is None:
            return None
        printed = self._fingerprint()
        if (
            self._peer is not None
            and printed is not None
            and printed == self._peer_print
        ):
            return self._peer
        checks = PASSED + (
            "; the project has no GUT tests"
            if "tests" in skipped
            else "; the project's tests passed"
        )
        request = request_for(
            project=self.gateway.project,
            stage=tx.stage,
            request=self.request,
            summary=summary,
            changes=found,
            diff="\n".join(self.gateway.text_diff()),
            checks=checks + ".",
            selection=self.selection,
            round=self.review_rounds + 1,
            previous=tuple(self._peer.blocking()) if self._peer is not None else (),
            risk=change_risk(tx.stage, found),
        )
        # The reviewer's own plan (``ReviewAgent.plan``): skip, light first or main.
        planner = getattr(self.reviewer, "plan", None)
        plan: ReviewPlan = (
            planner(request)
            if callable(planner)
            else ReviewPlan("main", float(self.review_seconds))
        )
        route_attrs = {
            metrics.REVIEW_RISK: request.risk.level,
            metrics.REVIEW_ROUTE: plan.route,
        }
        if plan.route == "skip":
            with self.gateway.span(
                "reviewer",
                "reviewer",
                {
                    metrics.AGENT_NAME: "reviewer",
                    metrics.ROUND: self.review_rounds + 1,
                    "joinery.review.status": "skipped",
                    **route_attrs,
                },
            ):
                pass
            peer = PeerReview("skipped", message=plan.message)
            self._peer, self._peer_print = peer, printed
            return peer
        if getattr(self.reviewer, "active", True):
            self.gateway.progress("Reviewing the change…")
        job: asyncio.Task[PeerReview] = asyncio.ensure_future(self.reviewer(request))
        self._reviewing = job
        with self.gateway.span(
            "reviewer",
            "reviewer",
            {
                metrics.AGENT_NAME: "reviewer",
                metrics.ROUND: self.review_rounds + 1,
                **route_attrs,
            },
        ) as span:
            try:
                done, _ = await asyncio.wait({job}, timeout=plan.seconds + REVIEW_GRACE)
            except asyncio.CancelledError:
                job.cancel()
                await asyncio.wait({job})
                raise
            finally:
                if self._reviewing is job:
                    self._reviewing = None
            span["joinery.review.status"] = (
                "timeout"
                if not done
                else "cancelled"
                if job.cancelled()
                else "error"
                if job.exception() is not None
                else job.result().status
            )
            if done and not job.cancelled() and job.exception() is None:
                result = job.result()
                if result.tier:
                    span[metrics.MODEL_TIER] = result.tier
                if result.model:
                    span[metrics.RESPONSE_MODEL] = result.model
                span[metrics.REVIEW_ESCALATED] = result.escalated
        if not done:
            job.cancel()
            await asyncio.wait({job})
            peer = PeerReview(
                "timeout",
                message="The reviewer ran out of time, so this opened without its notes.",
                elapsed_s=float(plan.seconds + REVIEW_GRACE),
            )
        elif job.cancelled():
            return None
        elif job.exception() is not None:
            peer = PeerReview(
                "error",
                message="The reviewer couldn't run, so this opened without its notes.",
            )
        else:
            peer = job.result()
        async with self.gateway.call_lock:
            # Apps the reviewer's reads started in the stage close again.
            for connection in list(self.gateway.staged.values()):
                await connection.close()
            self.gateway.staged.clear()
        self._peer, self._peer_print = peer, printed
        return peer

    async def _open(
        self,
        summary: str,
        found: list[dict[str, str]],
        origin: str,
        peer: PeerReview | None = None,
    ) -> dict[str, Any] | None:
        """The checked change's ``file_preview``; None for a live edit alone."""
        if not found:
            if self.gateway.transaction is not None:
                await self.gateway.cancel()  # an empty stage beside a live edit
            return None
        notes = [note.as_dict() for note in peer.notes] if peer is not None else []
        reviewer = (
            peer.status_dict(self.review_rounds)
            if peer is not None
            else {"status": "off" if self.reviewer is None else "skipped"}
        )
        tx = self.gateway.transaction
        if tx is not None:
            # Recorded with the change, so `joinery transactions list` shows them.
            tx.review_notes, tx.reviewer = notes, reviewer
        preview = self.gateway.open_review(summary.strip() or self.request)
        # The review panel's notes line reads review_notes and reviewer_status.
        preview["review_notes"] = notes
        preview["reviewer"] = reviewer
        preview["reviewer_status"] = reviewer["status"]
        gate, self.gateway.art_gate = self.gateway.art_gate, None
        if gate is not None:
            preview["art_gate"] = {k: v for k, v in gate.items() if k != "kind"}
        preview["origin"] = origin
        preview["checks"] = self._passed
        if self.todo is not None and self._done:
            preview["done_when"] = [r.as_dict() for r in self._done]
            preview["done_when_summary"] = done_when.counts_line(self._done)
        # Joinery's own measured facts ("Walkable: …") lead; the agent's follow.
        facts = list(dict.fromkeys([*preview.pop("joinery_facts", []), *self._facts]))
        if facts:
            preview["facts"] = facts[:3]
        self.review_id = str(preview["transaction_id"])
        return preview

    def _unchanged_failure(self) -> SubmitResult | None:
        """The last submit's fixable failure, when nothing changed since it."""
        last = self.last_result
        if last is None or last.status != "failed" or last.final:
            return None
        if self._checked is None or self._checked != self._fingerprint():
            return None
        return replace(last)

    async def end(self, ok: bool, *, last: bool = True) -> SubmitResult | None:
        """The provider finished its turn (``Done``).

        A change the agent left without submitting is submitted on its behalf;
        when nothing changed since its own submit failed, that failure stands
        rather than running the same checks again. With ``last`` a fixable
        failure is final too: no one is left to fix it.
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
            unchanged = self._unchanged_failure()
            if (
                unchanged is not None
                and last
                and all(
                    p.check in {"reviewer", "done_when"} for p in unchanged.problems
                )
            ):
                # The reviewer's notes and the done-when results never drop a change:
                # it opens with them.
                unchanged = None
            self._last_chance = last
            try:
                result = unchanged or await self.submit(self.request[:160], "joinery")
            finally:
                self._last_chance = False
            if result.status == "failed" and not result.final and last:
                await self._drop("failed")
                result.final = True
                result.next = NEXT_STOP
            self.last_result = result
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
        metrics.record_decision(self.gateway.project, transaction_id, accept)
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
        await self._stop_checks()
        if self.review_open:
            # The change on screen came from an earlier turn; it stays.
            self._to(TurnState.IN_REVIEW, reason)
            return
        await self._drop(reason)

    async def _drop(self, reason: str) -> None:
        """Throw this turn's change away: the stage and the live preview."""
        await self._stop_checks()
        await self.gateway.cancel()
        self.gateway.art_gate = None
        self.live_scenes.clear()
        self.live_edits = 0
        self.live_shapes = []
        self.review_open = False
        self.review_id = ""
        self._to(TurnState.IDLE, reason)


async def _verify(controller: TurnController) -> list[done_when.Result]:
    """The to-do's done-when items against the change as staged (done_when.verify)."""
    todo = controller.todo
    assert todo is not None
    tx = controller.gateway.transaction
    staged = tx is not None and tx.state == "staging" and bool(tx.diff())
    return await done_when.verify(
        todo,
        project=controller.gateway.project,
        stage=tx.stage if staged and tx is not None else None,
        ran=list(controller._passed.get("ran", [])),
        skipped=list(controller._passed.get("skipped", [])),
    )
