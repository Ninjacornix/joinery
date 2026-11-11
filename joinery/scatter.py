"""Scatter: place models on a surface or an area of a Godot scene, as a staged scene edit.

Placement is plain geometry, done here and seeded, so the same call places the
same instances. Candidates are thrown at random over the area and kept when
they are at least ``spacing`` from every kept one (a Poisson-disc spread), clear
of the paths, and on the surface. The surface is a model in the scene (a node
that instances a GLB, with its transform) or a GLB at the scene origin; its
triangles give each instance its height and, when asked, its tilt. Paths are
Path3D nodes of the scene or point lists.

The instances go into the scene file as one Node3D group of instanced scenes
(each model a child with its own transform), so the scene opens in stock Godot
and each rock can still be moved by hand. Scattering again with the same group
name replaces the group.
"""

from __future__ import annotations

import itertools
import math
import random
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from joinery.authoring import confined
from joinery.bible_rules import _accessor, _glb_chunks, _world_matrices

Vec3 = tuple[float, float, float]
#: A Transform3D: basis columns x, y, z, then the origin.
Transform = tuple[Vec3, Vec3, Vec3, Vec3]
IDENTITY: Transform = ((1, 0, 0), (0, 1, 0), (0, 0, 1), (0, 0, 0))
MAX_COUNT = 2000
MODEL_SUFFIXES = frozenset({".glb", ".gltf", ".tscn", ".scn"})
_HEADER = re.compile(r"^\[(\w+)(.*)\]\s*$")
_ATTR = re.compile(r'(\w+)=("(?:[^"\\]|\\.)*"|\w+\("(?:[^"\\]|\\.)*"\)|[^\s\]]+)')
_NUMBER = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
_GROUP_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


# --- the scene file -----------------------------------------------------------------


@dataclass
class Section:
    kind: str
    attrs: dict[str, str]
    lines: list[str]

    @property
    def header(self) -> str:
        return self.lines[0]

    def body(self) -> str:
        return "\n".join(self.lines[1:])

    def text(self, name: str) -> str:
        value = self.attrs.get(name, "")
        return value[1:-1] if value.startswith('"') and value.endswith('"') else value


def parse(text: str) -> list[Section]:
    sections: list[Section] = []
    for line in text.splitlines():
        match = _HEADER.match(line)
        if match:
            attrs = {k: v for k, v in _ATTR.findall(match.group(2))}
            sections.append(Section(match.group(1), attrs, [line]))
        elif sections:
            sections[-1].lines.append(line)
        else:
            sections.append(Section("", {}, [line]))
    return sections


def render(sections: list[Section]) -> str:
    out: list[str] = []
    for section in sections:
        lines = list(section.lines)
        while lines and not lines[-1].strip():
            lines.pop()
        if out:
            out.append("")
        out.extend(lines)
    return "\n".join(out) + "\n"


def node_path(section: Section) -> str:
    parent = section.text("parent")
    name = section.text("name")
    if "parent" not in section.attrs:
        return "."
    return name if parent == "." else f"{parent}/{name}"


def _floats(text: str) -> list[float]:
    return [float(v) for v in re.findall(_NUMBER, text)]


def _transform(section: Section) -> Transform:
    match = re.search(
        r"^transform = Transform3D\(([^)]*)\)", section.body(), re.MULTILINE
    )
    if not match:
        return IDENTITY
    v = _floats(match.group(1))
    if len(v) != 12:
        return IDENTITY
    return (
        (v[0], v[1], v[2]),
        (v[3], v[4], v[5]),
        (v[6], v[7], v[8]),
        (v[9], v[10], v[11]),
    )


def _apply(t: Transform, p: Sequence[float]) -> Vec3:
    x, y, z, o = t
    return (
        o[0] + x[0] * p[0] + y[0] * p[1] + z[0] * p[2],
        o[1] + x[1] * p[0] + y[1] * p[1] + z[1] * p[2],
        o[2] + x[2] * p[0] + y[2] * p[1] + z[2] * p[2],
    )


