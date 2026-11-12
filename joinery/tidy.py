"""`/tidy`: what the game never uses, with the evidence, and what goes with each.

Read-only. The walk starts where Godot and Joinery start a game: the main scene,
the autoloads and every other path in ``project.godot``, the export presets,
``joinery.toml``, the design files (blockouts, puzzles, dialogue) and data files a
script may read, the editor plugins in ``addons/`` and the features' own tools. From
there it follows every reference a saved scene, resource, script or shader makes:
``res://`` and ``uid://`` paths, paths relative to a script, and the ``class_name``s a
script uses. What the walk never reaches is unused.

A path a script builds at run time (``"res://levels/level_%d.tscn" % n``, a folder it
lists) can't be followed. The files it could name are "maybe used" and never part of
Remove all. Files only the project's tests load are "used by tests", never unused.

Each finding says what it is, how big it is with everything that goes with it (its
``.import`` and ``.uid``, the textures and scripts only it uses, its native source,
recipe and ``joinery.lock`` entry, a kit model's LODs), why Joinery thinks nothing
uses it, and how sure it is. Input actions, autoloads, nodes inside scenes that do
nothing and attached scripts that add nothing are found the same way
(``joinery.tidy_nodes``). Removing is one reviewed change (``joinery.tidy_remove``).
"""

from __future__ import annotations

import hashlib
import os
import re
import tomllib
from collections import defaultdict
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from joinery.godot_config import GodotConfig

