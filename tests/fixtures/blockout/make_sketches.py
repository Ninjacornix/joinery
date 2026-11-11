"""Draw the sample level sketches from their layouts, the way a person sketches on squared
paper: pencil walls that wobble and overshoot, gaps for doors, double lines for windows,
treads for stairs, hatching for platforms, an X for the spawn, a star for the goal,
circles for coins, diamonds for pickups, a flag for a checkpoint and spikes for a hazard.

Made with code in this repository (joinery.blockout.canvas), seeded, so the same layout
always gives the same picture: no image generator is involved. Run it to rewrite the
PNGs after changing a layout:

    uv run python tests/fixtures/blockout/make_sketches.py
"""

from __future__ import annotations

import math
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))

import itertools

from joinery import blockout
from joinery.blockout.canvas import RGB, Canvas

NAMES = ("keep", "house", "cliffs", "dungeon")
PAPER: RGB = (247, 244, 236)
GRID: RGB = (221, 229, 238)
PENCIL: RGB = (52, 54, 64)
SOFT: RGB = (120, 122, 132)
LONG_SIDE = 900
PAD = 48


def _wobbly(
    canvas: Canvas,
    rng: random.Random,
    a: tuple[float, float],
    b: tuple[float, float],
    width: float = 3.0,
    colour: RGB = PENCIL,
) -> None:
    length = math.dist(a, b)
    if length < 1:
        return
    ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
    over = rng.uniform(1.0, 5.0)
    a = (a[0] - ux * over, a[1] - uy * over)
    b = (b[0] + ux * rng.uniform(1.0, 5.0), b[1] + uy * rng.uniform(1.0, 5.0))
    steps = max(2, int(length / 18))
    drift = 0.0
    points = []
    for k in range(steps + 1):
        drift = drift * 0.6 + rng.uniform(-1.1, 1.1)
        t = k / steps
        points.append(
            (
                a[0] + (b[0] - a[0]) * t - uy * drift,
                a[1] + (b[1] - a[1]) * t + ux * drift,
            )
        )
    for p, q in itertools.pairwise(points):
        canvas.line(p, q, width, colour)


def _loop(
    canvas: Canvas,
    rng: random.Random,
    points: list[tuple[float, float]],
    width: float = 2.5,
) -> None:
    for p, q in zip(points, [*points[1:], points[0]], strict=False):
        _wobbly(canvas, rng, p, q, width)


