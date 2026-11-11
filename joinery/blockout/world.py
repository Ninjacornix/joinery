"""A layout in world units, with every wall piece worked out.

:func:`resolve` turns a :class:`~joinery.blockout.layout.Layout` into a :class:`World`:
plan coordinates scaled to world units (metres in 3D, pixels in 2D), defaults filled
in from the starting point's player (:class:`Profile`), stairs given their heights,
and the walls split into solid pieces around doors, windows and corridors. The
builder, the reachability check and the plan picture all read the same World, so
what the check walks is what Godot gets.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from typing import Literal

from joinery.blockout.layout import RECT_EDGES, Layout, Mode

XY = tuple[float, float]
EPS = 1e-6


@dataclass(frozen=True)
class Profile:
    """What the starting point's player can do, in world units."""

    blueprint: str
    mode: Mode
    #: Body radius (3D, top-down) or half its width (side view).
    radius: float
    height: float
    #: The highest ledge it walks up without jumping.
    step: float
    #: The highest ledge a jump reaches, and the widest gap it clears.
    jump: float
    across: float
    #: World units per plan unit when the layout's units are tiles (2D), else 1.
    tile: float = 1.0
    #: Can the player jump at all.
    jumps: bool = True


PROFILES: dict[str, Profile] = {
    "third-person-3d": Profile("third-person-3d", "3d", 0.4, 1.8, 0.3, 1.8, 3.0),
    "first-person-3d": Profile("first-person-3d", "3d", 0.35, 1.8, 0.3, 1.0, 2.5),
    "vr-room": Profile("vr-room", "3d", 0.3, 1.8, 0.3, 0.0, 0.0, jumps=False),
    "platformer-2d": Profile(
        "platformer-2d", "2d-side", 5.0, 14.0, 0.0, 48.0, 64.0, tile=16.0
    ),
    "top-down": Profile("top-down", "2d-topdown", 18.0, 0.0, 0.0, 0.0, 0.0, tile=32.0),
}
#: For a project without a starting point, or one whose player doesn't fit the mode.
GENERIC: dict[str, Profile] = {
    "3d": Profile("", "3d", 0.4, 1.8, 0.3, 1.0, 2.0),
    "2d-side": Profile("", "2d-side", 6.0, 16.0, 0.0, 48.0, 64.0, tile=16.0),
    "2d-topdown": Profile("", "2d-topdown", 14.0, 0.0, 0.0, 0.0, 0.0, tile=32.0),
}


def profile_for(blueprint: str, mode: Mode) -> Profile:
    """The player profile of ``blueprint`` when it plays in ``mode``, else a generic one."""
    found = PROFILES.get(blueprint)
    if found is not None and found.mode == mode:
        return found
    return GENERIC[mode]


@dataclass(frozen=True)
class Area:
    """Somewhere to stand: a room, corridor, stairs or platform top."""

    kind: Literal["room", "corridor", "stairs", "platform"]
    id: str
    polygon: tuple[XY, ...]
    floor: float = 0.0
    #: Stairs: the way up and the heights at the bottom and top edges.
    up: str = ""
    bottom: float = 0.0
    top: float = 0.0
    #: Platforms: how thick the block is (3D).
    thickness: float = 0.0

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        xs = [p[0] for p in self.polygon]
        ys = [p[1] for p in self.polygon]
        return min(xs), min(ys), max(xs), max(ys)

    @property
    def is_rect(self) -> bool:
        return len(self.polygon) == 4 and _axis_aligned(self.polygon)

    def contains(self, x: float, y: float, margin: float = 0.0) -> bool:
        """``(x, y)`` is inside, at least ``margin`` from the edges (negative: grown)."""
        if self.is_rect:
            x0, y0, x1, y1 = self.bounds
            return x0 + margin <= x <= x1 - margin and y0 + margin <= y <= y1 - margin
        if not _inside(self.polygon, x, y):
            return margin < 0 and _edge_distance(self.polygon, x, y) <= -margin
        return margin <= 0 or _edge_distance(self.polygon, x, y) >= margin

    def height_at(self, x: float, y: float) -> float:
        """The walking surface's height at ``(x, y)``: stairs rise along ``up``."""
        if self.kind == "platform":
            return self.top
        if self.kind != "stairs":
            return self.floor
        x0, y0, x1, y1 = self.bounds
        if self.up in {"north", "south"}:
            run, at = y1 - y0, (y - y0) if self.up == "south" else (y1 - y)
        else:
            run, at = x1 - x0, (x - x0) if self.up == "east" else (x1 - x)
        part = min(max(at / run, 0.0), 1.0) if run > EPS else 1.0
        return self.bottom + (self.top - self.bottom) * part


