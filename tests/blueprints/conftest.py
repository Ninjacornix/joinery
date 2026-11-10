"""A tiny fixture catalogue: core, two blueprints, five features.

Tests point `joinery.blueprints` at it, so the engine is tested on its own,
independent of the real blueprints. Everything is code-only greybox and every
`.gd` / `.tscn` gets a fresh uid, as the real catalogue must.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from joinery import blueprints
from joinery.blueprints import new_uid


@dataclass(frozen=True)
class FixtureCatalogue:
    root: Path
    blueprints: Path
    features: Path


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _script(path: Path, text: str) -> str:
    """Write a .gd with its .gd.uid; return the uid."""
    uid = new_uid()
    _write(path, text)
    _write(path.with_name(path.name + ".uid"), uid + "\n")
    return uid


def _scene(path: Path, res_script: str, script_uid: str, root_type: str, extra: str = "") -> None:
    _write(
        path,
        f'[gd_scene load_steps=2 format=3 uid="{new_uid()}"]\n\n'
        f'[ext_resource type="Script" uid="{script_uid}" path="{res_script}" id="1_s"]\n\n'
        f'[node name="Root" type="{root_type}"]\nscript = ExtResource("1_s")\n{extra}',
    )


MAIN_GD = """extends Node

## Fixture main scene: proves the merged project boots and is wired up.


func _ready() -> void:
	var probe: Node = get_node_or_null(^"/root/Probe")
	var problems: PackedStringArray = PackedStringArray()
	if probe == null:
		problems.append("Probe autoload missing")
	if not InputMap.has_action(&"pause"):
		problems.append("pause action missing")
	if not InputMap.has_action(&"move_left"):
		problems.append("move_left action missing")
	var level_path: String = str(ProjectSettings.get_setting("game/hooks/level_scene", ""))
	var packed: PackedScene = load(level_path) as PackedScene
	if packed == null:
		problems.append("level scene missing")
	else:
		add_child(packed.instantiate())
	if ProjectSettings.get_setting("game/blueprint", "") == "":
		problems.append("game/blueprint not set")
	if problems.is_empty():
		print("fixture boot: ok")
	else:
		for problem: String in problems:
			print("FAIL: ", problem)
"""

PROBE_GD = """extends Node

var started: bool = false


func _ready() -> void:
	started = true
"""

LEVEL_2D = """extends Node2D


func _ready() -> void:
	var floor_rect: ColorRect = ColorRect.new()
	floor_rect.size = Vector2(640, 32)
	floor_rect.position = Vector2(0, 600)
	floor_rect.color = Color(0.3, 0.3, 0.35)
	add_child(floor_rect)
"""

LEVEL_3D = """extends Node3D


func _ready() -> void:
	var box: MeshInstance3D = MeshInstance3D.new()
	box.mesh = BoxMesh.new()
	add_child(box)
"""

FEATURE_GD = """extends Node

var ready_count: int = 0


func _ready() -> void:
	ready_count += 1
"""

SERVER_PRESET = """[preset.0]

name="Linux Dedicated Server"
platform="Linux"
runnable=false
dedicated_server=true
custom_features="dedicated_server"
export_filter="all_resources"
include_filter=""
exclude_filter=""
export_path="build/server/server.x86_64"

[preset.0.options]

