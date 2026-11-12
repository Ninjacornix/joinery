"""A model made in a change is placed in the same change (ADR 0054).

The stage starts without Godot's import cache and the editor's res:// is the
project, so a model exported into the stage could be neither loaded by the
live editor nor instanced by the staged Godot tools until a second change.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from bridge.instructions import GAME_GUIDE, REVIEW_GUIDE
from bridge.protocol import Done, EditorAction, Message
from joinery import staged_assets
from joinery.editor_ops import SceneEdit, validate_edit
from joinery.gateway import LIVE_SCENE, ProjectGateway
from joinery.runner import Result
from joinery.transactions import FileTransaction
from joinery.turn import NEXT_REVIEW
from tests.test_turn_runner import Host

MODEL = "art/models/crt_tv.glb"
#: Words that tell the user about Joinery's insides instead of what was made.
INTERNAL = ("blocked", "staged", "stage ", "live editor", "can't load", "cannot load")


def fake_import(root: Path) -> None:
    """What Godot's import writes for each model in ``root`` (offline worker)."""
    for model in root.rglob("*.glb"):
        relative = model.relative_to(root)
        if relative.parts[0].startswith("."):
            continue
        res = "res://" + relative.as_posix()
        name = f"{model.name}-{hashlib.md5(res.encode()).hexdigest()}"
        imported = root / ".godot/imported"
        imported.mkdir(parents=True, exist_ok=True)
        (imported / (name + ".scn")).write_bytes(b"RSCC imported " + model.read_bytes())
        source = hashlib.md5(model.read_bytes()).hexdigest()
        (imported / (name + ".md5")).write_text(
            f'source_md5="{source}"\ndest_md5="0"\n'
        )
        model.with_name(model.name + ".import").write_text(
            '[remap]\n\nimporter="scene"\ntype="PackedScene"\n'
            f'path="res://.godot/imported/{name}.scn"\n\n[params]\n\n'
            "gltf/embedded_image_handling=3\n"
        )


class Importer:
    """Stands in for ``godot --import`` in the offline worker."""

    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.runs = 0

    async def __call__(
        self, argv: list[str], root: Path, *_a: Any, **_k: Any
    ) -> Result:
        assert "--import" in argv
        self.runs += 1
        if not self.ok:
            return Result(False, "", "ERROR: glTF: invalid buffer", 0.0)
        fake_import(root)
        return Result(True, "", "", 0.0)