def _compose(parent: Transform, child: Transform) -> Transform:
    origin = _apply(parent, child[3])
    base = (parent[0], parent[1], parent[2], (0.0, 0.0, 0.0))
    return (
        _apply(base, child[0]),
        _apply(base, child[1]),
        _apply(base, child[2]),
        origin,
    )


def _inverse(t: Transform) -> Transform:
    """The inverse of an affine transform (its basis must not be flat)."""
    (a, d, g), (b, e, h), (c, f, i), origin = t
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    if abs(det) < 1e-12:
        raise ValueError("The parent node is scaled flat, so nothing can go in it.")
    rows = (
        ((e * i - f * h) / det, (c * h - b * i) / det, (b * f - c * e) / det),
        ((f * g - d * i) / det, (a * i - c * g) / det, (c * d - a * f) / det),
        ((d * h - e * g) / det, (b * g - a * h) / det, (a * e - b * d) / det),
    )
    columns = tuple(tuple(rows[r][col] for r in range(3)) for col in range(3))
    inverse: Transform = (columns[0], columns[1], columns[2], (0.0, 0.0, 0.0))  # type: ignore[assignment]
    moved = _apply(inverse, origin)
    return (inverse[0], inverse[1], inverse[2], (-moved[0], -moved[1], -moved[2]))


def _world(nodes: dict[str, Section], path: str) -> Transform:
    if path == ".":
        return IDENTITY
    parent = path.rpartition("/")[0] or "."
    return _compose(_world(nodes, parent), _transform(nodes[path]))


def _find(nodes: dict[str, Section], name: str) -> str:
    wanted = name.removeprefix("./")
    if wanted in nodes:
        return wanted
    matches = [p for p in nodes if p.rpartition("/")[2] == wanted]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ValueError(f"The scene has no node {name!r}.")
    raise ValueError(f"Several nodes are named {name!r}: give its path ({matches[0]}).")


# --- the surface ----------------------------------------------------------------------


def glb_triangles(
    data: bytes, place: Transform = IDENTITY
) -> list[tuple[Vec3, Vec3, Vec3]]:
    """Every triangle of a binary glTF, in scene space (+Y up)."""
    document, binary = _glb_chunks(data)
    world = _world_matrices(document)
    found: list[tuple[Vec3, Vec3, Vec3]] = []
    for index, node in enumerate(document.get("nodes", [])):
        if "mesh" not in node:
            continue
        matrix = world[index]
        for primitive in document["meshes"][node["mesh"]].get("primitives", []):
            if primitive.get("mode", 4) != 4 or "POSITION" not in primitive.get(
                "attributes", {}
            ):
                continue
            points = [
                _apply(place, _matrix_point(matrix, p))
                for p in _accessor(
                    document, binary, primitive["attributes"]["POSITION"]
                )
            ]
            if "indices" in primitive:
                order = [
                    int(i[0]) for i in _accessor(document, binary, primitive["indices"])
                ]
            else:
                order = list(range(len(points)))
            for t in range(0, len(order) - 2, 3):
                found.append(
                    (points[order[t]], points[order[t + 1]], points[order[t + 2]])
                )
    return found


def _matrix_point(m: list[list[float]], p: Sequence[float]) -> Vec3:
    return (
        m[0][0] * p[0] + m[0][1] * p[1] + m[0][2] * p[2] + m[0][3],
        m[1][0] * p[0] + m[1][1] * p[1] + m[1][2] * p[2] + m[1][3],
        m[2][0] * p[0] + m[2][1] * p[1] + m[2][2] * p[2] + m[2][3],
    )