@dataclass(frozen=True)
class Piece:
    """One solid wall box along ``a`` -> ``b`` (its centre line)."""

    owner: str
    name: str
    a: XY
    b: XY
    thickness: float
    base: float
    top: float
    kind: Literal["wall", "window", "lintel"] = "wall"

    @property
    def length(self) -> float:
        return math.dist(self.a, self.b)

    @property
    def centre(self) -> XY:
        return ((self.a[0] + self.b[0]) / 2, (self.a[1] + self.b[1]) / 2)

    def distance(self, x: float, y: float) -> float:
        """Plan distance from ``(x, y)`` to the box's footprint (0 inside)."""
        ax, ay = self.a
        dx, dy = self.b[0] - ax, self.b[1] - ay
        length = math.hypot(dx, dy)
        if length < EPS:
            return max(math.dist(self.a, (x, y)) - self.thickness / 2, 0.0)
        ux, uy = dx / length, dy / length
        along = (x - ax) * ux + (y - ay) * uy
        across = abs(-(x - ax) * uy + (y - ay) * ux)
        out_along = max(-along, along - length, 0.0)
        out_across = max(across - self.thickness / 2, 0.0)
        return math.hypot(out_along, out_across)


@dataclass(frozen=True)
class Spot:
    """A marker or the spawn, in world units; ``height`` is the floor under it (3D)."""

    kind: str
    id: str
    at: XY
    size: float
    height: float = 0.0
    facing: str = "north"


@dataclass
class World:
    layout: Layout
    profile: Profile
    mode: Mode
    unit: float
    wall_height: float
    wall_thickness: float
    door_width: float
    door_height: float
    window_width: float
    #: Pixels per tile (2D).
    tile_px: float = 1.0
    areas: list[Area] = field(default_factory=list)
    platforms: list[Area] = field(default_factory=list)
    pieces: list[Piece] = field(default_factory=list)
    spawn: Spot | None = None
    markers: list[Spot] = field(default_factory=list)
    #: Problems found while resolving (unknown rooms or walls named by openings, …).
    problems: list[str] = field(default_factory=list)

    @property
    def three_d(self) -> bool:
        return self.mode == "3d"

    def floor_at(self, x: float, y: float) -> float | None:
        """The highest walking surface at ``(x, y)``, or None over the void."""
        best: float | None = None
        for area in [*self.areas, *self.platforms]:
            if area.kind == "platform" and self.mode != "3d":
                continue
            if area.contains(x, y):
                height = area.height_at(x, y)
                best = height if best is None else max(best, height)
        return best

    def area_at(self, x: float, y: float) -> Area | None:
        found = [a for a in [*self.platforms, *self.areas] if a.contains(x, y)]
        return found[0] if found else None

    def bounds(self) -> tuple[float, float, float, float]:
        points: list[XY] = []
        for area in [*self.areas, *self.platforms]:
            points.extend(area.polygon)
        for piece in self.pieces:
            points.extend((piece.a, piece.b))
        for spot in [*self.markers, *([self.spawn] if self.spawn else [])]:
            points.append(spot.at)
        if not points:
            return 0.0, 0.0, 1.0, 1.0
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        return min(xs), min(ys), max(xs), max(ys)


# --- Geometry helpers ----------------------------------------------------------------


def _axis_aligned(polygon: tuple[XY, ...]) -> bool:
    for i, p in enumerate(polygon):
        q = polygon[(i + 1) % len(polygon)]
        if abs(p[0] - q[0]) > EPS and abs(p[1] - q[1]) > EPS:
            return False
    return True


def _inside(polygon: tuple[XY, ...], x: float, y: float) -> bool:
    inside = False
    count = len(polygon)
    for i in range(count):
        (x1, y1), (x2, y2) = polygon[i], polygon[(i + 1) % count]
        if (y1 > y) != (y2 > y):
            cross = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < cross:
                inside = not inside
    return inside


def _segment_distance(a: XY, b: XY, x: float, y: float) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    length2 = dx * dx + dy * dy
    if length2 < EPS:
        return math.dist(a, (x, y))
    t = min(max(((x - a[0]) * dx + (y - a[1]) * dy) / length2, 0.0), 1.0)
    return math.dist((a[0] + t * dx, a[1] + t * dy), (x, y))


