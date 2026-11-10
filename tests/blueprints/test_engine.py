"""Applying blueprints and features to projects, on the fixture catalogue."""

from __future__ import annotations

import json
import shutil
import tomllib
from pathlib import Path

import pytest

from joinery import blueprints
from joinery.blueprints import (
    MANIFEST,
    add_feature,
    apply,
    plan,
    remove_feature,
    resolve,
)
from joinery.catalog import load_catalog
from joinery.godot_config import GodotConfig, action_events, packed_strings
from joinery.installer import Selection, write_selection
from joinery.project import create_project
from tests.blueprints.conftest import FixtureCatalogue
from tests.blueprints.godot_run import boot, needs_godot, run_godot


def _project(tmp_path: Path, blueprint: str | None = "alpha", features: tuple[str, ...] = ()) -> Path:
    project = tmp_path / "game"
    create_project(
        project, "Fixture Game", Selection(["godot"], []), load_catalog(),
        blueprint=blueprint, features=features,
    )
    return project


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and ".godot" not in p.relative_to(root).parts
    }


def test_apply_writes_files_settings_inputs_and_record(
    fixture_catalogue: FixtureCatalogue, tmp_path: Path
) -> None:
    project = _project(tmp_path, "alpha", ("controller",))
    config = GodotConfig.parse((project / "project.godot").read_text())
    assert config.get("application", "config/name") == '"Fixture Game"'
    assert config.get("application", "run/main_scene") == '"res://core/main.tscn"'
    assert packed_strings(config.get("application", "config/features") or "") == ["4.7"]
    assert config.get("game", "blueprint") == '"alpha"'
    assert config.get("game", "hooks/level_scene") == '"res://game/level.tscn"'
    assert config.get("game", "hooks/player_scene") is None
    assert config.get("display", "window/size/viewport_width") == "1280"
    assert config.get("rendering", "environment/defaults/default_clear_color") == (
        "Color(0.1, 0.1, 0.12, 1)"
    )
    assert config.get("autoload", "Probe") == '"*res://core/probe.gd"'
    left = config.get("input", "move_left") or ""
    assert '"deadzone": 0.25' in left
    events = action_events(left)
    assert len(events) == 4  # A, Left, d-pad left, left stick
    assert "InputEventJoypadButton" in events[2] and "InputEventJoypadMotion" in events[3]
    # Features only add to actions that exist: beta's move_forward is not created.
    assert config.get("input", "move_forward") is None
    assert config.sections() == sorted(config.sections())  # Godot's own order
    for rel in ("core/main.tscn", "core/probe.gd.uid", "game/level.gd", "features/controller/pad.gd"):
        assert (project / rel).is_file(), rel
    record = tomllib.loads((project / "joinery.toml").read_text())
    assert record["game"] == {
        "blueprint": "alpha",
        "blueprint_version": 1,
        "features": ["controller"],
        "feature_versions": {"controller": 1},
    }
    assert record["tools"]["enabled"] == ["godot"]
    manifest = json.loads((project / MANIFEST).read_text())
    assert list(manifest["units"]) == ["_core", "alpha", "controller"]
    assert manifest["units"]["controller"]["input_events"]["move_left"]
    assert (project / "design").is_dir()
    assert (project / "art/reference/.gdignore").is_file()


def test_apply_again_changes_no_byte(fixture_catalogue: FixtureCatalogue, tmp_path: Path) -> None:
    project = _project(tmp_path, "alpha", ("controller", "words"))
    before = _snapshot(project)
    result = apply(project, "alpha", ["controller", "words"])
    assert result.written == []
    assert _snapshot(project) == before
    # Adding an already-applied feature is also a no-op.
    add_feature(project, "words")
    assert _snapshot(project) == before


def test_plan_is_pure(fixture_catalogue: FixtureCatalogue, tmp_path: Path) -> None:
    project = _project(tmp_path, "alpha")
    before = _snapshot(project)
    planned = plan(project, "alpha", ["net"])
    assert _snapshot(project) == before
    assert planned.conflicts == []
    assert "features/net/net.gd" in planned.changes
    assert "features/net/alpha_turns.gd" in planned.changes  # alpha adapter
    assert "export_presets.cfg" in planned.changes
    assert any(e.key == "Net" and e.after for e in planned.config_edits)
    assert planned.preset_edits == ["Linux Dedicated Server"]


