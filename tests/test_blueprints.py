"""Catalogue validation: every rule catches its mistake on a small fixture."""

from __future__ import annotations

import re
import shutil
import tomllib
from collections.abc import Callable
from pathlib import Path

import pytest

from joinery import blueprints
from tests.blueprints.conftest import FixtureCatalogue, build_catalogue


@pytest.fixture
def fixture_catalogue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FixtureCatalogue:
    catalogue = build_catalogue(tmp_path / "catalogue")
    monkeypatch.setattr(blueprints, "BLUEPRINTS_DIR", catalogue.blueprints)
    monkeypatch.setattr(blueprints, "FEATURES_DIR", catalogue.features)
    return catalogue


def _edit(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text, (path, old)
    path.write_text(text.replace(old, new, 1))


def _append(path: Path, text: str) -> None:
    path.write_text(path.read_text() + text)


def test_fixture_catalogue_is_valid(fixture_catalogue: FixtureCatalogue) -> None:
    assert blueprints.validate_catalogue() == []
    assert [b.id for b in blueprints.list_blueprints()] == ["alpha", "beta"]
    assert [f.id for f in blueprints.list_features()] == [
        "controller", "lobby", "net", "solo", "words",
    ]


def _png(c: FixtureCatalogue) -> None:
    (c.blueprints / "alpha/overlay/game/sprite.png").write_bytes(b"\x89PNG\r\n\x1a\n")


def _extension(c: FixtureCatalogue) -> None:
    (c.features / "net/overlay/features/net/steam.gdextension").write_text("[configuration]\n")


def _library(c: FixtureCatalogue) -> None:
    target = c.features / "net/bin/libsteam.dylib"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"\xcf\xfa\xed\xfe")


def _unknown_kind(c: FixtureCatalogue) -> None:
    (c.blueprints / "alpha/overlay/game/notes.bin").write_bytes(b"\x00\x01")


def _embedded_media(c: FixtureCatalogue) -> None:
    (c.blueprints / "alpha/overlay/game/beep.tres").write_text(
        '[gd_resource type="AudioStreamWAV" format=3 uid="uid://bbeepbeep1"]\n\n'
        "[resource]\ndata = PackedByteArray(0, 1, 2)\n"
    )


def _required_external(c: FixtureCatalogue) -> None:
    _edit(c.features / "net/feature.toml", "required = false", "required = true")


def _outside(c: FixtureCatalogue) -> None:
    target = c.blueprints / "alpha/overlay/art/thing.tres"
    target.parent.mkdir(parents=True)
    target.write_text('[gd_resource type="Resource" format=3]\n')


def _reused_uid(c: FixtureCatalogue) -> None:
    uid = (c.blueprints / "alpha/overlay/game/level.gd.uid").read_text()
    (c.blueprints / "beta/overlay/game/level.gd.uid").write_text(uid)


def _missing_uid(c: FixtureCatalogue) -> None:
    (c.features / "net/overlay/features/net/net.gd.uid").unlink()


def _scene_without_uid(c: FixtureCatalogue) -> None:
    scene = c.blueprints / "beta/overlay/game/level.tscn"
    lines = scene.read_text().split("\n", 1)
    scene.write_text('[gd_scene load_steps=2 format=3]\n' + lines[1])


def _unknown_tool(c: FixtureCatalogue) -> None:
    _edit(c.blueprints / "alpha/blueprint.toml", '"krita"]', '"krita", "photoshop"]')


def _missing_preset(c: FixtureCatalogue) -> None:
    _edit(c.blueprints / "alpha/blueprint.toml", 'bible_preset = "none"', 'bible_preset = "nope"')


def _missing_level(c: FixtureCatalogue) -> None:
    _edit(c.blueprints / "beta/blueprint.toml", "res://game/level.tscn", "res://game/gone.tscn")


def _ui_action(c: FixtureCatalogue) -> None:
    _append(c.features / "controller/feature.toml", "\n[input.ui_accept]\npad_buttons = [0]\n")


def _odd_action(c: FixtureCatalogue) -> None:
    _append(c.blueprints / "beta/blueprint.toml", '\n[input.fly]\nkeys = ["F"]\n')


def _autoload_twice(c: FixtureCatalogue) -> None:
    _append(c.features / "solo/feature.toml", '\n[autoload]\nNet = "res://features/solo/solo.gd"\n')


def _setting_clash(c: FixtureCatalogue) -> None:
    _edit(c.features / "words/feature.toml", "[project.settings]\n", '[project.settings]\n"online/default_port" = 1\n')


def _root_twice(c: FixtureCatalogue) -> None:
    _edit(c.features / "solo/feature.toml", 'conflicts = ["net"]', 'conflicts = ["net"]\nextra_roots = ["locale"]')


