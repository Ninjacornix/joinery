"""The greybox scene for a resolved layout, as .tscn text: the same bytes every time.

3D levels are CSG boxes with collision (floors, wall pieces, ramps, platforms), the
way a Godot blockout is usually made, so the user can grab and resize any of them in
the editor. 2D levels are StaticBody2D rectangles with a Polygon2D each, and floors as
flat polygons under them. Every room, corridor, wall piece and marker is a named node:
``Rooms/hall/north_1``, ``Markers/goal_exit``. Markers are areas in the groups the
starting points and the playtest bot use (``goal``, ``coin``, ``pickup``,
``checkpoint``, ``hazard``); the spawn is a marker in ``player_spawn``, and players
appear under ``Players`` (the game core's level contract, ADR 0024).
"""

from __future__ import annotations

import math
from pathlib import Path

from joinery.blockout.layout import number
from joinery.blockout.world import XY, Area, Piece, Spot, World

#: The level script every blockout scene uses, written beside it in the project.
SCRIPT_PATH = "res://game/blockout/blockout_level.gd"
SCRIPT_SOURCE = Path(__file__).with_name("blockout_level.gd")
FLOOR_THICKNESS = 0.2
RAMP_THICKNESS = 0.3
#: Physics layers markers watch: 1 (a player on the default layer) and 2 ("players").
MARKER_MASK = 3

COLOURS = {
    "floor": (0.47, 0.5, 0.55),
    "corridor": (0.43, 0.46, 0.5),
    "wall": (0.78, 0.8, 0.83),
    "window": (0.62, 0.72, 0.8),
    "platform": (0.6, 0.64, 0.7),
    "ramp": (0.55, 0.58, 0.63),
    "goal": (0.3, 0.69, 0.31),
    "coin": (0.95, 0.79, 0.3),
    "pickup": (0.34, 0.63, 0.83),
    "checkpoint": (0.88, 0.88, 0.88),
    "hazard": (0.88, 0.35, 0.28),
}


def _colour(name: str) -> str:
    r, g, b = COLOURS[name]
    return f"Color({number(r)}, {number(g)}, {number(b)}, 1)"


def _vec3(x: float, y: float, z: float) -> str:
    return f"Vector3({number(x)}, {number(y)}, {number(z)})"


def _vec2(x: float, y: float) -> str:
    return f"Vector2({number(x)}, {number(y)})"


def _basis_transform(
    x_axis: tuple[float, float, float],
    y_axis: tuple[float, float, float],
    z_axis: tuple[float, float, float],
    origin: tuple[float, float, float],
) -> str:
    # Godot writes a basis by rows: x.x, y.x, z.x, then x.y, y.y, z.y, …
    rows = [axis[i] for i in range(3) for axis in (x_axis, y_axis, z_axis)]
    values = [*rows, *origin]
    return "Transform3D(" + ", ".join(number(v) for v in values) + ")"


def _translate(x: float, y: float, z: float) -> str:
    return _basis_transform((1, 0, 0), (0, 1, 0), (0, 0, 1), (x, y, z))


def _yaw(direction: XY, origin: tuple[float, float, float]) -> str:
    """A basis whose local X runs along plan ``direction`` (plan y is Godot +Z)."""
    dx, dz = direction
    return _basis_transform((dx, 0, dz), (0, 1, 0), (-dz, 0, dx), origin)


def _cross(
    a: tuple[float, float, float], b: tuple[float, float, float]
) -> tuple[float, float, float]:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