def _edge_distance(polygon: tuple[XY, ...], x: float, y: float) -> float:
    count = len(polygon)
    return min(
        _segment_distance(polygon[i], polygon[(i + 1) % count], x, y)
        for i in range(count)
    )


def polygon_area(polygon: tuple[XY, ...]) -> float:
    total = 0.0
    for i, (x1, y1) in enumerate(polygon):
        x2, y2 = polygon[(i + 1) % len(polygon)]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2


def simple(polygon: tuple[XY, ...]) -> bool:
    """No two non-neighbouring edges cross."""
    count = len(polygon)
    for i in range(count):
        a, b = polygon[i], polygon[(i + 1) % count]
        for j in range(i + 1, count):
            if j == i or (j + 1) % count == i or j == (i + 1) % count:
                continue
            c, d = polygon[j], polygon[(j + 1) % count]
            if _cross(a, b, c, d):
                return False
    return True


def _cross(a: XY, b: XY, c: XY, d: XY) -> bool:
    def orient(p: XY, q: XY, r: XY) -> float:
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    return (orient(a, b, c) * orient(a, b, d) < -EPS) and (
        orient(c, d, a) * orient(c, d, b) < -EPS
    )


def rect_polygon(x: float, y: float, w: float, h: float) -> tuple[XY, ...]:
    return ((x, y), (x + w, y), (x + w, y + h), (x, y + h))


def room_edges(area: Area) -> list[tuple[str, XY, XY]]:
    """A room's edges as (name, start, end): rect edges by compass name (north and
    south from their west end, east and west from their north end), polygon edges e0…"""
    if area.is_rect:
        x0, y0, x1, y1 = area.bounds
        return [
            ("north", (x0, y0), (x1, y0)),
            ("east", (x1, y0), (x1, y1)),
            ("south", (x0, y1), (x1, y1)),
            ("west", (x0, y0), (x0, y1)),
        ]
    count = len(area.polygon)
    return [
        (f"e{i}", area.polygon[i], area.polygon[(i + 1) % count]) for i in range(count)
    ]


# --- Resolving -----------------------------------------------------------------------


def resolve(layout: Layout, profile: Profile) -> World:
    """The layout in world units, with its wall pieces."""
    mode = layout.mode
    three_d = mode == "3d"
    if three_d or layout.units == "px":
        unit = 1.0
    else:
        unit = layout.tile if layout.tile else profile.tile
    unit *= layout.scale
    tile = 1.0 if three_d or layout.units == "px" else (layout.tile or profile.tile)
    world = World(
        layout=layout,
        profile=profile,
        mode=mode,
        unit=unit,
        wall_height=layout.wall_height if three_d else 0.0,
        wall_thickness=(
            layout.wall_thickness * layout.scale * tile
            if layout.wall_thickness
            else (0.2 if three_d else tile * 0.5)
        ),
        door_width=(
            layout.door_width * unit
            if layout.door_width
            else (1.0 if three_d else tile * 2)
        ),
        door_height=layout.door_height,
        window_width=(
            layout.window_width * unit
            if layout.window_width
            else (1.2 if three_d else tile * 2)
        ),
        tile_px=1.0 if three_d else (layout.tile or profile.tile),
    )

    def scaled(points: list[list[float]]) -> tuple[XY, ...]:
        return tuple((p[0] * unit, p[1] * unit) for p in points)

    def from_rect(rect: list[float]) -> tuple[XY, ...]:
        x, y, w, h = (v * unit for v in rect)
        if w < 0:
            x, w = x + w, -w
        if h < 0:
            y, h = y + h, -h
        return rect_polygon(x, y, w, h)

    floors: dict[str, float] = {}
    for room in layout.rooms:
        if (room.rect is None) == (room.polygon is None):
            world.problems.append(
                f"Room {room.id}: give it either a rect or a polygon, not both or neither."
            )
            continue
        polygon = (
            from_rect(room.rect)
            if room.rect is not None
            else scaled(room.polygon or [])
        )
        world.areas.append(
            Area("room", room.id, polygon, room.floor if three_d else 0.0)
        )
        floors[room.id] = room.floor
    for corridor in layout.corridors:
        world.areas.append(
            Area(
                "corridor",
                corridor.id,
                from_rect(corridor.rect),
                corridor.floor if three_d else 0.0,
            )
        )
    for stairs in layout.stairs:
        bottom = stairs.bottom
        top = stairs.top
        if bottom is None and stairs.from_room is not None:
            bottom = floors.get(stairs.from_room)
            if bottom is None:
                world.problems.append(
                    f"Stairs {stairs.id}: from_room {stairs.from_room} is not a room."
                )
        if top is None and stairs.to_room is not None:
            top = floors.get(stairs.to_room)
            if top is None:
                world.problems.append(
                    f"Stairs {stairs.id}: to_room {stairs.to_room} is not a room."
                )
        if three_d and (bottom is None or top is None):
            world.problems.append(
                f"Stairs {stairs.id}: give bottom and top heights, or from_room and to_room."
            )
        world.areas.append(
            Area(
                "stairs",
                stairs.id,
                from_rect(stairs.rect),
                up=stairs.up,
                bottom=(bottom or 0.0) if three_d else 0.0,
                top=(top or 0.0) if three_d else 0.0,
            )
        )
    for platform in layout.platforms:
        world.platforms.append(
            Area(
                "platform",
                platform.id,
                from_rect(platform.rect),
                top=platform.top if three_d else 0.0,
                thickness=(platform.thickness or platform.top) if three_d else 0.0,
            )
        )
    _walls(world)
    if layout.spawn is not None:
        x, y = layout.spawn.at[0] * unit, layout.spawn.at[1] * unit
        world.spawn = Spot(
            "spawn",
            "Spawn",
            (x, y),
            0.0,
            world.floor_at(x, y) or 0.0,
            layout.spawn.facing,
        )
    counts: dict[str, int] = {}
    default_size = 0.6 if three_d else unit * 0.75
    for marker in layout.markers:
        counts[marker.kind] = counts.get(marker.kind, 0) + 1
        x, y = marker.at[0] * unit, marker.at[1] * unit
        world.markers.append(
            Spot(
                marker.kind,
                marker.id or f"{marker.kind}_{counts[marker.kind]}",
                (x, y),
                marker.size * unit if marker.size else default_size,
                world.floor_at(x, y) or 0.0,
            )
        )
    return world


