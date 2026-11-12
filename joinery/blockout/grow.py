"""Grow a new level from the user's own example (ADR 0069).

The sample is something the user made: a small picture they painted (one pixel or one
block of pixels per cell), a grid drawn in text, or one of their own blockout
layouts. Never a downloaded image: only a local file is read, and nothing here
fetches anything. Joinery reads the sample into cells (wall or floor, plus the
spawn, goals and other markers), grows a new grid of the asked size with Wave
Function Collapse (:mod:`.wfc`), keeps the largest open region, places the spawn and
the goals far apart and the other markers the sample had, and writes an ordinary
blockout layout (``design/blockouts/<name>.toml``) that the usual check, build and
walk use (ADR 0043). The layout's ``[grown]`` table records the sample's path and
hash, the size and the seed, so growing again gives the same bytes.

A grown level must pass the blockout check, with the spawn reaching every goal; a
try that doesn't is dropped and the next one at the same seed is grown, up to
``TRIES``.

Painted samples, by colour: dark or transparent is wall, light grey or white is
floor; blue is the spawn, green a goal, yellow or orange a coin, cyan a pickup,
magenta a checkpoint, red a hazard. Text samples (TOML with ``grid = '''...'''``):
``#`` wall, ``.`` or space floor, ``S`` spawn, ``G`` goal, ``c`` coin, ``p`` pickup,
``k`` checkpoint, ``!`` hazard.
"""

from __future__ import annotations

import colorsys
import hashlib
import itertools
import math
import random
import tomllib
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from joinery.blockout import wfc
from joinery.blockout.canvas import RGB, Canvas
from joinery.blockout.check import Checked, check
from joinery.blockout.layout import (
    Grown,
    Layout,
    LayoutError,
    Marker,
    Mode,
    Platform,
    Room,
    Spawn,
    parse,
    slug,
    to_toml,
)
from joinery.blockout.plan import BACKGROUND, FLOOR, MARKERS, PLATFORM, SPAWN
from joinery.blockout.png import PngError
from joinery.blockout.png import read as read_png
from joinery.blockout.world import Profile, profile_for, resolve

if TYPE_CHECKING:
    from joinery.blockout import Built

WALL_TILE = 0
FLOOR_TILE = 1
#: Tries at one seed before giving up.
TRIES = 12
MIN_SIZE = 6
MAX_SIZE = 64
#: Plan units per cell when none is given: metres in 3D, tiles in 2D.
CELL = {"3d": 2.0, "2d-topdown": 2.0, "2d-side": 1.0}
#: Marker kinds a sample may carry besides the spawn.
KINDS = ("goal", "coin", "pickup", "checkpoint", "hazard")
TEXT_LEGEND = {
    "#": None,
    ".": None,
    " ": None,
    "S": "spawn",
    "G": "goal",
    "c": "coin",
    "p": "pickup",
    "k": "checkpoint",
    "!": "hazard",
}
MAX_MARKERS = 40
#: Blocks of wall a layout may hold (Layout.platforms).
MAX_BLOCKS = 128


class GrowError(ValueError):
    """The sample or the request can't grow a level; the text says what to change."""


@dataclass
class Sample:
    """The user's example read into cells."""

    mode: Mode | None
    #: rows of WALL_TILE / FLOOR_TILE
    tiles: list[list[int]]
    #: (kind, x, y) in cells, "spawn" included
    markers: list[tuple[str, int, int]] = field(default_factory=list)
    #: Plan units per cell the sample was read at (None: the mode's default).
    cell: float | None = None

    @property
    def width(self) -> int:
        return len(self.tiles[0]) if self.tiles else 0

    @property
    def height(self) -> int:
        return len(self.tiles)

    def count(self, kind: str) -> int:
        return sum(1 for k, _, _ in self.markers if k == kind)


@dataclass
class GrownLevel:
    layout: Layout
    checked: Checked
    sample: Sample
    tiles: list[list[int]]
    tries: int
    #: Why earlier tries were dropped.
    dropped: list[str] = field(default_factory=list)