class _Scene:
    """Collects resources and nodes, then writes them in Godot's order."""

    def __init__(self) -> None:
        self.ext: list[str] = []
        self.sub: dict[str, tuple[str, str, list[str]]] = {}
        self.nodes: list[str] = []
        self.names: set[str] = set()

    def resource(self, kind: str, key: str, lines: list[str]) -> str:
        found = self.sub.get(key)
        if found is None:
            ident = f"{kind}_{len(self.sub) + 1}"
            self.sub[key] = (kind, ident, lines)
            return ident
        return found[1]

    def node(
        self,
        name: str,
        kind: str,
        parent: str | None,
        lines: list[str] | None = None,
        groups: list[str] | None = None,
    ) -> str:
        path = name if parent in {None, "."} else f"{parent}/{name}"
        if parent is None:
            path = "."
        if path in self.names:
            raise ValueError(f"Two nodes would both be called {path}.")
        self.names.add(path)
        header = f'[node name="{name}" type="{kind}"'
        if parent is not None:
            header += f' parent="{parent}"'
        if groups:
            header += " groups=[" + ", ".join(f'"{g}"' for g in groups) + "]"
        self.nodes.append("\n".join([header + "]", *(lines or [])]))
        return "." if parent is None else path

    def text(self) -> str:
        steps = len(self.ext) + len(self.sub) + 1
        parts = [f"[gd_scene load_steps={steps} format=3]"]
        parts.extend(self.ext)
        for kind, ident, lines in self.sub.values():
            parts.append(
                "\n".join([f'[sub_resource type="{kind}" id="{ident}"]', *lines])
            )
        parts.extend(self.nodes)
        return "\n\n".join(parts) + "\n"


def _material(scene: _Scene, colour: str) -> str:
    return scene.resource(
        "StandardMaterial3D", "mat:" + colour, [f"albedo_color = {_colour(colour)}"]
    )


def build(world: World, layout_path: str, layout_sha: str) -> str:
    """The scene text for ``world``. ``layout_path`` (res://) and ``layout_sha`` (of
    the layout file's bytes) are recorded on the root, so a scene names its recipe."""
    scene = _Scene()
    scene.ext.append(f'[ext_resource type="Script" path="{SCRIPT_PATH}" id="1_level"]')
    meta = [
        'script = ExtResource("1_level")',
        f'mode = "{world.mode}"',
        f'metadata/blockout_layout = "{layout_path}"',
        f'metadata/blockout_sha256 = "{layout_sha}"',
    ]
    if world.three_d:
        _build_3d(scene, world, meta)
    else:
        _build_2d(scene, world, meta)
    return scene.text()


# --- 3D ------------------------------------------------------------------------------


def _build_3d(scene: _Scene, world: World, meta: list[str]) -> None:
    scene.node("Blockout", "Node3D", None, meta)
    env = scene.resource(
        "Environment",
        "env",
        [
            "background_mode = 1",
            "background_color = Color(0.16, 0.17, 0.19, 1)",
            "ambient_light_source = 2",
            "ambient_light_color = Color(0.72, 0.74, 0.78, 1)",
            "ambient_light_energy = 0.55",
        ],
    )
    scene.node(
        "Environment", "WorldEnvironment", ".", [f'environment = SubResource("{env}")']
    )
    pitch, yaw = math.radians(-55.0), math.radians(35.0)
    forward = (
        math.cos(pitch) * -math.sin(yaw),
        math.sin(pitch),
        math.cos(pitch) * -math.cos(yaw),
    )
    z_axis = (-forward[0], -forward[1], -forward[2])
    x_axis = _normal(_cross((0.0, 1.0, 0.0), z_axis))
    y_axis = _cross(z_axis, x_axis)
    scene.node(
        "Sun",
        "DirectionalLight3D",
        ".",
        [
            "transform = " + _basis_transform(x_axis, y_axis, z_axis, (0.0, 10.0, 0.0)),
            "shadow_enabled = true",
        ],
    )
    groups = {"room": "Rooms", "corridor": "Corridors", "stairs": "Stairs"}
    made: set[str] = set()
    for area in world.areas:
        group = groups[area.kind]
        if group not in made:
            scene.node(group, "Node3D", ".")
            made.add(group)
        parent = scene.node(area.id, "Node3D", group)
        if area.kind == "stairs":
            _ramp(scene, area, parent)
        else:
            _floor(scene, area, parent, "floor" if area.kind == "room" else "corridor")
    if world.platforms:
        scene.node("Platforms", "Node3D", ".")
        for platform in world.platforms:
            x0, y0, x1, y1 = platform.bounds
            thickness = max(platform.thickness, 0.1)
            scene.node(
                platform.id,
                "CSGBox3D",
                "Platforms",
                [
                    "transform = "
                    + _translate(
                        (x0 + x1) / 2, platform.top - thickness / 2, (y0 + y1) / 2
                    ),
                    "use_collision = true",
                    "size = " + _vec3(x1 - x0, thickness, y1 - y0),
                    f'material = SubResource("{_material(scene, "platform")}")',
                ],
            )
    for piece in world.pieces:
        if piece.owner not in scene.names:
            scene.node(piece.owner, "Node3D", ".")
        _wall_3d(scene, piece)
    _markers_3d(scene, world)
    scene.node("Players", "Node3D", ".")