# --- Walls ---------------------------------------------------------------------------

_PRIORITY = {"solid": 0, "window": 1, "door": 2, "gap": 3}


@dataclass
class _Segment:
    owner: str
    prefix: str
    a: XY
    b: XY
    thickness: float
    base: float
    top: float
    start: float
    end: float
    cuts: list[tuple[float, float, str, float, float]] = field(default_factory=list)

    @property
    def length(self) -> float:
        return math.dist(self.a, self.b)

    def unit(self) -> XY:
        length = self.length
        return (
            (self.b[0] - self.a[0]) / length,
            (self.b[1] - self.a[1]) / length,
        )

    def param(self, x: float, y: float) -> tuple[float, float]:
        """(distance along from ``a``, distance off the line)."""
        ux, uy = self.unit()
        dx, dy = x - self.a[0], y - self.a[1]
        return dx * ux + dy * uy, -dx * uy + dy * ux

    def point(self, t: float) -> XY:
        ux, uy = self.unit()
        return (self.a[0] + ux * t, self.a[1] + uy * t)


def _collinear(one: _Segment, two: _Segment, tolerance: float) -> bool:
    ux, uy = one.unit()
    vx, vy = two.unit()
    if abs(ux * vy - uy * vx) > 1e-3:
        return False
    _, off_a = one.param(*two.a)
    return abs(off_a) <= tolerance