def _bad_root(c: FixtureCatalogue) -> None:
    _edit(c.features / "solo/feature.toml", 'conflicts = ["net"]', 'conflicts = ["net"]\nextra_roots = ["addons"]')


def _cycle(c: FixtureCatalogue) -> None:
    _edit(c.features / "net/feature.toml", 'enhances = ["lobby"]', 'requires = ["lobby"]')


def _engine_key(c: FixtureCatalogue) -> None:
    _edit(c.blueprints / "alpha/blueprint.toml", "[project.settings]\n", '[project.settings]\n"game/blueprint" = "x"\n')


def _typo(c: FixtureCatalogue) -> None:
    _edit(c.blueprints / "alpha/blueprint.toml", 'icon = "Node2D"', 'icon = "Node2D"\ncolour = "red"')


def _folder_name(c: FixtureCatalogue) -> None:
    shutil.move(c.features / "solo", c.features / "alone")


def _unknown_requirement(c: FixtureCatalogue) -> None:
    _edit(c.features / "lobby/feature.toml", 'requires = ["net"]', 'requires = ["net", "chat"]')


def _default_unsupported(c: FixtureCatalogue) -> None:
    _edit(c.blueprints / "beta/blueprint.toml", 'default_features = ["controller"]', 'default_features = ["lobby"]')


def _adapter_unknown(c: FixtureCatalogue) -> None:
    (c.features / "net/adapters/gamma/overlay").mkdir(parents=True)


def _preset_twice(c: FixtureCatalogue) -> None:
    preset = c.features / "solo/export_presets/server.cfg"
    preset.parent.mkdir()
    shutil.copy(c.features / "net/export_presets/server.cfg", preset)
    _edit(c.features / "solo/feature.toml", 'conflicts = ["net"]', "conflicts = []")


def _file_clash(c: FixtureCatalogue) -> None:
    # Adapter files live in the feature's own folders, so two features cannot
    # write one path; a core file that a blueprint repeats is the real case.
    shutil.copy(c.blueprints / "_core/overlay/core/probe.gd", c.blueprints / "alpha/overlay/game/probe.gd")
    target = c.blueprints / "alpha/overlay/core"
    target.mkdir()
    shutil.copy(c.blueprints / "_core/overlay/core/probe.gd", target / "probe.gd")


CASES: dict[str, tuple[Callable[[FixtureCatalogue], None], str]] = {
    "banned asset": (_png, "game/sprite.png is an asset file"),
    "extension": (_extension, "steam.gdextension is a binary or extension"),
    "native library": (_library, "bin/libsteam.dylib is a binary or extension"),
    "unknown file kind": (_unknown_kind, "game/notes.bin is not a code or text file"),
    "embedded media": (_embedded_media, "game/beep.tres embeds a AudioStreamWAV"),
    "required external": (_required_external, "external Nothing is required"),
    "outside roots": (_outside, "art/thing.tres is outside its folders"),
    "reused uid": (_reused_uid, "reuses uid://"),
    "missing gd uid": (_missing_uid, "features/net/net.gd has no .gd.uid"),
    "scene without uid": (_scene_without_uid, "game/level.tscn header has no uid"),
    "unknown tool": (_unknown_tool, "suggested tool photoshop is not in the tool catalog"),
    "missing preset": (_missing_preset, "bible preset nope does not exist"),
    "missing level": (_missing_level, "hooks.level_scene res://game/gone.tscn is not shipped"),
    "ui action": (_ui_action, "input ui_accept would replace Godot's built-in"),
    "odd action": (_odd_action, "input fly is not in the standard action vocabulary"),
    "autoload twice": (_autoload_twice, "autoload Net is added by both"),
    "setting clash": (_setting_clash, "set online/default_port differently"),
    "root twice": (_root_twice, "folder locale/ is already used by"),
    "bad extra root": (_bad_root, "extra root addons is not allowed"),
    "requires cycle": (_cycle, "Features require each other in a cycle"),
    "engine key": (_engine_key, "game/blueprint is written by the engine"),
    "typo": (_typo, "colour: Extra inputs are not permitted"),
    "folder name": (_folder_name, "folder name alone differs from its id"),
    "unknown requirement": (_unknown_requirement, "requires unknown feature chat"),
    "default unsupported": (_default_unsupported, "default feature lobby does not support it"),
    "adapter unknown": (_adapter_unknown, "adapter for unknown blueprint gamma"),
    "preset twice": (_preset_twice, "both add export preset 'Linux Dedicated Server'"),
    "file clash": (_file_clash, "_core and alpha both write core/probe.gd"),
}


