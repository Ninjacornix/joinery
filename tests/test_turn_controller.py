"""Joinery's turn lifecycle: states, scene routing and the context meter."""

from __future__ import annotations

import json

import pytest

from joinery.turn import (
    ContextMeter,
    Problem,
    SubmitResult,
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
        }
    ]