def test_requires_are_added_and_reported(fixture_catalogue: FixtureCatalogue, tmp_path: Path) -> None:
    resolution = resolve("alpha", ["lobby"])
    assert resolution.problems == []
    assert [f.id for f in resolution.features] == ["net", "lobby"]
    assert resolution.added == {"net": "required by Lobby"}
    project = _project(tmp_path, "alpha")
    applied = add_feature(project, "lobby")
    assert applied.features == ["net", "lobby"]
    assert applied.added == {"net": "required by Lobby"}
    config = GodotConfig.parse((project / "project.godot").read_text())
    assert config.keys("autoload") == ["Probe", "Net", "Lobby"]  # dependency order


def test_conflicts_unsupported_and_unknown_are_refused(
    fixture_catalogue: FixtureCatalogue, tmp_path: Path
) -> None:
    assert resolve("alpha", ["solo", "net"]).problems == [
        "Solo mode conflicts with Online play; choose one."
    ]
    assert resolve("beta", ["lobby"]).problems == [
        "Lobby does not work with the Beta blueprint."
    ]
    assert "Unknown feature: jetpack" in resolve("alpha", ["jetpack"]).problems
    assert "Unknown blueprint: gamma" in resolve("gamma", []).problems
    with pytest.raises(ValueError, match="conflicts"):
        _project(tmp_path, "alpha", ("solo", "net"))
    assert not (tmp_path / "game").exists()  # refused before any file was made
    project = _project(tmp_path, "beta")
    before = _snapshot(project)
    with pytest.raises(ValueError, match="does not work"):
        add_feature(project, "lobby")
    assert _snapshot(project) == before
    with pytest.raises(ValueError, match="never swapped"):
        apply(project, "alpha", [])


def test_a_blueprint_refused_at_apply_leaves_no_half_made_project(
    fixture_catalogue: FixtureCatalogue, tmp_path: Path
) -> None:
    # Two units writing one file pass `resolve` but are refused when applied.
    clash = fixture_catalogue.features / "words/overlay/core/probe.gd"
    clash.parent.mkdir(parents=True)
    shutil.copy(fixture_catalogue.blueprints / "_core/overlay/core/probe.gd", clash)
    shutil.copy(
        fixture_catalogue.blueprints / "_core/overlay/core/probe.gd.uid",
        clash.with_name("probe.gd.uid"),
    )
    assert resolve("alpha", ["words"]).problems == []
    with pytest.raises(ValueError, match="both write core/probe.gd"):
        _project(tmp_path, "alpha", ("words",))
    assert not (tmp_path / "game").exists()
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError, match="both write"):
        create_project(
            empty, "Fixture Game", Selection(["godot"], []), load_catalog(),
            blueprint="alpha", features=("words",),
        )
    assert empty.is_dir() and list(empty.iterdir()) == []


def test_user_file_is_never_overwritten(fixture_catalogue: FixtureCatalogue, tmp_path: Path) -> None:
    project = _project(tmp_path, "alpha")
    mine = project / "features/net/net.gd"
    mine.parent.mkdir(parents=True)
    mine.write_text("# my own net code\n")
    before = _snapshot(project)
    with pytest.raises(ValueError, match="features/net/net.gd already exists"):
        add_feature(project, "net")
    assert _snapshot(project) == before


def test_user_edits_survive_reapply_and_removal(
    fixture_catalogue: FixtureCatalogue, tmp_path: Path
) -> None:
    project = _project(tmp_path, "alpha", ("net",))
    level = project / "game/level.gd"
    level.write_text(level.read_text() + "# tuned by me\n")
    edited = project / "features/net/net.gd"
    edited.write_text(edited.read_text() + "# my tweak\n")
    add_feature(project, "words")  # re-applies every unit
    assert level.read_text().endswith("# tuned by me\n")
    removed = remove_feature(project, "net")
    assert edited.is_file() and removed.kept == ["features/net/net.gd"]
    # The kept script keeps its uid, so scenes that load it by uid still do.
    assert (project / "features/net/net.gd.uid").is_file()
    assert "features/net/net.gd.uid" not in removed.deleted
    assert "features/net/alpha_turns.gd" in removed.deleted
    assert "features/net/alpha_turns.gd.uid" in removed.deleted
    assert not (project / "tests/features/net").exists()


