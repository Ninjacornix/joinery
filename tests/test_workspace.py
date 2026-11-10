"""Native source resolution and desktop opening must not change provenance."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from joinery import desktop
from joinery.recipe import load_recipe, record
from joinery.workspace import index, open_asset, watch_stamp
from tests.test_recipe import FakeRunner


@pytest.fixture(autouse=True)
def private_readiness_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "readiness-cache"
    monkeypatch.setenv("JOINERY_READINESS_CACHE", str(folder))
    return folder


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "art").mkdir()
    (tmp_path / "audio").mkdir()
    (tmp_path / "recipes").mkdir()
    (tmp_path / "joinery.toml").write_text(
        '[tools]\nenabled = ["blender", "supercollider"]\n'
    )
    (tmp_path / "joinery.lock").write_text("version = 1\n")
    monkeypatch.setattr(desktop.shutil, "which", lambda name: f"/apps/{name}")
    return tmp_path


def recipe(
    project: Path,
    tool: str,
    outputs: list[str],
    script: str = "recipes/build.py",
    sources: list[str] | None = None,
) -> None:
    for output in outputs:
        (project / output).write_bytes(b"test")
    (project / script).write_text("// operation log")
    (project / "recipes/asset.toml").write_text(
        f'tool = "{tool}"\nscript = {json.dumps(script)}\n'
        f"outputs = {json.dumps(outputs)}\nsources = {json.dumps(sources or [])}\n"
    )


def test_baked_image_opens_native_source_or_alternate_viewer(project: Path) -> None:
    recipe(project, "blender", ["art/object.blend", "art/texture.png"])
    asset = next(a for a in index(project)["assets"] if a["path"].endswith(".png"))
    options = {o["tool"]: o for o in asset["open_options"]}
    assert options["blender"]["path"] == "art/object.blend"
    assert options["krita"]["path"] == "art/texture.png"
    assert options["krita"]["available"] is True
    # Krita's MCP remains unavailable; desktop viewing does not enable it.
    assert "krita" not in (project / "joinery.toml").read_text()
    assert asset["integrity"] == "unrecorded"
    assert asset["verification"] == "Not checked by replay"


def test_audio_opens_in_ide_without_executing_script_or_in_audacity(
    project: Path,
) -> None:
    recipe(project, "supercollider", ["audio/creak.wav"], "recipes/creak.scd")
    assert open_asset(project, "audio/creak.wav", "supercollider", dry_run=True) == [
        "/apps/scide",
        str(project / "recipes/creak.scd"),
    ]
    assert open_asset(project, "audio/creak.wav", "audacity", dry_run=True) == [
        "/apps/audacity",
        str(project / "audio/creak.wav"),
    ]


def test_explicit_native_document_takes_priority(project: Path) -> None:
    (project / "art/source.kra").write_bytes(b"native")
    recipe(project, "krita", ["art/export.png"], sources=["art/source.kra"])
    assert open_asset(project, "art/export.png", "krita", dry_run=True)[-1].endswith(
        "source.kra"
    )


def test_opening_one_native_output_does_not_open_its_sibling(project: Path) -> None:
    recipe(project, "blender", ["art/first.blend", "art/second.blend"])
    assert open_asset(project, "art/second.blend", "blender", dry_run=True)[
        -1
    ].endswith("second.blend")


def test_native_source_field_survives_fingerprint_recording(project: Path) -> None:
    recipe(project, "supercollider", ["audio/tone.wav"], sources=["recipes/build.py"])
    path = project / "recipes/asset.toml"
    record(path, project, FakeRunner())
    assert load_recipe(path).sources == ("recipes/build.py",)


def test_human_import_and_unlocked_native_files_can_be_viewed(project: Path) -> None:
    native = project / "art/painting.kra"
    native.write_bytes(b"painting")
    (project / "joinery.lock").write_text(
        'version = 1\n[[asset]]\npath = "art/painting.kra"\n'
        f'sha256 = "{hashlib.sha256(native.read_bytes()).hexdigest()}"\n'
        'origin = "human-import"\nimported_by = "user"\nlicence = "own work"\n'
    )
    before = (project / "joinery.lock").read_bytes()
    assert index(project)["assets"][0]["integrity"] == "in sync"
    assert open_asset(project, "art/painting.kra", "krita", dry_run=True)[-1] == str(
        native
    )
    native.write_bytes(b"manual edit")
    assert index(project)["assets"][0]["integrity"] == "changed"
    native.unlink()
    assert index(project)["assets"][0]["integrity"] == "missing"
    assert (project / "joinery.lock").read_bytes() == before


def test_missing_app_explains_configuration(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (project / "art/image.kra").write_bytes(b"painting")
    monkeypatch.setattr(desktop.shutil, "which", lambda _: None)
    monkeypatch.setattr(desktop.sys, "platform", "linux")
    with pytest.raises(ValueError, match=r"desktop.krita"):
        open_asset(project, "art/image.kra", "krita", dry_run=True)


def test_custom_viewer_is_separate_from_mcp_command(project: Path) -> None:
    (project / "joinery.toml").write_text(
        '[tools]\n[[tools.custom]]\nid = "my-painter"\nname = "Painter"\n'
        'domain = "2d"\nserver = "painter"\ncommand = "mcp-server"\n'
        '[desktop.my-painter]\ncommand = "painter-gui"\n'
        'args = ["--document"]\nextensions = [".paint"]\n'
    )
    (project / "art/example.paint").write_bytes(b"source")
    assert open_asset(project, "art/example.paint", "my-painter", dry_run=True) == [
        "/apps/painter-gui",
        "--document",
        str(project / "art/example.paint"),
    ]


def test_launch_uses_argv_and_leaves_all_files_unchanged(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rel = "art/my painting $(ignored).kra"
    (project / rel).write_bytes(b"source")
    calls: list[list[str]] = []

    def spawn(argv: list[str], **kwargs: object) -> None:
        assert "shell" not in kwargs
        assert kwargs["start_new_session"] is True
        calls.append(argv)

    monkeypatch.setattr(subprocess, "Popen", spawn)
    before = {p: p.read_bytes() for p in project.rglob("*") if p.is_file()}
    open_asset(project, rel, "krita")
    assert calls == [["/apps/krita", str(project / rel)]]
    assert all(p.read_bytes() == content for p, content in before.items())


@pytest.mark.parametrize("relative", ["../outside.kra", "/tmp/outside.kra"])
def test_outside_project_paths_refused(project: Path, relative: str) -> None:
    with pytest.raises(ValueError, match="outside"):
        desktop.project_file(project, relative)


def test_symlink_cannot_escape_project(
    project: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    outside = tmp_path_factory.mktemp("outside") / "source.kra"
    outside.write_bytes(b"source")
    (project / "art/link.kra").symlink_to(outside)
    assert index(project)["assets"] == []
    assert "outside" in index(project)["issues"][0]
    with pytest.raises(ValueError):
        open_asset(project, "art/link.kra", "krita", dry_run=True)


def test_incompatible_tool_refused(project: Path) -> None:
    (project / "art/painting.kra").write_bytes(b"source")
    with pytest.raises(ValueError, match="No compatible"):
        open_asset(project, "art/painting.kra", "audacity", dry_run=True)


def test_mac_app_bundle_discovery(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(desktop.sys, "platform", "darwin")
    monkeypatch.setattr(desktop.shutil, "which", lambda _: None)
    app = Path("/Applications") / "krita.app"
    monkeypatch.setattr(Path, "is_dir", lambda p: p == app)
    assert desktop.launcher("krita", {}) == [
        "/usr/bin/open",
        "-a",
        str(app),
    ]


def test_workspace_cli_reports_bad_config_as_json(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from joinery.cli import main

    (project / "joinery.toml").write_text("[broken")
    monkeypatch.setattr(
        sys, "argv", ["joinery", "workspace", "--project", str(project)]
    )
    assert main() == 1
    assert "error" in json.loads(capsys.readouterr().out)


def test_watch_stamp_detects_native_changes_additions_and_removals(
    project: Path,
) -> None:
    initial = watch_stamp(project)
    native = project / "art/painting.kra"
    native.write_bytes(b"source")
    added = watch_stamp(project)
    assert added != initial
    native.write_bytes(b"edited source")
    assert watch_stamp(project) != added
    native.unlink()
    assert watch_stamp(project) == initial


def test_watch_stamp_includes_explicit_native_sources(project: Path) -> None:
    (project / "documents").mkdir()
    source = project / "documents/source.kra"
    source.write_bytes(b"source")
    recipe(project, "krita", ["art/export.png"], sources=["documents/source.kra"])
    before = watch_stamp(project)
    source.write_bytes(b"manual changes")
    assert watch_stamp(project) != before


def test_unchanged_poll_skips_asset_hashing(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from joinery.cli import main

    def unexpected_index(_project: Path) -> None:
        pytest.fail("unchanged polls must not rebuild the index or hash assets")

    stamp = watch_stamp(project)
    monkeypatch.setattr("joinery.cli.index", unexpected_index)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "joinery",
            "workspace",
            "--project",
            str(project),
            "--since",
            stamp,
        ],
    )
    assert main() == 0
    assert json.loads(capsys.readouterr().out) == {"unchanged": True, "stamp": stamp}


def test_render_source_links_only_to_its_recipe_preview(project: Path) -> None:
    recipe(project, "blender", ["art/sky.blend", "art/sky.png"])
    (project / "art/unrelated.png").write_bytes(b"unrelated")
    assets = {a["path"]: a for a in index(project)["assets"]}
    assert assets["art/sky.blend"]["previews"] == ["art/sky.png"]
    assert assets["art/sky.png"]["previews"] == []


def test_agent_menu_uses_registry_names(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from joinery.agents import all_agents
    from joinery.cli import main

    monkeypatch.setattr(sys, "argv", ["joinery", "agents", "--json"])
    assert main() == 0
    data = json.loads(capsys.readouterr().out)
    assert data["agents"] == [{"id": a.id, "name": a.name} for a in all_agents()]


def test_baked_maps_fold_under_their_source_as_a_material(project: Path) -> None:
    recipe(
        project,
        "blender",
        ["art/tiles_albedo.png", "art/tiles_roughness.png", "art/tiles.blend"],
    )
    assets = {a["path"]: a for a in index(project)["assets"]}
    source = assets["art/tiles.blend"]
    assert source["kind"] == "material"
    assert source["parts"] == ["art/tiles_albedo.png", "art/tiles_roughness.png"]
    assert source["part_of"] == ""
    for part in source["parts"]:
        assert assets[part]["part_of"] == "art/tiles.blend"
        assert assets[part]["kind"] == "image"
        # A folded part still opens on its own.
        assert {o["tool"] for o in assets[part]["open_options"]} >= {"blender", "krita"}


def test_render_only_source_is_an_image_and_siblings_stay_separate(
    project: Path,
) -> None:
    (project / "art/src").mkdir()
    recipe(project, "blender", ["art/sky.png", "art/src/sky.blend"])
    text = (project / "recipes/asset.toml").read_text()
    (project / "recipes/asset.toml").write_text(text + 'renders = ["art/sky.png"]\n')
    assets = {a["path"]: a for a in index(project)["assets"]}
    assert assets["art/src/sky.blend"]["kind"] == "image"
    assert assets["art/src/sky.blend"]["parts"] == ["art/sky.png"]


def test_two_native_outputs_are_not_folded_and_sounds_keep_their_kind(
    project: Path,
) -> None:
    recipe(project, "blender", ["art/first.blend", "art/second.blend"])
    assets = {a["path"]: a for a in index(project)["assets"]}
    assert assets["art/first.blend"]["parts"] == []
    assert assets["art/second.blend"]["part_of"] == ""
    assert assets["art/second.blend"]["kind"] == "model"
    recipe(project, "supercollider", ["audio/a.wav", "audio/b.wav"], "recipes/s.scd")
    sounds = [a for a in index(project)["assets"] if a["path"].startswith("audio/")]
    assert [(a["kind"], a["part_of"]) for a in sounds] == [("sound", ""), ("sound", "")]


def test_removed_desktop_apps_are_offered_only_when_installed(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recipe(project, "supercollider", ["audio/creak.wav"], "recipes/creak.scd")
    sound = next(a for a in index(project)["assets"] if a["path"] == "audio/creak.wav")
    assert "audacity" in {o["tool"] for o in sound["open_options"]}
    monkeypatch.setattr(
        desktop.shutil,
        "which",
        lambda name: None if name == "audacity" else f"/apps/{name}",
    )
    sound = next(a for a in index(project)["assets"] if a["path"] == "audio/creak.wav")
    assert "audacity" not in {o["tool"] for o in sound["open_options"]}


def test_tools_carry_readiness_from_the_cache_without_asking_docker(
    project: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from joinery import readiness
    from tests.test_readiness import FakeHost

    engine = tmp_path / "godot.macos.editor.arm64"
    engine.write_text("editor")
    host = FakeHost(engine=engine, docker_reason="Docker is not running.")
    # Nothing known yet: rows carry no readiness rather than a guess.
    tools = {t["id"]: t for t in index(project)["tools"]}
    assert "status" not in tools["blender"]
    readiness.status(project, refresh=True, host=host)

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the workspace never asks Docker")

    monkeypatch.setattr(readiness.subprocess, "run", refuse)
    monkeypatch.setattr(readiness.subprocess, "Popen", refuse)
    monkeypatch.setattr(readiness, "DockerHost", refuse)
    tools = {t["id"]: t for t in index(project)["tools"]}
    assert tools["blender"]["status"] == "needs_docker"
    assert tools["blender"]["status_label"] == "Needs Docker"
    assert "Docker" in tools["blender"]["status_reason"]
    assert tools["blender"]["action"] == {"id": "start_docker", "label": "Start Docker"}
    assert tools["blender"]["checked_at"]


def test_material_maker_image_inputs_are_not_folded_as_parts(project: Path) -> None:
    (project / "art/materials").mkdir()
    (project / "art/photos").mkdir()
    (project / "art/photos/bark.png").write_bytes(b"photo")
    (project / "art/materials/bark.ptex").write_text("{}")
    recipe(
        project,
        "material-maker",
        ["art/materials/bark_albedo.png"],
        "recipes/bark.json",
        sources=["art/materials/bark.ptex", "art/photos/bark.png"],
    )
    assets = {a["path"]: a for a in index(project)["assets"]}
    owner = assets["art/materials/bark.ptex"]
    assert owner["parts"] == ["art/materials/bark_albedo.png"]
    # The photo the graph reads is an input, not something the graph made.
    assert assets["art/photos/bark.png"]["part_of"] == ""


def test_assets_waiting_for_review_are_listed_and_marked(project: Path) -> None:
    from joinery.transactions import FileTransaction

    (project / "project.godot").write_text("config_version=5\n")
    tx = FileTransaction(project)
    tx.begin("Model an ashtray")
    tx.note_tool("blender")
    stage = tx.stage
    (stage / "art/sources").mkdir(parents=True)
    (stage / "art/models").mkdir()
    (stage / "art/sources/ashtray.blend").write_bytes(b"blend")
    (stage / "art/models/ashtray.glb").write_bytes(b"glTF")
    (stage / "art/models/ashtray.glb.import").write_text("[remap]\n")
    (stage / "art/sources/.gdignore").write_text("")
    (stage / "recipes/ashtray.toml").write_text(
        'tool = "blender"\nscript = "recipes/ashtray.py"\n'
        'outputs = ["art/sources/ashtray.blend", "art/models/ashtray.glb"]\n'
    )
    before = watch_stamp(project)
    tx.review()
    assert watch_stamp(project) != before, "Going to review refreshes the workspace"
    assets = {a["path"]: a for a in index(project)["assets"]}
    assert set(assets) == {"art/sources/ashtray.blend", "art/models/ashtray.glb"}
    source = assets["art/sources/ashtray.blend"]
    assert source["pending"] == tx.id and source["integrity"] == "pending"
    assert source["tool"] == "blender" and source["kind"] == "model"
    assert source["parts"] == ["art/models/ashtray.glb"], (
        "Folded like an accepted model"
    )
    assert source["staged"].endswith(f"{tx.id}/stage/art/sources/ashtray.blend")
    assert source["open_options"] == [], "Nothing to open before it is accepted"
    tx.reject()
    assert index(project)["assets"] == [], "A discarded change leaves the list"


def test_models_scenes_use_outside_art_are_listed(project: Path) -> None:
    (project / "props").mkdir()
    (project / "props/crate.glb").write_bytes(b"glTF")
    (project / "room.tscn").write_text(
        '[gd_scene format=3]\n\n[ext_resource type="PackedScene" '
        'path="res://props/crate.glb" id="1"]\n'
    )
    assets = {a["path"]: a for a in index(project)["assets"]}
    assert assets["props/crate.glb"]["kind"] == "model"
    assert assets["props/crate.glb"]["origin"] == "unrecorded"