Kind = Literal[
    "model",
    "texture",
    "material",
    "sound",
    "music",
    "script",
    "scene",
    "resource",
    "font",
    "shader",
    "input",
    "autoload",
    "node",
]
Confidence = Literal["unused", "maybe"]
#: Kinds in the order the card groups them, with their names.
KINDS: dict[str, str] = {
    "model": "Models",
    "texture": "Textures",
    "material": "Materials",
    "sound": "Sounds",
    "music": "Music",
    "scene": "Scenes",
    "script": "Scripts",
    "resource": "Resources",
    "font": "Fonts",
    "shader": "Shaders",
    "input": "Input actions",
    "autoload": "Autoloads",
    "node": "Nodes in scenes",
}
SUFFIX_KINDS: dict[str, str] = {
    **dict.fromkeys((".glb", ".gltf", ".obj", ".fbx", ".dae"), "model"),
    **dict.fromkeys(
        (
            ".png",
            ".jpg",
            ".jpeg",
            ".webp",
            ".svg",
            ".tga",
            ".exr",
            ".hdr",
            ".bmp",
            ".ktx",
            ".dds",
        ),
        "texture",
    ),
    **dict.fromkeys((".wav", ".ogg", ".mp3"), "sound"),
    **dict.fromkeys((".gd", ".cs"), "script"),
    **dict.fromkeys((".tscn", ".scn"), "scene"),
    **dict.fromkeys((".tres", ".res"), "resource"),
    **dict.fromkeys(
        (".ttf", ".otf", ".woff", ".woff2", ".fnt", ".font", ".fontdata"), "font"
    ),
    **dict.fromkeys((".gdshader", ".gdshaderinc", ".shader"), "shader"),
}
#: Text Godot or a game script reads, searched for the paths it names.
TEXT = frozenset(
    {
        ".tscn",
        ".tres",
        ".gd",
        ".cs",
        ".gdshader",
        ".gdshaderinc",
        ".shader",
        ".godot",
        ".cfg",
        ".json",
        ".dlg",
        ".toml",
        ".csv",
        ".txt",
        ".gdextension",
        ".gltf",
    }
)
#: Data a script may read by a path it builds (levels, dialogue, puzzles): a root.
DATA = frozenset({".json", ".dlg", ".csv", ".cfg", ".toml", ".txt"})
CODE = frozenset({".gd", ".cs"})
#: Folders never searched: version control, Godot's cache, Joinery's own state.
HIDDEN = frozenset({".git", ".godot", ".joinery", "node_modules", "__pycache__"})
#: Folders whose files are never findings: the tests, editor plugins, Joinery's
#: recipes and design sources, shared kits (removed from the Kits section).
NOT_GAME = ("tests/", "test/", "addons/", "recipes/", "design/", "kits/")
#: Never removed, whatever the walk says: the user's own words and references.
PROTECTED = (
    "art/bible.md",
    "design/brief.md",
    "art/reference/",
    "design/docs/",
    "design/",
    "project.godot",
    "joinery.toml",
    "joinery.lock",
    "export_presets.cfg",
)
#: Materials, by the resource type their header names.
MATERIAL_TYPES = frozenset(
    {
        "StandardMaterial3D",
        "ORMMaterial3D",
        "ShaderMaterial",
        "CanvasItemMaterial",
        "ParticleProcessMaterial",
        "PanoramaSkyMaterial",
        "ProceduralSkyMaterial",
        "PhysicalSkyMaterial",
        "FogMaterial",
    }
)
#: Settings whose paths are a tool's list, not something the game loads.
WEAK_SETTINGS = frozenset({"internationalization/locale/translations_pot_files"})
#: Engine callbacks: an autoload with one of these runs without being named.
CALLBACKS = re.compile(
    r"^\s*(?:static\s+)?func\s+_(?:init|ready|enter_tree|exit_tree|process|"
    r"physics_process|input|unhandled_input|unhandled_key_input|shortcut_input|"
    r"notification)\b",
    re.MULTILINE,
)
#: A script reads every input action (a controls page lists them).
EVERY_ACTION = re.compile(r"InputMap\s*\.\s*get_actions\s*\(")
#: A line that builds a path at run time from a prefix.
_DYNAMIC_LINE = re.compile(
    r"\bload\s*\(|ResourceLoader|DirAccess|get_files_at|get_directories_at|"
    r"list_directory|path_join|\bconst\b|\bvar\b|%|\+|\.format\s*\("
)
#: A line that loads or lists by a path relative to its script.
_LISTING_LINE = re.compile(
    r"\bload\s*\(|ResourceLoader|DirAccess|get_files_at|get_directories_at|"
    r"list_directory|path_join"
)
#: Joinery's own code names these project files (the sound pass adds the core's audio
#: nodes, replays boot through the core): they are Joinery's, used when it runs.
JOINERY_CODE = ("joinery", "bridge", "servers")
JOINERY_OWN = ("core/", "features/")
_STRING = re.compile(r'"((?:[^"\\\n]|\\.)*)"|\'((?:[^\'\\\n]|\\.)*)\'')
_UID = re.compile(r"uid://[a-z0-9]+")
_IDENT = re.compile(r"[A-Za-z_]\w*")
_CLASS = re.compile(r"^class_name\s+(\w+)", re.MULTILINE)
_HEAD_TYPE = re.compile(r'^\[gd_resource\b[^\]]*\btype="([^"]+)"')
_SCRIPT_CLASS = re.compile(r'script_class="(\w+)"')
#: Files read as text at most this big.
MAX_TEXT = 4 << 20


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Companion(_Model):
    """A file that goes when its finding goes."""

    path: str
    size: int
    #: What it is: "import settings", "uid", "only it uses it", "native source",
    #: "recipe", "recipe script", "LOD".
    what: str


class Finding(_Model):
    id: str
    #: What ``[tidy] keep`` holds to leave it alone: a path, ``input:<name>``,
    #: ``autoload:<name>``, ``<scene>::<node path>``, ``<script>::empty``.
    key: str
    kind: Kind
    #: The file it is, or the file it lives in (project.godot, a scene).
    path: str
    #: The action, autoload or node path; "" for a file.
    subject: str = ""
    title: str
    confidence: Confidence
    #: Bytes freed with everything that goes with it.
    size: int
    #: Why Joinery thinks nothing uses it, in one sentence.
    why: str
    #: What was found: where a path is built, what was searched.
    evidence: list[str] = []
    goes_with: list[Companion] = []
    #: Text edits the removal makes ("project.godot: the jump action").
    edits: list[str] = []