def test_remove_restores_project_files_byte_for_byte(
    fixture_catalogue: FixtureCatalogue, tmp_path: Path
) -> None:
    project = _project(tmp_path, "alpha", ("controller",))
    # A setting the user already had, which net overrides and must give back.
    config = GodotConfig.parse((project / "project.godot").read_text())
    config.set("online", "default_port", "7000")
    (project / "project.godot").write_text(config.dump())
    (project / "export_presets.cfg").write_text(
        '[preset.0]\n\nname="Web"\nplatform="Web"\n\n[preset.0.options]\n\nvram_texture_compression/for_desktop=true\n'
    )
    before = _snapshot(project)
    add_feature(project, "words")
    add_feature(project, "net")
    presets = (project / "export_presets.cfg").read_text()
    assert "[preset.1]" in presets and 'name="Linux Dedicated Server"' in presets
    assert GodotConfig.parse((project / "project.godot").read_text()).get(
        "online", "default_port"
    ) == "24565"
    remove_feature(project, "net")
    remove_feature(project, "words")
    assert _snapshot(project) == before


def test_remove_renumbers_presets_and_drops_created_file(
    fixture_catalogue: FixtureCatalogue, tmp_path: Path
) -> None:
    project = _project(tmp_path, "alpha", ("net",))
    presets = project / "export_presets.cfg"
    assert presets.read_text().startswith("[preset.0]\n")
    remove_feature(project, "net")
    assert not presets.exists()
    (project / "export_presets.cfg").write_text('[preset.0]\n\nname="Web"\n')
    add_feature(project, "net")
    text = presets.read_text() + '\n[preset.2]\n\nname="Mac"\n'
    presets.write_text(text)
    remove_feature(project, "net")
    config = GodotConfig.parse(presets.read_text())
    assert config.sections() == ["preset.0", "preset.1"]
    assert config.get("preset.1", "name") == '"Mac"'


def test_remove_refuses_a_feature_another_needs(
    fixture_catalogue: FixtureCatalogue, tmp_path: Path
) -> None:
    project = _project(tmp_path, "alpha", ("lobby",))
    with pytest.raises(ValueError, match="Lobby needs net"):
        remove_feature(project, "net")
    with pytest.raises(ValueError, match="not part of this project"):
        remove_feature(project, "solo")
    remove_feature(project, "lobby")
    remove_feature(project, "net")
    record = blueprints.read_record(project)
    assert record is not None and record.features == []


def test_game_record_survives_tool_setup(fixture_catalogue: FixtureCatalogue, tmp_path: Path) -> None:
    project = _project(tmp_path, "alpha", ("controller",))
    write_selection(Selection(["godot", "krita"], []), project / "joinery.toml")
    data = tomllib.loads((project / "joinery.toml").read_text())
    assert data["tools"]["enabled"] == ["godot", "krita"]
    assert data["game"]["features"] == ["controller"]
    add_feature(project, "words")
    data = tomllib.loads((project / "joinery.toml").read_text())
    assert data["tools"]["enabled"] == ["godot", "krita"]
    assert data["game"]["features"] == ["controller", "words"]
    assert "enabled" not in (project / "joinery.toml").read_text().split("[game]")[1]


def test_stage_root_reads_manifest_from_live_project(
    fixture_catalogue: FixtureCatalogue, tmp_path: Path
) -> None:
    project = _project(tmp_path, "alpha", ("controller",))
    stage = tmp_path / "stage"
    # Transactions copy the project without `.joinery/`.
    shutil.copytree(project, stage, ignore=shutil.ignore_patterns(".joinery", ".godot"))
    before = _snapshot(project)
    applied = add_feature(project, "words", root=stage)
    assert _snapshot(project) == before
    assert (stage / "locale/en.po").is_file()
    assert MANIFEST in applied.written
    manifest = json.loads((stage / MANIFEST).read_text())
    assert set(manifest["units"]) == {"_core", "alpha", "controller", "words"}
    assert "core/main.gd" not in applied.written  # unchanged files left alone


def test_project_without_blueprint_is_unchanged(tmp_path: Path) -> None:
    project = _project(tmp_path, None)
    assert blueprints.read_record(project) is None
    assert blueprints.project_game(project) is None
    assert not (project / MANIFEST).exists()
    with pytest.raises(ValueError, match="not started from a blueprint"):
        add_feature(project, "controller")


def test_project_game_summary(fixture_catalogue: FixtureCatalogue, tmp_path: Path) -> None:
    project = _project(tmp_path, "alpha", ("controller",))
    game = blueprints.project_game(project)
    assert game is not None
    json.dumps(game)
    assert game["blueprint"] == "alpha" and game["dimension"] == "2d"
    assert game["autoloads"] == ["Probe"]
    assert game["can_add"] == ["lobby", "net", "solo", "words"]
    assert "core/main.tscn" in game["units"]["_core"]["files"]