def project(root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (root / "project.godot").write_text("config_version=5\n")
    (root / "joinery.toml").write_text('[tools]\nenabled=["godot"]\n')
    monkeypatch.setattr("joinery.gateway.configured", lambda _: ([], []))
    return root


def staged(gateway: ProjectGateway, files: dict[str, bytes]) -> FileTransaction:
    tx = FileTransaction(gateway.project)
    tx.begin("CRT TV")
    gateway.transaction = tx
    for path, data in files.items():
        (tx.stage / path).parent.mkdir(parents=True, exist_ok=True)
        (tx.stage / path).write_bytes(data)
    return tx


def place(path: str = "res://" + MODEL) -> dict[str, Any]:
    return {
        "summary": "Replace the placeholder TV",
        "operations": [
            {
                "operation": "instantiate_scene",
                "resource_path": path,
                "replace": "res://room.tscn::TV",
                "fit_size": True,
            }
        ],
    }


# --- Finding the stage's imported copy ------------------------------------------------


def test_the_imported_copy_is_found_and_goes_stale_when_the_model_changes(
    tmp_path: Path,
) -> None:
    model = tmp_path / MODEL
    model.parent.mkdir(parents=True)
    model.write_bytes(b"glTF one")
    assert staged_assets.imported_file(tmp_path, MODEL) is None
    assert staged_assets.needs_import(tmp_path, [MODEL]) == [MODEL]
    fake_import(tmp_path)
    found = staged_assets.imported_file(tmp_path, MODEL)
    assert found is not None and found.suffix == ".scn"
    assert found.is_relative_to(tmp_path / ".godot/imported")
    assert staged_assets.needs_import(tmp_path, [MODEL]) == []
    # Blender exported again: the import record no longer matches the model.
    model.write_bytes(b"glTF two")
    assert staged_assets.imported_file(tmp_path, MODEL) is None
    assert staged_assets.needs_import(tmp_path, [MODEL]) == [MODEL]


def test_loaded_paths_reads_scene_edits_and_tool_arguments() -> None:
    edit = place()
    edit["operations"].append(
        {
            "operation": "set_node_properties",
            "target": "res://room.tscn::Lamp",
            "properties": {"mesh": {"resource_path": "res://art/lamp.tres"}},
        }
    )
    assert staged_assets.loaded_paths(edit) == [MODEL, "art/lamp.tres"]
    # The staged Godot tools' file arguments are project-relative.
    arguments = {"scenePath": "scenes/room.tscn", "texture": "art/wood.png", "n": 3}
    assert staged_assets.loaded_paths(arguments, bare=True) == [
        "scenes/room.tscn",
        "art/wood.png",
    ]
    # Never a path out of the project or into Joinery's own folders.
    assert (
        staged_assets.loaded_paths({"a": "res://../x.glb", "b": "res://.joinery/x.glb"})
        == []
    )


# --- The gateway -----------------------------------------------------------------------


def test_a_model_made_in_the_change_is_handed_to_the_editor_from_its_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    importer = Importer()
    monkeypatch.setattr("joinery.gateway.run_offline", importer)

    async def run() -> None:
        gateway = ProjectGateway(project(tmp_path, monkeypatch))
        try:
            tx = staged(gateway, {MODEL: b"glTF crt"})
            files = await gateway.staged_files(place())
            assert list(files) == ["res://" + MODEL]
            copy = Path(files["res://" + MODEL])
            assert copy.is_file() and copy.is_relative_to(tx.stage.resolve())
            assert copy.read_bytes().endswith(b"glTF crt")
            assert importer.runs == 1
            # Imported once: the next placement of the same model doesn't import again.
            assert await gateway.staged_files(place()) == files
            assert importer.runs == 1
            # Nothing reaches the project before the user accepts.
            assert not (tmp_path / MODEL).exists()
            assert all(c["path"] != ".godot" for c in tx.diff())
        finally:
            await gateway.close()

    asyncio.run(run())


def test_a_project_model_needs_no_stage_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    importer = Importer()
    monkeypatch.setattr("joinery.gateway.run_offline", importer)
    (tmp_path / MODEL).parent.mkdir(parents=True)
    (tmp_path / MODEL).write_bytes(b"glTF old")

    async def run() -> None:
        gateway = ProjectGateway(project(tmp_path, monkeypatch))
        try:
            staged(gateway, {"scripts/a.gd": b"extends Node\n"})
            assert await gateway.staged_files(place()) == {}
            assert importer.runs == 0
        finally:
            await gateway.close()

    asyncio.run(run())


def test_a_missing_or_broken_model_is_said_plainly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("joinery.gateway.run_offline", Importer(ok=False))

    async def run() -> None:
        gateway = ProjectGateway(project(tmp_path, monkeypatch))
        try:
            with pytest.raises(ValueError) as missing:
                await gateway.staged_files(place())
            assert "res://" + MODEL in str(missing.value)
            assert "Make or export it first" in str(missing.value)
            staged(gateway, {MODEL: b"not a model"})
            with pytest.raises(ValueError) as broken:
                await gateway.staged_files(place())
            assert "Export it again" in str(broken.value) or "export it again" in str(
                broken.value
            )
            for message in (str(missing.value), str(broken.value)):
                assert not any(word in message.lower() for word in INTERNAL), message
        finally:
            await gateway.close()

    asyncio.run(run())


def test_a_staged_godot_call_that_loads_a_new_model_imports_the_stage_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    importer = Importer()
    monkeypatch.setattr("joinery.gateway.run_offline", importer)

    async def run() -> None:
        gateway = ProjectGateway(project(tmp_path, monkeypatch))
        try:
            tx = staged(gateway, {MODEL: b"glTF crt"})
            await gateway._import_for(["scenes/room.tscn", MODEL])
            assert importer.runs == 1
            assert staged_assets.imported_file(tx.stage, MODEL) is not None
            await gateway._import_for([MODEL])
            assert importer.runs == 1
        finally:
            await gateway.close()

    asyncio.run(run())


# --- One turn, one review -------------------------------------------------------------


@pytest.fixture
def host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Host:
    return Host(tmp_path, monkeypatch)


def test_the_model_and_its_placement_open_as_one_review(
    host: Host, monkeypatch: pytest.MonkeyPatch
) -> None:
    importer = Importer()
    monkeypatch.setattr("joinery.gateway.run_offline", importer)
    submitted: list[Any] = []

    async def agent(bridge: Any) -> AsyncIterator[Any]:
        # Blender exported the model into this turn's change.
        tx = host.stage({MODEL: "glTF crt"})
        yield EditorAction(
            request_id="place-1",
            payload=place(),
            context={"scene_path": "res://room.tscn", "scene_instance": "1"},
            targets=("res://room.tscn::TV",),
        )
        while not host.editor_results:
            await asyncio.sleep(0.01)
        assert host.gateway is not None and host.gateway.turn is not None
        assert host.gateway.turn.live_edits == 1
        submitted.append(
            await host.gateway.turn.submit("A CRT TV in the room", "agent")
        )
        assert tx.state == "review"
        yield Message(text="I made a CRT TV and put it where the placeholder TV stood.")
        yield Done(ok=True)

    host.replies.append(
        (
            lambda events: any(e.get("kind") == "editor_action" for e in events),
            {
                "kind": "editor_result",
                "request_id": "place-1",
                "result": {"success": True},
            },
        )
    )
    host.run(agent)
    assert host.editor_results == [{"success": True}]
    action = next(e for e in host.events if e.get("kind") == "editor_action")
    copy = Path(action["staged"]["res://" + MODEL])
    assert ".joinery/transactions/" in copy.as_posix() and copy.suffix == ".scn"
    assert importer.runs >= 1
    result = submitted[0]
    assert result.status == "in_review" and result.live_edits == 1
    assert {"path": MODEL, "change": "created"} in result.changes
    kinds = host.kinds()
    preview = host.events[kinds.index("file_preview")]
    assert any(change["path"] == MODEL for change in preview["changes"])
    assert "notice" not in kinds


# --- What agents are told -------------------------------------------------------------


def test_replace_is_a_placement_of_a_selected_node(tmp_path: Path) -> None:
    (tmp_path / "joinery.toml").write_text('[tools]\nenabled=["godot"]\n')
    edit = validate_edit(tmp_path, place(), ("res://room.tscn::TV",))
    assert edit.operations[0].model_dump()["replace"] == "res://room.tscn::TV"
    with pytest.raises(ValueError, match="current editor context"):
        validate_edit(tmp_path, place(), ())
    with pytest.raises(ValueError, match="Only instantiate_scene"):
        SceneEdit.model_validate(
            {
                "summary": "x",
                "operations": [
                    {"operation": "delete_node", "target": "a::b", "replace": "a::c"}
                ],
            }
        )
    with pytest.raises(ValueError, match="fit_size needs replace"):
        SceneEdit.model_validate(
            {
                "summary": "x",
                "operations": [
                    {
                        "operation": "instantiate_scene",
                        "resource_path": "res://a.glb",
                        "fit_size": True,
                    }
                ],
            }
        )


def test_agents_place_what_they_make_and_say_it_plainly() -> None:
    assert "instantiate_scene" in GAME_GUIDE and "replace" in GAME_GUIDE
    assert "same change" in GAME_GUIDE
    assert "where you placed it" in REVIEW_GUIDE
    assert "never call something blocked" in REVIEW_GUIDE
    assert "where you placed it" in NEXT_REVIEW
    assert "instantiate_scene" in LIVE_SCENE
    for message in (
        staged_assets.MISSING,
        staged_assets.NOT_IMPORTED,
        LIVE_SCENE,
    ):
        assert not any(word in message.lower() for word in INTERNAL), message
