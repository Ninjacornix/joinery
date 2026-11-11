"""Complex models are made in Blender: one object is never a pile of placeholder shapes.

``[agents] complex_models`` in joinery.toml is ``"blender"`` (the default) or
``"placeholders"``. With ``"blender"``, a change that builds an object the player
would see as one thing (a chair, a lamp, a car) from two or more primitive meshes
or CSG shapes goes back to its author at submit, for every provider alike: make
it in Blender or with a kit and place it as one model.

What is never judged: simple level geometry (a floor, a wall, a ramp, a platform:
pieces named so, or at least ``LEVEL_SIZE`` metres long), collision and trigger
shapes (they are not meshes), anything the blockout tool built (its scene root
records ``blockout_layout``), nodes marked ``metadata/joinery_placeholder = true``
(the user asked for a placeholder), hidden or editor-only helpers, and objects
the change did not add or modify.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from joinery.roles import set_agents_values
from joinery.scatter import IDENTITY, Section, Transform, _compose, _transform, parse
from joinery.turn import Problem

KEY = "complex_models"
BLENDER = "blender"
PLACEHOLDERS = "placeholders"
CHOICES = (BLENDER, PLACEHOLDERS)

#: Primitive meshes by their full size (x, y, z) with Godot's defaults.
MESHES = frozenset(
    {
        "BoxMesh",
        "CylinderMesh",
        "SphereMesh",
        "CapsuleMesh",
        "PrismMesh",
        "TorusMesh",
        "PlaneMesh",
    }
)
CSG_SHAPES = frozenset(
    {"CSGBox3D", "CSGCylinder3D", "CSGSphere3D", "CSGTorus3D", "CSGPolygon3D"}
)
#: CSG nodes that only combine their children (a CSGMesh3D with a primitive
#: mesh is a shape too).
CSG_GROUPS = frozenset({"CSGCombiner3D", "CSGMesh3D"})
#: A piece this long (metres, along its longest side) is level geometry.
LEVEL_SIZE = 3.0
#: Name words of level geometry and helpers, never one object's parts.
LEVEL_WORDS = frozenset(
    {
        "floor",
        "ground",
        "wall",
        "walls",
        "platform",
        "platforms",
        "ramp",
        "ceiling",
        "roof",
        "stair",
        "stairs",
        "step",
        "steps",
        "terrain",
        "road",
        "path",
        "room",
        "corridor",
        "arena",
        "level",
        "border",
        "boundary",
        "trigger",
        "zone",
        "marker",
        "spawn",
        "goal",
        "checkpoint",
        "killzone",
        "water",
        "blockout",
        "greybox",
    }
)
#: Node types a scene of one object (a prop scene) may hold beside its shapes.
PROP_TYPES = frozenset(
    {
        "Node3D",
        "StaticBody3D",
        "RigidBody3D",
        "AnimatableBody3D",
        "CharacterBody3D",
        "Area3D",
        "CollisionShape3D",
        "CollisionPolygon3D",
        "Marker3D",
        "MeshInstance3D",
        *CSG_SHAPES,
        *CSG_GROUPS,
    }
)
#: Shapes sharing a space this close (metres) are parts of one object.
TOUCH = 0.1
FIX = (
    "Make it in Blender (or with a kit) and place it as one model. Only when the "
    "user asked for a placeholder, set metadata/joinery_placeholder = true on it."
)
_NUMBER = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_WORDS = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])")


# --- the setting ----------------------------------------------------------------------


def setting(project: Path) -> str:
    """``"blender"`` unless joinery.toml says ``complex_models = "placeholders"``."""
    try:
        raw = tomllib.loads((project / "joinery.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return BLENDER
    table = raw.get("agents", {})
    value = table.get(KEY, BLENDER) if isinstance(table, dict) else BLENDER
    return value if value in CHOICES else BLENDER


def set_setting(project: Path, value: str) -> None:
    if value not in CHOICES:
        raise ValueError("Complex models are made in Blender or as placeholders.")
    set_agents_values(project, {KEY: f'"{value}"'})
    if setting(project) != value:
        raise ValueError("joinery.toml keeps [agents] in a form Joinery can't edit.")


def with_setting(project: Path, context: Mapping[str, str]) -> dict[str, str]:
    """The editor context with the project's choice, so every agent reads it."""
    return {**context, KEY: setting(project)}


# --- one shape -------------------------------------------------------------------------


@dataclass(frozen=True)
class Shape:
    """A primitive mesh or CSG shape: where it is, in its object's space."""

    path: str
    name: str
    centre: tuple[float, float, float]
    #: Half its size along x, y and z.
    half: tuple[float, float, float]
    #: The node's text, so an unchanged shape is told from a changed one.
    text: str = ""