def _normal(v: tuple[float, float, float]) -> tuple[float, float, float]:
    length = math.sqrt(sum(c * c for c in v)) or 1.0
    return (v[0] / length, v[1] / length, v[2] / length)


def _floor(scene: _Scene, area: Area, parent: str, colour: str) -> None:
    material = f'material = SubResource("{_material(scene, colour)}")'
    if area.is_rect:
        x0, y0, x1, y1 = area.bounds
        scene.node(
            "Floor",
            "CSGBox3D",
            parent,
            [
                "transform = "
                + _translate(
                    (x0 + x1) / 2, area.floor - FLOOR_THICKNESS / 2, (y0 + y1) / 2
                ),
                "use_collision = true",
                "size = " + _vec3(x1 - x0, FLOOR_THICKNESS, y1 - y0),
                material,
            ],
        )
        return
    points = ", ".join(f"{number(x)}, {number(y)}" for x, y in area.polygon)
    scene.node(
        "Floor",
        "CSGPolygon3D",
        parent,
        [
            "transform = "
            + _basis_transform(
                (1, 0, 0), (0, 0, 1), (0, -1, 0), (0, area.floor - FLOOR_THICKNESS, 0)
            ),
            "use_collision = true",
            f"polygon = PackedVector2Array({points})",
            f"depth = {number(FLOOR_THICKNESS)}",
            material,
        ],
    )


def _ramp(scene: _Scene, area: Area, parent: str) -> None:
    x0, y0, x1, y1 = area.bounds
    direction = {
        "north": (0.0, -1.0),
        "south": (0.0, 1.0),
        "east": (1.0, 0.0),
        "west": (-1.0, 0.0),
    }[area.up]
    run = (y1 - y0) if area.up in {"north", "south"} else (x1 - x0)
    width = (x1 - x0) if area.up in {"north", "south"} else (y1 - y0)
    rise = area.top - area.bottom
    length = math.hypot(run, rise)
    slope = math.atan2(rise, run)
    x_axis = (
        math.cos(slope) * direction[0],
        math.sin(slope),
        math.cos(slope) * direction[1],
    )
    side = (-direction[1], 0.0, direction[0])
    y_axis = _normal(_cross(side, x_axis))
    if y_axis[1] < 0:
        y_axis = (-y_axis[0], -y_axis[1], -y_axis[2])
    z_axis = _cross(x_axis, y_axis)
    top_centre = ((x0 + x1) / 2, (area.bottom + area.top) / 2, (y0 + y1) / 2)
    origin = tuple(top_centre[i] - y_axis[i] * RAMP_THICKNESS / 2 for i in range(3))
    scene.node(
        "Ramp",
        "CSGBox3D",
        parent,
        [
            "transform = "
            + _basis_transform(
                x_axis, y_axis, z_axis, (origin[0], origin[1], origin[2])
            ),
            "use_collision = true",
            "size = " + _vec3(length, RAMP_THICKNESS, width),
            f'material = SubResource("{_material(scene, "ramp")}")',
        ],
    )


def _wall_3d(scene: _Scene, piece: Piece) -> None:
    length = piece.length
    dx, dy = (piece.b[0] - piece.a[0]) / length, (piece.b[1] - piece.a[1]) / length
    cx, cy = piece.centre
    height = piece.top - piece.base
    colour = "window" if piece.kind == "window" else "wall"
    scene.node(
        piece.name,
        "CSGBox3D",
        piece.owner,
        [
            "transform = " + _yaw((dx, dy), (cx, piece.base + height / 2, cy)),
            "use_collision = true",
            "size = " + _vec3(length, height, piece.thickness),
            f'material = SubResource("{_material(scene, colour)}")',
        ],
        groups=["no_teleport"],
    )


_FACING = {
    "north": (1.0, 0.0),
    "south": (-1.0, 0.0),
    "east": (0.0, 1.0),
    "west": (0.0, -1.0),
}


