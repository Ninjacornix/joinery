"""Joinery's turn lifecycle: states, scene routing and the context meter."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from joinery.claims import PathClaims
from joinery.submission import Validation
from joinery.turn import (
    ContextMeter,
    Problem,
    SubmitResult,
    TurnController,
    TurnState,
    allowed,
    scene_route,
)

UNSAVED = {"unsaved_scenes": json.dumps(["res://levels/room.tscn"])}


@pytest.mark.parametrize(
    ("scene", "context", "route"),
    [
        ("", UNSAVED, "live"),
        ("res://", {}, "live"),
        ("res://levels/room.tscn", UNSAVED, "live"),
        ("levels/room.tscn", UNSAVED, "live"),
        ("res://levels/hall.tscn", UNSAVED, "staged"),
        ("res://levels/room.tscn", {}, "staged"),
        ("res://levels/room.tscn", {"unsaved_scenes": "not json"}, "staged"),
        ("res://levels/room.tscn", {"scene_path": "res://levels/room.tscn"}, "live"),
        ("levels/room.tscn", {"scene_path": "res://levels/room.tscn"}, "live"),
        ("res://levels/hall.tscn", {"scene_path": "res://levels/room.tscn"}, "staged"),
    ],
)
def test_scene_route(scene: str, context: dict[str, str], route: str) -> None:
    assert scene_route(scene, context) == route


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (TurnState.IDLE, TurnState.WORKING),
        (TurnState.IDLE, TurnState.COMPACTING),
        (TurnState.WORKING, TurnState.SUBMITTING),
        (TurnState.WORKING, TurnState.IDLE),
        (TurnState.SUBMITTING, TurnState.IN_REVIEW),
        (TurnState.SUBMITTING, TurnState.WORKING),
        (TurnState.SUBMITTING, TurnState.IDLE),
        (TurnState.IN_REVIEW, TurnState.DECIDED),
        (TurnState.IN_REVIEW, TurnState.WORKING),
        (TurnState.DECIDED, TurnState.IDLE),
        (TurnState.COMPACTING, TurnState.IDLE),
    ],
)
def test_allowed_transitions(source: TurnState, target: TurnState) -> None:
    assert allowed(source, target)


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (TurnState.IDLE, TurnState.IN_REVIEW),
        (TurnState.IDLE, TurnState.SUBMITTING),
        (TurnState.WORKING, TurnState.DECIDED),
        (TurnState.DECIDED, TurnState.WORKING),
        (TurnState.COMPACTING, TurnState.WORKING),
        (TurnState.SUBMITTING, TurnState.DECIDED),
    ],
)
def test_illegal_transitions(source: TurnState, target: TurnState) -> None:
    assert not allowed(source, target)


def test_context_meter_triggers_near_the_window() -> None:
    meter = ContextMeter()
    meter.update(900_000, 0)
    assert not meter.needs_compaction(), "An unknown window never triggers"
    meter.update(0, 1_000_000)
    assert meter.used == 900_000 and meter.needs_compaction()
    meter.update(700_000, 0)
    assert not meter.needs_compaction()
    meter.update(800_000, 0)
    assert meter.needs_compaction()
    meter.reset()
    assert not meter.needs_compaction()


def test_submit_result_reads_as_a_tool_result() -> None:
    result = SubmitResult(
        status="failed",
        attempt=1,
        attempts_left=2,
        final=False,
        problems=[Problem("references", "room.tscn uses crate.glb", ["room.tscn"])],
        next="Fix each problem, then call submit_for_review again.",
    )
    data = result.as_tool_result()
    assert data["status"] == "failed" and data["attempts_left"] == 2
    assert data["problems"] == [
        {
            "check": "references",
            "message": "room.tscn uses crate.glb",
            "paths": ["room.tscn"],
            "items": [
                {
                    "rule": "references.missing_file",
                    "file": "room.tscn",
                    "node": "",
                    "measured": "room.tscn uses crate.glb",
                    "expected": "every file a scene loads exists in the project or "
                    "in this change",
                    "fix": "Make the missing file in this change, or point the scene "
                    "at one that exists.",
                }
            ],
        }
    ]


class FakeGateway:
    """Just enough gateway for the controller's own bookkeeping."""

    def __init__(self) -> None:
        self.transaction: Any = None
        self.art_gate: dict[str, Any] | None = None
        self.turn: Any = None
        self.call_lock = asyncio.Lock()
        self.cancelled = 0
        self.progress: Any = lambda _text: None
        self.claims = PathClaims()
        self.is_lane = False
        self.task_id = "main"

    async def submit_checks(self, _live: set[str]) -> Validation:
        return Validation()

    async def cancel(self) -> None:
        self.cancelled += 1
        self.transaction = None

    def start_media(self, _emit: Any) -> None:
        pass