@dataclass(frozen=True)
class Finding:
    """One object built from placeholder shapes."""

    file: str
    name: str
    shapes: tuple[Shape, ...]

    def key(self) -> tuple[str, frozenset[tuple[Any, ...]]]:
        """The object as it stands: its shapes' nodes, places and sizes."""
        return (
            self.name,
            frozenset(
                (s.path, s.text, *(round(v, 4) for v in (*s.centre, *s.half)))
                for s in self.shapes
            ),
        )

    def line(self) -> str:
        where = (self.file + ": " if self.file else "") + ", ".join(
            s.path for s in self.shapes
        )
        return (
            f"{self.name} is built from {len(self.shapes)} placeholder shapes. "
            f"{FIX} ({where})"
        )


def _floats(text: str) -> list[float]:
    """The numbers in a value ("Vector3(0.5, 1, 2)": the 3 of its type is not one)."""
    return [float(v) for v in _NUMBER.findall(text.partition("(")[2] or text)]


def _number(props: Mapping[str, str], key: str, fallback: float) -> float:
    values = _floats(props.get(key, ""))
    return abs(values[0]) if values else fallback


def _vector(
    props: Mapping[str, str], key: str, fallback: Sequence[float]
) -> list[float]:
    values = _floats(props.get(key, ""))
    return [abs(v) for v in values] if len(values) == len(fallback) else list(fallback)


def size_of(kind: str, props: Mapping[str, str]) -> tuple[float, float, float] | None:
    """The full size of a primitive mesh or CSG shape; None for anything else."""
    if kind in {"BoxMesh", "PrismMesh", "CSGBox3D"}:
        x, y, z = _vector(props, "size", (1, 1, 1))
        return (x, y, z)
    if kind in {"CylinderMesh", "CSGCylinder3D"}:
        radius = max(
            _number(props, "top_radius", 0.5),
            _number(props, "bottom_radius", 0.5),
            _number(props, "radius", 0.5),
        )
        return (radius * 2, _number(props, "height", 2.0), radius * 2)
    if kind in {"SphereMesh", "CSGSphere3D"}:
        radius = _number(props, "radius", 0.5)
        return (radius * 2, _number(props, "height", radius * 2), radius * 2)
    if kind == "CapsuleMesh":
        radius = _number(props, "radius", 0.5)
        return (radius * 2, _number(props, "height", 2.0), radius * 2)
    if kind in {"TorusMesh", "CSGTorus3D"}:
        outer = _number(props, "outer_radius", 1.0)
        inner = _number(props, "inner_radius", 0.5)
        return (outer * 2, max(outer - inner, 0.01), outer * 2)
    if kind == "PlaneMesh":
        x, z = _vector(props, "size", (2, 2))
        return (x, 0.0, z)
    if kind == "CSGPolygon3D":
        return (1.0, 1.0, _number(props, "depth", 1.0))
    return None


def is_level_piece(name: str, size: Sequence[float]) -> bool:
    """A floor, wall, ramp or platform by its name, or anything ``LEVEL_SIZE`` long."""
    words = {w.lower() for w in _WORDS.findall(name)}
    return bool(words & LEVEL_WORDS) or max(size, default=0.0) >= LEVEL_SIZE


def _bounds(transform: Transform, size: Sequence[float]) -> Shape:
    x, y, z, origin = transform
    half = tuple(
        sum(abs(axis[i]) * size[j] / 2 for j, axis in enumerate((x, y, z)))
        for i in range(3)
    )
    return Shape("", "", origin, (half[0], half[1], half[2]))


def _touch(a: Shape, b: Shape) -> bool:
    return all(
        abs(a.centre[i] - b.centre[i]) <= a.half[i] + b.half[i] + TOUCH
        for i in range(3)
    )


def clusters(shapes: Sequence[Shape]) -> list[list[Shape]]:
    """The shapes in groups that touch or overlap, each group one object."""
    groups: list[list[Shape]] = []
    for shape in shapes:
        joined = [g for g in groups if any(_touch(shape, s) for s in g)]
        merged = [shape]
        for group in joined:
            merged += group
            groups.remove(group)
        groups.append(merged)
    return [sorted(g, key=lambda s: s.path) for g in groups]


def common_name(names: Iterable[str]) -> str:
    """The name the parts share ("ChairSeat", "ChairLeg1": "Chair"), or the first."""
    listed = sorted(names)
    if not listed:
        return ""
    prefix = listed[0]
    for name in listed[1:]:
        while not name.startswith(prefix):
            prefix = prefix[:-1]
    prefix = prefix.rstrip("_0123456789")
    return prefix if len(prefix) >= 3 else listed[0]


# --- a scene file ----------------------------------------------------------------------