class Group(_Model):
    kind: str
    label: str
    count: int
    size: int
    maybe: int = 0


class CacheFolder(_Model):
    path: str
    what: str
    size: int
    files: int


class Report(_Model):
    project: str
    summary: str
    #: Sure findings, and the bytes removing them all frees.
    count: int
    size: int
    maybe: int
    groups: list[Group] = []
    findings: list[Finding] = []
    kept: list[str] = []
    used_by_tests: list[str] = []
    read: dict[str, int] = {}
    caches: list[CacheFolder] = []
    cache_size: int = 0


# --- the project as text ------------------------------------------------------------


def _hidden(part: str) -> bool:
    return part in HIDDEN or part.startswith(".")


def walk(project: Path) -> tuple[list[str], set[str]]:
    """Every file below ``project`` (posix, relative) and the folders Godot ignores
    (they hold a ``.gdignore``: native sources, references)."""
    files: list[str] = []
    ignored: set[str] = set()
    for directory, children, names in os.walk(project, followlinks=False):
        parent = Path(directory)
        rel = parent.relative_to(project).as_posix()
        children[:] = sorted(
            c for c in children if not _hidden(c) and not (parent / c).is_symlink()
        )
        if ".gdignore" in names and rel != ".":
            ignored.add(rel + "/")
        for name in sorted(names):
            if name.startswith(".") or (parent / name).is_symlink():
                continue
            files.append(name if rel == "." else f"{rel}/{name}")
    return files, ignored


def suffix(rel: str) -> str:
    return ".godot" if rel == "project.godot" else PurePosixPath(rel).suffix.lower()


def size_of(project: Path, rel: str) -> int:
    try:
        return (project / rel).stat().st_size
    except OSError:
        return 0


def read_text(project: Path, rel: str) -> str:
    path = project / rel
    try:
        if path.stat().st_size > MAX_TEXT:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def strings(text: str) -> list[tuple[int, str]]:
    """Every quoted string of ``text`` with its line number."""
    found: list[tuple[int, str]] = []
    for number, line in enumerate(text.splitlines(), 1):
        if '"' not in line and "'" not in line:
            continue
        for match in _STRING.finditer(line):
            found.append((number, match.group(1) or match.group(2) or ""))
    return found


def _lines(text: str) -> list[str]:
    return text.splitlines()


def uid_map(project: Path, files: list[str]) -> dict[str, str]:
    """``uid://…`` -> the file it names, from .uid files, imports and scene headers."""
    found: dict[str, str] = {}
    present = set(files)
    for rel in files:
        if rel.endswith(".uid"):
            owner = rel.removesuffix(".uid")
            match = _UID.search(read_text(project, rel))
            if match and owner in present:
                found[match.group(0)] = owner
        elif rel.endswith(".import"):
            owner = rel.removesuffix(".import")
            match = re.search(
                r'^uid="(uid://[a-z0-9]+)"', read_text(project, rel), re.MULTILINE
            )
            if match and owner in present:
                found[match.group(1)] = owner
        elif suffix(rel) in {".tscn", ".tres"}:
            try:
                with (project / rel).open(encoding="utf-8", errors="replace") as handle:
                    head = handle.readline()
            except OSError:
                continue
            match = re.search(r'\buid="(uid://[a-z0-9]+)"', head)
            if match:
                found[match.group(1)] = rel
    return found


@dataclass
class Dynamic:
    """A path a file builds at run time: what it starts (and ends) with, and where."""

    prefix: str
    suffix: str
    source: str
    line: int
    text: str

    def matches(self, rel: str) -> bool:
        return rel.startswith(self.prefix) and rel.endswith(self.suffix)

    def where(self) -> str:
        return f"{self.source}:{self.line} builds res://{self.prefix}…{self.suffix}"