def sketch(name: str) -> bytes:
    """The sketch of tests/fixtures/blockout/<name>.toml, as PNG bytes."""
    built = blockout.make(HERE, (HERE / f"{name}.toml").read_text(encoding="utf-8"))
    world = built.checked.world
    rng = random.Random(name)
    x0, y0, x1, y1 = world.bounds()
    scale = (LONG_SIDE - PAD * 2) / max(x1 - x0, y1 - y0)
    canvas = Canvas(
        int((x1 - x0) * scale) + PAD * 2, int((y1 - y0) * scale) + PAD * 2, PAPER
    )

    def at(x: float, y: float) -> tuple[float, float]:
        return (PAD + (x - x0) * scale, PAD + (y - y0) * scale)

    square = world.unit * scale
    for k in range(int(canvas.width / square) + 2):
        canvas.line((k * square, 0), (k * square, canvas.height), 1.0, GRID)
    for k in range(int(canvas.height / square) + 2):
        canvas.line((0, k * square), (canvas.width, k * square), 1.0, GRID)
    for area in world.areas:
        if area.kind != "stairs":
            continue
        ax0, ay0, ax1, ay1 = area.bounds
        _loop(canvas, rng, [at(*p) for p in area.polygon], 2.0)
        along_y = area.up in {"north", "south"}
        run = (ay1 - ay0) if along_y else (ax1 - ax0)
        treads = max(3, int(run / (0.4 * world.unit)))
        for t in range(1, treads):
            part = t / treads
            if along_y:
                y = ay0 + (ay1 - ay0) * part
                _wobbly(canvas, rng, at(ax0, y), at(ax1, y), 1.5)
            else:
                x = ax0 + (ax1 - ax0) * part
                _wobbly(canvas, rng, at(x, ay0), at(x, ay1), 1.5)
    for platform in world.platforms:
        px0, py0, px1, py1 = (*at(*platform.bounds[:2]), *at(*platform.bounds[2:]))
        _loop(canvas, rng, [(px0, py0), (px1, py0), (px1, py1), (px0, py1)], 2.5)
        gap = 10.0
        offset = 0.0
        while offset < (px1 - px0) + (py1 - py0):
            start = (
                (px0 + offset, py0)
                if offset < px1 - px0
                else (px1, py0 + offset - (px1 - px0))
            )
            end_x = px0 + offset - (py1 - py0)
            end = (end_x, py1) if end_x >= px0 else (px0, py0 + offset)
            canvas.line(start, end, 1.0, SOFT)
            offset += gap
    for piece in world.pieces:
        if piece.kind == "lintel":
            continue
        a, b = at(*piece.a), at(*piece.b)
        if piece.kind == "window":
            length = math.dist(a, b) or 1.0
            nx, ny = -(b[1] - a[1]) / length * 4, (b[0] - a[0]) / length * 4
            _wobbly(canvas, rng, (a[0] + nx, a[1] + ny), (b[0] + nx, b[1] + ny), 1.8)
            _wobbly(canvas, rng, (a[0] - nx, a[1] - ny), (b[0] - nx, b[1] - ny), 1.8)
        else:
            _wobbly(canvas, rng, a, b, 3.0)
    mark = max(9.0, min(18.0, square * 0.45))
    for marker in world.markers:
        cx, cy = at(*marker.at)
        if marker.kind == "goal":
            star = []
            for k in range(10):
                radius = mark * (1.2 if k % 2 == 0 else 0.5)
                angle = -math.pi / 2 + math.pi * k / 5
                star.append(
                    (cx + radius * math.cos(angle), cy + radius * math.sin(angle))
                )
            _loop(canvas, rng, star, 2.2)
        elif marker.kind == "coin":
            _loop(
                canvas,
                rng,
                [
                    (
                        cx + mark * 0.6 * math.cos(math.tau * k / 10),
                        cy + mark * 0.6 * math.sin(math.tau * k / 10),
                    )
                    for k in range(10)
                ],
                2.0,
            )
        elif marker.kind == "pickup":
            _loop(
                canvas,
                rng,
                [
                    (cx, cy - mark),
                    (cx + mark * 0.7, cy),
                    (cx, cy + mark),
                    (cx - mark * 0.7, cy),
                ],
                2.2,
            )
        elif marker.kind == "checkpoint":
            _wobbly(canvas, rng, (cx, cy + mark), (cx, cy - mark), 2.2)
            _loop(
                canvas,
                rng,
                [(cx, cy - mark), (cx + mark, cy - mark * 0.6), (cx, cy - mark * 0.2)],
                2.0,
            )
        else:
            spikes = []
            for k in range(7):
                spikes.append(
                    (
                        cx - mark + k * mark / 3,
                        cy + (mark * 0.5 if k % 2 == 0 else -mark * 0.6),
                    )
                )
            for p, q in itertools.pairwise(spikes):
                _wobbly(canvas, rng, p, q, 2.2)
    if world.spawn is not None:
        cx, cy = at(*world.spawn.at)
        _wobbly(
            canvas,
            rng,
            (cx - mark * 0.8, cy - mark * 0.8),
            (cx + mark * 0.8, cy + mark * 0.8),
            3.2,
        )
        _wobbly(
            canvas,
            rng,
            (cx + mark * 0.8, cy - mark * 0.8),
            (cx - mark * 0.8, cy + mark * 0.8),
            3.2,
        )
    return canvas.png()


def main() -> None:
    for name in NAMES:
        (HERE / f"{name}.png").write_bytes(sketch(name))
        print(f"Drew {name}.png")


if __name__ == "__main__":
    main()
