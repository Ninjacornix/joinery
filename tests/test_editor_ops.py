from pathlib import Path

import pytest
from pydantic import ValidationError

from joinery.editor_ops import NodeOperation, validate_edit


@pytest.fixture
def game(tmp_path: Path) -> Path:
    (tmp_path / "joinery.toml").write_text('[tools]\nenabled=["godot"]\n')
    return tmp_path


def test_plain_godot_box(game: Path) -> None:
    edit = validate_edit(
        game,
        {
            "summary": "Box",
            "operations": [{"operation": "create_box", "size": [1, 1, 1]}],
        },
        (),
    )
    assert edit.operations[0].size == (1, 1, 1)


@pytest.mark.parametrize(
    "op",
    [
        {"operation": "execute_script", "code": "whatever"},
        {"operation": "create_box", "size": [1, -1, 1]},
        {"operation": "create_box", "size": [1, 1, float("nan")]},
        {"operation": "create_box", "name": "../escape"},
        {"operation": "create_box", "size": [1, 1, 1], "script": "bad"},
        {"operation": "set_transform", "target": "@Door", "scale": [0, 1, 1]},
        {"operation": "set_transform", "target": "@Door"},
    ],
)
def test_invalid_operations_fail_closed(game: Path, op: dict) -> None:
    with pytest.raises(ValidationError):
        validate_edit(game, {"summary": "Bad", "operations": [op]}, ("@Door",))


def test_target_binding(game: Path) -> None:
    args = {
        "summary": "Move",
        "operations": [
            {"operation": "set_transform", "target": "@Door", "position": [1, 0, 0]}
        ],
    }
    with pytest.raises(ValueError, match="selected"):
        validate_edit(game, args, ("@Window",))
    assert validate_edit(game, args, ("@Door",))


def test_respects_shared_project_gate(
    game: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from joinery.policy import Severity, Verdict

    seen = []

    def deny(server: str, tool: str, project: Path) -> Verdict:
        seen.append((server, tool, project))
        return Verdict(Severity.DENY, "Denied by the shared policy")

    monkeypatch.setattr("joinery.editor_ops.mcp_violation", deny)
    with pytest.raises(ValueError, match="shared policy"):
        validate_edit(
            game, {"summary": "Box", "operations": [{"operation": "create_box"}]}, ()
        )
    assert seen == [("godot", "scene_edit", game)]


MUG = {"operation": "instantiate_scene", "resource_path": "res://art/models/mug.glb"}


def test_on_stands_a_new_instance_on_a_node(game: Path) -> None:
    table = "res://kitchen.tscn::Table"
    edit = validate_edit(
        game,
        {
            "summary": "Mug on the table",
            "operations": [{**MUG, "on": table, "at": [0.2, -0.1], "as_child": True}],
        },
        (table,),
    )
    (op,) = edit.operations
    assert isinstance(op, NodeOperation)
    assert op.on == table and op.at == (0.2, -0.1) and op.as_child
    with pytest.raises(ValueError, match="current editor context"):
        validate_edit(
            game, {"summary": "Mug", "operations": [{**MUG, "on": table}]}, ()
        )


@pytest.mark.parametrize(
    "op",
    [
        {**MUG, "at": [0, 0]},
        {**MUG, "as_child": True},
        {**MUG, "on": "@Table", "replace": "@Table"},
        {"operation": "create_node", "on": "@Table"},
        {**MUG, "on": "@Table", "at": [0, 1e9]},
    ],
)
def test_on_is_only_for_placing_an_instance(game: Path, op: dict) -> None:
    with pytest.raises(ValidationError):
        validate_edit(game, {"summary": "Bad", "operations": [op]}, ("@Table",))
