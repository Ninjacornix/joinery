"""Create Joinery game projects. Plain Godot projects, plus Joinery's own files."""

from __future__ import annotations

import shutil
from collections.abc import Sequence
from pathlib import Path

from joinery import game_setup, memory, setup_imports
from joinery.catalog import ROOT, Catalog
from joinery.installer import Selection, write_mcp_config, write_selection
from joinery.wiring import write_agent_wiring, write_memory_stubs

TEMPLATE = ROOT / "templates" / "project"
ASSET_DIRS = ("art", "audio", "recipes")
#: Tells the editor addon to show the first-project tour on the first open (tour.gd),
#: which removes it. In .joinery/, so it is never part of the game.
TOUR_PENDING = ".joinery/tour-pending"


def mark_tour_pending(path: Path) -> None:
    """A new project: the editor shows the first-project tour once. Never fails."""
    try:
        marker = path / TOUR_PENDING
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("")
    except OSError:
        pass


def create_project(
    path: Path,
    name: str,
    selection: Selection,
    catalog: Catalog,
    *,
    blueprint: str | None = None,
    features: Sequence[str] = (),
    steam_app_id: str | None = None,
    connect: str | None = None,
    report: game_setup.Report | None = None,
    wizard: bool = False,
    references: Sequence[tuple[Path, str]] = (),
    documents: Sequence[Path] = (),
) -> None:
    """Scaffold a project. Refuses a non-empty folder so nothing is overwritten.

    The creation-only gate is wired for every supported agent (Claude Code,
    Codex, Gemini CLI, opencode), so Rule Zero holds whichever one the user runs.
    With `blueprint`, the project starts as that playable greybox game with the
    chosen features, its game brief and the art bible draft with the
    blueprint's preset; without, it stays an empty Godot project.

    With `wizard` (what `joinery new` does) it is exactly the project the
    new-project wizard makes (`game_setup.apply_choices`): the Joinery addon
    goes in with its MIT licence and the plugin on, and with Steam its
    settings are written and the pinned GodotSteam extension is downloaded.
    Harnesses that only boot the game leave these out.

    `references` ((path, note) pairs) and `documents` are the user's own
    reference images and design documents (`setup_imports`): checked before
    anything is written, then copied in with the starting point.
    """
    if path.exists() and any(path.iterdir()):
        raise ValueError(f"{path} is not empty. Pick a new or empty folder.")
    if '"' in name or "\n" in name:
        raise ValueError("The project name cannot contain quotes or line breaks.")
    if blueprint is None and features:
        raise ValueError(
            "Features need a blueprint. Add --blueprint (see joinery blueprints)."
        )
    if blueprint is not None:
        # Refuse an unknown blueprint or a bad feature mix before any file exists.
        game_setup.check_choices(blueprint, features, steam_app_id, connect)
    elif steam_app_id or connect:
        raise ValueError(
            "Steam settings need a blueprint. Add --blueprint and --features steam."
        )
    if (references or documents) and blueprint is None:
        raise ValueError(
            "References and design documents go on the art bible and the game brief, "
            "which come with a starting point. Add --blueprint (blank is an empty game)."
        )
    existed = path.exists()
    path.mkdir(parents=True, exist_ok=True)
    try:
        # Every picked file is checked before the project gets anything.
        imports = setup_imports.plan(path, references, documents)
        _scaffold(path, name, selection)
        if wizard:
            game_setup.install_addon(path, report)
        if blueprint is not None:
            game_setup.apply_choices(
                path,
                blueprint,
                features,
                steam_app_id,
                connect,
                addon=False,
                steam_setup=wizard,
                report=report,
                imports=imports,
            )
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


def _scaffold(path: Path, name: str, selection: Selection) -> None:
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
    # Project memory every agent reads, and each agent's own file pointing at it.
    memory.ensure(path)
    write_memory_stubs(path)
    write_selection(selection, path / "joinery.toml")
    write_mcp_config(selection, path)
