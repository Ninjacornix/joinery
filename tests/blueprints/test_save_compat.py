"""Old saves keep loading, in stock Godot (ADR 0066).

On two blueprints, a save is made the way playing makes one (the save-load feature's
own test, ``--save-phase=save``: a coin or a gem taken, then slot 1 saved) and kept
with ``joinery saves add``. Renaming a field a saveable node writes (the coin's
``collected``, the arena's ``score``) loses that data on load, and the check names
the save and the key. Raising ``save/data_version`` and adding the step in
``res://game/save_migrations.gd`` brings it back. A save from a version the game has
no step for fails to load, with the reason.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from joinery import saves, scene_text
from tests.blueprints.godot_run import (
    godot_binary,
    make_project,
    needs_godot,
    run_godot,
)

TEST = "res://tests/features/save-load/save_load_test.gd"
#: (file, [(old, new) text], the renamed key, migration) per blueprint: a field renamed
#: in save_state and load_state, and the step that fixes old saves.
RENAMES = {
    "platformer-2d": (
        "game/props/coin.gd",
        [('{"collected":', '{"taken":'), ('get("collected"', 'get("taken"')],
        "collected",
        """extends RefCounted
## Saves before version 2 called a coin's flag "collected".


func migrations() -> Dictionary:
	return {1: _coins_taken}


func _coins_taken(save: Dictionary) -> Dictionary:
	var nodes: Dictionary = save["nodes"]
	for id: String in nodes:
		var state: Dictionary = nodes[id]
		if id.begins_with("coin_") and state.has("collected"):
			state["taken"] = state["collected"]
			state.erase("collected")
	return save
""",
    ),
    "top-down": (
        "game/level.gd",
        [('"score": score', '"points": score'), ('get("score", score)', 'get("points", score)')],
        "score",
        """extends RefCounted
## Saves before version 2 called the arena's score "score".


func migrations() -> Dictionary:
	return {1: _points}


func _points(save: Dictionary) -> Dictionary:
	var level: Dictionary = save["nodes"].get("level", {})
	if level.has("score"):
		level["points"] = level["score"]
		level.erase("score")
	return save
""",
    ),
}
ADAPTERS = {
    "platformer-2d": ('"collected")', '"taken")'),
    "top-down": ('get("score"', 'get("points"'),
}


def _saved(tmp_path: Path, blueprint: str) -> tuple[Path, Path]:
    project = make_project(tmp_path, blueprint, ["save-load"])
    folder = tmp_path / "saves"
    run_godot(
        project,
        TEST,
        (
            "--autostart",
            f"--settings-path={tmp_path / 'settings.cfg'}",
            f"--saves-dir={folder}",
            "--save-phase=save",
        ),
        timeout=180,
        marker="save-load",
    )
    slot = folder / "slot_1.json"
    assert slot.is_file()
    return project, slot


def _check(project: Path) -> list[saves.Outcome]:
    binary = godot_binary()
    assert binary is not None
    return saves.check(project, godot=binary)


def _set_data_version(project: Path, version: int) -> None:
    path = project / "project.godot"
    text = path.read_text(encoding="utf-8")
    assert scene_text.get_setting(text, "save", "data_version") == "1"
    path.write_text(scene_text.set_setting(text, "save", "data_version", version))


@needs_godot
@pytest.mark.parametrize("blueprint", ["platformer-2d", "top-down"])
def test_a_rename_breaks_old_saves_and_a_migration_fixes_them(
    tmp_path: Path, blueprint: str
) -> None:
    project, slot = _saved(tmp_path, blueprint)
    binary = godot_binary()
    kept = saves.add(project, slot, "first-level", "1.0", godot=binary)
    assert [k.relative for k in kept] == ["tests/saves/1.0/first-level.json"]
    summary = json.loads(
        (project / "tests/saves/1.0/first-level.summary.json").read_text()
    )
    assert summary["loaded"] is True and summary["level"].startswith("res://")
    assert summary["line"], "the adapter's slot line is kept with the save"
    assert [o.status for o in _check(project)] == ["passed"]

    # The rename, in the script and in the adapter that reads the same field.
    path, pairs, gone, migration = RENAMES[blueprint]
    script = project / path
    source = script.read_text(encoding="utf-8")
    for before_text, after_text in pairs:
        assert before_text in source, (path, before_text)
        source = source.replace(before_text, after_text)
    script.write_text(source, encoding="utf-8")
    adapter = project / "features/save-load/blueprint_adapter.gd"
    before, after = ADAPTERS[blueprint]
    adapter.write_text(adapter.read_text(encoding="utf-8").replace(before, after))
    broken = _check(project)
    assert [o.status for o in broken] == ["lost"], [o.line() for o in broken]
    assert any(f".{gone} is gone" in lost for lost in broken[0].lost), broken[0].lost
    assert any("slot list said" in lost for lost in broken[0].lost), broken[0].lost
    assert "tests/saves/1.0/first-level.json" in broken[0].line()
    message = saves.problems_text(broken)
    assert "save/data_version" in message and "save_migrations.gd" in message

    # A newer save version with no step for the old one: the game refuses the save.
    _set_data_version(project, 2)
    refused = _check(project)
    assert [o.status for o in refused] == ["failed"], [o.line() for o in refused]
    assert "older version" in refused[0].detail and "migration step" in refused[0].detail

    # The migration step brings every value back.
    (project / saves.MIGRATIONS).write_text(migration, encoding="utf-8")
    fixed = _check(project)
    assert [o.status for o in fixed] == ["passed"], [o.line() for o in fixed]