@dataclass
class _Node:
    path: str
    parent: str | None
    name: str
    kind: str
    props: dict[str, str]
    section: Section
    instance: bool


def _props(section: Section) -> dict[str, str]:
    props: dict[str, str] = {}
    for line in section.lines[1:]:
        key, sep, value = line.partition(" = ")
        if sep and key and not key.startswith((" ", "\t", '"')):
            props[key.strip()] = value.strip()
    return props


def _nodes(sections: list[Section]) -> dict[str, _Node]:
    nodes: dict[str, _Node] = {}
    for section in sections:
        if section.kind != "node":
            continue
        name = section.text("name")
        if "parent" not in section.attrs:
            path, parent = ".", None
        else:
            raw = section.text("parent")
            parent = "." if raw in {"", "."} else raw
            path = name if parent == "." else f"{parent}/{name}"
        nodes[path] = _Node(
            path,
            parent,
            name,
            section.text("type"),
            _props(section),
            section,
            "instance" in section.attrs,
        )
    return nodes


def _hidden(node: _Node, nodes: Mapping[str, _Node]) -> bool:
    """Marked a placeholder, hidden, or an editor-only helper, itself or above."""
    current: _Node | None = node
    while current is not None:
        props = current.props
        if props.get("metadata/joinery_placeholder") == "true":
            return True
        if props.get("visible") == "false":
            return True
        if props.get("metadata/joinery_editor_only") == "true":
            return True
        if '"editor_only"' in current.section.attrs.get("groups", ""):
            return True
        current = nodes.get(current.parent) if current.parent else None
    return False


def _local(node: _Node) -> Transform:
    transform = _transform(node.section)
    if transform != IDENTITY:
        return transform
    position = _floats(node.props.get("position", ""))
    scale = _floats(node.props.get("scale", "")) or [1.0, 1.0, 1.0]
    origin = position if len(position) == 3 else [0.0, 0.0, 0.0]
    if len(scale) != 3:
        scale = [1.0, 1.0, 1.0]
    return (
        (scale[0], 0.0, 0.0),
        (0.0, scale[1], 0.0),
        (0.0, 0.0, scale[2]),
        (origin[0], origin[1], origin[2]),
    )


def _shape_size(
    node: _Node, resources: Mapping[str, tuple[str, dict[str, str]]]
) -> tuple[float, float, float] | None:
    if node.kind in CSG_SHAPES:
        return size_of(node.kind, node.props)
    if node.kind in {"MeshInstance3D", "CSGMesh3D"}:
        match = re.match(r'SubResource\("([^"]+)"\)', node.props.get("mesh", ""))
        if match and match.group(1) in resources:
            kind, props = resources[match.group(1)]
            if kind in MESHES:
                return size_of(kind, props)
    return None


def scene_findings(text: str, file: str = "") -> list[Finding]:
    """The objects in one scene's text built from placeholder shapes."""
    sections = parse(text)
    nodes = _nodes(sections)
    root = nodes.get(".")
    if root is None or "metadata/blockout_layout" in root.props:
        return []
    resources = {
        s.text("id"): (s.text("type"), _props(s))
        for s in sections
        if s.kind == "sub_resource"
    }
    sizes = {path: _shape_size(node, resources) for path, node in nodes.items()}

    def shapeish(path: str) -> bool:
        return sizes[path] is not None or nodes[path].kind in CSG_GROUPS

    prop_scene = all(
        not n.instance and n.kind in PROP_TYPES for p, n in nodes.items() if p != "."
    )
    groups: dict[str, list[Shape]] = {}
    for path, node in nodes.items():
        size = sizes[path]
        if size is None or _hidden(node, nodes) or is_level_piece(node.name, size):
            continue
        # The object is the parent of the topmost shape in this shape's chain.
        top = path
        chain = [node]
        while (up := nodes[top].parent) is not None and shapeish(up):
            top = up
            chain.append(nodes[up])
        owner = nodes[top].parent or "."
        transform = IDENTITY
        for link in reversed(chain if top != "." else chain[:-1]):
            transform = _compose(transform, _local(link))
        bounds = _bounds(transform, size)
        groups.setdefault(owner, []).append(
            Shape(path, node.name, bounds.centre, bounds.half, node.section.body())
        )
    found: list[Finding] = []
    for owner, shapes in groups.items():
        # Loose shapes in a level may be separate things (two stacked crates); in a
        # scene of one object (a prop scene) every touching pair is that object.
        least = 3 if owner == "." and not prop_scene else 2
        for cluster in clusters(shapes):
            if len(cluster) < least:
                continue
            if prop_scene:
                name = root.name
            elif owner == ".":
                name = common_name(s.name for s in cluster)
            else:
                name = nodes[owner].name
            found.append(Finding(file, name, tuple(cluster)))
    return found


