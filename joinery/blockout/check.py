"""Checking a layout before anything is built: shapes, sizes and the walk to every goal.

Every problem is written for the agent that wrote the layout: what is wrong, where,
and what to change, so it can fix the layout and call the tool again. Errors stop the
build; warnings go along with it.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from joinery.blockout import reach as reach_check
from joinery.blockout.layout import TARGETS, Layout
from joinery.blockout.world import Area, Profile, World, polygon_area, resolve, simple

#: The steepest stairs or ramp a player walks up.
MAX_SLOPE_DEGREES = 40.0
MAX_EXTENT_3D = 1000.0
MAX_EXTENT_2D = 100_000.0
MAX_PIECES = 800

Say = Callable[[str], None]


@dataclass(frozen=True)
class Problem:
    severity: Literal["error", "warning"]
    text: str

    def as_dict(self) -> dict[str, str]:
        return {"severity": self.severity, "text": self.text}


@dataclass
class Checked:
    world: World
    reach: reach_check.Reach | None
    problems: list[Problem] = field(default_factory=list)

    @property
    def errors(self) -> list[Problem]:
        return [p for p in self.problems if p.severity == "error"]

    @property
    def ok(self) -> bool:
        return not self.errors


def check(layout: Layout, profile: Profile, *, expected_mode: str = "") -> Checked:
    """Resolve and check ``layout`` for ``profile``'s player; walks only a sound layout."""
    world = resolve(layout, profile)
    found = Checked(world, None)
    errors = found.problems

    def error(text: str) -> None:
        errors.append(Problem("error", text))

    def warn(text: str) -> None:
        errors.append(Problem("warning", text))

    if expected_mode and layout.mode != expected_mode:
        hint = {
            "3d": 'mode = "3d" with units in metres',
            "2d-side": 'mode = "2d-side" (a side view) with units in tiles',
            "2d-topdown": 'mode = "2d-topdown" with units in tiles',
        }[expected_mode]
        error(
            f"This game ({profile.blueprint or 'the project'}) plays as {expected_mode}; "
            f"write the layout with {hint}."
        )
    if layout.mode == "3d" and layout.units != "m":
        error('A 3D layout is in metres: set units = "m" (and scale for sketch units).')
    if layout.scale <= 0:
        error("scale must be above 0.")
    for text in world.problems:
        error(text)
    _names(layout, error)
    _shapes(world, error, warn)
    _sizes(world, error)
    if layout.spawn is None:
        error('Add a spawn: [spawn] at = [x, y] (and facing = "north" or another way).')
    if not any(m.kind in TARGETS for m in layout.markers):
        warn(
            "There is no goal, pickup, coin or checkpoint, so nothing shows the level "
            "can be walked; add at least a goal marker."
        )
    if found.errors:
        return found
    walked = reach_check.check(world)
    found.reach = walked
    if not walked.spawn_ok:
        error(walked.spawn_reason)
    for target in walked.targets:
        if not target.reached and target.reason:
            error(target.reason)
    return found


def _names(layout: Layout, error: Say) -> None:
    seen: dict[str, str] = {}
    for table in ("rooms", "corridors", "stairs", "platforms", "walls"):
        for item in getattr(layout, table):
            if item.id in seen:
                error(
                    f"Two pieces are both called {item.id} ({seen[item.id]} and {table}); give each its own id."
                )
            seen.setdefault(item.id, table)
    markers: set[str] = set()
    counts: dict[str, int] = {}
    for marker in layout.markers:
        counts[marker.kind] = counts.get(marker.kind, 0) + 1
        ident = marker.id or f"{marker.kind}_{counts[marker.kind]}"
        if ident in markers or ident == "Spawn":
            error(f"Two markers are both called {ident}; give each its own id.")
        markers.add(ident)