# --- reading the sample -----------------------------------------------------------


def _colour_kind(r: int, g: int, b: int, a: int) -> str | None:
    """'wall', 'floor' or a marker kind for one painted pixel."""
    if a < 128:
        return "wall"
    hue, light, saturation = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    if saturation < 0.35 or light < 0.12 or light > 0.92:
        return "wall" if light < 0.5 else "floor"
    degrees = hue * 360
    if degrees < 20 or degrees >= 330:
        return "hazard"
    if degrees < 70:
        return "coin"
    if degrees < 170:
        return "goal"
    if degrees < 200:
        return "pickup"
    if degrees < 260:
        return "spawn"
    return "checkpoint"


def _block(pixels: list[list[tuple[int, int, int, int]]]) -> int:
    """The side of the pixel blocks the picture was painted in (1 for pixel art)."""
    runs: list[int] = []
    for row in pixels:
        run = 1
        for a, b in itertools.pairwise(row):
            if a == b:
                run += 1
            else:
                runs.append(run)
                run = 1
        runs.append(run)
    for x in range(len(pixels[0])):
        run = 1
        for y in range(1, len(pixels)):
            if pixels[y][x] == pixels[y - 1][x]:
                run += 1
            else:
                runs.append(run)
                run = 1
        runs.append(run)
    return max(1, math.gcd(*runs))


