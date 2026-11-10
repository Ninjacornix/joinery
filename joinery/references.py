"""Which project files a saved scene or resource loads, and which of them are gone.

A scene saved in the project (``.tscn``) or a resource (``.tres``) names every
file it loads as an ``[ext_resource ... path="res://..."]`` line. A scene that
points at a file the project does not have opens with a missing dependency, so
a review that would leave one is refused, and a discard or revert that leaves
one says so.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path, PurePosixPath

#: Saved Godot documents whose external resources are checked.
SCENE_SUFFIXES = frozenset({".tscn", ".tres"})
_EXT_RESOURCE = re.compile(r"^\[ext_resource\b[^\n]*\]", re.MULTILINE)
_PATH = re.compile(r'\bpath="(res://[^"]+)"')
_UID = re.compile(r'\buid="(uid://[^"]+)"')
#: Folders never searched for scenes: Joinery's and Godot's own state.
SKIPPED = frozenset({".git", ".godot", ".joinery", "addons"})


def references(text: str) -> list[tuple[str, str]]:
    """The (path, uid) of every external resource in a saved scene, in order.

    Paths are project-relative (``art/models/crate.glb``); the uid is empty when
    the line has none.
    """
    found: list[tuple[str, str]] = []
    for line in _EXT_RESOURCE.findall(text):
        path = _PATH.search(line)
        if path is None:
            continue
        relative = path.group(1).removeprefix("res://")
        pure = PurePosixPath(relative)
        if pure.is_absolute() or ".." in pure.parts:
            continue
        uid = _UID.search(line)
        found.append((relative, uid.group(1) if uid else ""))
    return found


def _known_uids(root: Path) -> set[str]:
    """Every uid the project declares (imports, .uid files, scene headers).

    Godot finds a moved file by its uid, so a reference whose path is gone but
    whose uid is still declared is not missing.
    """
    uids: set[str] = set()
    for path in scenes(root, suffixes=SCENE_SUFFIXES | {".import", ".uid"}):
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        if path.suffix == ".uid":
            uids.add(text.strip())
            continue
        # A scene declares its own uid on its first line; an import in [remap].
        head = text[:4096] if path.suffix == ".import" else text.split("\n", 1)[0]
        uids.update(_UID.findall(head))
    return uids


def scenes(root: Path, suffixes: Iterable[str] = SCENE_SUFFIXES) -> list[Path]:
    """Saved scenes and resources below ``root``, outside Joinery's and Godot's state."""
    wanted = frozenset(suffixes)
    found: list[Path] = []
    stack = [root]
    while stack:
        folder = stack.pop()
        try:
            children = list(folder.iterdir())
        except OSError:
            continue
        for child in children:
            if child.is_symlink():
                continue
            if child.is_dir():
                if child.name not in SKIPPED:
                    stack.append(child)
            elif child.suffix in wanted:
                found.append(child)
    return sorted(found)


def missing(root: Path, documents: Iterable[str]) -> dict[str, list[str]]:
    """``{document: [missing path, ...]}`` for the given scenes under ``root``.

    ``documents`` are project-relative paths; ones that are not saved scenes or
    resources, or that do not exist, are skipped.
    """
    gaps: dict[str, list[str]] = {}
    uids: set[str] | None = None
    for document in documents:
        path = root / document
        if path.suffix not in SCENE_SUFFIXES or not path.is_file():
            continue
        lost: list[str] = []
        for relative, uid in references(path.read_text(errors="replace")):
            if (root / relative).exists():
                continue
            if uid:
                if uids is None:
                    uids = _known_uids(root)
                if uid in uids:
                    continue
            if relative not in lost:
                lost.append(relative)
        if lost:
            gaps[document] = lost
    return gaps


def users_of(root: Path, removed: Iterable[str]) -> dict[str, list[str]]:
    """Scenes in ``root`` that still load one of the ``removed`` files, which are gone.

    ``{scene: [removed path, ...]}``. Only files that no longer exist count.
    """
    gone = {path for path in removed if not (root / path).exists()}
    if not gone:
        return {}
    found: dict[str, list[str]] = {}
    for scene in scenes(root):
        relative = scene.relative_to(root).as_posix()
        try:
            text = scene.read_text(errors="replace")
        except OSError:
            continue
        used = [path for path, _uid in references(text) if path in gone]
        if used:
            found[relative] = list(dict.fromkeys(used))
    return found


def refusal(gaps: dict[str, list[str]]) -> str:
    """Why a change with scenes that load files nobody is adding isn't submitted.

    The stage survives: the agent can add the file or fix the scene and submit again.
    """
    lines = [
        f"{scene} uses {', '.join(paths)}, which "
        + ("isn't" if len(paths) == 1 else "aren't")
        + " in the project or in this change"
        for scene, paths in sorted(gaps.items())
    ]
    return (
        "Not submitted: "
        + "; ".join(lines)
        + ". Make the model or texture and the scene that uses it "
        "in the same change, or point the scene at a file the project has."
    )


def orphan_notice(users: dict[str, list[str]], why: str) -> str:
    """Plain words for scenes left loading files that a decision took away.

    ``why`` ends the first sentence: "only the discarded change would have added".
    """
    if not users:
        return ""
    parts = [
        f"{scene} still uses {', '.join(paths)}"
        for scene, paths in sorted(users.items())
    ]
    return (
        "; ".join(parts)
        + f", which {why}. "
        + ("That scene" if len(users) == 1 else "Those scenes")
        + " will open with a missing file. Ask Journeyman to make it again, "
        "or to take it out of the scene."
    )


#: ``why`` for a discarded change and for a revert.
DISCARDED = "only the discarded change would have added"
REVERTED = "the reverted change had added"


def created(changes: Iterable[dict[str, str]]) -> list[str]:
    """Files a change creates (not folders)."""
    return [
        change["path"]
        for change in changes
        if not change.get("before") and change.get("after") not in {"", "directory"}
    ]