def changed_findings(before: str, after: str, file: str = "") -> list[Finding]:
    """The objects the change built from placeholders: those already in ``before``,
    shape for shape, are not the change's."""
    old = {f.key() for f in scene_findings(before, file)} if before else set()
    return [f for f in scene_findings(after, file) if f.key() not in old]


def problem(findings: Sequence[Finding]) -> Problem | None:
    if not findings:
        return None
    return Problem(
        "complex_models",
        " ".join(f.line() for f in findings),
        sorted({f.file for f in findings if f.file}),
    )


def check_scenes(
    project: Path, stage: Path, original: Path, changes: Sequence[Mapping[str, str]]
) -> Problem | None:
    """The staged scenes' new or changed objects built from placeholder shapes."""
    if setting(project) != BLENDER:
        return None
    findings: list[Finding] = []
    for change in changes:
        path = change["path"]
        if not path.endswith(".tscn") or not change.get("after"):
            continue
        try:
            after = (stage / path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        old = original / path
        before = (
            old.read_text(encoding="utf-8", errors="replace")
            if change.get("before") and old.is_file()
            else ""
        )
        findings += changed_findings(before, after, path)
    return problem(findings)


# --- live edits of the scene on screen -------------------------------------------------


def live_shapes(payload: Mapping[str, Any]) -> list[tuple[str, Shape]]:
    """The placeholder shapes a live scene edit adds, each with its parent ("" the
    scene's root)."""
    operations = payload.get("operations", [])
    found: list[tuple[str, Shape]] = []
    for op in operations if isinstance(operations, list) else []:
        if not isinstance(op, dict):
            continue
        name = str(op.get("name") or "")
        props = op.get("properties") or {}
        props = props if isinstance(props, dict) else {}
        size: tuple[float, float, float] | None = None
        parent = ""
        position: Any
        if op.get("operation") == "create_box":
            raw = op.get("size") or (1, 1, 1)
            size = (float(raw[0]), float(raw[1]), float(raw[2]))
            name = name or "Box"
            position = op.get("position") or (0, 0, 0)
        elif op.get("operation") == "create_node":
            kind = str(op.get("node_type", ""))
            text = {k: str(v) for k, v in props.items()}
            if kind in CSG_SHAPES:
                size = size_of(kind, text)
            elif kind in {"MeshInstance3D", "CSGMesh3D"}:
                mesh = props.get("mesh")
                if isinstance(mesh, dict) and mesh.get("resource_type") in MESHES:
                    inner = mesh.get("properties") or {}
                    size = size_of(
                        str(mesh["resource_type"]),
                        {k: str(v) for k, v in inner.items()}
                        if isinstance(inner, dict)
                        else {},
                    )
            parent = str(op.get("parent") or "")
            name = name or kind
            position = props.get("position") or (0, 0, 0)
        else:
            continue
        if size is None or is_level_piece(name, size):
            continue
        if props.get("metadata/joinery_placeholder") is True:
            continue
        if props.get("visible") is False:
            continue
        try:
            centre = (float(position[0]), float(position[1]), float(position[2]))
        except (TypeError, ValueError, IndexError, KeyError):
            centre = (0.0, 0.0, 0.0)
        half = (size[0] / 2, size[1] / 2, size[2] / 2)
        found.append((parent, Shape(name, name, centre, half)))
    return found


def check_live(project: Path, shapes: Sequence[tuple[str, Shape]]) -> Problem | None:
    """The objects this turn's live edits built from placeholder shapes."""
    if not shapes or setting(project) != BLENDER:
        return None
    by_parent: dict[str, list[Shape]] = {}
    for parent, shape in shapes:
        by_parent.setdefault(parent, []).append(shape)
    findings = [
        Finding(
            "the scene on screen", common_name(s.name for s in cluster), tuple(cluster)
        )
        for parent, group in by_parent.items()
        for cluster in clusters(group)
        if len(cluster) >= (3 if parent == "" else 2)
    ]
    return problem(findings)


# --- `joinery complex-models` ----------------------------------------------------------


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--set", choices=CHOICES, default=None)
    parser.add_argument("--json", action="store_true")


def main(args: argparse.Namespace) -> int:
    """The Settings page's "Complex models in Blender" switch."""
    project = args.project.resolve()
    try:
        if args.set is not None:
            set_setting(project, args.set)
        value = setting(project)
    except (OSError, ValueError) as exc:
        if args.json:
            print(json.dumps({"error": str(exc)}))
        else:
            print(str(exc), file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({"complex_models": value, "blender": value == BLENDER}))
    else:
        print(
            "Complex models: "
            + ("made in Blender" if value == BLENDER else "placeholder shapes allowed")
        )
    return 0