@dataclass
class Refs:
    """What one file names: the files, and the paths it builds."""

    files: set[str] = field(default_factory=set)
    weak: set[str] = field(default_factory=set)
    dynamic: list[Dynamic] = field(default_factory=list)


def _clean(path: str) -> str:
    for mark in ("::", "#"):
        path = path.split(mark, 1)[0]
    return path.strip().strip("/")


def references(
    project: Path,
    rel: str,
    text: str,
    present: set[str],
    folders: set[str],
    uids: dict[str, str],
) -> Refs:
    """The files ``rel`` (whose text is ``text``) names, and the paths it builds."""
    found = Refs()
    kind = suffix(rel)
    code = kind in CODE or kind in {".gdshader", ".gdshaderinc", ".shader"}
    here = str(PurePosixPath(rel).parent)
    lines = _lines(text) if code else []
    for number, value in strings(text):
        if "uid://" in value:
            for uid in _UID.findall(value):
                if uid in uids:
                    found.files.add(uids[uid])
        if "res://" in value:
            path = _clean(value[value.index("res://") + 6 :])
            if path in present:
                found.files.add(path)
                continue
            line = lines[number - 1] if 0 < number <= len(lines) else ""
            if not code or not path or not _DYNAMIC_LINE.search(line):
                continue
            cut = min(
                (i for i in (path.find("%"), path.find("{")) if i >= 0), default=-1
            )
            if cut >= 0:
                head, tail = path[:cut], re.sub(r"^[^.]*", "", path[cut:])
            elif path in folders:
                head, tail = path + "/", ""
            else:
                head, tail = path, ""
            if "/" not in head and not head:
                continue
            found.dynamic.append(Dynamic(head, tail, rel, number, line.strip()[:160]))
        elif code and value and "://" not in value and len(value) < 200:
            # A path relative to the script: preload("bt_nodes.gd"), "trees".
            joined = os.path.normpath(os.path.join(here, value)).replace("\\", "/")
            if joined.startswith(".."):
                continue
            if joined in present and suffix(joined) in SUFFIX_KINDS:
                found.files.add(joined)
            elif joined in folders and joined not in {".", here}:
                line = lines[number - 1] if 0 < number <= len(lines) else ""
                if _LISTING_LINE.search(line):
                    found.dynamic.append(
                        Dynamic(joined + "/", "", rel, number, line.strip()[:160])
                    )
    if kind == ".gltf":
        # A glTF names its buffers and images relative to itself.
        for match in re.finditer(r'"uri"\s*:\s*"([^"]+)"', text):
            joined = os.path.normpath(os.path.join(here, match.group(1)))
            if joined in present:
                found.files.add(joined)
    found.files.discard(rel)
    return found


# --- the walk ------------------------------------------------------------------------


@dataclass
class Graph:
    project: Path
    files: list[str]
    present: set[str]
    ignored: set[str]
    folders: set[str]
    uids: dict[str, str]
    texts: dict[str, str] = field(default_factory=dict)
    refs: dict[str, Refs] = field(default_factory=dict)
    referrers: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    classes: dict[str, str] = field(default_factory=dict)
    #: Files the game reaches, with the first file that named each.
    used: dict[str, str] = field(default_factory=dict)
    #: Files only a built path could name, with that path.
    maybe: dict[str, Dynamic] = field(default_factory=dict)
    by_tests: set[str] = field(default_factory=set)
    roots: dict[str, str] = field(default_factory=dict)
    #: Folders the game's core and features own (core/, features/<id>/): they come
    #: and go with the feature, in Game features, never one file at a time.
    owned: tuple[str, ...] = ()

    def text(self, rel: str) -> str:
        if rel not in self.texts:
            self.texts[rel] = read_text(self.project, rel)
        return self.texts[rel]

    def in_ignored(self, rel: str) -> bool:
        return any(rel.startswith(folder) for folder in self.ignored)


