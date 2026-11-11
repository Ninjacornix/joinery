"""The sound pass: a model-free audit of what a level is missing, and its plan by file."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from joinery import blueprints, scene_text, sound_pass, sound_plan
from joinery.cli import main as cli_main
from joinery.scene_text import Scene


def _game(tmp_path: Path, blueprint: str = "third-person-3d") -> Path:
    project = tmp_path / "game"
    project.mkdir()
    (project / "project.godot").write_text(
        'config_version=5\n[application]\nconfig/name="Sound fixture"\n'
    )
    (project / "joinery.toml").write_text('[tools]\nenabled = ["godot"]\n')
    blueprints.apply(project, blueprint, [])
    return project


@pytest.fixture(scope="module")
def third_person(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _game(tmp_path_factory.mktemp("sound"))


def _apply(project: Path, tasks: list[sound_plan.SoundTask]) -> None:
    for task in tasks:
        for step in task.steps:
            if step.tool == "write_file":
                path = project / step.arguments["filePath"]
                path.write_text(step.arguments["content"], encoding="utf-8")


def test_audit_finds_what_a_third_person_level_is_missing(third_person: Path) -> None:
    audit = sound_pass.audit(third_person)
    assert audit.level == "res://game/level.tscn"
    assert audit.player == "res://game/player/player.tscn"
    assert audit.dimension == "3d"
    found = {(f.category, sound_pass.rel(f.scene)) for f in audit.findings}
    assert {
        ("footsteps", "game/player/player.tscn"),
        ("footsteps", "game/level.tscn"),
        ("jump_land", "game/player/player.tscn"),
        ("pickups", "game/props/coin.tscn"),
        ("switches", "game/props/lever.tscn"),
        ("switches", "game/props/pressure_plate.tscn"),
        ("ambience", "game/level.tscn"),
        ("ui", "project.godot"),
    } <= found
    # Floors are guessed from their names and colours: green ground, stone towers.
    assert audit.surfaces == ["default", "grass", "stone"]
    floors = next(f for f in audit.findings if f.id.startswith("surfaces:"))
    assert floors.placement is not None
    tagged = floors.placement.detail["floors"]
    assert tagged["Geometry/Arena"] == "grass"
    assert tagged["Geometry/TowerTop"] == "stone"
    assert not any("Wall" in path for path in tagged)
    steps = next(f for f in audit.findings if f.id.startswith("footsteps:"))
    assert [s.name for s in steps.sounds] == [
        "step_default",
        "step_grass",
        "step_stone",
    ]
    coin = next(f for f in audit.findings if f.category == "pickups")
    assert coin.placement is not None
    assert coin.placement.detail == {"trigger": "body_entered", "only_group": "player"}
    lever = next(f for f in audit.findings if "lever" in f.scene)
    assert lever.sounds[0].kind == "lever" and lever.placement is not None
    assert lever.placement.detail["trigger"] == "switched"
    plate = next(f for f in audit.findings if "pressure_plate" in f.scene)
    assert plate.sounds[0].kind == "plate"
    ambience = next(f for f in audit.findings if f.category == "ambience")
    assert ambience.sounds[0].kind == "wind"
    assert ambience.placement is not None and ambience.placement.detail["mask"] == "2"
    assert audit.headline == "11 sounds missing in level.tscn"
    rows = {row["id"]: row["missing"] for row in audit.categories}
    assert rows == {
        "footsteps": 3,
        "jump_land": 2,
        "pickups": 1,
        "hits": 0,
        "switches": 2,
        "ambience": 1,
        "ui": 2,
    }


def test_plan_groups_by_file_and_never_shares_one(third_person: Path) -> None:
    audit = sound_pass.audit(third_person)
    tasks = sound_plan.plan(third_person, audit)
    titles = [t.title for t in tasks]
    assert titles == [
        "Add footsteps, jump and land to the player",
        "Add wind ambience and floor surfaces to level.tscn",
        "Add pickup sound to coin",
        "Add lever and plate sounds to 2 props",
        "Add UI click and hover sounds",
    ]
    owners: dict[str, str] = {}
    for task in tasks:
        for target in task.targets:
            assert target not in owners, (
                f"{target} in {owners.get(target)} and {task.key}"
            )
            owners[target] = task.key
        written = [
            s.arguments["filePath"] for s in task.steps if s.tool == "write_file"
        ]
        assert len(written) == len(set(written)) <= sound_plan.MAX_FILES
        made = [s for s in task.steps if s.tool == "make_sound"]
        # Sounds come first, so the scene can load them when it is written.
        assert task.steps[: len(made)] == made
        assert len(task.facts) <= 3
    player = tasks[0]
    assert player.facts == [
        "Footsteps on 3 surfaces",
        "Jump and land",
        "−24 to −22 LUFS",
    ]
    assert player.apps == ["godot", "supercollider"]
    assert tasks[2].apps == ["godot", "surge-xt"]
    assert "audio/ambience/level_wind.ogg.import" in tasks[1].targets


def test_patched_scenes_hold_the_core_sound_nodes(third_person: Path) -> None:
    tasks = sound_plan.plan(third_person, sound_pass.audit(third_person))
    texts = {
        s.arguments["filePath"]: s.arguments["content"]
        for t in tasks
        for s in t.steps
        if s.tool == "write_file"
    }
    player = Scene(texts["game/player/player.tscn"])
    steps = player.node("Footsteps")
    assert steps is not None and steps.parent == "."
    assert player.script_of(steps) == sound_pass.FOOTSTEPS
    surfaces = scene_text.dictionary_keys(steps.props()["surfaces"])
    assert list(surfaces) == ["default", "grass", "stone"]
    assert player.ext_path(surfaces["grass"]) == "res://audio/sfx/step_grass.wav"
    assert player.ext_path(steps.props()["jump"]) == "res://audio/sfx/player_jump.wav"
    level = Scene(texts["game/level.tscn"])
    zone = level.node("Ambience")
    assert zone is not None and zone.type == "Area3D"
    assert level.script_of(zone) == sound_pass.AMBIENCE_ZONE
    assert (
        level.ext_path(zone.props()["stream"]) == "res://audio/ambience/level_wind.ogg"
    )
    shape = level.node("Ambience/Shape")
    assert shape is not None and level.sub_of(shape.props()["shape"]) is not None
    arena = level.node("Geometry/Arena")
    assert arena is not None and arena.props()["metadata/surface"] == '"grass"'
    # New nodes come after the nodes they hang under.
    paths = [n.path for n in level.nodes()]
    assert paths.index("Ambience/Shape") > paths.index("Ambience") > paths.index("HUD")
    coin = Scene(texts["game/props/coin.tscn"])
    cue = coin.node("PickupSound")
    assert cue is not None and coin.script_of(cue) == sound_pass.SOUND_CUE
    assert cue.props()["trigger"] == '&"body_entered"'
    assert cue.props()["only_group"] == '&"player"'
    settings = texts["project.godot"]
    assert (
        scene_text.get_setting(settings, "audio", "ui/click")
        == '"res://audio/sfx/ui_click.wav"'
    )
    assert 'buses/default_bus_layout="res://default_bus_layout.tres"' in settings
    # Only what changed differs: every original line of the coin is still there.
    original = [
        line
        for line in (third_person / "game/props/coin.tscn").read_text().splitlines()
        if line
    ]
    kept = [
        line for line in texts["game/props/coin.tscn"].splitlines() if line in original
    ]
    assert kept == original


def test_a_second_pass_finds_nothing(tmp_path: Path) -> None:
    project = _game(tmp_path)
    _apply(project, sound_plan.plan(project, sound_pass.audit(project)))
    again = sound_pass.audit(project)
    assert again.findings == [], [f.missing for f in again.findings]
    assert sound_plan.plan(project, again) == []
    assert again.headline == "Nothing missing in level.tscn"
    assert "Footsteps on 3 surfaces" in again.present


def test_a_new_surface_extends_the_footsteps(tmp_path: Path) -> None:
    project = _game(tmp_path)
    _apply(project, sound_plan.plan(project, sound_pass.audit(project)))
    level = project / "game/level.tscn"
    scene = Scene(level.read_text())
    scene.set_props("Geometry/Ledge", {"metadata/surface": '"metal"'})
    level.write_text(scene.text())
    audit = sound_pass.audit(project)
    assert [f.missing for f in audit.findings] == ["No footsteps on metal"]
    tasks = sound_plan.plan(project, audit)
    assert len(tasks) == 1
    text = next(s for s in tasks[0].steps if s.tool == "write_file").arguments[
        "content"
    ]
    patched = Scene(text)
    node = patched.node("Footsteps")
    assert node is not None
    keys = scene_text.dictionary_keys(node.props()["surfaces"])
    assert list(keys) == ["default", "grass", "stone", "metal"]
    assert len(patched.of_kind("node")) == len(
        Scene((project / "game/player/player.tscn").read_text()).of_kind("node")
    )


def test_the_playtest_ranks_what_the_player_ran_into(tmp_path: Path) -> None:
    project = _game(tmp_path)
    run = project / ".joinery/playtests/abc"
    run.mkdir(parents=True)
    (run / "report.json").write_text(
        json.dumps(
            {
                "played_seconds": 95.0,
                "goals": {"levels_started": 1, "score_changes": 4, "world_changes": 2},
            }
        )
    )
    audit = sound_pass.audit(project)
    assert audit.playtest is not None and audit.playtest["pickups"] == 4
    assert audit.findings[0].category == "pickups"
    assert audit.findings[0].evidence == "the playtest bot picked up 4 things"
    jump = next(f for f in audit.findings if f.category == "jump_land")
    assert jump.evidence == "the playtest bot ran and jumped for 1:35"


def test_hits_and_switches_come_from_signals(tmp_path: Path) -> None:
    project = tmp_path / "hits"
    (project / "game").mkdir(parents=True)
    (project / "project.godot").write_text(
        'config_version=5\n[game]\nhooks/level_scene="res://game/level.tscn"\n'
        '[audio]\nui/click="res://a.wav"\nui/hover="res://b.wav"\n'
    )
    (project / "game/enemy.gd").write_text(
        "extends CharacterBody3D\nsignal damaged(amount: int)\nsignal died\n"
    )
    (project / "game/door.gd").write_text("extends Node3D\nsignal opened\n")
    (project / "game/trap.gd").write_text(
        "extends Node3D\nfunc take_damage(n: int) -> void:\n\tpass\n"
    )
    (project / "game/level.tscn").write_text(
        "[gd_scene format=3]\n\n"
        '[ext_resource type="Script" path="res://game/enemy.gd" id="1"]\n'
        '[ext_resource type="Script" path="res://game/door.gd" id="2"]\n'
        '[ext_resource type="Script" path="res://game/trap.gd" id="3"]\n\n'
        '[node name="Level" type="Node3D"]\n\n'
        '[node name="Enemy" type="CharacterBody3D" parent="." groups=["enemy"]]\n'
        'script = ExtResource("1")\n\n'
        '[node name="CellDoor" type="Node3D" parent="."]\nscript = ExtResource("2")\n\n'
        '[node name="Trap" type="Node3D" parent="."]\nscript = ExtResource("3")\n\n'
        '[node name="Music" type="AudioStreamPlayer" parent="."]\nbus = &"Ambience"\n'
    )
    audit = sound_pass.audit(project)
    by_node = {f.node: f for f in audit.findings}
    assert by_node["Enemy"].category == "hits"
    assert by_node["Enemy"].sounds[0].kind == "hit"
    assert by_node["Enemy"].placement is not None
    assert by_node["Enemy"].placement.detail["trigger"] == "damaged"
    assert by_node["CellDoor"].sounds[0].kind == "door"
    assert (
        by_node["Trap"].placement is None and "no signal" in by_node["Trap"].needs_you
    )
    assert "Ambience" in audit.present and "UI clicks" in audit.present
    tasks = sound_plan.plan(project, audit)
    assert len(tasks) == 1 and tasks[0].category == "hits"
    level = Scene(
        next(s for s in tasks[0].steps if s.tool == "write_file").arguments["content"]
    )
    assert level.node("Enemy/HitSound") is not None
    assert level.node("CellDoor/DoorSound") is not None


def test_a_2d_level_gets_an_ambience_player(tmp_path: Path) -> None:
    project = _game(tmp_path, "platformer-2d")
    audit = sound_pass.audit(project)
    assert audit.dimension == "2d"
    ambience = next(f for f in audit.findings if f.category == "ambience")
    assert (
        ambience.placement is not None and ambience.placement.what == "ambience_player"
    )
    assert any(f.category == "footsteps" for f in audit.findings)


def test_cli_json_and_plan(
    third_person: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        cli_main(["sound-pass", "--project", str(third_person), "--json", "--plan"])
        == 0
    )
    data = json.loads(capsys.readouterr().out)
    assert data["headline"] == "11 sounds missing in level.tscn"
    assert len(data["plan"]) == 5
    assert cli_main(["sound-pass", "--project", str(third_person)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("11 sounds missing in level.tscn")
    assert (
        cli_main(
            ["sound-pass", "--project", str(third_person), "--scene", "res://nope.tscn"]
        )
        == 1
    )


def test_scene_text_round_trips_untouched_sections(third_person: Path) -> None:
    for path in sorted((third_person / "game").rglob("*.tscn")):
        text = path.read_text()
        assert Scene(text).text() == text, path
    settings = (third_person / "project.godot").read_text()
    changed = scene_text.set_setting(settings, "audio", "ui/hover", '"res://x.wav"')
    assert (
        scene_text.set_setting(changed, "audio", "ui/hover", '"res://x.wav"') == changed
    )
    assert scene_text.set_setting("config_version=5\n", "audio", "ui/click", '"a"') == (
        'config_version=5\n\n[audio]\n\nui/click="a"\n'
    )