def _markers_3d(scene: _Scene, world: World) -> None:
    scene.node("Markers", "Node3D", ".")
    if world.spawn is not None:
        spawn = world.spawn
        scene.node(
            "Spawn",
            "Marker3D",
            "Markers",
            [
                "transform = "
                + _yaw(
                    _FACING[spawn.facing],
                    (spawn.at[0], spawn.height + 0.05, spawn.at[1]),
                )
            ],
            groups=["player_spawn"],
        )
    for marker in world.markers:
        _marker_3d(scene, marker)


def _marker_3d(scene: _Scene, marker: Spot) -> None:
    radius = max(marker.size / 2, 0.3)
    path = scene.node(
        marker.id,
        "Area3D",
        "Markers",
        [
            "transform = " + _translate(marker.at[0], marker.height, marker.at[1]),
            "collision_layer = 0",
            f"collision_mask = {MARKER_MASK}",
            "monitorable = false",
        ],
        groups=[marker.kind],
    )
    shape = scene.resource(
        "CylinderShape3D",
        f"zone:{number(radius)}",
        [f"height = {number(2.0)}", f"radius = {number(max(radius, 0.6))}"],
    )
    scene.node(
        "Shape",
        "CollisionShape3D",
        path,
        ["transform = " + _translate(0, 1.0, 0), f'shape = SubResource("{shape}")'],
    )
    material = _material(scene, marker.kind)
    if marker.kind in {"goal", "checkpoint", "hazard"}:
        mesh = scene.resource(
            "CylinderMesh",
            f"pad:{marker.kind}:{number(radius)}",
            [
                f'material = SubResource("{material}")',
                f"top_radius = {number(radius)}",
                f"bottom_radius = {number(radius)}",
                "height = 0.1",
            ],
        )
        offset = 0.05
    elif marker.kind == "coin":
        mesh = scene.resource(
            "CylinderMesh",
            f"coin:{number(radius)}",
            [
                f'material = SubResource("{material}")',
                f"top_radius = {number(radius * 0.6)}",
                f"bottom_radius = {number(radius * 0.6)}",
                "height = 0.08",
            ],
        )
        offset = 1.0
    else:
        mesh = scene.resource(
            "SphereMesh",
            f"pickup:{number(radius)}",
            [
                f'material = SubResource("{material}")',
                f"radius = {number(radius * 0.6)}",
                f"height = {number(radius * 1.2)}",
            ],
        )
        offset = 1.0
    turn = (0, 0, -1) if marker.kind == "coin" else (0, 1, 0)
    if marker.kind == "coin":
        transform = _basis_transform((1, 0, 0), turn, (0, 1, 0), (0, offset, 0))
    else:
        transform = _translate(0, offset, 0)
    scene.node(
        "Look",
        "MeshInstance3D",
        path,
        ["transform = " + transform, f'mesh = SubResource("{mesh}")'],
    )
    if marker.kind == "goal":
        pole = scene.resource(
            "CylinderMesh",
            "pole",
            [
                f'material = SubResource("{material}")',
                "top_radius = 0.06",
                "bottom_radius = 0.06",
                "height = 2.0",
            ],
        )
        scene.node(
            "Pole",
            "MeshInstance3D",
            path,
            ["transform = " + _translate(0, 1.0, 0), f'mesh = SubResource("{pole}")'],
        )


# --- 2D ------------------------------------------------------------------------------


def _colour2d(name: str) -> str:
    return f"color = {_colour(name)}"


def _packed(points: list[XY] | tuple[XY, ...]) -> str:
    return (
        "PackedVector2Array("
        + ", ".join(f"{number(x)}, {number(y)}" for x, y in points)
        + ")"
    )