def test_describe_and_catalogue_json(fixture_catalogue: FixtureCatalogue) -> None:
    described = blueprints.describe("alpha")
    json.dumps(described)
    assert described["input"]["move_left"] == {
        "keys": ["A", "Left"],
        "mouse": [],
        "pad": ["D-pad left", "Left stick left"],
    }
    assert described["input"]["pause"]["pad"] == ["Start"]
    assert described["features_supported"] == ["controller", "lobby", "net", "solo", "words"]
    assert "dir" not in described and "merge" not in described
    catalogue = blueprints.catalogue_json()
    json.dumps(catalogue)
    assert [b["id"] for b in catalogue["blueprints"]] == ["alpha", "beta"]
    net = next(f for f in catalogue["features"] if f["id"] == "net")
    assert net["external"][0]["source"] == "TBD, pending user approval"
    assert any(p["id"] == "none" for p in catalogue["presets"])
    with pytest.raises(ValueError, match="Unknown blueprint"):
        blueprints.describe("gamma")


@needs_godot
def test_fixture_project_boots_in_stock_godot(
    fixture_catalogue: FixtureCatalogue, tmp_path: Path
) -> None:
    project = _project(tmp_path, "alpha", ("controller", "net", "lobby", "words"))
    boot(project, marker="fixture boot")
    run_godot(project, "res://tests/game/smoke.gd", marker="smoke")


def test_reapply_respects_what_the_user_removed(
    fixture_catalogue: FixtureCatalogue, tmp_path: Path
) -> None:
    project = _project(tmp_path, "alpha", ("controller", "net"))
    (project / "game/level.gd").unlink()
    config = GodotConfig.parse((project / "project.godot").read_text())
    config.erase("autoload", "Net")
    config.erase("input", "jump")
    left = config.get("input", "move_left") or ""
    config.set("input", "move_left", left.replace(action_events(left)[0] + "\n, ", ""))
    (project / "project.godot").write_text(config.dump())
    applied = add_feature(project, "words")
    assert not (project / "game/level.gd").exists()
    config = GodotConfig.parse((project / "project.godot").read_text())
    assert config.get("autoload", "Net") is None
    assert config.get("input", "jump") is None
    assert len(action_events(config.get("input", "move_left") or "")) == 3
    assert any("game/level.gd" in note for note in applied.notes)


def test_new_unit_version_updates_untouched_files_only(
    fixture_catalogue: FixtureCatalogue, tmp_path: Path
) -> None:
    project = _project(tmp_path, "alpha", ("net",))
    shipped = fixture_catalogue.features / "net/overlay/features/net/net.gd"
    shipped.write_text(shipped.read_text() + "# version 2\n")
    toml = fixture_catalogue.features / "net/feature.toml"
    toml.write_text(toml.read_text().replace("version = 1", "version = 2"))
    applied = apply(project, "alpha", [])
    assert "features/net/net.gd" in applied.written
    assert (project / "features/net/net.gd").read_text().endswith("# version 2\n")
    assert blueprints.read_record(project).feature_versions == {"net": 2}  # type: ignore[union-attr]
    (project / "features/net/net.gd").write_text("# mine\n")
    shipped.write_text(shipped.read_text() + "# version 3\n")
    with pytest.raises(ValueError, match="was edited, and the new version of net"):
        apply(project, "alpha", [])


def test_new_unit_version_dropping_a_file_keeps_an_edited_ones_uid(
    fixture_catalogue: FixtureCatalogue, tmp_path: Path
) -> None:
    project = _project(tmp_path, "alpha", ("net",))
    (project / "features/net/net.gd").write_text("# mine\n")
    overlay = fixture_catalogue.features / "net/overlay/features/net"
    (overlay / "net.gd").unlink()
    (overlay / "net.gd.uid").unlink()
    test_dir = fixture_catalogue.features / "net/overlay/tests/features/net"
    (test_dir / "net_test.gd").unlink()
    (test_dir / "net_test.gd.uid").unlink()
    toml = fixture_catalogue.features / "net/feature.toml"
    toml.write_text(
        toml.read_text()
        .replace("version = 1", "version = 2")
        .replace('[autoload]\nNet = "res://features/net/net.gd"\n', "")
    )
    applied = apply(project, "alpha", [])
    assert (project / "features/net/net.gd").read_text() == "# mine\n"
    assert (project / "features/net/net.gd.uid").is_file()
    assert any("Kept features/net/net.gd" in note for note in applied.notes)
    # An untouched dropped file goes, with its uid.
    assert "tests/features/net/net_test.gd" in applied.deleted
    assert "tests/features/net/net_test.gd.uid" in applied.deleted
    assert not (project / "tests/features/net").exists()
