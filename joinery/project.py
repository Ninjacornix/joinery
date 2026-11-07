"""Create Joinery game projects. Plain Godot projects, plus Joinery's own files."""

from __future__ import annotations

import json
from pathlib import Path

from joinery.catalog import ROOT, Catalog
from joinery.installer import Selection, write_mcp_config, write_selection

TEMPLATE = ROOT / "templates" / "project"
ASSET_DIRS = ("art", "audio", "recipes")


def _hook_settings() -> dict[str, object]:
    args = ["run", "--frozen", "--quiet", "--project", str(ROOT), "joinery", "hook"]
    hook = {
        "type": "command",
        "command": "uv",
        "args": args,
        "onFailure": "block",
        "timeout": 30,
    }
    return {
        "enableAllProjectMcpServers": False,
        "env": {"DISABLE_TELEMETRY": "1", "DISABLE_ERROR_REPORTING": "1"},
        "hooks": {
            "PreToolUse": [{"matcher": "mcp__.*|Bash|WebFetch", "hooks": [hook]}]
        },
    }


def create_project(
    path: Path, name: str, selection: Selection, catalog: Catalog
) -> None:
    """Scaffold a project. Refuses a non-empty folder so nothing is overwritten."""
    if path.exists() and any(path.iterdir()):
        raise ValueError(f"{path} is not empty")
    if '"' in name or "\n" in name:
        raise ValueError("project name cannot contain quotes or newlines")
    path.mkdir(parents=True, exist_ok=True)
    for directory in ASSET_DIRS:
        (path / directory).mkdir()
        (path / directory / ".gitkeep").touch()
    text = (TEMPLATE / "project.godot").read_text().replace("{{name}}", name)
    (path / "project.godot").write_text(text)
    (path / "joinery.lock").write_text("version = 1\n")
    (path / ".gitignore").write_text(".godot/\nout/\n")
    (path / ".gitattributes").write_text((ROOT / ".gitattributes").read_text())
    (path / ".claude").mkdir()
    settings = json.dumps(_hook_settings(), indent=2) + "\n"
    (path / ".claude" / "settings.json").write_text(settings)
    write_selection(selection, path / "joinery.toml")
    write_mcp_config(selection, path)
