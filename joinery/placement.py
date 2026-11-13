"""Models a change makes but no scene uses, and whether the request placed them.

"Make a mug and put it on the table" is one request with two parts. A turn that
exports ``mug.glb`` and stops has done half of it. Here a change's new models are
matched against every scene in its stage and the live edits of the scene on
screen; a model nothing uses is reported. When the user's request puts it
somewhere ("on the table", "in the kitchen", a node or scene it names) that is a
problem the agent fixes in the same change; otherwise a note asking the agent to
say where it is or why it stays unplaced.

Shared core: the turn controller runs this for every agent's precheck and submit.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from joinery import references
from joinery.reasons import Reason

#: Exported models Godot imports as a scene; a ``.blend`` is their native source.
MODEL_SUFFIXES = frozenset({".glb", ".gltf", ".fbx", ".obj", ".dae"})
#: A prop scene (one model wrapped with its collision) lives with the art.
PROP_DIRS = ("art/",)
_LOD = re.compile(r"_lod\d+$", re.IGNORECASE)
_VERB = r"(?:put|place|add|stand|sit|hang|drop|position|mount|set\s+(?:it|them))"
_PREP = (
    r"(?:on\s+top\s+of|next\s+to|in\s+front\s+of|onto|on|into|inside|in|at|"
    r"beside|by|under(?:neath)?|near|above|over|against|to)"
)
_INTENT = re.compile(
    rf"\b{_VERB}\b[^.;!?]{{0,80}}?\b(?P<prep>{_PREP})\s+"
    r"(?:the\s+|a\s+|an\s+|my\s+|our\s+|this\s+|that\s+)?(?P<what>[A-Za-z][\w-]*)",
    re.IGNORECASE,
)
_KEEP_OUT = re.compile(
    r"\b(?:don'?t|do\s+not|never|no\s+need\s+to|without)\s+"
    r"(?:put(?:ting)?|plac(?:e|ing)|add(?:ing)?|instanc\w*)\b"
    r"|\bnot\s+in\s+(?:the|any)\s+scenes?\b"
    r"|\b(?:just|only)\s+(?:make\s+)?the\s+(?:model|asset|file)\b"
    r"|\b(?:model|asset)\s+only\b",
    re.IGNORECASE,
)
#: Words after a preposition that name a tool or a format, not a place.
_NOT_PLACES = frozenset(
    {
        "blender",
        "krita",
        "godot",
        "material",
        "surge",
        "supercollider",
        "it",
        "them",
        "there",
        "here",
        "place",
        "game",
        "project",
        "glb",
        "gltf",
        "metres",
        "meters",
        "cm",
        "scale",
        "size",
        "least",
        "once",
        "same",
        "order",
    }
)
#: Node and scene names too common to mean "put it there" when a request says them.
_GENERIC = frozenset(
    {"main", "world", "root", "node", "scene", "level", "game", "environment"}
)
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


@dataclass(frozen=True)
class Intent:
    """Where the request puts what it makes: the words, and the node they name."""

    #: The request's own words ("on the table"); "" when it names only a node/scene.
    words: str
    #: The node or scene name it names ("Table", "kitchen"), "" when none is known.
    name: str = ""
    #: The live scene's identity of that node ("res://main.tscn::Table"), if on screen.
    identity: str = ""

    def where(self) -> str:
        return self.words or f"in {self.name}"


@dataclass(frozen=True)
class Unplaced:
    """New models no scene uses, and what the request said about placing them."""

    paths: list[str]
    intent: Intent | None

    def reasons(self) -> list[Reason]:
        return [
            Reason(
                "placement.unplaced_model",
                path,
                self.intent.name if self.intent else "",
                "in no scene",
                "placed " + self.intent.where() if self.intent else "",
                fix(self.intent),
            )
            for path in self.paths
        ]

    def message(self) -> str:
        names = ", ".join(PurePosixPath(p).name for p in self.paths)
        verb = "isn't" if len(self.paths) == 1 else "aren't"
        if self.intent is None:
            return (
                f"{names} {verb} in any scene yet. Say in your reply where "
                + ("it is" if len(self.paths) == 1 else "they are")
                + " or why it stays unplaced; place it only if the user asked."
            )
        return (
            f"{names} {verb} in any scene yet; the request puts it "
            f"{self.intent.where()}. " + fix(self.intent)
        )


def fix(intent: Intent | None) -> str:
    """One line on how the model gets placed."""
    if intent is None:
        return "Say where it is or why it isn't placed."
    if intent.identity:
        return (
            f"Instance it on {intent.name} with edit_scene instantiate_scene "
            f"and on: {intent.identity}."
        )
    return (
        "Instance it in the scene the request names: edit_scene instantiate_scene "
        "(with on: the node it rests on) in the scene on screen, or the Godot scene "
        "tools for another scene."
    )


def keeps_out(request: str) -> bool:
    """The request says not to place what it makes."""
    return bool(_KEEP_OUT.search(request))


def _words(name: str) -> str:
    """A node or scene name as words: ``KitchenTable`` -> ``kitchen table``."""
    return " ".join(_CAMEL.sub(" ", name).replace("_", " ").split()).lower()


def scene_nodes(editor_context: Mapping[str, str]) -> list[tuple[str, str]]:
    """(name, identity) of every node of the scene on screen, root first; a node
    named after its own class ("Camera3D") is left out."""
    try:
        rows = json.loads(editor_context.get("nodes", "[]") or "[]")
    except ValueError:
        return []
    found: list[tuple[str, str]] = []
    for row in rows if isinstance(rows, list) else []:
        if (
            isinstance(row, dict)
            and row.get("name")
            and row.get("id")
            and row.get("name") != row.get("type")
        ):
            found.append((str(row["name"]), str(row["id"])))
    return found


def _place_name(name: str, kind: str = "") -> bool:
    """A name specific enough that saying it means that place."""
    spoken = _words(name)
    return len(spoken) >= 3 and name != kind and spoken not in _GENERIC


def intent(
    request: str,
    nodes: Iterable[tuple[str, str]] = (),
    scenes: Iterable[str] = (),
    own: Iterable[str] = (),
) -> Intent | None:
    """Where ``request`` puts what it makes, or None when it doesn't place it.

    ``nodes`` are the scene on screen's (name, identity), ``scenes`` the project's
    scene names (file stems) and ``own`` the new models' stems, which never count
    as a place they go.
    """
    text = " ".join(request.split())
    lowered = text.lower()
    nodes = list(nodes)
    mine = {_words(stem) for stem in own}

    def node_for(word: str) -> tuple[str, str]:
        wanted = _words(word)
        for name, identity in nodes:
            if _words(name) == wanted and _words(name) not in mine:
                return name, identity
        for name, identity in nodes:
            spoken = _words(name)
            if spoken not in mine and wanted in spoken.split():
                return name, identity
        return "", ""

    for found in _INTENT.finditer(text):
        what = found.group("what")
        if what.lower() in _NOT_PLACES or _words(what) in mine:
            continue
        name, identity = node_for(what)
        start = found.start("prep")
        return Intent(text[start : found.end("what")], name or what, identity)
    # A node or scene the request names is where the new model goes.
    for name, identity in nodes[1:]:
        spoken = _words(name)
        if _place_name(name) and spoken not in mine and _named(spoken, lowered):
            return Intent("", name, identity)
    for stem in scenes:
        spoken = _words(stem)
        if _place_name(stem) and spoken not in mine and _named(spoken, lowered):
            return Intent("", stem)
    return None


def _named(words: str, text: str) -> bool:
    return re.search(rf"\b{re.escape(words)}s?\b", text) is not None


def new_models(changes: Iterable[Mapping[str, str]]) -> list[str]:
    """Models a change creates: exported models and prop scenes, not kit parts."""
    found: list[str] = []
    for change in changes:
        path = change["path"]
        if change.get("before") or change.get("after") in {"", "directory"}:
            continue
        pure = PurePosixPath(path)
        suffix = pure.suffix.lower()
        if pure.parts[0] in {"kits", "addons"} or "reference" in pure.parts:
            continue
        if _LOD.search(pure.stem):
            continue
        if suffix in MODEL_SUFFIXES or (
            suffix == ".tscn" and path.startswith(PROP_DIRS)
        ):
            found.append(path)
    return found


def used(stage: Path) -> set[str]:
    """Every project file a scene or resource in ``stage`` loads."""
    paths: set[str] = set()
    for scene in references.scenes(stage):
        try:
            text = scene.read_text(errors="replace")
        except OSError:
            continue
        paths.update(path for path, _uid in references.references(text))
    return paths


def unplaced(
    stage: Path,
    changes: Iterable[Mapping[str, str]],
    live: Iterable[str],
) -> list[str]:
    """The change's new models that no staged scene and no live edit uses.

    ``live`` are the project paths this turn's live edits loaded.
    """
    models = new_models(changes)
    if not models:
        return []
    placed = used(stage) | {p.removeprefix("res://") for p in live}
    return [path for path in models if path not in placed]


def scene_stems(project: Path) -> list[str]:
    """The project's saved scenes' names (``kitchen`` for ``levels/kitchen.tscn``)."""
    return sorted(
        {path.stem for path in references.scenes(project, suffixes={".tscn"})}
    )


def check(
    project: Path,
    stage: Path,
    changes: Iterable[Mapping[str, str]],
    request: str,
    editor_context: Mapping[str, str],
    live: Iterable[str],
) -> Unplaced | None:
    """The change's unplaced new models, with the request's placement intent."""
    if keeps_out(request):
        return None
    paths = unplaced(stage, list(changes), live)
    if not paths:
        return None
    return Unplaced(
        paths,
        intent(
            request,
            scene_nodes(editor_context),
            scene_stems(project),
            [PurePosixPath(p).stem for p in paths],
        ),
    )