def _walls(world: World) -> None:
    layout = world.layout
    thickness = world.wall_thickness
    height = world.wall_height
    rooms = {a.id: a for a in world.areas if a.kind == "room"}
    passages = [a for a in world.areas if a.kind in {"corridor", "stairs"}]
    segments: list[_Segment] = []
    for room_layout in layout.rooms:
        room = rooms.get(room_layout.id)
        if room is None:
            continue
        for name, a, b in room_edges(room):
            if name in room_layout.open:
                continue
            length = math.dist(a, b)
            if length < EPS:
                continue
            segments.append(
                _Segment(
                    f"Rooms/{room.id}",
                    name,
                    a,
                    b,
                    thickness,
                    room.floor,
                    room.floor + height,
                    -thickness / 2,
                    length + thickness / 2,
                )
            )
    for corridor_layout in layout.corridors:
        corridor = next(a for a in world.areas if a.id == corridor_layout.id)
        if not corridor_layout.walls:
            continue
        x0, y0, x1, y1 = corridor.bounds
        sides = (
            [("north", (x0, y0), (x1, y0)), ("south", (x0, y1), (x1, y1))]
            if x1 - x0 >= y1 - y0
            else [("west", (x0, y0), (x0, y1)), ("east", (x1, y0), (x1, y1))]
        )
        for name, a, b in sides:
            segments.append(
                _Segment(
                    f"Corridors/{corridor.id}",
                    name,
                    a,
                    b,
                    thickness,
                    corridor.floor,
                    corridor.floor + height,
                    0.0,
                    math.dist(a, b),
                )
            )
    for wall in layout.walls:
        a = (wall.start[0] * world.unit, wall.start[1] * world.unit)
        b = (wall.end[0] * world.unit, wall.end[1] * world.unit)
        length = math.dist(a, b)
        if length < EPS:
            world.problems.append(
                f"Wall {wall.id}: its start and end are the same point."
            )
            continue
        wall_t = wall.thickness * world.unit if wall.thickness else thickness
        floor = wall.floor if world.three_d else 0.0
        segments.append(
            _Segment(
                "Walls",
                wall.id,
                a,
                b,
                wall_t,
                floor,
                floor + (wall.height if wall.height else height),
                0.0,
                length,
            )
        )
    _openings(world, segments, rooms)
    _crossings(world, segments, passages, rooms)
    _dedupe(segments)
    for segment in segments:
        world.pieces.extend(_pieces(world, segment))


def _openings(world: World, segments: list[_Segment], rooms: dict[str, Area]) -> None:
    for index, opening in enumerate(world.layout.openings):
        room = rooms.get(opening.room)
        label = f"Opening {index + 1} ({opening.kind} in {opening.room} {opening.wall})"
        if room is None:
            world.problems.append(f"{label}: there is no room {opening.room}.")
            continue
        edges = {name: (a, b) for name, a, b in room_edges(room)}
        if opening.wall not in edges:
            names = ", ".join(edges)
            world.problems.append(
                f"{label}: room {room.id} has no wall {opening.wall} (its walls: {names})."
            )
            continue
        a, b = edges[opening.wall]
        length = math.dist(a, b)
        width = (
            opening.width * world.unit
            if opening.width
            else (world.window_width if opening.kind == "window" else world.door_width)
        )
        at = opening.at * world.unit if opening.at is not None else length / 2
        if at - width / 2 < -EPS or at + width / 2 > length + EPS:
            world.problems.append(
                f"{label}: a {width / world.unit:g}-wide opening at {at / world.unit:g} "
                f"doesn't fit in a wall {length / world.unit:g} long."
            )
            continue
        ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
        centre = (a[0] + ux * at, a[1] + uy * at)
        low = opening.sill if opening.kind == "window" else 0.0
        high = (
            opening.height
            if opening.height is not None
            else (2.0 if opening.kind == "window" else world.door_height)
        )
        hit = False
        for segment in segments:
            if not segment.owner.startswith("Rooms/"):
                continue
            along, off = segment.param(*centre)
            su = segment.unit()
            if abs(su[0] * uy - su[1] * ux) > 1e-3 or abs(off) > segment.thickness:
                continue
            if along < segment.start - EPS or along > segment.end + EPS:
                continue
            kind = (
                opening.kind if world.three_d or opening.kind != "window" else "window"
            )
            segment.cuts.append((along - width / 2, along + width / 2, kind, low, high))
            hit = True
        if not hit and opening.wall not in _open_edges(world, opening.room):
            world.problems.append(f"{label}: that wall is not built.")


def _open_edges(world: World, room_id: str) -> list[str]:
    room = next((r for r in world.layout.rooms if r.id == room_id), None)
    return list(room.open) if room is not None else []


