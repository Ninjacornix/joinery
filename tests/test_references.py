"""A scene and the files it loads are staged, decided and reverted together."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from joinery import references
from joinery.cli import main
from joinery.gateway import ProjectGateway
from joinery.runner import Result
from joinery.transactions import FileTransaction

SCENE = """[gd_scene load_steps=2 format=3 uid="uid://bashtrayscene"]

[ext_resource type="PackedScene" path="res://art/models/ashtray.glb" id="1_model"]

[node name="Ashtray" type="Node3D"]

[node name="Model" parent="." instance=ExtResource("1_model")]
"""


def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    (tmp_path / "joinery.toml").write_text('[tools]\nenabled=["godot"]\n')
    monkeypatch.setattr("joinery.gateway.configured", lambda _: ([], []))
    monkeypatch.setattr("joinery.gateway.evidence", AsyncMock(return_value=[]))
    monkeypatch.setattr("joinery.gateway.approved", lambda _project: True)
    monkeypatch.setattr("joinery.gateway._approved_in", lambda *_: True)
    monkeypatch.setattr("joinery.gateway.check_assets", lambda *_: [])

    async def offline(_argv: list[str], _root: Path, *_a: Any, **_k: Any) -> Result:
        return Result(True, "", "", 0.0)

    monkeypatch.setattr("joinery.gateway.run_offline", offline)
    return tmp_path


def finish(root: Path, stage: dict[str, str]) -> tuple[ProjectGateway, FileTransaction]:
    gateway = ProjectGateway(root)
    tx = FileTransaction(root)
    tx.begin("Ashtray")
    gateway.transaction = tx
    for path, text in stage.items():
        (tx.stage / path).parent.mkdir(parents=True, exist_ok=True)
        (tx.stage / path).write_text(text)
    return gateway, tx


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_references_read_paths_and_uids() -> None:
    text = (
        SCENE + '[ext_resource type="Script" uid="uid://bx" path="res://a.gd" id="2"]\n'
    )
    assert references.references(text) == [
        ("art/models/ashtray.glb", ""),
        ("a.gd", "uid://bx"),
    ]
    assert references.references('[ext_resource path="res://../x.glb" id="1"]') == []


def test_a_scene_without_its_model_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path, monkeypatch)
    gateway, _tx = finish(root, {"scenes/props/ashtray.tscn": SCENE})
    with pytest.raises(ValueError) as error:
        run(gateway.finish())
    message = str(error.value)
    assert "scenes/props/ashtray.tscn uses art/models/ashtray.glb" in message
    assert "same change" in message and "Nothing was staged" in message
    run(gateway.close())


def test_a_scene_staged_with_its_model_reaches_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path, monkeypatch)
    gateway, _tx = finish(
        root,
        {"scenes/props/ashtray.tscn": SCENE, "art/models/ashtray.glb": "glTF"},
    )
    preview = run(gateway.finish())
    assert preview is not None and preview["kind"] == "file_preview"
    run(gateway.close())


def test_a_model_already_in_the_project_is_fine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path, monkeypatch)
    (root / "art/models").mkdir(parents=True)
    (root / "art/models/ashtray.glb").write_text("glTF")
    gateway, _tx = finish(root, {"scenes/props/ashtray.tscn": SCENE})
    assert run(gateway.finish())["kind"] == "file_preview"
    run(gateway.close())


def test_a_moved_file_found_by_its_uid_is_not_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path, monkeypatch)
    (root / "art").mkdir()
    (root / "art/ashtray.glb.import").write_text(
        '[remap]\nimporter="scene"\nuid="uid://bglb"\n'
    )
    scene = SCENE.replace(
        'path="res://art/models', 'uid="uid://bglb" path="res://art/models'
    )
    gateway, _tx = finish(root, {"scenes/props/ashtray.tscn": scene})
    assert run(gateway.finish())["kind"] == "file_preview"
    run(gateway.close())


def test_deleting_a_model_a_scene_still_uses_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path, monkeypatch)
    (root / "art/models").mkdir(parents=True)
    (root / "art/models/ashtray.glb").write_text("glTF")
    (root / "room.tscn").write_text(SCENE)
    gateway, tx = finish(root, {})
    (tx.stage / "art/models/ashtray.glb").unlink()
    with pytest.raises(ValueError, match="room.tscn uses art/models/ashtray.glb"):
        run(gateway.finish())
    run(gateway.close())


def test_discarding_says_which_accepted_scenes_lose_a_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path, monkeypatch)
    # A scene the user saved meanwhile already points at the staged model.
    (root / "room.tscn").write_text(SCENE)
    gateway, tx = finish(root, {"art/models/ashtray.glb": "glTF"})
    tx.review()
    decision = run(gateway.decide(tx.id, False))
    assert "room.tscn still uses art/models/ashtray.glb" in decision["message"]
    assert "only the discarded change would have added" in decision["message"]
    run(gateway.close())


def test_discarding_an_unrelated_change_says_nothing_more(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path, monkeypatch)
    gateway, tx = finish(root, {"art/models/ashtray.glb": "glTF"})
    tx.review()
    assert "message" not in run(gateway.decide(tx.id, False))
    run(gateway.close())


def test_cli_discard_and_revert_carry_the_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    tx = FileTransaction(tmp_path)
    tx.begin("Ashtray")
    (tx.stage / "art").mkdir()
    (tx.stage / "art/ashtray.glb").write_text("glTF")
    tx.review()
    (tmp_path / "room.tscn").write_text(SCENE.replace("models/", ""))

    def cli(*args: str) -> dict[str, Any]:
        main(["transactions", *args])
        result: dict[str, Any] = json.loads(capsys.readouterr().out)
        return result

    rejected = cli("reject", "--project", str(tmp_path), "--id", tx.id)
    assert "room.tscn still uses art/ashtray.glb" in rejected["warning"]

    again = FileTransaction(tmp_path)
    again.begin("Ashtray again")
    (again.stage / "art").mkdir(exist_ok=True)
    (again.stage / "art/ashtray.glb").write_text("glTF")
    again.review()
    again.accept()
    reverted = cli("revert", "--project", str(tmp_path), "--id", again.id)
    assert "the reverted change had added" in reverted["transactions"][0]["warning"]


def test_every_agent_is_told_to_stage_a_model_with_its_scene() -> None:
    from bridge.instructions import GAME_GUIDE

    assert (
        "Stage a model or texture and the scene that uses it in one change"
        in GAME_GUIDE
    )
