"""Typed live-scene requests users send to Godot; native execution has its own harness."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from joinery.editor_ops import validate_edit

OPERATIONS = [
    pytest.param(
        {
            "operation": "create_node",
            "parent": "@Root",
            "name": "Hammer",
            "node_type": "MeshInstance3D",
            "properties": {
                "mesh": {"resource_type": "BoxMesh", "properties": {"size": [1, 1, 1]}}
            },
        },
        id="create-mesh-resource",
    ),
    pytest.param(
        {
            "operation": "instantiate_scene",
            "parent": "@Root",
            "resource_path": "res://art/models/hammer.glb",
            "name": "Hammer",
        },
        id="place-imported-model",
    ),
    pytest.param(
        {"operation": "delete_node", "target": "@Selected"}, id="delete-selected-object"
    ),
    pytest.param(
        {"operation": "duplicate_node", "target": "@Selected", "name": "SecondHammer"},
        id="duplicate-object",
    ),
    pytest.param(
        {"operation": "rename_node", "target": "@Selected", "name": "PlayerHammer"},
        id="rename-object",
    ),
    pytest.param(
        {"operation": "reparent_node", "target": "@Selected", "parent": "@Root"},
        id="reparent-object",
    ),
    pytest.param(
        {
            "operation": "set_node_properties",
            "target": "@Selected",
            "properties": {
                "stream": {"resource_path": "res://audio/impact.wav"},
                "volume_db": -6,
                "autoplay": False,
            },
        },
        id="configure-audio-node",
    ),
    pytest.param(
        {
            "operation": "attach_script",
            "target": "@Selected",
            "script_path": "res://scripts/hammer.gd",
        },
        id="attach-existing-script",
    ),
    pytest.param(
        {
            "operation": "connect_signal",
            "target": "@Selected",
            "receiver": "@Root",
            "signal_name": "body_entered",
            "method_name": "_on_body_entered",
        },
        id="connect-signal",
    ),
    pytest.param(
        {
            "operation": "disconnect_signal",
            "target": "@Selected",
            "receiver": "@Root",
            "signal_name": "body_entered",
            "method_name": "_on_body_entered",
        },
        id="disconnect-signal",
    ),
    pytest.param(
        {
            "operation": "set_transform",
            "target": "@Selected",
            "position": [1, 2, 3],
            "rotation_degrees": [0, 90, 0],
            "relative": True,
        },
        id="move-rotate-relative",
    ),
]


@pytest.fixture
def game(tmp_path: Path) -> Path:
    (tmp_path / "joinery.toml").write_text('[tools]\nenabled=["godot"]\n')
    return tmp_path


@pytest.mark.parametrize("operation", OPERATIONS)
def test_common_scene_requests_keep_target_and_resource_information(
    game: Path, operation: dict
) -> None:
    edit = validate_edit(
        game,
        {"summary": "Requested scene edit", "operations": [operation]},
        ("@Root", "@Selected"),
    )
    actual = edit.operations[0].model_dump(mode="json", exclude_none=True)
    for key, value in operation.items():
        assert actual[key] == value
    assert edit.summary == "Requested scene edit"


@pytest.mark.parametrize("operation", OPERATIONS)
def test_stale_context_cannot_redirect_a_scene_operation(
    game: Path, operation: dict
) -> None:
    with pytest.raises(ValueError, match="selected|current editor context"):
        validate_edit(
            game, {"summary": "Old context", "operations": [operation]}, ("@Other",)
        )


@pytest.mark.parametrize(
    "operation",
    [
        {"operation": "instantiate_scene", "resource_path": "res://../outside.glb"},
        {"operation": "instantiate_scene", "resource_path": "/outside.glb"},
        {
            "operation": "attach_script",
            "target": "@Selected",
            "script_path": "res://../outside.gd",
        },
        {
            "operation": "attach_script",
            "target": "@Selected",
            "script_path": "res://scripts/code.cs",
        },
        {"operation": "rename_node", "target": "@Selected", "name": ""},
        {"operation": "rename_node", "target": "@Selected", "name": "bad/name"},
        {"operation": "set_node_properties", "target": "@Selected", "properties": {}},
        *[
            {
                "operation": "set_node_properties",
                "target": "@Selected",
                "properties": {key: "unreviewed"},
            }
            for key in ("script", "owner", "name")
        ],
        *[
            {"operation": "connect_signal", "target": "@Selected", **fields}
            for fields in (
                {"receiver": "@Root"},
                {"signal_name": "finished"},
                {"method_name": "_on_finished"},
            )
        ],
        *[
            {
                "operation": "set_node_properties",
                "target": "@Selected",
                "properties": {"material": {"properties": {"roughness": value}}},
            }
            for value in (float("nan"), float("inf"), -float("inf"))
        ],
    ],
)
def test_invalid_scene_resource_and_signal_requests_fail_before_editor_execution(
    game: Path, operation: dict
) -> None:
    with pytest.raises(ValidationError):
        validate_edit(
            game,
            {"summary": "Malformed request", "operations": [operation]},
            ("@Root", "@Selected"),
        )


def test_large_mixed_scene_request_preserves_operation_order(game: Path) -> None:
    operations = [
        {
            "operation": "create_node",
            "name": "Impact",
            "node_type": "AudioStreamPlayer3D",
        },
        {"operation": "set_transform", "target": "@Selected", "position": [1, 0, 0]},
        {"operation": "rename_node", "target": "@Selected", "name": "Hammer"},
    ]
    edit = validate_edit(
        game,
        {"summary": "Move, name and prepare audio", "operations": operations},
        ("@Selected",),
    )
    assert [op.operation for op in edit.operations] == [
        "create_node",
        "set_transform",
        "rename_node",
    ]


def test_scene_request_cannot_bypass_batch_limit(game: Path) -> None:
    with pytest.raises(ValidationError):
        validate_edit(
            game,
            {
                "summary": "Too many nodes",
                "operations": [{"operation": "create_box"}] * 33,
            },
            (),
        )