def _shapes(world: World, error: Say, warn: Say) -> None:
    profile = world.profile
    unit = world.unit
    body = profile.radius * 2
    clearance = body + (0.1 if world.three_d else 2.0)
    smallest = max(clearance, 1.5 if world.three_d else body * 1.5)
    for area in world.areas:
        x0, y0, x1, y1 = area.bounds
        label = f"{area.kind.capitalize()} {area.id}"
        if area.kind == "room" and not area.is_rect:
            if not simple(area.polygon):
                error(
                    f"{label}: its polygon crosses itself; list the corners in order around it."
                )
            elif polygon_area(area.polygon) < smallest * smallest:
                error(f"{label} is too small to walk in.")
            continue
        narrow = min(x1 - x0, y1 - y0)
        if area.kind == "corridor" and narrow < clearance:
            error(
                f"{label} is {narrow / unit:g} wide; the player needs at least "
                f"{clearance / unit:g}. Widen it."
            )
        elif area.kind == "room" and narrow < smallest:
            error(
                f"{label} is {narrow / unit:g} across; make it at least {smallest / unit:g}."
            )
        if area.kind == "stairs" and world.three_d:
            _stairs(area, error)
    for index, opening in enumerate(world.layout.openings):
        label = f"Opening {index + 1} ({opening.kind} in {opening.room} {opening.wall})"
        width = (
            opening.width * unit
            if opening.width
            else (world.window_width if opening.kind == "window" else world.door_width)
        )
        if opening.kind != "window" and width < clearance:
            error(
                f"{label} is {width / unit:g} wide; the player ({body / unit:g} across) "
                f"needs at least {clearance / unit:g}."
            )
        if world.three_d and opening.kind == "door":
            height = opening.height if opening.height is not None else world.door_height
            if height < profile.height + 0.1:
                error(
                    f"{label} is {height:g} m high; the player is {profile.height:g} m tall. "
                    f"Make it at least {profile.height + 0.1:g} m."
                )
        if world.three_d and opening.kind == "window":
            top = opening.height if opening.height is not None else 2.0
            if not 0 <= opening.sill < top <= world.wall_height:
                error(
                    f"{label}: its sill ({opening.sill:g} m) must be below its top "
                    f"({top:g} m), which must be at most the wall height ({world.wall_height:g} m)."
                )
    _overlaps(world, error)
    for marker in world.layout.markers:
        if marker.size is not None and marker.size <= 0:
            error(f"Marker {marker.id or marker.kind}: size must be above 0.")


def _stairs(area: Area, error: Say) -> None:
    x0, y0, x1, y1 = area.bounds
    run = (y1 - y0) if area.up in {"north", "south"} else (x1 - x0)
    rise = abs(area.top - area.bottom)
    if rise < 1e-6:
        return
    angle = math.degrees(math.atan2(rise, run))
    if angle > MAX_SLOPE_DEGREES:
        needed = rise / math.tan(math.radians(MAX_SLOPE_DEGREES))
        error(
            f"Stairs {area.id} rise {rise:g} m over {run:g} m ({angle:.0f} degrees); "
            f"players walk up {MAX_SLOPE_DEGREES:g} at most. Make them at least "
            f"{needed:.1f} m long toward {area.up}."
        )


def _overlaps(world: World, error: Say) -> None:
    rooms = [a for a in world.areas if a.kind == "room"]
    for index, one in enumerate(rooms):
        for two in rooms[index + 1 :]:
            shared = _overlap(one, two)
            if shared > 0.05 * world.unit * world.unit:
                error(
                    f"Rooms {one.id} and {two.id} overlap; rooms that touch should share "
                    "a wall line instead (one room's edge on the other's)."
                )


def _overlap(one: Area, two: Area) -> float:
    a0, b0, a1, b1 = one.bounds
    c0, d0, c1, d1 = two.bounds
    width = min(a1, c1) - max(a0, c0)
    height = min(b1, d1) - max(b0, d0)
    if width <= 0 or height <= 0:
        return 0.0
    if one.is_rect and two.is_rect:
        return width * height
    hits = 0
    samples = 12
    for i in range(samples):
        for j in range(samples):
            x = max(a0, c0) + width * (i + 0.5) / samples
            y = max(b0, d0) + height * (j + 0.5) / samples
            if one.contains(x, y, 1e-6) and two.contains(x, y, 1e-6):
                hits += 1
    return width * height * hits / (samples * samples)


def _sizes(world: World, error: Say) -> None:
    layout = world.layout
    x0, y0, x1, y1 = world.bounds()
    limit = MAX_EXTENT_3D if world.three_d else MAX_EXTENT_2D
    if max(x1 - x0, y1 - y0) > limit:
        error(
            f"The level is {max(x1 - x0, y1 - y0) / world.unit:g} across; keep it under "
            f"{limit / world.unit:g}. Check scale and units."
        )
    if world.three_d:
        if not 1.0 <= layout.wall_height <= 30.0:
            error("wall_height is in metres, between 1 and 30.")
        if not 0.02 <= world.wall_thickness <= 2.0:
            error("wall_thickness is in metres, between 0.02 and 2.")
        for platform in world.platforms:
            if not -50 <= platform.top <= 100:
                error(
                    f"Platform {platform.id}: top is a height in metres, between -50 and 100."
                )
    if len(world.pieces) > MAX_PIECES:
        error(
            f"The layout makes {len(world.pieces)} wall pieces; keep it under {MAX_PIECES}."
        )
