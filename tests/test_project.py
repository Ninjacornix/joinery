import base64
import json
import os
import shlex
from pathlib import Path

import pytest

from joinery.catalog import ROOT, CustomTool, enabled_tools, load_catalog
from joinery.deps import check_deps
from joinery.installer import Selection
from joinery.lockfile import check_assets
from joinery.policy import load_denylist
from joinery.project import create_project


def test_new_project_is_plain_godot_with_joinery_files(tmp_path: Path) -> None:
    project = tmp_path / "game"
    catalog = load_catalog()
    create_project(project, "My Game", Selection(["blender", "godot"], []), catalog)
    godot = (project / "project.godot").read_text()
    assert 'config/name="My Game"' in godot and "{{" not in godot
    assert enabled_tools(catalog, project / "joinery.toml") == ["blender", "godot"]
    assert check_assets(project, catalog) == []
    assert not (project / ".mcp.json").exists()
    assert check_deps(project, load_denylist()) == []


def test_new_project_inherits_hook_and_telemetry_off(tmp_path: Path) -> None:
    project = tmp_path / "game"
    create_project(project, "G", Selection(["godot"], []), load_catalog())
    settings = json.loads((project / ".claude/settings.json").read_text())
    assert settings["env"]["DISABLE_TELEMETRY"] == "1"
    hook = settings["hooks"]["PreToolUse"][0]["hooks"][0]
    assert hook["type"] == "command"
    command = hook["command"]
    if os.name == "nt":
        prefix = "powershell.exe -NoProfile -NonInteractive -EncodedCommand "
        assert command.startswith(prefix)
        script = base64.b64decode(command[len(prefix) :]).decode("utf-16-le")
        assert "$info.FileName='uv'" in script
        assert "'joinery', 'hook', '--agent', 'claude'" in script
        engine = str(ROOT).replace("'", "''")
        assert "'--project', '" + engine + "'" in script
        assert "exit $process.ExitCode" in script
    else:
        assert shlex.split(command) == [
            "uv",
            "run",
            "--frozen",
            "--quiet",
            "--project",
            str(ROOT),
            "joinery",
            "hook",
            "--agent",
            "claude",
        ]


def test_new_project_wires_the_gate_for_every_agent(tmp_path: Path) -> None:
    project = tmp_path / "game"
    create_project(project, "G", Selection(["godot"], []), load_catalog())
    # Rule Zero must hold whichever agent the user runs in the project.
    for path in (
        ".claude/settings.json",
        ".codex/hooks.json",
        ".gemini/settings.json",
        ".opencode/plugin/joinery-gate.js",
    ):
        assert (project / path).is_file(), path


def test_custom_tool_lands_in_mcp_config(tmp_path: Path) -> None:
    tool = CustomTool(id="x", name="X", domain="2d", server="x", command="x-mcp")
    project = tmp_path / "game"
    create_project(project, "G", Selection(["godot"], [tool]), load_catalog())
    assert (
        json.loads((project / ".mcp.json").read_text())["mcpServers"]["x"]["command"]
        == "x-mcp"
    )


def test_refuses_non_empty_folder(tmp_path: Path) -> None:
    (tmp_path / "keep.txt").write_text("x")
    with pytest.raises(ValueError, match="not empty"):
        create_project(tmp_path, "G", Selection(["godot"], []), load_catalog())
    assert (tmp_path / "keep.txt").read_text() == "x"
