"""Project commands with parameters (.joinery/commands/*.toml, ADR 0077)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from joinery import project_commands as pc
from joinery.catalog import ROOT
from joinery.cli import main as cli_main

ENEMY = """
title = "New enemy"
description = "Add an enemy with a behaviour"
prompt = "Make an enemy called {{name}} that does {{ behaviour }} at {{speed}} m/s. Boss: {{boss}}."

[[parameters]]
name = "name"
description = "What it's called"

[[parameters]]
name = "behaviour"
type = "choice"
choices = ["patrol", "chase"]
default = "patrol"

[[parameters]]
name = "speed"
type = "number"
default = 2.5

[[parameters]]
name = "boss"
type = "boolean"
default = false

[[checks]]
file = "res://enemies/{{name}}.tscn"

[[checks]]
changed = "enemies/*"

[[checks]]
reply = "(?i)enemy"

[[subcommands]]
name = "new-room"
params = { theme = "lair of {{name}}" }
"""

ROOM = """
title = "New room"
prompt = "Add a {{theme}} room."

[[parameters]]
name = "theme"
"""


def project(tmp_path: Path, **files: str) -> Path:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    folder = tmp_path / pc.FOLDER
    folder.mkdir(parents=True)
    for name, text in files.items():
        (folder / f"{name.replace('_', '-')}.toml").write_text(text)
    return tmp_path


def test_a_command_fills_its_parameters_after_its_subcommands(tmp_path: Path) -> None:
    root = project(tmp_path, new_enemy=ENEMY, new_room=ROOM)
    text = pc.request(root, "new-enemy", {"name": "Bat", "speed": "4"})
    assert text == (
        "Do these in order, as one change:\n"
        "1. Add a lair of Bat room.\n"
        "2. Make an enemy called Bat that does patrol at 4 m/s. Boss: no."
    )
    assert pc.request(root, "new-room", {"theme": "ice"}) == "Add a ice room."
    with pytest.raises(ValueError, match="needs name"):
        pc.request(root, "new-enemy", {})
    with pytest.raises(ValueError, match="one of: patrol, chase"):
        pc.request(root, "new-enemy", {"name": "Bat", "behaviour": "fly"})
    with pytest.raises(ValueError, match="speed is a number"):
        pc.request(root, "new-enemy", {"name": "Bat", "speed": "fast"})
    with pytest.raises(ValueError, match="no parameter colour"):
        pc.request(root, "new-enemy", {"name": "Bat", "colour": "red"})


def test_bad_files_are_named_not_fatal(tmp_path: Path) -> None:
    root = project(
        tmp_path,
        new_room=ROOM,
        broken='title = "x"\nprompt = "{{missing}}"\n',
        tidy='title = "x"\nprompt = "y"\n',
        loops='title = "x"\nprompt = "y"\n[[subcommands]]\nname = "loops"\n',
    )
    (root / pc.FOLDER / "Bad Name.toml").write_text('title="x"\nprompt="y"\n')
    found, problems = pc.load(root)
    assert sorted(found) == ["loops", "new-room"]
    text = "\n".join(problems)
    assert "broken.toml" in text and "{{missing}}" in text
    assert "tidy.toml: /tidy is one of the sidebar's own commands." in text
    assert "Bad Name.toml" in text
    with pytest.raises(ValueError, match="loop"):
        pc.request(root, "loops", {})
    with pytest.raises(ValueError, match="broken.toml"):
        pc.request(root, "broken", {})


def test_checks_read_the_staged_change_and_the_reply(tmp_path: Path) -> None:
    root = project(tmp_path, new_enemy=ENEMY, new_room=ROOM)
    command = pc.get(root, "new-enemy")
    stage = tmp_path / "stage"
    (stage / "enemies").mkdir(parents=True)
    (stage / "enemies/Bat.tscn").write_text("[gd_scene]\n")
    given = {"name": "Bat"}
    results = pc.check(
        root, command, given, ["enemies/Bat.tscn"], "Made the enemy.", stage
    )
    assert [r["ok"] for r in results] == [True, True, True]
    results = pc.check(root, command, given, ["ui/hud.gd"], "Done.", None)
    assert [r["ok"] for r in results] == [False, False, False]
    assert "enemies/Bat.tscn isn't in the project" in results[0]["why"]


def test_reserved_names_match_the_sidebars_commands() -> None:
    """A project command can't hide one of the sidebar's own (slash_commands.gd)."""
    source = (ROOT / "godot_addon/joinery/slash_commands.gd").read_text()
    block = source[source.index("const COMMANDS := {") :]
    block = block[: block.index("\n}\n")]
    names = {m.group(1) for m in re.finditer(r'^\t"/([a-z-]+)":', block, re.MULTILINE)}
    assert names and names <= pc.RESERVED


def test_the_cli_lists_and_renders(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path, new_enemy=ENEMY, new_room=ROOM)
    assert cli_main(["command", "list", "--project", str(root), "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    enemy = next(c for c in listed["commands"] if c["name"] == "new-enemy")
    assert enemy["parameters"][0] == {
        "name": "name",
        "type": "string",
        "description": "What it's called",
        "default": None,
        "choices": [],
        "required": True,
    }
    assert enemy["parameters"][2]["default"] == 2.5
    assert enemy["subcommands"] == ["new-room"] and enemy["checks"] == 3
    code = cli_main(
        [
            "command",
            "render",
            "new-room",
            "--project",
            str(root),
            "--param",
            "theme=ice",
            "--json",
        ]
    )
    assert code == 0
    assert json.loads(capsys.readouterr().out)["request"] == "Add a ice room."
    code = cli_main(["command", "render", "new-room", "--project", str(root)])
    assert code == 2
    assert "needs theme" in capsys.readouterr().out
    code = cli_main(
        ["command", "render", "new-room", "--project", str(root), "--param", "oops"]
    )
    assert code == 2
    assert "name=value" in capsys.readouterr().out


def test_commands_are_kept_in_git() -> None:
    from joinery.timeline import IGNORE_RULES

    assert "!.joinery/commands/" in IGNORE_RULES
