"""Open native documents in desktop tools, independently of MCP readiness.

Never run a recipe or an MCP command to open a document. In particular,
SuperCollider documents go to scide, not sclang (which executes them).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


@dataclass(frozen=True)
class DesktopTool:
    command: str
    app: str
    extensions: tuple[str, ...]
    native: tuple[str, ...]


IMAGES = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tga", ".tif", ".tiff", ".exr")
AUDIO = (".wav", ".ogg", ".mp3", ".flac", ".aiff", ".aif")
TOOLS = {
    "blender": DesktopTool("blender", "Blender", (".blend",), (".blend",)),
    "krita": DesktopTool(
        "krita", "krita", (".kra", ".ora", ".psd", *IMAGES), (".kra",)
    ),
    "gimp": DesktopTool("gimp", "GIMP", (".xcf", ".ora", ".psd", *IMAGES), (".xcf",)),
    "inkscape": DesktopTool(
        "inkscape", "Inkscape", (".svg", ".svgz"), (".svg", ".svgz")
    ),
    # Audacity is no longer an editing tool, but it still opens sound files by
    # hand when it is installed. Other programs are added per project with a
    # [desktop.<id>] entry in joinery.toml.
    "audacity": DesktopTool(
        "audacity", "Audacity", (".aup3", ".aup", *AUDIO), (".aup3", ".aup")
    ),
    "supercollider": DesktopTool(
        "scide", "SuperCollider", (".scd", ".sc"), (".scd", ".sc")
    ),
    "material-maker": DesktopTool(
        "material-maker", "Material Maker", (".ptex",), (".ptex",)
    ),
    "godot": DesktopTool(
        "godot", "Godot", (".tscn", ".tres", ".gd", ".godot"), (".tscn", ".tres", ".gd")
    ),
}


class DesktopOverride(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    command: str = Field(min_length=1)
    args: tuple[str, ...] = ()
    extensions: tuple[str, ...] = ()


def overrides(project: Path) -> dict[str, DesktopOverride]:
    config = project / "joinery.toml"
    if not config.is_file():
        return {}
    raw = tomllib.loads(config.read_text()).get("desktop", {})
    return {key: DesktopOverride.model_validate(value) for key, value in raw.items()}


def project_file(project: Path, relative: str) -> Path:
    """Refuse absolute paths, traversal and symlinks outside this project."""
    root = project.resolve()
    path = (root / relative).resolve()
    if Path(relative).is_absolute() or not path.is_relative_to(root):
        raise ValueError(f"path is outside the project: {relative}")
    return path


def this_editor() -> Path | None:
    """The Joinery editor binary: "Open in Godot" means this editor, not a stock copy."""
    from joinery.distribution import engine_binary

    try:
        return engine_binary()
    except ValueError:
        return None


def launcher(tool: str, settings: dict[str, DesktopOverride]) -> list[str]:
    """Resolve a program without starting it or running version probes."""
    override = settings.get(tool)
    spec = TOOLS.get(tool)
    if override:
        command = shutil.which(override.command)
        if command:
            return [command, *override.args]
        app = Path(override.command).expanduser()
        if sys.platform == "darwin" and app.suffix == ".app" and app.is_dir():
            args = ["--args", *override.args] if override.args else []
            return ["/usr/bin/open", "-a", str(app), *args]
        raise ValueError(f"Configured application not found: {override.command}")
    if tool == "godot":
        editor = this_editor()
        if editor is not None:
            return [str(editor)]
    if spec:
        command = shutil.which(spec.command)
        if command:
            return [command]
        if sys.platform == "darwin":
            for folder in (Path("/Applications"), Path.home() / "Applications"):
                app = folder / f"{spec.app}.app"
                if app.is_dir():
                    return ["/usr/bin/open", "-a", str(app)]
    raise ValueError(
        f"Application not found; configure [desktop.{tool}] in joinery.toml"
    )


def launch(argv: list[str], project: Path) -> None:
    """Launch without a shell; detach so closing the CLI leaves the editor open."""
    subprocess.Popen(
        argv,
        cwd=project,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
