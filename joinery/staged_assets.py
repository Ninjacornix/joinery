"""Placing what a change made, inside the same change.

A turn's change is staged: a model Blender exports lands in the stage
(``.joinery/transactions/<id>/stage``), not in the project, until the user
accepts. The editor's ``res://`` is the project, and the stage starts without
Godot's import cache (``.godot`` is never copied), so neither the live editor
nor the staged Godot tools could load the new model: the scene that places it
had to wait for a second change.

Here the stage is imported in the offline worker before anything loads such a
file, and the live editor is handed the stage's own imported copy of each file
only the change has (:func:`staged_only`, :func:`imported_file`). The scene keeps
the project path (``res://art/…``), which exists once the change is accepted.
Shared core: every provider's live edits and staged Godot calls come through
the gateway (ADR 0054).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

#: Source files Godot imports into ``.godot/imported`` before they can be loaded.
IMPORTED_SUFFIXES = frozenset(
    {
        ".glb",
        ".gltf",
        ".fbx",
        ".obj",
        ".blend",
        ".dae",
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".svg",
        ".tga",
        ".bmp",
        ".exr",
        ".hdr",
        ".ktx",
        ".wav",
        ".ogg",
        ".mp3",
        ".ttf",
        ".otf",
        ".woff",
        ".woff2",
    }
)
#: Saved Godot resources the editor loads as they are, without an import.
NATIVE_SUFFIXES = frozenset({".tscn", ".scn", ".tres", ".res"})
_IMPORTED = re.compile(r"^(res://\.godot/imported/.+-[0-9a-f]{32})\.[^/]+$")


def _relative(path: str) -> str | None:
    """``path`` as a safe project-relative path, or None when it isn't one."""
    text = path.replace("\\", "/").removeprefix("res://").strip()
    parts = Path(text).parts
    if not text or Path(text).is_absolute() or ".." in parts or not parts:
        return None
    if parts[0] in {".godot", ".joinery", ".git"}:
        return None
    return Path(text).as_posix()


def loaded_paths(value: Any, *, bare: bool = False) -> list[str]:
    """Every ``res://`` path a scene edit or tool call loads, in first-use order.

    A scene edit names them as ``resource_path`` (``instantiate_scene``, and a
    property given as ``{resource_path: …}``); tool arguments name them as any
    string. With ``bare`` a project-relative path counts too (the Godot tools'
    file arguments are made project-relative). Only files Godot loads count
    (imported or native resources).
    """
    found: list[str] = []

    def visit(item: Any) -> None:
        if isinstance(item, str):
            relative = _relative(item) if bare or item.startswith("res://") else None
            if relative is not None and _loadable(relative):
                found.append(relative)
        elif isinstance(item, Mapping):
            for child in item.values():
                visit(child)
        elif isinstance(item, list | tuple):
            for child in item:
                visit(child)

    visit(value)
    return list(dict.fromkeys(found))


def _loadable(relative: str) -> bool:
    suffix = Path(relative).suffix.lower()
    return suffix in IMPORTED_SUFFIXES or suffix in NATIVE_SUFFIXES


def staged_only(project: Path, stage: Path, paths: Iterable[str]) -> list[str]:
    """The paths the stage has and the project doesn't: made in this change."""
    return [
        path
        for path in paths
        if (stage / path).is_file() and not (project / path).is_file()
    ]


def _remap(stage: Path, relative: str) -> list[str]:
    """The ``res://.godot/imported/…`` files the stage's ``.import`` names."""
    settings = stage / (relative + ".import")
    try:
        lines = settings.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    section = ""
    found: list[str] = []
    valid = True
    for raw in lines:
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        if section != "remap" or "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        value = value.strip('"')
        if key == "valid" and value == "false":
            valid = False
        elif key == "path" or key.startswith("path."):
            found.append(value)
    return found if valid else []


def imported_file(stage: Path, relative: str) -> Path | None:
    """The stage's current imported copy of ``relative``, or None.

    None when Godot hasn't imported it in the stage yet, or imported an older
    version of it: the import's ``.md5`` record no longer matches the source.
    A native resource (``.tscn``, ``.tres``) is its own file.
    """
    source = stage / relative
    if not source.is_file():
        return None
    if Path(relative).suffix.lower() in NATIVE_SUFFIXES:
        return source
    for path in _remap(stage, relative):
        match = _IMPORTED.match(path)
        if match is None:
            continue
        target = stage / path.removeprefix("res://")
        record = stage / (match.group(1).removeprefix("res://") + ".md5")
        if target.is_file() and _source_md5(record) == _md5(source):
            return target
    return None


def _md5(path: Path) -> str:
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _source_md5(record: Path) -> str:
    try:
        text = record.read_text(encoding="utf-8")
    except OSError:
        return ""
    for line in text.splitlines():
        key, _, value = line.partition("=")
        if key.strip() == "source_md5":
            return value.strip().strip('"')
    return ""


def needs_import(stage: Path, paths: Iterable[str]) -> list[str]:
    """The imported files among ``paths`` the stage has no current import of."""
    return [
        path
        for path in paths
        if Path(path).suffix.lower() in IMPORTED_SUFFIXES
        and (stage / path).is_file()
        and imported_file(stage, path) is None
    ]


#: What the agent hears when a file it placed couldn't be imported. Plain and
#: actionable: what to fix, never how Joinery stages or imports.
NOT_IMPORTED = (
    "Godot couldn't open {path}, so it wasn't placed. Check the export (one "
    "scene root, applied transforms), export it again, then place it."
)
MISSING = (
    "{path} doesn't exist. Make or export it first (it can be in this same "
    "change), then place it."
)