def _cells(kinds: list[list[str]]) -> Sample:
    """A sample from a grid of kinds; touching marker cells of one kind are one marker."""
    height, width = len(kinds), len(kinds[0])
    tiles = [
        [WALL_TILE if kinds[y][x] == "wall" else FLOOR_TILE for x in range(width)]
        for y in range(height)
    ]
    markers: list[tuple[str, int, int]] = []
    seen: set[tuple[int, int]] = set()
    for y in range(height):
        for x in range(width):
            kind = kinds[y][x]
            if kind in {"wall", "floor"} or (x, y) in seen:
                continue
            group = [(x, y)]
            seen.add((x, y))
            queue = deque(group)
            while queue:
                cx, cy = queue.popleft()
                for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                    if (
                        0 <= nx < width
                        and 0 <= ny < height
                        and (nx, ny) not in seen
                        and kinds[ny][nx] == kind
                    ):
                        seen.add((nx, ny))
                        group.append((nx, ny))
                        queue.append((nx, ny))
            group.sort(key=lambda c: (c[1], c[0]))
            mx, my = group[len(group) // 2]
            markers.append((kind, mx, my))
    return Sample(None, tiles, markers)


def _from_png(data: bytes) -> Sample:
    try:
        width, height, pixels = read_png(data)
    except PngError as exc:
        raise GrowError(str(exc)) from None
    block = _block(pixels)
    kinds = [
        [_colour_kind(*pixels[y][x]) or "floor" for x in range(0, width, block)]
        for y in range(0, height, block)
    ]
    return _cells(kinds)


def _from_text(grid: str) -> list[list[str]]:
    lines = [line.rstrip() for line in grid.strip("\n").split("\n")]
    width = max((len(line) for line in lines), default=0)
    kinds: list[list[str]] = []
    for y, line in enumerate(lines):
        row: list[str] = []
        for x, char in enumerate(line.ljust(width)):
            if char not in TEXT_LEGEND:
                raise GrowError(
                    f"grid: {char!r} at column {x + 1}, row {y + 1} is not in the "
                    "legend (# wall, . floor, S spawn, G goal, c coin, p pickup, "
                    "k checkpoint, ! hazard)."
                )
            if char == "#":
                row.append("wall")
            elif char in ". ":
                row.append("floor")
            else:
                row.append(str(TEXT_LEGEND[char]))
        kinds.append(row)
    return kinds


def _from_layout(layout: Layout, profile: Profile) -> Sample:
    """A blockout of the user's, read in cells of one plan unit (a metre or a tile):
    floor where an area is, wall where a wall piece or a solid block is."""
    world = resolve(layout, profile)
    cell = 1.0 if world.three_d else (layout.tile or profile.tile)
    x0, y0, x1, y1 = world.bounds()
    width = max(1, math.ceil((x1 - x0) / cell))
    height = max(1, math.ceil((y1 - y0) / cell))
    if width * height > 200 * 200:
        raise GrowError("The sample layout is too large to read; use a smaller one.")
    solid = [p for p in world.pieces if p.kind != "lintel"]
    kinds: list[list[str]] = []
    for j in range(height):
        row = []
        for i in range(width):
            x, y = x0 + (i + 0.5) * cell, y0 + (j + 0.5) * cell
            open_ = world.floor_at(x, y) is not None or (
                not world.three_d and world.area_at(x, y) is not None
            )
            if (
                open_
                and any(platform.contains(x, y) for platform in world.platforms)
                and not world.three_d
            ):
                open_ = False
            if open_ and any(piece.distance(x, y) < cell * 0.45 for piece in solid):
                open_ = False
            row.append("floor" if open_ else "wall")
        kinds.append(row)

    def cell_of(at: tuple[float, float]) -> tuple[int, int]:
        i = min(width - 1, max(0, int((at[0] - x0) / cell)))
        j = min(height - 1, max(0, int((at[1] - y0) / cell)))
        return i, j

    for spot in [*world.markers, *([world.spawn] if world.spawn else [])]:
        i, j = cell_of(spot.at)
        kinds[j][i] = spot.kind if spot.kind in KINDS else "spawn"
    sample = _cells(kinds)
    sample.mode = layout.mode
    sample.cell = 1.0
    return sample


def read_sample(path: Path, profile_for_mode: Any) -> tuple[Sample, bytes]:
    """The sample at ``path`` (PNG or TOML) and its bytes."""
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise GrowError(f"Can't read the sample {path}: {exc}") from None
    if data.startswith(b"\x89PNG"):
        return _from_png(data), data
    try:
        table = tomllib.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise GrowError(
            f"The sample {path.name} is neither a PNG nor TOML: {exc}"
        ) from None
    if "grid" in table:
        grid = table.get("grid")
        if not isinstance(grid, str):
            raise GrowError("grid must be text: the level drawn in rows.")
        sample = _cells(_from_text(grid))
        mode = table.get("mode")
        if mode is not None and mode not in CELL:
            raise GrowError("mode is 3d, 2d-topdown or 2d-side.")
        sample.mode = mode
        return sample, data
    try:
        layout = parse(table)
    except LayoutError as exc:
        raise GrowError(
            "The TOML sample is neither a grid (grid = '''...''') nor a blockout "
            "layout: " + "; ".join(exc.problems[:3])
        ) from None
    return _from_layout(layout, profile_for_mode(layout.mode)), data


# --- growing ------------------------------------------------------------------------


def _edges(tiles: list[list[int]]) -> list[int | None]:
    """The tile each whole side of the sample is (top, right, bottom, left)."""
    sides = [
        tiles[0],
        [row[-1] for row in tiles],
        tiles[-1],
        [row[0] for row in tiles],
    ]
    return [line[0] if len(set(line)) == 1 else None for line in sides]


def _regions(tiles: list[list[int]]) -> list[set[tuple[int, int]]]:
    height, width = len(tiles), len(tiles[0])
    seen: set[tuple[int, int]] = set()
    found: list[set[tuple[int, int]]] = []
    for y in range(height):
        for x in range(width):
            if tiles[y][x] != FLOOR_TILE or (x, y) in seen:
                continue
            region = {(x, y)}
            seen.add((x, y))
            queue = deque(region)
            while queue:
                cx, cy = queue.popleft()
                for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                    if (
                        0 <= nx < width
                        and 0 <= ny < height
                        and tiles[ny][nx] == FLOOR_TILE
                        and (nx, ny) not in seen
                    ):
                        seen.add((nx, ny))
                        region.add((nx, ny))
                        queue.append((nx, ny))
            found.append(region)
    return found


def _stands(tiles: list[list[int]], x: int, y: int, side: bool) -> bool:
    """A cell a marker can sit on: open, and in a side view with ground under it
    and room for the player's head."""
    if tiles[y][x] != FLOOR_TILE:
        return False
    if not side:
        return True
    below = y + 1 >= len(tiles) or tiles[y + 1][x] == WALL_TILE
    head = y > 0 and tiles[y - 1][x] == FLOOR_TILE
    # Clear of walls on both sides, so the body fits beside the wall pieces.
    beside = 0 < x < len(tiles[0]) - 1 and (
        tiles[y][x - 1] == FLOOR_TILE and tiles[y][x + 1] == FLOOR_TILE
    )
    return below and head and beside


def _distances(
    region: set[tuple[int, int]], start: tuple[int, int]
) -> dict[tuple[int, int], int]:
    out = {start: 0}
    queue = deque([start])
    while queue:
        cx, cy = queue.popleft()
        for nxt in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
            if nxt in region and nxt not in out:
                out[nxt] = out[(cx, cy)] + 1
                queue.append(nxt)
    return out


def _hops(
    spots: list[tuple[int, int]], start: tuple[int, int], rise: int, across: int
) -> dict[tuple[int, int], int]:
    """Side view: hops from ``start`` between standing places, each at most ``rise``
    cells up and ``across`` cells over (any drop allowed). A rough guide for where
    to put things; the blockout check walks the real thing."""
    out = {start: 0}
    queue = deque([start])
    while queue:
        here = queue.popleft()
        for there in spots:
            if there in out:
                continue
            if abs(there[0] - here[0]) <= across and here[1] - there[1] <= rise:
                out[there] = out[here] + 1
                queue.append(there)
    return out


def _place(
    sample: Sample,
    tiles: list[list[int]],
    region: set[tuple[int, int]],
    side: bool,
    rng: random.Random,
    jump: tuple[int, int] = (3, 4),
) -> list[tuple[str, int, int]] | None:
    """Spawn, goals and the other markers on cells of ``region``; None if it can't."""
    height, width = len(tiles), len(tiles[0])
    spots = sorted(
        (c for c in region if _stands(tiles, c[0], c[1], side)),
        key=lambda c: (c[1], c[0]),
    )
    if len(spots) < 2:
        return None
    spawns = [(x, y) for k, x, y in sample.markers if k == "spawn"]
    if spawns:
        rx = spawns[0][0] / max(sample.width - 1, 1)
        ry = spawns[0][1] / max(sample.height - 1, 1)
    else:
        rx, ry = (0.0, 1.0) if side else (0.0, 0.0)
    want = (rx * (width - 1), ry * (height - 1))
    spawn = min(spots, key=lambda c: ((c[0] - want[0]) ** 2 + (c[1] - want[1]) ** 2, c))
    if side:
        far = _hops(spots, spawn, *jump)
        spots = [c for c in spots if c in far]
    else:
        far = _distances(region, spawn)
    placed: list[tuple[str, int, int]] = [("spawn", *spawn)]
    taken = {spawn}

    def free(cell: tuple[int, int], gap: int) -> bool:
        return all(abs(cell[0] - t[0]) + abs(cell[1] - t[1]) > gap for t in taken)

    for _ in range(max(1, sample.count("goal"))):
        options = [c for c in spots if free(c, 3)]
        if not options:
            return None
        goal = max(
            options,
            key=lambda c: (
                min(abs(c[0] - t[0]) + abs(c[1] - t[1]) for t in taken) + far.get(c, 0),
                c,
            ),
        )
        placed.append(("goal", *goal))
        taken.add(goal)
    scale = (width * height) / max(sample.width * sample.height, 1)
    for kind in ("coin", "pickup", "checkpoint", "hazard"):
        count = round(sample.count(kind) * scale)
        if sample.count(kind) and not count:
            count = 1
        for _ in range(count):
            if len(placed) >= MAX_MARKERS:
                break
            options = [c for c in spots if free(c, 1)]
            if not options:
                break
            cell = options[int(rng.random() * len(options))]
            placed.append((kind, *cell))
            taken.add(cell)
    return placed


def _blocks(
    tiles: list[list[int]], x0: int, y0: int, x1: int, y1: int
) -> list[tuple[int, int, int, int]]:
    """Wall cells in [x0, x1) x [y0, y1) merged into rectangles (x, y, w, h)."""
    used = [[False] * len(tiles[0]) for _ in tiles]
    out: list[tuple[int, int, int, int]] = []
    for y in range(y0, y1):
        for x in range(x0, x1):
            if tiles[y][x] != WALL_TILE or used[y][x]:
                continue
            w = 1
            while x + w < x1 and tiles[y][x + w] == WALL_TILE and not used[y][x + w]:
                w += 1
            h = 1
            while y + h < y1 and all(
                tiles[y + h][i] == WALL_TILE and not used[y + h][i]
                for i in range(x, x + w)
            ):
                h += 1
            for j in range(y, y + h):
                for i in range(x, x + w):
                    used[j][i] = True
            out.append((x, y, w, h))
    return out


def to_layout(
    tiles: list[list[int]],
    markers: list[tuple[str, int, int]],
    *,
    name: str,
    mode: Mode,
    cell: float,
    wall_height: float = 3.0,
) -> Layout:
    """A blockout layout from a grown grid: one room over the open part, its wall
    cells as solid blocks (tall platforms in 3D, obstacles or ground in 2D)."""
    height, width = len(tiles), len(tiles[0])
    ring = (
        all(t == WALL_TILE for t in tiles[0])
        and all(t == WALL_TILE for t in tiles[-1])
        and all(row[0] == WALL_TILE and row[-1] == WALL_TILE for row in tiles)
    )
    x0, y0, x1, y1 = (1, 1, width - 1, height - 1) if ring else (0, 0, width, height)
    blocks = _blocks(tiles, x0, y0, x1, y1)
    if len(blocks) > MAX_BLOCKS:
        raise GrowError(
            f"The grown level has {len(blocks)} separate wall blocks; a layout holds "
            f"{MAX_BLOCKS}. Grow a smaller level or use a sample with larger shapes."
        )
    three_d = mode == "3d"

    def at(x: float, y: float) -> list[float]:
        return [round(x * cell, 4), round(y * cell, 4)]

    platforms = [
        Platform(
            id=f"block_{i + 1}",
            rect=[*at(x, y), round(w * cell, 4), round(h * cell, 4)],
            **({"top": wall_height} if three_d else {}),
        )
        for i, (x, y, w, h) in enumerate(blocks)
    ]
    counters: dict[str, int] = {}
    placed: list[Marker] = []
    spawn: Spawn | None = None
    for kind, x, y in markers:
        point = at(x + 0.5, y + 0.5)
        if kind == "spawn":
            if mode == "2d-side":
                # A side-view spawn drops onto the ground below it: start it clear of
                # the ground, in the cell above the one it stands in.
                point = at(x + 0.5, y)
            spawn = Spawn(at=point, facing="north" if not three_d else "south")
            continue
        counters[kind] = counters.get(kind, 0) + 1
        placed.append(
            Marker(kind=kind, at=point, id=f"{kind}_{counters[kind]}")  # type: ignore[arg-type]
        )
    return Layout(
        name=name,
        mode=mode,
        units="m" if three_d else "tiles",
        wall_height=wall_height,
        rooms=[
            Room(
                id="grown",
                rect=[
                    *at(x0, y0),
                    round((x1 - x0) * cell, 4),
                    round((y1 - y0) * cell, 4),
                ],
            )
        ],
        platforms=platforms,
        spawn=spawn,
        markers=placed,
    )


def grow(
    sample: Sample,
    *,
    width: int,
    height: int,
    seed: int,
    mode: Mode,
    profile: Profile,
    name: str,
    recipe: dict[str, Any],
    pattern: int = 3,
    cell: float | None = None,
    tries: int = TRIES,
    expected_mode: str = "",
) -> GrownLevel:
    """Grow a level from ``sample``; see the module docstring. Raises GrowError."""
    if not (MIN_SIZE <= width <= MAX_SIZE and MIN_SIZE <= height <= MAX_SIZE):
        raise GrowError(
            f"size: {width}x{height}; each side is {MIN_SIZE} to {MAX_SIZE} cells."
        )
    if not 0 <= seed <= 2**31 - 1:
        raise GrowError("seed: a whole number from 0 to 2147483647.")
    if not any(FLOOR_TILE in row for row in sample.tiles) or not any(
        WALL_TILE in row for row in sample.tiles
    ):
        raise GrowError("The sample needs both wall and floor to grow from.")
    side = mode == "2d-side"
    try:
        edges = _edges(sample.tiles)
        model = wfc.learn(
            sample.tiles, pattern, 2 if side else 8, [e is not None for e in edges]
        )
    except ValueError as exc:
        raise GrowError(str(exc)) from None
    unit = cell if cell is not None else (sample.cell or CELL[mode])
    dropped: list[str] = []
    for attempt in range(1, tries + 1):
        rng = random.Random(seed * 1009 + attempt)
        try:
            tiles = wfc.run(model, width, height, rng, edges)
        except wfc.Contradiction:
            dropped.append(f"try {attempt}: the patterns ran into a contradiction")
            continue
        regions = _regions(tiles)
        if not regions:
            dropped.append(f"try {attempt}: no open floor")
            continue
        main = max(regions, key=lambda r: (len(r), min(r)))
        total = sum(len(r) for r in regions)
        if len(main) < max(8, total // 4):
            dropped.append(f"try {attempt}: the open floor is in small pieces")
            continue
        tiles = [
            [FLOOR_TILE if (x, y) in main else WALL_TILE for x in range(width)]
            for y in range(height)
        ]
        cell_px = unit * (1.0 if mode == "3d" else profile.tile)
        jump = (
            max(1, int(profile.jump / cell_px)),
            max(1, int(profile.across / cell_px) - 1),
        )
        markers = _place(sample, tiles, main, side, rng, jump)
        if markers is None:
            dropped.append(f"try {attempt}: no room for the spawn and goals")
            continue
        layout = to_layout(tiles, markers, name=name, mode=mode, cell=unit)
        layout.grown = Grown(
            **recipe,
            seed=seed,
            size=[width, height],
            cell=unit,
            pattern=pattern,
            tries=attempt,
        )
        checked = check(layout, profile, expected_mode=expected_mode)
        if checked.ok:
            return GrownLevel(layout, checked, sample, tiles, attempt, dropped)
        first = next(
            (p.text for p in checked.problems if p.severity == "error"), "check failed"
        )
        dropped.append(f"try {attempt}: {first}")
    raise GrowError(
        f"No level grown from this sample at seed {seed} passed the blockout check in "
        f"{tries} tries ({'; '.join(dropped[-3:])}). Try another seed, a larger size, "
        "or a sample with wider passages."
    )


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def picture(sample: Sample, size: int = 480) -> bytes:
    """The sample as Joinery read it (wall, floor, markers), for the review."""
    scale = max(1.0, (size - 32) / max(sample.width, sample.height))
    pad = 16
    canvas = Canvas(
        math.ceil(sample.width * scale) + pad * 2,
        math.ceil(sample.height * scale) + pad * 2,
        BACKGROUND,
    )
    for y, row in enumerate(sample.tiles):
        for x, tile in enumerate(row):
            colour: RGB = FLOOR if tile == FLOOR_TILE else PLATFORM
            canvas.rect(
                pad + x * scale,
                pad + y * scale,
                pad + (x + 1) * scale,
                pad + (y + 1) * scale,
                colour,
            )
    for kind, x, y in sample.markers:
        colour = SPAWN if kind == "spawn" else MARKERS[kind]
        canvas.circle(
            pad + (x + 0.5) * scale, pad + (y + 0.5) * scale, scale * 0.4, colour
        )
    return canvas.png()


# --- from a project ----------------------------------------------------------------


@dataclass
class Made:
    """A grown level checked and built for a project (nothing written yet)."""

    grown: GrownLevel
    built: Built
    #: The sample as Joinery read it, for the review beside the grown plan.
    sample_png: bytes


def _sample_path(project: Path, sample: str) -> tuple[Path, str]:
    """The sample file and how the recipe names it (project-relative when inside)."""
    if "://" in sample and not sample.startswith("res://"):
        raise GrowError(
            "The sample must be a file of the user's own on this computer, not a "
            "link: levels grow only from examples the user made."
        )
    path = Path(sample.removeprefix("res://")).expanduser()
    if not path.is_absolute():
        path = project / path if (project / path).is_file() else path.absolute()
    path = path.resolve()
    if not path.is_file():
        raise GrowError(f"No sample at {sample}.")
    try:
        return path, path.relative_to(project.resolve()).as_posix()
    except ValueError:
        return path, str(path)


def make(
    project: Path,
    sample: str,
    *,
    width: int,
    height: int,
    seed: int,
    name: str = "grown",
    mode: str | None = None,
    blueprint: str | None = None,
    scene: str | None = None,
    pattern: int = 3,
    cell: float | None = None,
) -> Made:
    """Grow a level for ``project`` from the user's ``sample`` and build its files."""
    from joinery import blockout

    path, recorded = _sample_path(project, sample)
    chosen = blueprint or blockout.project_blueprint(project)
    read, data = read_sample(path, lambda m: profile_for(chosen, m))
    resolved = mode or read.mode or blockout.BLUEPRINT_MODES.get(chosen) or "3d"
    if resolved not in CELL:
        raise GrowError("mode is 3d, 2d-topdown or 2d-side.")
    level_mode: Mode = resolved  # type: ignore[assignment]
    grown = grow(
        read,
        width=width,
        height=height,
        seed=seed,
        mode=level_mode,
        profile=profile_for(chosen, level_mode),
        name=slug(name),
        recipe={"sample": recorded, "sample_sha256": sha256(data)},
        pattern=pattern,
        cell=cell,
        expected_mode=blockout.BLUEPRINT_MODES.get(chosen, ""),
    )
    built = blockout.make(
        project, to_toml(grown.layout), scene=scene, blueprint=blueprint
    )
    return Made(grown, built, picture(read))


def regrow(project: Path, layout_text: str, *, blueprint: str | None = None) -> Made:
    """Grow a recorded layout again from its ``[grown]`` recipe (sample, size, seed)."""
    try:
        layout = parse(layout_text)
    except LayoutError as exc:
        raise GrowError("; ".join(exc.problems)) from None
    recipe = layout.grown
    if recipe is None:
        raise GrowError("This layout wasn't grown: it has no [grown] table.")
    path, _ = _sample_path(project, recipe.sample)
    if sha256(path.read_bytes()) != recipe.sample_sha256:
        raise GrowError(
            f"The sample {recipe.sample} changed since the level was grown; the same "
            "seed would grow a different level."
        )
    return make(
        project,
        recipe.sample,
        width=recipe.size[0],
        height=recipe.size[1],
        seed=recipe.seed,
        name=layout.name,
        mode=layout.mode,
        blueprint=blueprint,
        pattern=recipe.pattern,
        cell=recipe.cell,
    )