def _settings_refs(graph: Graph, rel: str) -> tuple[set[str], set[str]]:
    """(paths, weak paths) a Godot ConfigFile names; weak: a tool's list only."""
    strong: set[str] = set()
    weak: set[str] = set()
    try:
        config = GodotConfig.parse(graph.text(rel))
    except (ValueError, IndexError):
        return strong, weak
    for section in config.sections():
        for key in config.keys(section):
            raw = config.get(section, key) or ""
            named = references(
                graph.project, rel, raw, graph.present, graph.folders, graph.uids
            ).files
            for match in re.finditer(r"res://([^\"',)\s]+)", raw):
                path = _clean(match.group(1))
                if path in graph.present:
                    named.add(path)
            (
                weak
                if f"{section}/{key}".removeprefix("/") in WEAK_SETTINGS
                or key in WEAK_SETTINGS
                else strong
            ).update(named)
    return strong, weak - strong


def owned(project: Path) -> tuple[str, ...]:
    """core/ and each applied feature's folder, from the ``[game]`` record."""
    try:
        game = tomllib.loads(
            (project / "joinery.toml").read_text(encoding="utf-8")
        ).get("game", {})
    except (OSError, ValueError):
        return ()
    if not isinstance(game, dict) or not game.get("blueprint"):
        return ()
    features = [f for f in game.get("features", []) if isinstance(f, str)]
    return ("core/", *(f"features/{f}/" for f in features))


def build(project: Path) -> Graph:
    """Read the project and walk it from where the game starts."""
    project = project.resolve()
    files, ignored = walk(project)
    present = set(files)
    folders = {str(PurePosixPath(f).parent) for f in files} - {"."}
    folders |= {str(p) for f in folders for p in PurePosixPath(f).parents} - {"."}
    graph = Graph(project, files, present, ignored, folders, uid_map(project, files))
    graph.owned = owned(project)
    for rel in files:
        if suffix(rel) not in TEXT or graph.in_ignored(rel):
            continue
        if rel.startswith(("recipes/", "kits/")):
            continue
        text = graph.text(rel)
        if rel in {"project.godot", "export_presets.cfg"}:
            strong, weak = _settings_refs(graph, rel)
            graph.refs[rel] = Refs(files=strong, weak=weak)
        else:
            graph.refs[rel] = references(
                project, rel, text, present, folders, graph.uids
            )
        if suffix(rel) in CODE:
            match = _CLASS.search(text)
            if match:
                graph.classes[match.group(1)] = rel
    for source, found in graph.refs.items():
        for target in found.files | found.weak:
            graph.referrers[target].add(source)
    tokens: dict[str, set[str]] = {}

    def named_classes(rel: str) -> set[str]:
        if not graph.classes or suffix(rel) not in CODE | {".tscn", ".tres"}:
            return set()
        if rel not in tokens:
            text = graph.text(rel)
            words = set(_IDENT.findall(text)) if suffix(rel) in CODE else set()
            words |= set(_SCRIPT_CLASS.findall(text))
            tokens[rel] = words
        return {
            graph.classes[name]
            for name in tokens[rel] & graph.classes.keys()
            if graph.classes[name] != rel
        }

    for rel in graph.refs:
        for target in named_classes(rel):
            graph.refs[rel].files.add(target)
            graph.referrers[target].add(rel)
    graph.roots = _roots(graph)
    _reach(graph)
    return graph


@cache
def joinery_paths() -> frozenset[str]:
    """Project paths under core/ and features/ that Joinery's own code names."""
    from joinery.catalog import ROOT

    found: set[str] = set()
    for package in JOINERY_CODE:
        for path in (ROOT / package).rglob("*.py"):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            for match in re.finditer(r"res://([A-Za-z0-9_\-./]+\.[A-Za-z0-9]+)", text):
                if match.group(1).startswith(JOINERY_OWN):
                    found.add(match.group(1))
    return frozenset(found)


