"""The third-person-3d blueprint: catalogue checks and headless play tests.

The static checks need nothing but the repo. The play tests build a project
with `joinery.blueprints` and run `res://tests/game/play_test.gd` in stock
Godot; they skip cleanly when the engine helpers or a Godot binary are absent.
"""

from __future__ import annotations

import importlib
import re
import tomllib
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
BLUEPRINT = ROOT / "blueprints" / "third-person-3d"
OVERLAY = BLUEPRINT / "overlay"
BLUEPRINT_ID = "third-person-3d"
PLAY_TEST = "res://tests/game/play_test.gd"
USER_ARGS = ("--autostart", "--settings-path={settings}")

BANNED_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".webp", ".svg", ".glb", ".gltf", ".blend",
    ".wav", ".ogg", ".mp3", ".ttf", ".otf",
}
WRITE_ROOTS = ("game/", "tests/game/")
# res:// roots provided by the shared game core, not by this blueprint.
CORE_ROOTS = ("res://core/", "res://tests/support/")
VOCABULARY = {
    "move_left", "move_right", "move_up", "move_down", "move_forward", "move_back",
    "look_left", "look_right", "look_up", "look_down", "jump", "interact", "attack",
    "dash", "sprint", "crouch", "restart", "pause",
}
KEY_NAMES = (
    {chr(c) for c in range(65, 91)}
    | {str(d) for d in range(10)}
    | {"Space", "Escape", "Tab", "Backspace", "Enter", "Left", "Up", "Right", "Down",
       "Shift", "Ctrl", "Alt"}
    | {f"F{n}" for n in range(1, 13)}
)
UID_RE = re.compile(r"^uid://[a-y0-8]+$")


def _blueprint() -> dict[str, Any]:
    return tomllib.loads((BLUEPRINT / "blueprint.toml").read_text(encoding="utf-8"))


def _overlay_files() -> list[Path]:
    return sorted(p for p in OVERLAY.rglob("*") if p.is_file() and p.name != ".DS_Store")


def _res(path: Path) -> str:
    return "res://" + path.relative_to(OVERLAY).as_posix()


# --- static checks -----------------------------------------------------------


def test_blueprint_toml_matches_the_catalogue_entry() -> None:
    bp = _blueprint()
    assert bp["id"] == BLUEPRINT_ID
    assert bp["dimension"] == "3d"
    assert bp["version"] == 1
    assert bp["bible_preset"] == "lowpoly-3d"
    assert bp["suggested_tools"] == ["godot", "blender", "material-maker", "krita", "surge-xt"]
    assert bp["default_features"] == ["controller", "save-load"]
    assert bp["main_scene"] == "res://core/main.tscn"
    assert bp["name"] and bp["pitch"] and bp["icon"]
    brief = bp["brief"]
    for key in ("genre", "camera", "core_loop", "progression", "verbs"):
        assert brief[key], key
    assert all(len(verb) == 3 for verb in brief["verbs"])


def test_suggested_tools_are_catalogue_tools() -> None:
    policy = tomllib.loads((ROOT / "policy" / "preferred_tools.toml").read_text(encoding="utf-8"))
    known = {tool["id"] for tool in policy["tool"]}
    assert set(_blueprint()["suggested_tools"]) <= known


def test_hooks_point_at_overlay_scenes() -> None:
    hooks = _blueprint()["hooks"]
    for key in ("level_scene", "player_scene"):
        path = OVERLAY / hooks[key].removeprefix("res://")
        assert path.is_file(), hooks[key]
    for scene in hooks["spawnable_scenes"]:
        assert (OVERLAY / scene.removeprefix("res://")).is_file(), scene


def test_inputs_use_the_shared_vocabulary_and_known_keys() -> None:
    inputs = _blueprint()["input"]
    assert set(inputs) <= VOCABULARY
    needed = {"move_forward", "move_back", "move_left", "move_right", "jump", "sprint",
              "interact", "look_left", "look_right", "look_up", "look_down"}
    assert needed <= set(inputs)
    for action, spec in inputs.items():
        assert set(spec) <= {"keys", "mouse", "pad_buttons", "pad_axes", "deadzone", "create"}
        assert spec["keys"], action
        assert set(spec["keys"]) <= KEY_NAMES, action
    keys = [key for spec in inputs.values() for key in spec["keys"]]
    assert len(keys) == len(set(keys)), "one key bound to two actions"


def test_overlay_writes_only_its_roots_and_no_media() -> None:
    for path in _overlay_files():
        rel = path.relative_to(OVERLAY).as_posix()
        assert rel.startswith(WRITE_ROOTS), rel
        assert path.suffix.lower() not in BANNED_SUFFIXES, rel


def test_every_script_has_a_uid_and_uids_are_unique() -> None:
    seen: dict[str, str] = {}
    for path in _overlay_files():
        if path.suffix == ".gd":
            side = path.with_name(path.name + ".uid")
            assert side.is_file(), f"{path.name} has no .uid"
            uid = side.read_text(encoding="utf-8").strip()
        elif path.suffix == ".tscn":
            header = path.read_text(encoding="utf-8").splitlines()[0]
            match = re.search(r'uid="([^"]+)"', header)
            assert match, f"{path.name} header has no uid"
            uid = match.group(1)
        else:
            continue
        assert UID_RE.match(uid), uid
        assert uid not in seen, f"{path.name} and {seen.get(uid)} share {uid}"
        seen[uid] = path.name