def _build_2d(scene: _Scene, world: World, meta: list[str]) -> None:
    scene.node("Blockout", "Node2D", None, meta)
    groups = {"room": "Rooms", "corridor": "Corridors", "stairs": "Stairs"}
    made: set[str] = set()
    side = world.mode == "2d-side"
    for area in world.areas:
        group = groups[area.kind]
        if group not in made:
            scene.node(group, "Node2D", ".")
            made.add(group)
        parent = scene.node(area.id, "Node2D", group)
        if area.kind == "stairs" and side:
            _slope_2d(scene, area, parent)
            continue
        scene.node(
            "Floor",
            "Polygon2D",
            parent,
            [
                "z_index = -1",
                _colour2d("floor" if area.kind != "corridor" else "corridor"),
                "polygon = " + _packed(area.polygon),
            ],
        )
    if world.platforms:
        scene.node("Platforms", "Node2D", ".")
        for platform in world.platforms:
            x0, y0, x1, y1 = platform.bounds
            _box_2d(
                scene,
                platform.id,
                "Platforms",
                ((x0 + x1) / 2, (y0 + y1) / 2),
                x1 - x0,
                y1 - y0,
                0.0,
                "platform",
            )
    for piece in world.pieces:
        if piece.owner not in scene.names:
            scene.node(piece.owner, "Node2D", ".")
        length = piece.length
        angle = math.atan2(piece.b[1] - piece.a[1], piece.b[0] - piece.a[0])
        _box_2d(
            scene,
            piece.name,
            piece.owner,
            piece.centre,
            length,
            piece.thickness,
            angle,
            "window" if piece.kind == "window" else "wall",
        )
    _markers_2d(scene, world)
    scene.node("Players", "Node2D", ".")


def _box_2d(
    scene: _Scene,
    name: str,
    parent: str,
    centre: XY,
    length: float,
    thickness: float,
    angle: float,
    colour: str,
) -> None:
    lines = ["position = " + _vec2(*centre)]
    if abs(angle) > 1e-9:
        lines.append(f"rotation = {number(angle)}")
    path = scene.node(name, "StaticBody2D", parent, lines)
    shape = scene.resource(
        "RectangleShape2D",
        f"rect:{number(length)}:{number(thickness)}",
        ["size = " + _vec2(length, thickness)],
    )
    scene.node("Shape", "CollisionShape2D", path, [f'shape = SubResource("{shape}")'])
    half_l, half_t = length / 2, thickness / 2
    scene.node(
        "Look",
        "Polygon2D",
        path,
        [
            _colour2d(colour),
            "polygon = "
            + _packed(
                [
                    (-half_l, -half_t),
                    (half_l, -half_t),
                    (half_l, half_t),
                    (-half_l, half_t),
                ]
            ),
        ],
    )


def _slope_2d(scene: _Scene, area: Area, parent: str) -> None:
    x0, y0, x1, y1 = area.bounds
    if area.up == "west":
        points = [(x0, y0), (x1, y1), (x0, y1)]
    else:
        points = [(x1, y0), (x1, y1), (x0, y1)]
    body = scene.node("Slope", "StaticBody2D", parent)
    scene.node("Shape", "CollisionPolygon2D", body, ["polygon = " + _packed(points)])
    scene.node(
        "Look", "Polygon2D", body, [_colour2d("ramp"), "polygon = " + _packed(points)]
    )


def _markers_2d(scene: _Scene, world: World) -> None:
    scene.node("Markers", "Node2D", ".")
    if world.spawn is not None:
        scene.node(
            "Spawn",
            "Marker2D",
            "Markers",
            ["position = " + _vec2(*world.spawn.at)],
            groups=["player_spawn"],
        )
    for marker in world.markers:
        radius = max(marker.size / 2, 4.0)
        path = scene.node(
            marker.id,
            "Area2D",
            "Markers",
            [
                "position = " + _vec2(*marker.at),
                "collision_layer = 0",
                f"collision_mask = {MARKER_MASK}",
                "monitorable = false",
            ],
            groups=[marker.kind],
        )
        shape = scene.resource(
            "CircleShape2D", f"circle:{number(radius)}", [f"radius = {number(radius)}"]
        )
        scene.node(
            "Shape", "CollisionShape2D", path, [f'shape = SubResource("{shape}")']
        )
        sides = 4 if marker.kind in {"coin", "pickup"} else 8
        points = [
            (
                radius
                * math.cos(math.tau * k / sides + (0 if sides == 4 else math.pi / 8)),
                radius
                * math.sin(math.tau * k / sides + (0 if sides == 4 else math.pi / 8)),
            )
            for k in range(sides)
        ]
        scene.node(
            "Look",
            "Polygon2D",
            path,
            [_colour2d(marker.kind), "polygon = " + _packed(points)],
        )