def tool_file(rel: str) -> bool:
    """A feature's own tool or an editor plugin: it reads the project, the game
    doesn't run it, so the paths it builds say nothing about the game."""
    return rel.startswith("addons/") or bool(re.match(r"features/[^/]+/tools/", rel))


def _roots(graph: Graph) -> dict[str, str]:
    """Where the game starts -> why: the files settings, design and tools name."""
    roots: dict[str, str] = {}

    def add(paths: set[str] | frozenset[str], why: str) -> None:
        for path in sorted(paths):
            roots.setdefault(path, why)

    add(
        joinery_paths() & graph.present,
        "Joinery itself uses it (the sound pass, replays)",
    )

    for rel, why in (
        ("project.godot", "project.godot names it"),
        ("export_presets.cfg", "the export presets name it"),
        ("joinery.toml", "joinery.toml names it"),
    ):
        if rel in graph.refs:
            add(graph.refs[rel].files, why)
    for rel, found in graph.refs.items():
        if rel.startswith("design/"):
            add(found.files, f"the design file {rel} names it")
        elif rel.startswith("addons/"):
            add(found.files | {rel}, "an editor plugin in addons/ uses it")
        elif re.match(r"features/[^/]+/tools/", rel):
            add(found.files | {rel}, f"it is the {rel.split('/')[1]} feature's tool")
        elif (
            suffix(rel) in DATA
            and not rel.startswith(NOT_GAME)
            and rel not in {"project.godot", "export_presets.cfg", "joinery.toml"}
        ):
            add(found.files, f"the data file {rel} names it")
    return roots


def _reach(graph: Graph) -> None:
    """used: from the roots; maybe: from the paths used files build; by_tests: from
    the tests."""
    queue = list(graph.roots)
    graph.used = dict.fromkeys(graph.roots, "")
    for rel in queue:
        for target in sorted(graph.refs.get(rel, Refs()).files):
            if target not in graph.used:
                graph.used[target] = rel
                queue.append(target)
    built = [
        d
        for rel in graph.used
        if not tool_file(rel)
        for d in graph.refs.get(rel, Refs()).dynamic
    ]
    pending: list[str] = []
    for rel in graph.files:
        if rel in graph.used or rel.endswith((".import", ".uid")):
            continue
        hit = next((d for d in built if d.matches(rel)), None)
        if hit is not None:
            graph.maybe[rel] = hit
            pending.append(rel)
    for rel in pending:
        for target in sorted(graph.refs.get(rel, Refs()).files):
            if target not in graph.used and target not in graph.maybe:
                graph.maybe[target] = graph.maybe[rel]
                pending.append(target)
    tests = [r for r in graph.files if r.startswith(("tests/", "test/"))]
    seen = set(tests)
    for rel in tests:
        found = graph.refs.get(rel, Refs())
        targets = set(found.files)
        for dynamic in found.dynamic:
            targets |= {f for f in graph.files if dynamic.matches(f)}
        for target in targets:
            if target not in seen:
                seen.add(target)
                tests.append(target)
    graph.by_tests = seen - graph.used.keys() - graph.maybe.keys()


# --- what a file is -----------------------------------------------------------------


def kind_of(graph: Graph, rel: str) -> str:
    """The finding kind of a game file, or "" for one that is never a finding."""
    found = SUFFIX_KINDS.get(suffix(rel), "")
    if not found:
        return ""
    if found == "sound" and any("music" in part.lower() for part in rel.split("/")):
        return "music"
    if found == "resource" and suffix(rel) == ".tres":
        head = graph.text(rel).split("\n", 1)[0]
        match = _HEAD_TYPE.search(head)
        if match and match.group(1) in MATERIAL_TYPES:
            return "material"
        if any("music" in part.lower() for part in rel.split("/")[:-1]):
            return "music"
    return found