def test_scene_references_resolve_inside_the_overlay() -> None:
    uids: dict[str, str] = {}
    for path in _overlay_files():
        if path.suffix == ".gd":
            uids[_res(path)] = path.with_name(path.name + ".uid").read_text(encoding="utf-8").strip()
        elif path.suffix == ".tscn":
            header = path.read_text(encoding="utf-8").splitlines()[0]
            uids[_res(path)] = re.search(r'uid="([^"]+)"', header).group(1)  # type: ignore[union-attr]
    ext = re.compile(r'\[ext_resource type="\w+" uid="([^"]+)" path="([^"]+)"')
    for scene in OVERLAY.rglob("*.tscn"):
        for uid, res in ext.findall(scene.read_text(encoding="utf-8")):
            assert res in uids, f"{scene.name} -> missing {res}"
            assert uids[res] == uid, f"{scene.name} -> {res} uid mismatch"


def test_script_paths_exist_in_overlay_or_core() -> None:
    ref = re.compile(r'"(res://[^"]+)"')
    present = {_res(p) for p in _overlay_files()}
    for script in OVERLAY.rglob("*.gd"):
        for res in ref.findall(script.read_text(encoding="utf-8")):
            if res.startswith(CORE_ROOTS):
                continue
            assert res in present, f"{script.name} -> {res}"


def test_level_has_the_core_contract_nodes() -> None:
    level = (OVERLAY / "game" / "level.tscn").read_text(encoding="utf-8")
    assert '[node name="Players" type="Node3D" parent="."]' in level
    assert 'groups=["player_spawn"]' in level
    coins = re.findall(r'save_id = "(coin_\d+)"', level)
    assert len(coins) == 5 and len(set(coins)) == 5
    assert "Game.level_ready(self)" in (OVERLAY / "game" / "level.gd").read_text(encoding="utf-8")


def test_player_scene_follows_the_player_contract() -> None:
    script = (OVERLAY / "game" / "player" / "player.gd").read_text(encoding="utf-8")
    assert "const SYNC_PROPERTIES: Array[String]" in script
    assert '@export var save_id: String = "player"' in script
    assert "func save_state() -> Dictionary" in script
    assert "func load_state(state: Dictionary) -> void" in script
    assert "is_multiplayer_authority()" in script
    scene = (OVERLAY / "game" / "player" / "player.tscn").read_text(encoding="utf-8")
    assert 'groups=["player", "saveable"]' in scene
    assert 'type="SpringArm3D"' in scene and 'type="CapsuleMesh"' in scene


def test_saveable_world_objects_have_state_methods() -> None:
    for name in ("coin", "crate", "lever"):
        script = (OVERLAY / "game" / "props" / f"{name}.gd").read_text(encoding="utf-8")
        assert "@export var save_id: String" in script, name
        assert "func save_state() -> Dictionary" in script, name
        assert "func load_state(state: Dictionary) -> void" in script, name
        assert "GameEvents.world_changed.emit(self)" in script, name


def test_engine_catalogue_lists_the_blueprint() -> None:
    blueprints = _engine("joinery.blueprints")
    ids = [bp.id for bp in blueprints.list_blueprints()]
    assert BLUEPRINT_ID in ids
    described = blueprints.describe(BLUEPRINT_ID)
    assert described["dimension"] == "3d"
    known = {feature.id for feature in blueprints.list_features()}
    # Sibling features land separately; until then only their absence may show.
    pending = {
        f"blueprint {BLUEPRINT_ID}: default feature {fid} does not exist"
        for fid in _blueprint()["default_features"]
        if fid not in known
    }
    problems = [
        p for p in blueprints.validate_catalogue() if BLUEPRINT_ID in p and p not in pending
    ]
    assert problems == []


# --- headless play -------------------------------------------------------------


def _engine(name: str) -> ModuleType:
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        pytest.skip(f"{name} is not available yet ({exc.name})")


def _all_features(blueprints: ModuleType) -> list[str]:
    ids: list[str] = []
    for feature in blueprints.list_features():
        allowed = list(feature.blueprints)
        if "*" in allowed or BLUEPRINT_ID in allowed:
            ids.append(feature.id)
    return ids


def _play(tmp_path: Path, features: list[str]) -> None:
    run = _engine("tests.blueprints.godot_run")
    if run.godot_binary() is None:
        pytest.skip("no Godot binary (set JOINERY_TEST_GODOT)")
    project = run.make_project(tmp_path, BLUEPRINT_ID, features)
    done = run.run_godot(project, PLAY_TEST, user_args=USER_ARGS, timeout=240)
    output = str(getattr(done, "stdout", "")) + str(getattr(done, "output", ""))
    if output:
        assert f"{BLUEPRINT_ID}: ok" in output
        assert "FAIL:" not in output


def test_play_blueprint_alone(tmp_path: Path) -> None:
    _play(tmp_path, [])


def test_play_with_all_features(tmp_path: Path) -> None:
    blueprints = _engine("joinery.blueprints")
    features = _all_features(blueprints)
    if not features:
        pytest.skip("no features in the catalogue yet")
    _play(tmp_path, features)