binary_format/embed_pck=false
"""


def build_catalogue(base: Path) -> FixtureCatalogue:
    bp = base / "blueprints"
    ft = base / "features"

    core = bp / "_core"
    _write(
        core / "unit.toml",
        'id = "_core"\nname = "Game core"\nkind = "core"\nversion = 1\n\n'
        '[autoload]\nProbe = "res://core/probe.gd"\n\n'
        '[input.pause]\nkeys = ["Escape", "P"]\n',
    )
    _script(core / "overlay/core/probe.gd", PROBE_GD)
    main_uid = _script(core / "overlay/core/main.gd", MAIN_GD)
    _scene(core / "overlay/core/main.tscn", "res://core/main.gd", main_uid, "Node")

    for name, dimension, level, root_type, extra in (
        ("alpha", "2d", LEVEL_2D, "Node2D", ""),
        ("beta", "3d", LEVEL_3D, "Node3D", ""),
    ):
        unit = bp / name
        width = 1280 if name == "alpha" else 1920
        _write(
            unit / "blueprint.toml",
            f'id = "{name}"\nname = "{name.title()}"\n'
            f'pitch = "A tiny {dimension} test game."\nversion = 1\n'
            f'dimension = "{dimension}"\nicon = "{root_type}"\n'
            'default_features = ["controller"]\nbible_preset = "none"\n'
            'suggested_tools = ["godot", "krita"]\nmain_scene = "res://core/main.tscn"\n\n'
            '[hooks]\nlevel_scene = "res://game/level.tscn"\nplayer_scene = ""\n'
            "spawnable_scenes = []\n\n"
            '[brief]\ngenre = ["test"]\ncamera = "Fixed."\n'
            'core_loop = "30 s: move."\nprogression = "None."\n'
            'verbs = [["Run", "move_left / move_right", "Lean"]]\n\n'
            f'[project.settings]\n"display/window/size/viewport_width" = {width}\n'
            '"rendering/environment/defaults/default_clear_color" = { raw = "Color(0.1, 0.1, 0.12, 1)" }\n\n'
            '[input.move_left]\nkeys = ["A", "Left"]\n\n'
            '[input.move_right]\nkeys = ["D", "Right"]\n\n'
            '[input.jump]\nkeys = ["Space"]\nmouse = ["left"]\n'
            + ('\n[input.move_forward]\nkeys = ["W"]\n' if name == "beta" else ""),
        )
        uid = _script(unit / "overlay/game/level.gd", level)
        _scene(unit / "overlay/game/level.tscn", "res://game/level.gd", uid, root_type, extra)
        _write(
            unit / "overlay/tests/game/smoke.gd",
            "extends SceneTree\n\n\nfunc _init() -> void:\n\tprint(\"smoke: ok\")\n\tquit(0)\n",
        )
        _write(unit / "overlay/tests/game/smoke.gd.uid", new_uid() + "\n")

    def feature(name: str, toml: str, files: dict[str, str] | None = None) -> Path:
        unit = ft / name
        _write(unit / "feature.toml", toml)
        for rel, text in (files or {}).items():
            if rel.endswith(".gd"):
                _script(unit / "overlay" / rel, text)
            else:
                _write(unit / "overlay" / rel, text)
        return unit

    feature(
        "controller",
        'id = "controller"\nname = "Controller"\nsummary = "Pad bindings."\nversion = 1\n\n'
        '[input.move_left]\npad_axes = ["0-"]\npad_buttons = [13]\ndeadzone = 0.25\n\n'
        '[input.move_right]\npad_axes = ["0+"]\npad_buttons = [14]\ndeadzone = 0.25\n\n'
        '[input.jump]\npad_buttons = [0]\n\n'
        '[input.move_forward]\npad_axes = ["1-"]\n\n'
        '[input.pause]\npad_buttons = [6]\n',
        {"features/controller/pad.gd": FEATURE_GD},
    )
    net = feature(
        "net",
        'id = "net"\nname = "Online play"\nsummary = "LAN games."\nversion = 1\n'
        'enhances = ["lobby"]\n\n'
        '[[external]]\nname = "Nothing"\nsource = "TBD, pending user approval"\n'
        'version = "TBD, pending user approval"\n'
        'install = "separate, user-approved step; not designed yet"\nrequired = false\n\n'
        '[project.settings]\n"online/default_port" = 24565\n"online/lan_broadcast" = "255.255.255.255"\n\n'
        '[autoload]\nNet = "res://features/net/net.gd"\n\n'
        '[adapters.alpha.project.settings]\n"online/alpha_turns" = true\n',
        {
            "features/net/net.gd": FEATURE_GD,
            "tests/features/net/net_test.gd": "extends SceneTree\n\n\nfunc _init() -> void:\n\tquit(0)\n",
        },
    )
    _write(net / "export_presets/server.cfg", SERVER_PRESET)
    _script(net / "adapters/alpha/overlay/features/net/alpha_turns.gd", FEATURE_GD)
    feature(
        "lobby",
        'id = "lobby"\nname = "Lobby"\nsummary = "Waiting room."\nversion = 1\n'
        'requires = ["net"]\nblueprints = ["alpha"]\n\n'
        '[autoload]\nLobby = "res://features/lobby/lobby.gd"\n',
        {"features/lobby/lobby.gd": FEATURE_GD},
    )
    feature(
        "solo",
        'id = "solo"\nname = "Solo mode"\nsummary = "Offline only."\nversion = 1\n'
        'conflicts = ["net"]\n\n[project.settings]\n"solo/enabled" = true\n',
        {"features/solo/solo.gd": FEATURE_GD},
    )
    feature(
        "words",
        'id = "words"\nname = "Translations"\nsummary = "Languages."\nversion = 1\n'
        'extra_roots = ["locale"]\n\n'
        '[project.settings]\n"internationalization/locale/fallback" = "en"\n\n'
        '[append]\n"internationalization/locale/translations" = ["res://locale/en.po"]\n',
        {
            "features/words/words.gd": FEATURE_GD,
            "locale/en.po": 'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n',
        },
    )
    return FixtureCatalogue(base, bp, ft)


@pytest.fixture
def fixture_catalogue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FixtureCatalogue]:
    catalogue = build_catalogue(tmp_path / "catalogue")
    monkeypatch.setattr(blueprints, "BLUEPRINTS_DIR", catalogue.blueprints)
    monkeypatch.setattr(blueprints, "FEATURES_DIR", catalogue.features)
    yield catalogue