def controller() -> tuple[TurnController, list[dict[str, Any]], FakeGateway]:
    events: list[dict[str, Any]] = []
    gateway = FakeGateway()
    return TurnController(gateway, events.append), events, gateway  # type: ignore[arg-type]


def states(events: list[dict[str, Any]]) -> list[str]:
    return [e["state"] for e in events if e["kind"] == "turn_state"]


def test_every_transition_is_announced() -> None:
    turn, events, gateway = controller()
    assert gateway.turn is turn
    turn.begin("Rename the lamp")
    turn.record_live_edit("", True)
    result = asyncio.run(turn.end(True))
    assert result is not None and result.status == "in_review"
    assert states(events) == ["working", "submitting", "in_review"]
    assert all(e["turn_id"] == turn.turn_id for e in events)
    turn.live_decided()
    assert states(events)[-2:] == ["decided", "idle"]


def test_an_illegal_transition_raises() -> None:
    turn, _events, _gateway = controller()
    with pytest.raises(RuntimeError, match="cannot go from idle to in_review"):
        turn._to(TurnState.IN_REVIEW)


def test_mutations_are_refused_outside_working() -> None:
    turn, _events, _gateway = controller()
    with pytest.raises(ValueError, match="active user request"):
        turn.admit_mutation()
    turn.begin_compaction()
    with pytest.raises(ValueError, match="summarising"):
        turn.admit_mutation()
    turn.end_compaction()
    turn.begin("Rename")
    turn.admit_mutation()


def test_a_request_while_a_review_is_open_only_looks() -> None:
    turn, events, _gateway = controller()
    turn.begin("Rename the lamp")
    turn.record_live_edit("", True)
    asyncio.run(turn.end(True))
    turn.begin("What else is in the room?")
    with pytest.raises(ValueError, match="Accept or reject the change on screen"):
        turn.admit_mutation()
    asyncio.run(turn.end(True))
    assert turn.state is TurnState.IN_REVIEW
    asyncio.run(turn.cancel())
    assert turn.state is TurnState.IN_REVIEW, "A stop keeps the open review"
    assert states(events)[-2:] == ["working", "in_review"]


def test_the_scene_on_screen_is_edited_live_saved_or_not() -> None:
    turn, _events, _gateway = controller()
    turn.begin("Rename the lamp")
    assert turn.admit_live_edit({"scene_path": "res://room.tscn"}) == "res://room.tscn"
    unsaved = {"scene_path": "res://levels/room.tscn", **UNSAVED}
    assert turn.admit_live_edit(unsaved) == "res://levels/room.tscn"
    assert turn.admit_live_edit({"scene_path": ""}) == ""


def test_a_failed_turn_drops_its_change() -> None:
    turn, events, gateway = controller()
    turn.begin("Rename the lamp")
    turn.record_live_edit("", True)
    asyncio.run(turn.end(False))
    assert gateway.cancelled == 1 and turn.live_edits == 0
    assert events[-1]["state"] == "idle" and events[-1]["reason"] == "failed"