class Ground:
    """Heights and normals of the upward-facing triangles of a surface."""

    def __init__(self, triangles: list[tuple[Vec3, Vec3, Vec3]]) -> None:
        self.cells: dict[tuple[int, int], list[tuple[Vec3, Vec3, Vec3, Vec3]]] = {}
        xs = [p[0] for t in triangles for p in t] or [0.0]
        zs = [p[2] for t in triangles for p in t] or [0.0]
        self.bounds = (min(xs), min(zs), max(xs), max(zs))
        extent = max(self.bounds[2] - self.bounds[0], self.bounds[3] - self.bounds[1])
        self.size = max(extent / 64, 0.25)
        for a, b, c in triangles:
            normal = _normal(a, b, c)
            if normal[1] <= 0.2:
                continue
            for cell in self._cells(a, b, c):
                self.cells.setdefault(cell, []).append((a, b, c, normal))

    def _cells(self, a: Vec3, b: Vec3, c: Vec3) -> list[tuple[int, int]]:
        low_x = math.floor(min(a[0], b[0], c[0]) / self.size)
        high_x = math.floor(max(a[0], b[0], c[0]) / self.size)
        low_z = math.floor(min(a[2], b[2], c[2]) / self.size)
        high_z = math.floor(max(a[2], b[2], c[2]) / self.size)
        return [
            (i, j) for i in range(low_x, high_x + 1) for j in range(low_z, high_z + 1)
        ]

    def at(self, x: float, z: float) -> tuple[float, Vec3] | None:
        """The highest surface point above (x, z), with its normal."""
        cell = (math.floor(x / self.size), math.floor(z / self.size))
        best: tuple[float, Vec3] | None = None
        for a, b, c, normal in self.cells.get(cell, []):
            weights = _barycentric(x, z, a, b, c)
            if weights is None:
                continue
            y = weights[0] * a[1] + weights[1] * b[1] + weights[2] * c[1]
            if best is None or y > best[0]:
                best = (y, normal)
        return best


def _normal(a: Vec3, b: Vec3, c: Vec3) -> Vec3:
    u = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
    v = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
    n = (
        u[1] * v[2] - u[2] * v[1],
        u[2] * v[0] - u[0] * v[2],
        u[0] * v[1] - u[1] * v[0],
    )
    length = math.sqrt(sum(x * x for x in n)) or 1.0
    return (n[0] / length, n[1] / length, n[2] / length)


def _barycentric(
    x: float, z: float, a: Vec3, b: Vec3, c: Vec3
) -> tuple[float, float, float] | None:
    det = (b[2] - c[2]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[2] - c[2])
    if abs(det) < 1e-12:
        return None
    w1 = ((b[2] - c[2]) * (x - c[0]) + (c[0] - b[0]) * (z - c[2])) / det
    w2 = ((c[2] - a[2]) * (x - c[0]) + (a[0] - c[0]) * (z - c[2])) / det
    w3 = 1 - w1 - w2
    if min(w1, w2, w3) < -1e-9:
        return None
    return (w1, w2, w3)


# --- paths ------------------------------------------------------------------------------


def _curve_points(
    sections: list[Section], node: Section, place: Transform
) -> list[tuple[float, float]]:
    match = re.search(r'SubResource\("([^"]+)"\)', node.body())
    if not match:
        raise ValueError(f"{node.text('name')} has no curve.")
    curve = next(
        (
            s
            for s in sections
            if s.kind == "sub_resource" and s.text("id") == match.group(1)
        ),
        None,
    )
    if curve is None:
        raise ValueError(f"The curve of {node.text('name')} is not in the scene.")
    points = re.search(r'"points":\s*PackedVector3Array\(([^)]*)\)', curve.body())
    values = _floats(points.group(1)) if points else []
    # Curve3D keeps (in handle, out handle, position) per point.
    out = []
    for i in range(0, len(values) - 8, 9):
        x, _y, z = _apply(place, values[i + 6 : i + 9])
        out.append((x, z))
    return out


def _distance_to(point: tuple[float, float], line: list[tuple[float, float]]) -> float:
    best = math.inf
    px, pz = point
    for (ax, az), (bx, bz) in itertools.pairwise(line):
        dx, dz = bx - ax, bz - az
        length = dx * dx + dz * dz
        t = (
            0.0
            if length == 0
            else max(0.0, min(1.0, ((px - ax) * dx + (pz - az) * dz) / length))
        )
        best = min(best, math.hypot(px - (ax + t * dx), pz - (az + t * dz)))
    if len(line) == 1:
        best = math.hypot(px - line[0][0], pz - line[0][1])
    return best


# --- placing ------------------------------------------------------------------------------


