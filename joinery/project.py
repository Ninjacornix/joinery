"""Create Joinery game projects. Plain Godot projects, plus Joinery's own files."""

from __future__ import annotations

import shutil
from collections.abc import Sequence
from pathlib import Path

from joinery import blueprints
from joinery.catalog import ROOT, Catalog
from joinery.installer import Selection, write_mcp_config, write_selection
from joinery.wiring import write_agent_wiring

TEMPLATE = ROOT / "templates" / "project"
ASSET_DIRS = ("art", "audio", "recipes")


def create_project(
    path: Path,
    name: str,
    selection: Selection,
    catalog: Catalog,
    *,
    blueprint: str | None = None,
    features: Sequence[str] = (),
) -> None:
    """Scaffold a project. Refuses a non-empty folder so nothing is overwritten.

    The creation-only gate is wired for every supported agent (Claude Code,
    Codex, Gemini CLI, opencode), so Rule Zero holds whichever one the user runs.
    With `blueprint`, the project starts as that playable greybox game with the
    chosen features; without, it stays an empty Godot project.
    """
    if path.exists() and any(path.iterdir()):
        raise ValueError(f"{path} is not empty")
    if '"' in name or "\n" in name:
        raise ValueError("project name cannot contain quotes or newlines")
    if blueprint is None and features:
        raise ValueError("features need a blueprint to build on")
    if blueprint is not None:
        # Refuse an unknown blueprint or a bad feature mix before any file exists.
        problems = blueprints.resolve(blueprint, features).problems
        if problems:
            raise ValueError("\n".join(problems))
    existed = path.exists()
    path.mkdir(parents=True, exist_ok=True)
    try:
        _scaffold(path, name, selection, blueprint, features)
    except BaseException:
        # The folder was empty or absent, so everything in it is ours: leave
        # no half-made project behind (a blueprint can still refuse at apply).
        if existed:
            for child in path.iterdir():
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink()
        else:
            shutil.rmtree(path)
        raise


def _scaffold(
    path: Path,
    name: str,
    selection: Selection,
    blueprint: str | None,
    features: Sequence[str],
) -> None:
    for directory in ASSET_DIRS:
        (path / directory).mkdir()
        (path / directory / ".gitkeep").touch()
    # The game brief lives in design/; reference boards stay out of Godot imports.
    (path / "design").mkdir()
    (path / "design" / ".gitkeep").touch()
    (path / "art" / "reference").mkdir()
    (path / "art" / "reference" / ".gdignore").write_text("")
    text = (TEMPLATE / "project.godot").read_text().replace("{{name}}", name)
    (path / "project.godot").write_text(text)
    (path / "joinery.lock").write_text("version = 1\n")
    (path / ".gitignore").write_text(".godot/\nout/\n")
    (path / ".gitattributes").write_text((ROOT / ".gitattributes").read_text())
    write_agent_wiring(path)
    write_selection(selection, path / "joinery.toml")
    write_mcp_config(selection, path)
    if blueprint is not None:
        blueprints.apply(path, blueprint, features)
