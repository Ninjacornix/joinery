"""A top-down (or side-on) picture of a blockout, for the review beside the sketch.

Drawn from the resolved world, not rendered by an engine: floors, wall pieces, door
gaps, platforms, stairs, the spawn, every marker, and the routes the reachability
check walked from the spawn (a target it can't reach gets a red ring). It is a
diagram of the scene Joinery builds, made the same way every time.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Callable

from joinery.blockout.canvas import RGB, Canvas
from joinery.blockout.reach import Reach
from joinery.blockout.world import World

BACKGROUND: RGB = (31, 33, 37)
FLOOR: RGB = (96, 101, 110)
CORRIDOR: RGB = (84, 89, 97)
STAIRS: RGB = (112, 118, 128)
PLATFORM: RGB = (138, 145, 156)
WALL: RGB = (214, 217, 222)
WINDOW: RGB = (140, 176, 204)
ROUTE: RGB = (92, 168, 255)
MISSED: RGB = (232, 92, 72)
MARKERS: dict[str, RGB] = {
    "goal": (80, 190, 96),
    "coin": (242, 201, 76),
    "pickup": (86, 160, 211),
    "checkpoint": (226, 226, 226),
    "hazard": (224, 90, 72),
}
SPAWN: RGB = (92, 168, 255)


def render(world: World, reach: Reach | None = None, size: int = 640) -> bytes:
    """The plan as PNG bytes, at most ``size`` pixels on its long side."""
    x0, y0, x1, y1 = world.bounds()
    span = max(x1 - x0, y1 - y0, 1e-6)
    pad = 24
    scale = (size - pad * 2) / span
    width = math.ceil((x1 - x0) * scale) + pad * 2
    height = math.ceil((y1 - y0) * scale) + pad * 2
    canvas = Canvas(width, height, BACKGROUND)

    def at(x: float, y: float) -> tuple[float, float]:
        return (pad + (x - x0) * scale, pad + (y - y0) * scale)

    colours = {"room": FLOOR, "corridor": CORRIDOR, "stairs": STAIRS}
    for area in world.areas:
        canvas.polygon([at(*p) for p in area.polygon], colours[area.kind])
        if area.kind == "stairs":
            _treads(canvas, area.bounds, area.up, at, scale)
    for platform in world.platforms:
        canvas.polygon([at(*p) for p in platform.polygon], PLATFORM)
    for piece in world.pieces:
        thickness = max(piece.thickness * scale, 2.0)
        colour = WINDOW if piece.kind == "window" else WALL
        if piece.kind == "lintel":
            continue
        canvas.line(at(*piece.a), at(*piece.b), thickness, colour)
    if reach is not None:
        for target in reach.targets:
            for a, b in zip(target.route, target.route[1:], strict=False):
                canvas.line(at(a[0], a[1]), at(b[0], b[1]), 2.0, ROUTE)
    dot = max(5.0, min(12.0, scale * 0.35 if world.three_d else scale * 0.3))
    for marker in world.markers:
        cx, cy = at(*marker.at)
        colour = MARKERS[marker.kind]
        if marker.kind == "goal":
            canvas.circle(cx, cy, dot * 1.3, colour)
            canvas.circle(cx, cy, dot * 0.6, BACKGROUND)
            canvas.circle(cx, cy, dot * 0.35, colour)
        elif marker.kind == "hazard":
            canvas.polygon(
                [
                    (cx, cy - dot),
                    (cx + dot, cy + dot * 0.8),
                    (cx - dot, cy + dot * 0.8),
                ],
                colour,
            )
        elif marker.kind == "pickup":
            canvas.polygon(
                [(cx, cy - dot), (cx + dot, cy), (cx, cy + dot), (cx - dot, cy)], colour
            )
        elif marker.kind == "checkpoint":
            canvas.rect(
                cx - dot * 0.8, cy - dot * 0.8, cx + dot * 0.8, cy + dot * 0.8, colour
            )
        else:
            canvas.circle(cx, cy, dot * 0.8, colour)
    if reach is not None:
        for target in reach.targets:
            if not target.reached:
                cx, cy = at(*target.at)
                _ring(canvas, cx, cy, dot * 2.0, MISSED)
    if world.spawn is not None:
        cx, cy = at(*world.spawn.at)
        dx, dy = {
            "north": (0.0, -1.0),
            "south": (0.0, 1.0),
            "east": (1.0, 0.0),
            "west": (-1.0, 0.0),
        }[world.spawn.facing if world.three_d else "north"]
        size_ = dot * 1.4
        tip = (cx + dx * size_, cy + dy * size_)
        left = (
            cx - dx * size_ * 0.6 - dy * size_ * 0.8,
            cy - dy * size_ * 0.6 + dx * size_ * 0.8,
        )
        right = (
            cx - dx * size_ * 0.6 + dy * size_ * 0.8,
            cy - dy * size_ * 0.6 - dx * size_ * 0.8,
        )
        canvas.polygon([tip, left, right], SPAWN)
    return canvas.png()


def _ring(canvas: Canvas, cx: float, cy: float, radius: float, colour: RGB) -> None:
    sides = 32
    points = [
        (
            cx + radius * math.cos(math.tau * k / sides),
            cy + radius * math.sin(math.tau * k / sides),
        )
        for k in range(sides + 1)
    ]
    for a, b in itertools.pairwise(points):
        canvas.line(a, b, 2.5, colour)


def _treads(
    canvas: Canvas,
    bounds: tuple[float, float, float, float],
    up: str,
    at: Callable[[float, float], tuple[float, float]],
    scale: float,
) -> None:
    x0, y0, x1, y1 = bounds
    along_y = up in {"north", "south"}
    run = (y1 - y0) if along_y else (x1 - x0)
    steps = max(2, int(run / 0.3)) if scale > 0 else 2
    steps = min(steps, 24)
    for k in range(1, steps):
        part = k / steps
        if along_y:
            y = y0 + (y1 - y0) * part
            canvas.line(at(x0, y), at(x1, y), 1.0, BACKGROUND)
        else:
            x = x0 + (x1 - x0) * part
            canvas.line(at(x, y0), at(x, y1), 1.0, BACKGROUND)
