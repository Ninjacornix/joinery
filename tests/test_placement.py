"""A model the request puts somewhere is placed in the same change (shared core)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from joinery import placement
from joinery.turn import MAX_SUBMITS, TurnState
from tests.test_submit_for_review import SCENE, Session

NODES = json.dumps(
    [
        {"id": "res://kitchen.tscn::.", "name": "Kitchen", "type": "Node3D"},
        {"id": "res://kitchen.tscn::Table", "name": "Table", "type": "Node3D"},
        {"id": "res://kitchen.tscn::Camera3D", "name": "Camera3D", "type": "Camera3D"},
        {
            "id": "res://kitchen.tscn::KitchenShelf",
            "name": "KitchenShelf",
            "type": "Node3D",
        },
    ]
)
TABLE = "res://kitchen.tscn::Table"


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    return Session(tmp_path, monkeypatch)


# --- reading the request -------------------------------------------------------------


@pytest.mark.parametrize(
    ("request_text", "words", "name"),
    [
        ("Create a mug and put it on the table", "on the table", "Table"),
        (
            "make a lamp and place it next to the shelf",
            "next to the shelf",
            "KitchenShelf",
        ),
        ("Add a crate to the courtyard", "to the courtyard", "courtyard"),
        (
            "model a vase, then set it on top of the Table",
            "on top of the Table",
            "Table",
        ),
        ("hang a painting above the bed", "above the bed", "bed"),
    ],
)
def test_placement_words_name_where_it_goes(
    request_text: str, words: str, name: str
) -> None:
    nodes = placement.scene_nodes({"nodes": NODES})
    found = placement.intent(request_text, nodes, own=["mug", "lamp"])
    assert found is not None
    assert (found.words, found.name) == (words, name)


def test_a_named_node_or_scene_is_placement_intent() -> None:
    nodes = placement.scene_nodes({"nodes": NODES})
    found = placement.intent("A mug for the table, please", nodes)
    assert found == placement.Intent("", "Table", TABLE)
    assert placement.intent("a chair for the kitchen", [], ["kitchen"]) == (
        placement.Intent("", "kitchen")
    )


@pytest.mark.parametrize(
    "request_text",
    [
        "Make a mug",
        "Make a mug in Blender",
        "Model a mug at 10 cm tall",
        "Make a mug for the main menu",  # the scene's root name is too common
        "Put a camera3d on it",  # a node named after its class is no place
    ],
)
def test_no_placement_intent(request_text: str) -> None:
    nodes = placement.scene_nodes({"nodes": NODES})
    assert placement.intent(request_text, nodes, ["main"]) is None


@pytest.mark.parametrize(
    "request_text",
    [
        "Make a mug but don't put it on the table yet",
        "Make a mug, do not place it",
        "Just the model, not in the scene",
        "A mug model only",
    ],
)
def test_the_request_can_keep_it_out(request_text: str) -> None:
    assert placement.keeps_out(request_text)


def test_only_new_models_count_not_kit_parts_lods_or_sources() -> None:
    changes = [
        {"path": "art/models/mug.glb", "before": "", "after": "a"},
        {"path": "art/models/mug.blend", "before": "", "after": "a"},
        {"path": "art/models/mug_lod1.glb", "before": "", "after": "a"},
        {"path": "kits/blender/cup.glb", "before": "", "after": "a"},
        {"path": "art/models/old.glb", "before": "x", "after": "a"},
        {"path": "art/props/mug.tscn", "before": "", "after": "a"},
        {"path": "levels/kitchen.tscn", "before": "", "after": "a"},
    ]
    assert placement.new_models(changes) == ["art/models/mug.glb", "art/props/mug.tscn"]


def test_a_model_used_by_a_staged_scene_or_a_live_edit_is_placed(
    tmp_path: Path,
) -> None:
    (tmp_path / "art/models").mkdir(parents=True)
    (tmp_path / "scenes").mkdir()
    (tmp_path / "scenes/ashtray.tscn").write_text(SCENE)
    changes = [
        {"path": "art/models/ashtray.glb", "before": "", "after": "a"},
        {"path": "art/models/mug.glb", "before": "", "after": "a"},
        {"path": "art/models/cup.glb", "before": "", "after": "a"},
    ]
    assert placement.unplaced(tmp_path, changes, ["art/models/cup.glb"]) == [
        "art/models/mug.glb"
    ]


# --- precheck and submit ---------------------------------------------------------------


def _mug(session: Session, request: str) -> None:
    session.gateway.editor_context = {
        "scene_path": "res://kitchen.tscn",
        "nodes": NODES,
    }
    session.turn.begin(request)
    session.stage({"art/models/mug.glb": "glTF"})


def test_an_unplaced_model_the_request_places_is_sent_back_once(
    session: Session,
) -> None:
    _mug(session, "Create a mug and put it on the table")

    async def go() -> tuple[dict[str, Any], Any, Any]:
        checked = await session.turn.precheck()
        first = await session.turn.submit("A mug")
        second = await session.turn.submit("A mug")
        return checked, first, second

    checked, first, second = session.run(go)
    assert checked["status"] == "problems"
    (problem,) = checked["problems"]
    assert problem["check"] == "placement"
    assert problem["message"] == (
        "mug.glb isn't in any scene yet; the request puts it on the table. "
        f"Instance it on Table with edit_scene instantiate_scene and on: {TABLE}."
    )
    (item,) = problem["items"]
    assert item["rule"] == "placement.unplaced_model"
    assert item["file"] == "art/models/mug.glb" and item["node"] == "Table"
    assert item["expected"] == "placed on the table"
    # The precheck said it without using it up; the submit sends it back.
    assert first.status == "failed" and not first.final
    assert [p.check for p in first.problems] == ["placement"]
    # Said once: the next submit opens with a note, so the mug is never lost.
    assert second.status == "in_review"
    assert second.notes and "mug.glb isn't in any scene yet" in second.notes[0]
    assert second.as_tool_result()["notes"] == second.notes


def test_a_model_placed_live_passes(session: Session) -> None:
    _mug(session, "Create a mug and put it on the table")
    session.turn.record_live_edit(
        "res://kitchen.tscn",
        True,
        {
            "summary": "Mug on the table",
            "operations": [
                {
                    "operation": "instantiate_scene",
                    "resource_path": "res://art/models/mug.glb",
                    "on": TABLE,
                }
            ],
        },
    )
    result = session.run(lambda: session.turn.submit("A mug on the table"))
    assert result.status == "in_review" and result.notes == []
    assert "notes" not in result.as_tool_result()


def test_without_placement_intent_it_is_a_note(session: Session) -> None:
    _mug(session, "Make a mug")
    checked = session.run(session.turn.precheck)
    assert checked["status"] == "clean" and checked["problems"] == []
    assert checked["notes"] == [
        ("mug.glb isn't in any scene yet. Say in your reply where it is or why it "
        "stays unplaced; place it only if the user asked.")
    ]


def test_a_request_that_keeps_it_out_says_nothing(session: Session) -> None:
    _mug(session, "Make a mug for the table but don't place it yet")
    result = session.run(lambda: session.turn.submit("A mug"))
    assert result.status == "in_review" and result.notes == []


def test_joinery_s_own_last_submit_never_drops_the_model(session: Session) -> None:
    _mug(session, "Create a mug and put it on the table")
    result = session.run(lambda: session.turn.end(True))
    assert result is not None and result.status == "in_review"
    assert result.notes and session.turn.state is TurnState.IN_REVIEW


def test_the_last_attempt_turns_it_into_a_note(session: Session) -> None:
    _mug(session, "Create a mug and put it on the table")
    session.turn.attempts = MAX_SUBMITS - 1
    result = session.run(lambda: session.turn.submit("A mug"))
    assert result.status == "in_review" and result.notes
