"""A short ranked map of a game project, for the stable part of every prompt.

Like Aider's repository map: the project's scenes, scripts, autoloads, assets and
features in about 1–2k tokens, with the files the rest of the project refers to
most listed first, so an agent knows where things are before it reads anything.

Ranking is PageRank over references: a scene's ``ext_resource`` lines, a
script's ``res://`` strings and the ``class_name``s it uses, and
``project.godot``'s main scene, autoloads and hooks, which also seed the walk.
The text is cached under ``.joinery/map/`` by a hash of the files it is made
from, so the same project gives the same bytes (and the provider's prompt cache
keeps hitting) until a file it reads changes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tomllib
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from joinery.references import references

MAP_DIR = ".joinery/map"
VERSION = 1
#: Characters of map at most (about 1.75k tokens at four characters a token).
BUDGET = 7000
#: Cached maps kept per project.
KEEP = 3
SKIP_DIRS = frozenset({".git", ".godot", ".joinery", "addons", "node_modules"})
CODE = frozenset({".gd", ".cs", ".gdshader"})
DOCUMENTS = frozenset({".tscn", ".tres"})
#: Read for the map, so hashed by content.
READ = CODE | DOCUMENTS | {".godot", ".toml"}
#: Not assets: Godot's sidecars, notes and records.
NOT_ASSETS = frozenset(
    {
        ".import",
        ".uid",
        ".md",
        ".txt",
        ".lock",
        ".cfg",
        ".json",
        ".gitkeep",
        ".gdignore",
        "",
    }
)
_RES = re.compile(r"res://[A-Za-z0-9_\-./ ]+\.[A-Za-z0-9]+")
_NODE = re.compile(
    r'^\[node name="([^"]+)"(?: type="([^"]+)")?([^\]]*)\]', re.MULTILINE
)
_PARENT = re.compile(r'parent="([^"]*)"')
_INSTANCE = re.compile(r'instance=ExtResource\("([^"]+)"\)')
_EXT = re.compile(
    r'^\[ext_resource\b[^\]]*\bpath="res://([^"]+)"[^\]]*\bid="([^"]+)"', re.MULTILINE
)
_ROOT_SCRIPT = re.compile(r'^script = ExtResource\("([^"]+)"\)', re.MULTILINE)
_EXTENDS = re.compile(r"^extends\s+(\S+)", re.MULTILINE)
_CLASS = re.compile(r"^class_name\s+(\w+)", re.MULTILINE)
_SIGNAL = re.compile(r"^signal\s+(\w+)", re.MULTILINE)
_FUNC = re.compile(r"^(?:static\s+)?func\s+([A-Za-z]\w*)", re.MULTILINE)
_EXPORT = re.compile(r"^@export[^\n]*?\bvar\s+(\w+)", re.MULTILINE)
_IDENT = re.compile(r"\b[A-Z]\w+\b")


@dataclass
class Scene:
    root: str = ""
    kind: str = ""
    nodes: int = 0
    script: str = ""
    children: list[str] = field(default_factory=list)
    instances: Counter[str] = field(default_factory=Counter)


@dataclass
class Script:
    extends: str = ""
    class_name: str = ""
    signals: list[str] = field(default_factory=list)
    exports: list[str] = field(default_factory=list)
    funcs: list[str] = field(default_factory=list)


def _files(project: Path) -> list[str]:
    found: list[str] = []
    for path in project.rglob("*"):
        rel = path.relative_to(project)
        if any(part in SKIP_DIRS or part.startswith(".") for part in rel.parts[:-1]):
            continue
        if path.is_file() and not path.name.startswith("."):
            found.append(rel.as_posix())
    return sorted(found)


def _suffix(rel: str) -> str:
    return PurePosixPath(rel).suffix.lower() if rel != "project.godot" else ".godot"


def _text(project: Path, rel: str) -> str:
    try:
        return (project / rel).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def fingerprint(project: Path, files: list[str] | None = None) -> str:
    """A hash of what the map is made from: the content of every file it reads, and
    the path and size of every other file (assets are counted, not read)."""
    digest = hashlib.sha256(f"map v{VERSION}\n".encode())
    for rel in files if files is not None else _files(project):
        digest.update(rel.encode() + b"\0")
        path = project / rel
        if _suffix(rel) in READ:
            try:
                digest.update(hashlib.sha256(path.read_bytes()).digest())
            except OSError:
                digest.update(b"?")
        else:
            try:
                digest.update(str(path.stat().st_size).encode())
            except OSError:
                digest.update(b"?")
    return digest.hexdigest()


def _scene(text: str) -> tuple[Scene, dict[str, str]]:
    ids = {ident: rel for rel, ident in _EXT.findall(text)}
    scene = Scene()
    for name, kind, rest in _NODE.findall(text):
        scene.nodes += 1
        parent = _PARENT.search(rest)
        instance = _INSTANCE.search(rest)
        if instance and instance.group(1) in ids:
            scene.instances[ids[instance.group(1)]] += 1
        if parent is None:
            scene.root, scene.kind = (
                name,
                kind
                or (
                    "instance of " + ids.get(instance.group(1), "?") if instance else ""
                ),
            )
        elif parent.group(1) == "." and len(scene.children) < 8:
            scene.children.append(name)
    head = text.split("\n[node ", 2)
    if len(head) > 1:
        root_block = head[1].split("\n[", 1)[0]
        script = _ROOT_SCRIPT.search(root_block)
        if script and script.group(1) in ids:
            scene.script = ids[script.group(1)]
    return scene, ids


def _script(text: str) -> Script:
    extends = _EXTENDS.search(text)
    name = _CLASS.search(text)
    return Script(
        extends=extends.group(1).strip('"') if extends else "",
        class_name=name.group(1) if name else "",
        signals=_SIGNAL.findall(text),
        exports=_EXPORT.findall(text),
        funcs=[f for f in _FUNC.findall(text)],
    )


def _settings(text: str) -> dict[str, dict[str, str]]:
    sections: dict[str, dict[str, str]] = defaultdict(dict)
    section = ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
        elif "=" in line and not line.startswith(";") and section:
            key, _, value = line.partition("=")
            sections[section][key.strip()] = value.strip()
    return sections


def _rank(
    edges: dict[str, Counter[str]], nodes: set[str], seeds: set[str]
) -> dict[str, float]:
    """PageRank with the walk restarting at the seeds (main scene, autoloads) more
    often than anywhere else."""
    if not nodes:
        return {}
    teleport = {n: (10.0 if n in seeds else 1.0) for n in nodes}
    total = sum(teleport.values())
    teleport = {n: v / total for n, v in teleport.items()}
    rank = dict(teleport)
    for _ in range(30):
        new = {n: 0.15 * teleport[n] for n in nodes}
        dangling = 0.0
        for node in nodes:
            out = edges.get(node) or Counter()
            weight = sum(out.values())
            if not weight:
                dangling += rank[node]
                continue
            for target, count in out.items():
                if target in new:
                    new[target] += 0.85 * rank[node] * count / weight
        for node in nodes:
            new[node] += 0.85 * dangling * teleport[node]
        rank = new
    return rank


def _short(items: list[str], limit: int) -> str:
    shown = ", ".join(items[:limit])
    return shown + (f" +{len(items) - limit}" if len(items) > limit else "")


def build(project: Path, budget: int = BUDGET) -> str:
    """The map's text (uncached)."""
    project = project.resolve()
    files = _files(project)
    present = set(files)
    edges: dict[str, Counter[str]] = defaultdict(Counter)
    scenes: dict[str, Scene] = {}
    scripts: dict[str, Script] = {}
    texts: dict[str, str] = {}
    for rel in files:
        suffix = _suffix(rel)
        if suffix not in CODE | DOCUMENTS:
            continue
        text = _text(project, rel)
        texts[rel] = text
        if suffix == ".tscn":
            scenes[rel], _ = _scene(text)
        if suffix in DOCUMENTS:
            for target, _uid in references(text):
                if target in present:
                    edges[rel][target] += 1
        else:
            if suffix == ".gd":
                scripts[rel] = _script(text)
            for match in _RES.findall(text):
                target = match.removeprefix("res://")
                if target in present and target != rel:
                    edges[rel][target] += 1
    classes = {s.class_name: rel for rel, s in scripts.items() if s.class_name}
    if classes:
        for rel, text in texts.items():
            if _suffix(rel) != ".gd":
                continue
            for ident in set(_IDENT.findall(text)) & classes.keys():
                if classes[ident] != rel:
                    edges[rel][classes[ident]] += 1
    settings = _settings(_text(project, "project.godot"))
    application = settings.get("application", {})
    main = application.get("run/main_scene", "").strip('"').removeprefix("res://")
    autoloads = {
        name: value.strip('"').lstrip("*").removeprefix("res://")
        for name, value in settings.get("autoload", {}).items()
    }
    seeds = {p for p in [main, *autoloads.values()] if p in present}
    for value in settings.get("game", {}).values():
        for match in _RES.findall(value):
            if match.removeprefix("res://") in present:
                seeds.add(match.removeprefix("res://"))
    nodes = set(texts) | {t for out in edges.values() for t in out}
    rank = _rank(edges, nodes, seeds)
    used_by: Counter[str] = Counter()
    for out in edges.values():
        for target in out:
            used_by[target] += 1

    def ordered(paths: list[str]) -> list[str]:
        return sorted(paths, key=lambda p: (-rank.get(p, 0.0), p))

    def tested(rel: str) -> bool:
        return rel.split("/", 1)[0] in {"tests", "test"}

    try:
        game = tomllib.loads(_text(project, "joinery.toml")).get("game", {})
    except tomllib.TOMLDecodeError:
        game = {}
    head: list[str] = ["Project map (most referenced first; read a file for more):"]
    name = application.get("config/name", "").strip('"')
    line = f'Game "{name}"' if name else "Game"
    if game.get("blueprint"):
        line += f": blueprint {game['blueprint']}"
    features = [str(f) for f in game.get("features", []) if isinstance(f, str)]
    if features:
        line += f"; features {', '.join(features)}"
    if main:
        line += f"; main scene {main}"
    head.append(line + ".")
    if autoloads:
        head.append(
            "Autoloads: " + ", ".join(f"{n} {p}" for n, p in autoloads.items()) + "."
        )
    actions = [
        k for k in settings.get("input", {}) if "/" not in k and not k.startswith('"')
    ]
    if actions:
        head.append("Input actions: " + _short(actions, 16) + ".")

    def scene_line(rel: str) -> str:
        scene = scenes[rel]
        parts = [
            f"{scene.root} ({scene.kind})" if scene.kind else scene.root,
            f"{scene.nodes} nodes",
        ]
        if scene.script:
            parts.append(f"script {scene.script}")
        if scene.children:
            parts.append("children " + _short(scene.children, 6))
        if scene.instances:
            parts.append(
                "instances "
                + _short(
                    [
                        f"{PurePosixPath(p).name}×{n}"
                        if n > 1
                        else PurePosixPath(p).name
                        for p, n in scene.instances.most_common()
                    ],
                    5,
                )
            )
        if used_by[rel]:
            parts.append(f"used by {used_by[rel]}")
        return f"- {rel}: " + "; ".join(parts)

    def script_line(rel: str) -> str:
        script = scripts[rel]
        parts = []
        if script.class_name:
            parts.append(f"class {script.class_name}")
        if script.extends:
            parts.append(f"extends {script.extends}")
        if script.signals:
            parts.append("signals " + _short(script.signals, 4))
        if script.exports:
            parts.append("exports " + _short(script.exports, 5))
        public = [f for f in script.funcs if not f.startswith("_")]
        if public:
            parts.append("funcs " + _short(public, 5))
        if used_by[rel]:
            parts.append(f"used by {used_by[rel]}")
        return f"- {rel}: " + "; ".join(parts)

    scene_lines = [scene_line(r) for r in ordered([r for r in scenes if not tested(r)])]
    script_lines = [
        script_line(r)
        for r in ordered(
            [r for r in scripts if not tested(r) and r not in autoloads.values()]
        )
    ]
    autoload_lines = [script_line(r) for r in autoloads.values() if r in scripts]
    resource_lines = [
        f"- {r}" + (f": used by {used_by[r]}" if used_by[r] else "")
        for r in ordered([r for r in files if _suffix(r) == ".tres" and not tested(r)])
    ]
    folders: dict[str, Counter[str]] = defaultdict(Counter)
    weight: dict[str, float] = defaultdict(float)
    for rel in files:
        suffix = _suffix(rel)
        if (
            suffix in READ
            or suffix in NOT_ASSETS
            or tested(rel)
            or "/" not in rel
            or rel.startswith("recipes/")
        ):
            continue
        folder = str(PurePosixPath(rel).parent)
        folders[folder][suffix] += 1
        weight[folder] += rank.get(rel, 0.0)
    asset_lines = [
        f"- {folder}/: "
        + ", ".join(
            f"{n} {s}" for s, n in sorted(counts.items(), key=lambda x: (-x[1], x[0]))
        )
        for folder, counts in sorted(
            folders.items(), key=lambda i: (-weight[i[0]], i[0])
        )
    ]
    recipe_lines: list[str] = []
    for rel in files:
        if rel.startswith("recipes/") and rel.endswith(".toml"):
            try:
                recipe = tomllib.loads(_text(project, rel))
            except tomllib.TOMLDecodeError:
                continue
            outputs = [str(o) for o in recipe.get("outputs", [])]
            recipe_lines.append(
                f"- {rel} ({recipe.get('tool', '')}) → {_short(outputs, 2)}"
            )
    tests = sum(1 for r in files if tested(r) and _suffix(r) in CODE)

    sections: list[tuple[str, list[str], str]] = [
        ("Autoload scripts:", autoload_lines, "autoload scripts"),
        (f"Scenes ({len(scene_lines)}):", scene_lines, "scenes"),
        (f"Scripts ({len(script_lines)}):", script_lines, "scripts"),
        (f"Resources ({len(resource_lines)}):", resource_lines, "resources"),
        ("Assets by folder:", asset_lines, "asset folders"),
        (f"Recipes ({len(recipe_lines)}):", recipe_lines, "recipes"),
    ]
    caps = {
        "autoload scripts": 12,
        "scenes": 15,
        "scripts": 25,
        "resources": 8,
        "asset folders": 15,
        "recipes": 8,
    }
    tail = [f"Tests: {tests} scripts under tests/."] if tests else []

    def render() -> str:
        out = list(head)
        for title, lines, label in sections:
            if not lines:
                continue
            cap = caps[label]
            out.append(title)
            out += lines[:cap]
            if len(lines) > cap:
                out.append(
                    f"  +{len(lines) - cap} more {label}: ask for the full map or list the folder."
                )
        return "\n".join(out + tail)

    text = render()
    while len(text) > budget:
        # Drop the lowest-ranked entry of the section that takes the most room.
        room = {
            label: sum(len(x) for x in lines[: caps[label]])
            for _, lines, label in sections
            if min(len(lines), caps[label]) > 2
        }
        if not room:
            break
        label = max(room, key=lambda lb: room[lb])
        listed = next(len(lines) for _, lines, lb in sections if lb == label)
        caps[label] = min(caps[label], listed) - 1
        text = render()
    return text