def test_unshippable_files_are_never_copied(fixture_catalogue: FixtureCatalogue) -> None:
    _png(fixture_catalogue)
    _unknown_kind(fixture_catalogue)
    files = blueprints._overlay(fixture_catalogue.blueprints / "alpha/overlay")
    assert "game/sprite.png" not in files
    assert "game/notes.bin" not in files
    assert "game/level.gd" in files


@pytest.mark.parametrize("case", sorted(CASES))
def test_validation_catches(case: str, fixture_catalogue: FixtureCatalogue) -> None:
    breaks, message = CASES[case]
    breaks(fixture_catalogue)
    problems = blueprints.validate_catalogue()
    assert any(message in problem for problem in problems), problems


def test_bad_unit_is_reported_not_raised(fixture_catalogue: FixtureCatalogue) -> None:
    (fixture_catalogue.features / "broken").mkdir()
    (fixture_catalogue.features / "broken/feature.toml").write_text("id = [")
    problems = blueprints.validate_catalogue()
    assert any("broken/feature.toml" in p for p in problems), problems
    with pytest.raises(ValueError, match="feature.toml"):
        blueprints.list_features()


def _godot_text_to_id(text: str) -> int:
    """ResourceUID::text_to_id, as Godot 4.7 decodes a uid text."""
    number = 0
    for char in text.removeprefix("uid://"):
        number = (number * 34) % 2**64
        number += ord(char) - ord("a") if char.islower() else ord(char) - ord("0") + 25
    return number & 0x7FFFFFFFFFFFFFFF


def _godot_id_to_text(number: int) -> str:
    """ResourceUID::id_to_text: base 34, digits a-y then 0-8."""
    digits = "abcdefghijklmnopqrstuvwxy012345678"
    out = ""
    while True:
        number, rest = divmod(number, 34)
        out = digits[rest] + out
        if not number:
            return "uid://" + out


def test_new_uid_is_godot_text() -> None:
    uids = {blueprints.new_uid() for _ in range(500)}
    assert len(uids) == 500
    for uid in uids:
        assert re.fullmatch(r"uid://[a-y0-8]{1,13}", uid), uid
        # Godot writes back exactly the text it read: no other base round-trips.
        assert _godot_id_to_text(_godot_text_to_id(uid)) == uid


def test_game_record_replaces_only_its_table() -> None:
    record = blueprints.GameRecord(
        blueprint="alpha", blueprint_version=2, features=["net"], feature_versions={"net": 1}
    )
    old = (
        "# Tools for this project.\n[tools]\nenabled = [\"godot\"]\n\n"
        "[game]\nblueprint = \"alpha\"\nblueprint_version = 1\nfeatures = []\n"
        "feature_versions = {}\n\n# Custom tools\n[[tools.custom]]\nid = \"x\"\n"
    )
    new = blueprints.record_text(old, record)
    assert new == (
        "# Tools for this project.\n[tools]\nenabled = [\"godot\"]\n\n"
        "[game]\nblueprint = \"alpha\"\nblueprint_version = 2\nfeatures = [\"net\"]\n"
        "feature_versions = { net = 1 }\n\n# Custom tools\n[[tools.custom]]\nid = \"x\"\n"
    )
    assert blueprints.record_text(new, record) == new
    empty = blueprints.GameRecord(blueprint="alpha", blueprint_version=1)
    assert blueprints.record_text("", empty).endswith("feature_versions = {}\n")
    at_end = blueprints.record_text("[tools]\nenabled = []\n", empty)
    assert at_end.startswith("[tools]\nenabled = []\n\n[game]\n")
    assert blueprints.record_text(at_end, record).count("[game]") == 1


@pytest.mark.parametrize(
    "old",
    [
        '[game]\n# picked in the wizard\nblueprint = "alpha"\nblueprint_version = 1\n',
        '[game] # chosen\nblueprint = "alpha"\nblueprint_version = 1\n',
        '[game]\nblueprint = "alpha"\n\n# later\nblueprint_version = 1\n\n# Tools\n[tools]\nx = 1\n',
    ],
)
def test_game_record_with_comments_stays_valid_toml(old: str) -> None:
    record = blueprints.GameRecord(blueprint="beta", blueprint_version=3, features=["net"])
    new = blueprints.record_text(old, record)
    data = tomllib.loads(new)
    assert data["game"]["blueprint"] == "beta"
    assert data["game"]["blueprint_version"] == 3
    assert new.count("[game]") == 1
    if "[tools]" in old:
        assert data["tools"] == {"x": 1}
        assert "# Tools\n[tools]" in new
    assert blueprints.record_text(new, record) == new