def _rotation_to(normal: Vec3) -> tuple[Vec3, Vec3, Vec3]:
    """The rotation (basis columns) that turns +Y onto `normal`."""
    nx, ny, nz = normal
    axis = (nz, 0.0, -nx)  # up x normal
    s = math.sqrt(axis[0] ** 2 + axis[2] ** 2)
    c = max(-1.0, min(1.0, ny))
    if s < 1e-9:
        return ((1, 0, 0), (0, 1, 0), (0, 0, 1))
    kx, kz = axis[0] / s, axis[2] / s
    t = 1 - c
    # Rodrigues, axis (kx, 0, kz), as columns: the second one is the normal.
    return (
        (t * kx * kx + c, s * kz, t * kx * kz),
        (-s * kz, c, s * kx),
        (t * kx * kz, -s * kx, t * kz * kz + c),
    )


def _basis(
    normal: Vec3, yaw: float, scale: float, align: str
) -> tuple[Vec3, Vec3, Vec3]:
    cy, sy = math.cos(yaw), math.sin(yaw)
    spin = ((cy, 0.0, -sy), (0.0, 1.0, 0.0), (sy, 0.0, cy))
    if align == "normal":
        tilt = _rotation_to(normal)
        base: Transform = (tilt[0], tilt[1], tilt[2], (0.0, 0.0, 0.0))
        spin = (_apply(base, spin[0]), _apply(base, spin[1]), _apply(base, spin[2]))
    return tuple(  # type: ignore[return-value]
        (v[0] * scale, v[1] * scale, v[2] * scale) for v in spin
    )


def _number(value: float) -> str:
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return "0" if text in {"-0", ""} else text


def _transform_text(basis: tuple[Vec3, Vec3, Vec3], origin: Vec3) -> str:
    values = [*basis[0], *basis[1], *basis[2], *origin]
    return "Transform3D(" + ", ".join(_number(v) for v in values) + ")"


def _model_path(project: Path, value: str) -> str:
    relative = value.removeprefix("res://")
    path = PurePosixPath(relative)
    if path.suffix.lower() not in MODEL_SUFFIXES:
        raise ValueError(f"{value} is not a model or scene (.glb, .gltf, .tscn).")
    if not confined(project, relative).is_file():
        raise ValueError(f"{value} is not a file in this project.")
    return path.as_posix()