def candidate(graph: Graph, rel: str) -> bool:
    """A game file that can be a finding: Godot sees it and it isn't a tool's."""
    return (
        bool(kind_of(graph, rel))
        and not rel.startswith(NOT_GAME)
        and not rel.startswith(graph.owned)
        and not graph.in_ignored(rel)
        and not protected(rel)
    )


def protected(rel: str) -> bool:
    return any(rel == p or (p.endswith("/") and rel.startswith(p)) for p in PROTECTED)


def sidecars(graph: Graph, rel: str) -> list[str]:
    return [s for s in (rel + ".import", rel + ".uid") if s in graph.present]


def finding_id(*parts: str) -> str:
    return hashlib.sha1("\x00".join(parts).encode()).hexdigest()[:12]


def human_size(size: int) -> str:
    """ "38 MB", "640 KB", "12 bytes"."""
    if size >= 1 << 30:
        return f"{size / (1 << 30):.1f} GB"
    if size >= 10 << 20:
        return f"{size / (1 << 20):.0f} MB"
    if size >= 1 << 20:
        return f"{size / (1 << 20):.1f} MB"
    if size >= 1 << 10:
        return f"{size / (1 << 10):.0f} KB"
    return f"{size} byte" + ("" if size == 1 else "s")


# --- settings ------------------------------------------------------------------------


def tidy_settings(project: Path) -> dict[str, Any]:
    try:
        raw = tomllib.loads((project / "joinery.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    table = raw.get("tidy", {})
    return table if isinstance(table, dict) else {}


def keep_list(project: Path) -> list[str]:
    """``[tidy] keep``: what the user said to leave alone."""
    value = tidy_settings(project).get("keep", [])
    if not isinstance(value, list):
        return []
    return [
        str(v).replace("\\", "/").removeprefix("res://").strip()
        for v in value
        if isinstance(v, str) and v.strip()
    ]


def final_list(project: Path) -> list[str]:
    """``[match] final``: assets the user marked done."""
    from joinery.match import settings

    return settings(project).final


def kept(key: str, path: str, keep: list[str], final: list[str]) -> bool:
    for entry in keep:
        if key == entry or path == entry:
            return True
        if entry.endswith("/") and path.startswith(entry):
            return True
    return any(path == f or path.startswith(f.rstrip("/") + "/") for f in final)


def _write_keep(project: Path, values: list[str]) -> None:
    config = project / "joinery.toml"
    text = config.read_text(encoding="utf-8") if config.is_file() else ""
    line = "keep = [" + ", ".join(f'"{v}"' for v in values) + "]"
    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines) if l.strip() == "[tidy]"), -1)
    if start < 0:
        lines += ["", "[tidy]", line]
    else:
        end = next(
            (
                i
                for i in range(start + 1, len(lines))
                if lines[i].lstrip().startswith("[")
            ),
            len(lines),
        )
        found = next(
            (
                i
                for i in range(start + 1, end)
                if lines[i].split("=")[0].strip() == "keep"
            ),
            -1,
        )
        if found >= 0:
            lines[found] = line
        else:
            lines.insert(start + 1, line)
    updated = "\n".join(lines).strip("\n") + "\n"
    tomllib.loads(updated)
    config.write_text(updated, encoding="utf-8")


def set_keep(project: Path, key: str, keep: bool = True) -> list[str]:
    """Add ``key`` to ``[tidy] keep`` (or take it out); the user's own choice."""
    clean = key.replace("\\", "/").removeprefix("res://").strip()
    if not clean or '"' in clean or "\n" in clean:
        raise ValueError("Name what to keep: a project path, input:<action>, …")
    current = keep_list(project)
    wanted = (
        sorted(set(current) | {clean}) if keep else [k for k in current if k != clean]
    )
    _write_keep(project, wanted)
    return wanted