_memo: dict[Path, tuple[str, str]] = {}


def _quick_key(project: Path, files: list[str]) -> str:
    digest = hashlib.sha256()
    for rel in files:
        try:
            stat = (project / rel).stat()
        except OSError:
            continue
        digest.update(f"{rel}\0{stat.st_size}\0{stat.st_mtime_ns}\n".encode())
    return digest.hexdigest()


def cached(project: Path) -> str:
    """The map, from ``.joinery/map/<hash>.txt`` when the project's files are the
    same; built and kept otherwise. "" for a folder that isn't a Godot project."""
    project = project.resolve()
    if not (project / "project.godot").is_file():
        return ""
    files = _files(project)
    quick = _quick_key(project, files)
    memo = _memo.get(project)
    if memo is not None and memo[0] == quick:
        return memo[1]
    key = fingerprint(project, files)[:24]
    folder = project / MAP_DIR
    path = folder / f"{key}.txt"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        text = build(project)
        try:
            folder.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            kept = sorted(folder.glob("*.txt"), key=lambda p: p.stat().st_mtime_ns)
            for old in kept[:-KEEP]:
                old.unlink(missing_ok=True)
        except OSError:
            pass  # A read-only project still gets its map, uncached.
    _memo[project] = (quick, text)
    return text


def tokens(text: str) -> int:
    """Rough tokens of ``text`` (four characters a token), as ADR 0056 measured."""
    return (len(text) + 3) // 4


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Add ``map --project P [--json] [--fresh]``."""
    parser = subparsers.add_parser(
        "map", help="a short ranked map of the project: scenes, scripts, assets"
    )
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--json", action="store_true", help="print JSON")
    parser.add_argument(
        "--fresh", action="store_true", help="rebuild, ignoring the cache"
    )


def run(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve()
    if not (project / "project.godot").is_file():
        message = f"No Godot project at {project}."
        print(json.dumps({"error": message}) if args.json else message)
        return 1
    text = build(project) if args.fresh else cached(project)
    if args.json:
        data: dict[str, Any] = {
            "map": text,
            "characters": len(text),
            "tokens": tokens(text),
            "fingerprint": fingerprint(project),
        }
        print(json.dumps(data))
    else:
        print(text)
    return 0