def _crossings(
    world: World,
    segments: list[_Segment],
    passages: list[Area],
    rooms: dict[str, Area],
) -> None:
    """Corridors and stairs cut the room walls they cross; corridor walls stop where
    they enter a room or another corridor."""
    for segment in segments:
        ux, uy = segment.unit()
        axis_x = abs(uy) < 1e-6
        axis_y = abs(ux) < 1e-6
        if not (axis_x or axis_y):
            continue
        line = segment.a[1] if axis_x else segment.a[0]
        owner_id = segment.owner.split("/", 1)[-1]
        for area in [*passages, *rooms.values()]:
            if area.id == owner_id or not area.is_rect:
                continue
            x0, y0, x1, y1 = area.bounds
            across_lo, across_hi = (y0, y1) if axis_x else (x0, x1)
            along_lo, along_hi = (x0, x1) if axis_x else (y0, y1)
            if segment.owner.startswith("Rooms/"):
                if area.kind == "room":
                    continue
                # A passage crosses this wall when its long axis is across the wall.
                long_across = (y1 - y0 >= x1 - x0) if axis_x else (x1 - x0 >= y1 - y0)
                if area.kind == "stairs":
                    long_across = (area.up in {"north", "south"}) == axis_x
                if not long_across or not (across_lo - EPS <= line <= across_hi + EPS):
                    continue
            elif not (across_lo + EPS < line < across_hi - EPS):
                continue
            start = segment.param(*((along_lo, line) if axis_x else (line, along_lo)))[
                0
            ]
            end = segment.param(*((along_hi, line) if axis_x else (line, along_hi)))[0]
            low, high = min(start, end), max(start, end)
            if high - low > EPS:
                segment.cuts.append((low, high, "gap", 0.0, 0.0))


def _dedupe(segments: list[_Segment]) -> None:
    """A wall two rooms share is built once: later segments give up the stretch an
    earlier collinear one covers (and its height grows to cover both)."""
    for index, segment in enumerate(segments):
        for earlier in segments[:index]:
            if not _collinear(
                earlier, segment, (earlier.thickness + segment.thickness) / 2
            ):
                continue
            first = segment.param(*earlier.point(earlier.start))[0]
            last = segment.param(*earlier.point(earlier.end))[0]
            low, high = min(first, last), max(first, last)
            low, high = max(low, segment.start), min(high, segment.end)
            if high - low <= EPS:
                continue
            segment.cuts.append((low, high, "gap", 0.0, 0.0))
            earlier.base = min(earlier.base, segment.base)
            earlier.top = max(earlier.top, segment.top)


def _pieces(world: World, segment: _Segment) -> list[Piece]:
    points = {segment.start, segment.end}
    for low, high, *_ in segment.cuts:
        points.add(min(max(low, segment.start), segment.end))
        points.add(min(max(high, segment.start), segment.end))
    stops = sorted(points)
    runs: list[tuple[float, float, str, float, float]] = []
    for low, high in itertools.pairwise(stops):
        if high - low < 1e-4:
            continue
        mid = (low + high) / 2
        state, sill, top = "solid", 0.0, 0.0
        for cut_low, cut_high, kind, cut_sill, cut_top in segment.cuts:
            if (
                cut_low - EPS <= mid <= cut_high + EPS
                and _PRIORITY[kind] > _PRIORITY[state]
            ):
                state, sill, top = kind, cut_sill, cut_top
        if runs and runs[-1][2] == state and abs(runs[-1][1] - low) < 1e-4:
            runs[-1] = (runs[-1][0], high, state, sill, top)
        else:
            runs.append((low, high, state, sill, top))
    pieces: list[Piece] = []
    counts: dict[str, int] = {}

    def add(low: float, high: float, kind: str, base: float, top: float) -> None:
        if high - low < 0.02 * world.unit or top - base < 0.02 and world.three_d:
            return
        label = segment.prefix if kind == "wall" else f"{segment.prefix}_{kind}"
        counts[label] = counts.get(label, 0) + 1
        piece_kind: Literal["wall", "window", "lintel"] = (
            "wall" if kind == "wall" else ("window" if kind == "sill" else "lintel")
        )
        pieces.append(
            Piece(
                segment.owner,
                f"{label}_{counts[label]}",
                segment.point(low),
                segment.point(high),
                segment.thickness,
                base,
                top,
                piece_kind,
            )
        )

    for low, high, state, sill, top in runs:
        if state == "solid":
            add(low, high, "wall", segment.base, segment.top)
        elif state == "door" and world.three_d:
            add(low, high, "lintel", segment.base + top, segment.top)
        elif state == "window":
            if world.three_d:
                add(low, high, "sill", segment.base, segment.base + sill)
                add(low, high, "lintel", segment.base + top, segment.top)
            else:
                add(low, high, "sill", segment.base, segment.top)
    return pieces


__all__ = [
    "RECT_EDGES",
    "Area",
    "Piece",
    "Profile",
    "Spot",
    "World",
    "profile_for",
    "resolve",
]