def scatter(
    project: Path,
    scene: str,
    assets: list[str],
    count: int,
    *,
    seed: int = 1,
    area: Sequence[float] | None = None,
    surface: str = "",
    spacing: float = 1.0,
    scale: Sequence[float] = (0.8, 1.2),
    align: str = "up",
    paths: Sequence[Any] = (),
    path_clearance: float = 1.0,
    path_band: float = 0.0,
    parent: str = ".",
    name: str = "Scatter",
    height: float = 0.0,
) -> dict[str, Any]:
    """Place `count` instances of the models in a scene file. See the module notes."""
    relative = scene.removeprefix("res://")
    if PurePosixPath(relative).suffix != ".tscn":
        raise ValueError("Scatter into a .tscn scene of this project.")
    target = confined(project, relative)
    if not assets or len(assets) > 32:
        raise ValueError("Name 1 to 32 models to place.")
    models = [_model_path(project, a) for a in assets]
    if (
        isinstance(count, bool)
        or not isinstance(count, int)
        or not 1 <= count <= MAX_COUNT
    ):
        raise ValueError(f"Place 1 to {MAX_COUNT} instances.")
    if not _GROUP_NAME.match(name):
        raise ValueError(
            "The group name is letters, digits and '_' (a Godot node name)."
        )
    numbers = [spacing, path_clearance, path_band, height, *scale]
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in numbers):
        raise ValueError(
            "Spacing, scale, height and path distances are finite numbers."
        )
    if not 0.01 <= spacing <= 1000:
        raise ValueError("spacing is 0.01 to 1000 metres.")
    if len(scale) != 2 or not 0.01 <= scale[0] <= scale[1] <= 100:
        raise ValueError("scale is [smallest, largest], from 0.01 to 100.")
    if align not in {"up", "normal"}:
        raise ValueError('align is "up" (upright) or "normal" (follow the surface).')
    if path_clearance < 0 or path_band < 0:
        raise ValueError("Path distances are 0 or more metres.")
    if target.is_file():
        sections = parse(target.read_text(encoding="utf-8"))
    else:
        words = re.split(r"[^A-Za-z0-9]+", PurePosixPath(relative).stem)
        root = "".join(w.capitalize() for w in words if w)
        root = root if root[:1].isalpha() else "Scene" + root
        sections = parse(f'[gd_scene format=3]\n\n[node name="{root}" type="Node3D"]\n')
    nodes = {node_path(s): s for s in sections if s.kind == "node"}
    if "." not in nodes:
        raise ValueError(f"{relative} has no root node.")
    parent_path = "." if parent in {"", "."} else _find(nodes, parent)
    group_path = name if parent_path == "." else f"{parent_path}/{name}"
    resources = {
        s.text("id"): s.text("path") for s in sections if s.kind == "ext_resource"
    }

    ground: Ground | None = None
    if surface:
        if surface.removeprefix("res://").lower().endswith(".glb"):
            data = confined(project, _model_path(project, surface)).read_bytes()
            ground = Ground(glb_triangles(data))
        else:
            where = _find(nodes, surface)
            used = re.search(
                r'ExtResource\("([^"]+)"\)', nodes[where].attrs.get("instance", "")
            )
            model = resources.get(used.group(1), "") if used else ""
            if not model.lower().endswith(".glb"):
                raise ValueError(
                    f"{surface} is not a model: scatter on a node that instances a GLB, "
                    "or give an area and a height."
                )
            data = confined(project, model.removeprefix("res://")).read_bytes()
            ground = Ground(glb_triangles(data, _world(nodes, where)))
    if area is not None:
        if len(area) != 4 or not all(
            isinstance(v, (int, float)) and math.isfinite(v) for v in area
        ):
            raise ValueError("area is [min_x, min_z, max_x, max_z] in metres.")
        box = (float(area[0]), float(area[1]), float(area[2]), float(area[3]))
        if (
            box[2] <= box[0]
            or box[3] <= box[1]
            or max(box[2] - box[0], box[3] - box[1]) > 10000
        ):
            raise ValueError("area is [min_x, min_z, max_x, max_z] with max above min.")
    elif ground is not None:
        box = ground.bounds
    else:
        raise ValueError(
            "Give an area [min_x, min_z, max_x, max_z] or a surface to scatter on."
        )
    lines: list[list[tuple[float, float]]] = []
    for item in paths:
        if isinstance(item, str):
            where = _find(nodes, item)
            if nodes[where].text("type") != "Path3D":
                raise ValueError(f"{item} is not a Path3D.")
            lines.append(_curve_points(sections, nodes[where], _world(nodes, where)))
        elif (
            isinstance(item, list)
            and item
            and all(
                isinstance(p, list)
                and len(p) == 2
                and all(isinstance(v, (int, float)) for v in p)
                for p in item
            )
        ):
            lines.append([(float(p[0]), float(p[1])) for p in item])
        else:
            raise ValueError(
                "Each path is a Path3D node name or a list of [x, z] points."
            )
    if path_band and not lines:
        raise ValueError("path_band keeps instances near a path: name the path.")

    rng = random.Random(f"joinery-scatter:{seed}")
    cell = spacing / math.sqrt(2)
    taken: dict[tuple[int, int], tuple[float, float]] = {}
    placed: list[tuple[str, tuple[Vec3, Vec3, Vec3], Vec3]] = []
    attempts = count * 40
    for _ in range(attempts):
        if len(placed) >= count:
            break
        x, z = rng.uniform(box[0], box[2]), rng.uniform(box[1], box[3])
        model = models[rng.randrange(len(models))]
        size = rng.uniform(scale[0], scale[1])
        yaw = rng.uniform(0.0, math.tau)
        key = (math.floor(x / cell), math.floor(z / cell))
        near = (
            taken.get((key[0] + i, key[1] + j))
            for i in range(-2, 3)
            for j in range(-2, 3)
        )
        if any(
            p is not None and math.hypot(p[0] - x, p[1] - z) < spacing for p in near
        ):
            continue
        if lines:
            gap = min(_distance_to((x, z), line) for line in lines)
            if gap < path_clearance or (path_band and gap > path_clearance + path_band):
                continue
        normal: Vec3 = (0.0, 1.0, 0.0)
        y = height
        if ground is not None:
            hit = ground.at(x, z)
            if hit is None:
                continue
            y, normal = hit
        taken[key] = (x, z)
        placed.append((model, _basis(normal, yaw, size, align), (x, y, z)))

    # Out with the old group, in with the new.
    keep = [
        s
        for s in sections
        if not (
            s.kind == "node"
            and (
                node_path(s) == group_path or node_path(s).startswith(group_path + "/")
            )
        )
    ]
    text_now = "\n".join(line for s in keep for line in s.lines[1:] + [s.header])
    keep = [
        s
        for s in keep
        if not (
            s.kind == "ext_resource"
            and s.text("id").startswith("scatter_")
            and f'ExtResource("{s.text("id")}")' not in text_now
        )
    ]
    ids = {s.text("id") for s in keep if s.kind == "ext_resource"}
    by_path = {
        s.text("path"): s.text("id")
        for s in keep
        if s.kind == "ext_resource" and s.text("type") == "PackedScene"
    }
    added: list[Section] = []
    for model in dict.fromkeys(m for m, _b, _o in placed):
        if f"res://{model}" in by_path:
            continue
        number = 1
        while f"scatter_{number}" in ids:
            number += 1
        ids.add(f"scatter_{number}")
        by_path[f"res://{model}"] = f"scatter_{number}"
        header = f'[ext_resource type="PackedScene" path="res://{model}" id="scatter_{number}"]'
        added.append(Section("ext_resource", {}, [header]))
    last = max(
        (i for i, s in enumerate(keep) if s.kind in {"gd_scene", "ext_resource"}),
        default=0,
    )
    keep[last + 1 : last + 1] = added
    group_parent = "." if parent_path == "." else parent_path
    keep.append(
        Section(
            "node",
            {},
            [f'[node name="{name}" type="Node3D" parent="{group_parent}"]'],
        )
    )
    counts: dict[str, int] = {}
    # Placements are in scene space; the group sits under its parent.
    local = _inverse(_world(nodes, parent_path))
    for model, world_basis, world_origin in placed:
        origin = _apply(local, world_origin)
        rotate: Transform = (local[0], local[1], local[2], (0.0, 0.0, 0.0))
        basis = (
            _apply(rotate, world_basis[0]),
            _apply(rotate, world_basis[1]),
            _apply(rotate, world_basis[2]),
        )
        counts[model] = counts.get(model, 0) + 1
        stem = re.sub(r"[^A-Za-z0-9_]", "_", PurePosixPath(model).stem)
        label = (
            "".join(p.capitalize() for p in stem.split("_") if not p.isdigit())
            or "Model"
        )
        label += "_" + "_".join(p for p in stem.split("_") if p.isdigit())
        node = f"{label.rstrip('_')}_{sum(counts.values()):03d}"
        keep.append(
            Section(
                "node",
                {},
                [
                    f'[node name="{node}" parent="{group_path}" instance=ExtResource("{by_path["res://" + model]}")]',
                    f"transform = {_transform_text(basis, origin)}",
                ],
            )
        )
    first = keep[0]
    if first.kind == "gd_scene" and "load_steps" in first.attrs:
        steps = sum(1 for s in keep if s.kind in {"ext_resource", "sub_resource"}) + 1
        first.lines[0] = re.sub(r"load_steps=\d+", f"load_steps={steps}", first.lines[0])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render(keep), encoding="utf-8")
    result: dict[str, Any] = {
        "scene": relative,
        "group": group_path,
        "placed": len(placed),
        "requested": count,
        "per_model": counts,
        "area": [round(v, 3) for v in box],
        "status": "staged",
        "requires_review": True,
    }
    if len(placed) < count:
        result["note"] = (
            f"Only {len(placed)} of {count} fit: lower the spacing, widen the area, "
            "or move the paths."
        )
    return result
