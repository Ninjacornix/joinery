"""How closely a layout an agent read from a sketch matches the layout the sketch shows.

Used by the live smoke test and evals. Both layouts are resolved and fitted to the
same frame (their rooms' bounding boxes), so an agent that chose another origin,
scale or unit is not penalised for it. The score weighs rooms (overlap of each
expected room with its best match), markers (each expected marker's nearest one of
the same kind) and doors (how many).
"""

from __future__ import annotations

import math
from typing import Any

from joinery.blockout.layout import Layout
from joinery.blockout.world import GENERIC, World, resolve

Box = tuple[float, float, float, float]


def _frame(world: World) -> Box:
    boxes = [a.bounds for a in world.areas if a.kind == "room"] or [world.bounds()]
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def _fit(box: Box, frame: Box) -> Box:
    width = max(frame[2] - frame[0], 1e-9)
    height = max(frame[3] - frame[1], 1e-9)
    return (
        (box[0] - frame[0]) / width,
        (box[1] - frame[1]) / height,
        (box[2] - frame[0]) / width,
        (box[3] - frame[1]) / height,
    )


def _iou(a: Box, b: Box) -> float:
    inner = max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(
        0.0, min(a[3], b[3]) - max(a[1], b[1])
    )
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inner
    return inner / union if union > 0 else 0.0


def compare(expected: Layout, actual: Layout) -> dict[str, Any]:
    """{score 0..1, rooms "n of m", doors "n of m", markers "n of m"}."""
    want = resolve(expected, GENERIC[expected.mode])
    got = resolve(actual, GENERIC[actual.mode])
    frame_want, frame_got = _frame(want), _frame(got)
    rooms_want = [_fit(a.bounds, frame_want) for a in want.areas if a.kind == "room"]
    rooms_got = [_fit(a.bounds, frame_got) for a in got.areas if a.kind == "room"]
    overlaps = [max((_iou(r, g) for g in rooms_got), default=0.0) for r in rooms_want]
    matched = sum(1 for value in overlaps if value >= 0.5)
    room_score = sum(overlaps) / len(overlaps) if overlaps else 1.0

    def spot(world: World, frame: Box, at: tuple[float, float]) -> tuple[float, float]:
        box = _fit((at[0], at[1], at[0], at[1]), frame)
        return box[0], box[1]

    marker_scores: list[float] = []
    found = 0
    for marker in want.markers:
        here = spot(want, frame_want, marker.at)
        distances = [
            math.dist(here, spot(got, frame_got, other.at))
            for other in got.markers
            if other.kind == marker.kind
        ]
        nearest = min(distances, default=math.inf)
        marker_scores.append(max(0.0, 1.0 - nearest / 0.15))
        found += 1 if nearest <= 0.1 else 0
    marker_score = sum(marker_scores) / len(marker_scores) if marker_scores else 1.0

    def doors(layout: Layout) -> int:
        return sum(1 for o in layout.openings if o.kind != "window")

    door_want, door_got = doors(expected), doors(actual)
    door_score = 1.0 - abs(door_want - door_got) / max(door_want, door_got, 1)
    score = room_score * 0.6 + marker_score * 0.25 + door_score * 0.15
    return {
        "score": round(score, 3),
        "rooms": f"{matched} of {len(rooms_want)}",
        "doors": f"{door_got} of {door_want}",
        "markers": f"{found} of {len(want.markers)}",
    }
