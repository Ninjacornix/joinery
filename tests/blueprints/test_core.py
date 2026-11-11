"""The shared game core (`blueprints/_core`): its unit, its files, and its
behaviour in stock Godot.

The GDScript side (`res://tests/support/core_test.gd`) drives menus by keyboard
and mouse, pauses, writes settings to disk, swaps sessions, spawns, runs
actions, and saves and restores world state. It runs on the blank blueprint,
the smallest game the core ships with.
"""

from __future__ import annotations

import re
from pathlib import Path

from joinery import blueprints
from tests.blueprints.godot_run import boot, make_project, needs_godot, run_godot

CORE = blueprints.BLUEPRINTS_DIR / blueprints.CORE_DIR_NAME
OVERLAY = CORE / "overlay"
AUTOLOADS = {
    "Settings": "res://core/settings.gd",
    "GameEvents": "res://core/game_events.gd",
    "Menus": "res://core/menus.gd",
    "Game": "res://core/game.gd",
    "Music": "res://core/audio/music.gd",
    "UiSounds": "res://core/audio/ui_sounds.gd",
}
CORE_TEST = "res://tests/support/core_test.gd"
GODOT_UID = re.compile(r"^uid://[a-y0-8]+$")
EXT_RESOURCE = re.compile(r'\[ext_resource [^\]]*uid="(uid://[^"]+)" path="res://([^"]+)"')
HEADER_UID = re.compile(r'^\[gd_(?:scene|resource)[^\]]*uid="(uid://[^"]+)"')
FUNC = re.compile(r"^(?:static )?func (\w+)\((.*?)\)\s*(->\s*[^:]+)?:", re.MULTILINE | re.DOTALL)
VAR = re.compile(r"^\s*(?:@\w+(?:\([^)]*\))?\s+)*(?:static )?var (\w+)\s*([:=])?", re.MULTILINE)


def overlay_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file() and p.name != ".DS_Store")


def check_overlay(root: Path, roots: tuple[str, ...]) -> list[str]:
    """Problems with an overlay: roots, banned types, uids, typing."""
    problems: list[str] = []
    uids: dict[str, str] = {}
    script_uids: dict[str, str] = {}
    for path in overlay_files(root):
        rel = path.relative_to(root).as_posix()
        if not rel.startswith(roots):
            problems.append(f"{rel} is outside {roots}")
        if path.suffix.lower() in blueprints.BANNED_SUFFIXES:
            problems.append(f"{rel} is an asset file; overlays are code-only greybox")
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".gd":
            sidecar = path.with_name(path.name + ".uid")
            if not sidecar.is_file():
                problems.append(f"{rel} has no .gd.uid")
            else:
                uid = sidecar.read_text().strip()
                script_uids[rel] = uid
            problems.extend(f"{rel}: {p}" for p in typing_problems(text))
        if path.suffix == ".uid":
            uid = text.strip()
            if not GODOT_UID.match(uid):
                problems.append(f"{rel}: {uid} is not a Godot uid")
            if uid in uids:
                problems.append(f"{rel} reuses the uid of {uids[uid]}")
            uids[uid] = rel
        if path.suffix in (".tscn", ".tres"):
            header = HEADER_UID.match(text)
            if header is None:
                problems.append(f"{rel} has no uid in its header")
            else:
                if header.group(1) in uids:
                    problems.append(f"{rel} reuses the uid of {uids[header.group(1)]}")
                uids[header.group(1)] = rel
    for path in overlay_files(root):
        if path.suffix != ".tscn":
            continue
        for uid, target in EXT_RESOURCE.findall(path.read_text(encoding="utf-8")):
            if target in script_uids and script_uids[target] != uid:
                problems.append(f"{path.name} points at {target} with a stale uid {uid}")
    return problems


def split_params(params: str) -> list[str]:
    """Parameters split on top-level commas (defaults may hold calls)."""
    parts: list[str] = []
    depth = 0
    current = ""
    for char in params.replace("\n", " "):
        depth += char in "([{"
        depth -= char in ")]}"
        if char == "," and depth == 0:
            parts.append(current.strip())
            current = ""
        else:
            current += char
    parts.append(current.strip())
    return [p for p in parts if p]


def typing_problems(text: str) -> list[str]:
    """Named functions need typed parameters and a return type; vars a type."""
    problems: list[str] = []
    for match in FUNC.finditer(text):
        name, params, returns = match.groups()
        if returns is None:
            problems.append(f"func {name} has no return type")
        for param in split_params(params):
            if ":" not in param.split("=")[0] and not param.startswith("..."):
                problems.append(f"func {name}: parameter {param!r} has no type")
    for match in VAR.finditer(text):
        if match.group(2) != ":":
            problems.append(f"var {match.group(1)} has no type")
    return problems


def test_core_unit_declares_autoloads_and_pause() -> None:
    core = blueprints.load_blueprint(CORE)
    assert core.kind == "core"
    assert list(core.merge.autoload.items()) == list(AUTOLOADS.items())
    assert core.merge.input["pause"].keys == ["Escape", "P"]
    for path in AUTOLOADS.values():
        assert (OVERLAY / path.removeprefix("res://")).is_file(), path


def test_core_overlay_is_tidy() -> None:
    assert check_overlay(OVERLAY, blueprints.CORE_ROOTS) == []
    assert (OVERLAY / "default_bus_layout.tres").is_file()
    assert (OVERLAY / "tests/support/play.gd").is_file()


def test_typing_check_catches_untyped_code() -> None:
    sample = (
        "var loose = 1\nvar fine: int = 2\nfunc f(a, b: int, c: Color = Color(0, 0, 0)):\n"
        "\tvar inner = 3\n"
    )
    assert typing_problems(sample) == [
        "func f has no return type",
        "func f: parameter 'a' has no type",
        "var loose has no type",
        "var inner has no type",
    ]


def test_core_catalogue_has_no_problems() -> None:
    mine = [p for p in blueprints.validate_catalogue() if "_core" in p or "blank" in p]
    assert mine == []


def test_core_merges_into_project(tmp_path: Path) -> None:
    project = make_project(tmp_path, "blank")
    text = (project / "project.godot").read_text()
    positions = [text.index(f'{name}="*{path}"') for name, path in AUTOLOADS.items()]
    assert positions == sorted(positions), "autoloads keep their order"
    assert 'run/main_scene="res://core/main.tscn"' in text
    assert "pause={" in text
    for path in ("core/main.tscn", "tests/support/play.gd", "default_bus_layout.tres"):
        assert (project / path).is_file(), path


@needs_godot
def test_core_behaviour_in_godot(tmp_path: Path) -> None:
    project = make_project(tmp_path, "blank")
    settings = tmp_path / "settings" / "core.cfg"
    result = run_godot(
        project,
        CORE_TEST,
        ("--autostart", f"--settings-path={settings}"),
        timeout=180,
        marker="core",
    )
    assert "core: ok" in result.output
    assert settings.is_file(), "settings are written where --settings-path says"


@needs_godot
def test_core_boots_to_menu_and_straight_to_level(tmp_path: Path) -> None:
    project = make_project(tmp_path, "blank")
    settings = f"--settings-path={tmp_path / 'boot.cfg'}"
    boot(project, frames=90, user_args=(settings,))
    boot(project, frames=90, user_args=("--autostart", settings))
