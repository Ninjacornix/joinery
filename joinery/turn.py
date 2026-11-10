"""One agent's turn, owned by Joinery rather than by the provider.

Every provider (Codex, Claude Code, Gemini CLI, opencode) runs its own model
loop, but the turn around it is the same: the agent works on one staged change,
hands it over with ``submit_for_review``, Joinery checks it, and the user
decides. The states here are that lifecycle; nothing in this module knows which
provider is running.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal


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
