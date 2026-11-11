"""Can the player walk from the spawn to every goal? A grid check of the resolved world.

Plans (3D and top-down 2D) are rasterised into cells a fraction of the player's
radius wide. A cell is standable when there is a floor under it and no wall piece
within the player's radius at body height; the player walks to a neighbour when the
height difference is a step, jumps up a ledge as high as the starting point's jump,
drops down, and jumps across gaps as wide as its jump. Side views (2D platformers)
use cells of half a tile: the player stands on solid ground, walks, falls off edges
and jumps along an arc.

The answer names each target (goal, pickup, coin, checkpoint) as reached or not,
with a route of waypoints the playtest bot follows in Godot, and for each target it
can't reach, why, in words an agent can act on.
"""

from __future__ import annotations

import heapq
import math
from collections.abc import Callable
from dataclasses import dataclass, field

from joinery.blockout.layout import TARGETS
from joinery.blockout.world import XY, Area, Profile, World

Free = Callable[[int], bool]
Cell = Callable[[int, int], bool]

#: Cells per check at most; a bigger level gets coarser cells.
MAX_CELLS = 160_000
#: The farthest a player may drop and still walk on (metres).
MAX_DROP = 8.0


@dataclass
class Target:
    id: str
    kind: str
    at: XY
    reached: bool = False
    #: Waypoints (x, y, height, jump) from the spawn; empty when not reached.
    route: list[tuple[float, float, float, bool]] = field(default_factory=list)
    reason: str = ""


@dataclass
class Reach:
    spawn_ok: bool = True
    spawn_reason: str = ""
    targets: list[Target] = field(default_factory=list)
    cell: float = 0.0

    @property
    def reached(self) -> int:
        return sum(1 for t in self.targets if t.reached)

    def fact(self) -> str:
        """The review's fact: "Walkable: spawn reaches 3 of 3 goals"."""
        total = len(self.targets)
        noun = "goal" if total == 1 else "goals"
        return f"Walkable: spawn reaches {self.reached} of {total} {noun}"


class _Grid:
    def __init__(self, world: World, cell: float) -> None:
        x0, y0, x1, y1 = world.bounds()
        pad = cell * 4 + world.profile.radius * 2
        self.x0, self.y0 = x0 - pad, y0 - pad
        self.cell = cell
        self.w = max(1, math.ceil((x1 - x0 + pad * 2) / cell))
        self.h = max(1, math.ceil((y1 - y0 + pad * 2) / cell))

    def centre(self, i: int, j: int) -> XY:
        return (self.x0 + (i + 0.5) * self.cell, self.y0 + (j + 0.5) * self.cell)

    def index(self, x: float, y: float) -> tuple[int, int]:
        return (
            min(max(int((x - self.x0) / self.cell), 0), self.w - 1),
            min(max(int((y - self.y0) / self.cell), 0), self.h - 1),
        )

    def span(self, lo: float, hi: float, axis: int) -> range:
        origin = self.x0 if axis == 0 else self.y0
        size = self.w if axis == 0 else self.h
        first = max(int((lo - origin) / self.cell), 0)
        last = min(int((hi - origin) / self.cell) + 1, size)
        return range(first, last)


def _cell_size(world: World, base: float) -> float:
    x0, y0, x1, y1 = world.bounds()
    cell = base
    while (x1 - x0) * (y1 - y0) / (cell * cell) > MAX_CELLS:
        cell *= 1.25
    return cell


def check(world: World) -> Reach:
    """Walk the world from its spawn to every target marker."""
    if world.spawn is None:
        return Reach(False, "There is no spawn: add [spawn] at = [x, y].")
    if world.mode == "2d-side":
        return _side(world)
    return _plan(world)


# --- Plans (3D and top-down) ---------------------------------------------------------


def _plan(world: World) -> Reach:
    profile = world.profile
    three_d = world.three_d
    cell = _cell_size(world, profile.radius / 2)
    grid = _Grid(world, cell)
    count = grid.w * grid.h
    heights: list[float | None] = [None] * count
    for area in world.areas:
        _paint(grid, area, heights, three_d)
    if three_d:
        for platform in world.platforms:
            _paint(grid, platform, heights, True)
    blocked = [False] * count
    radius = profile.radius
    for piece in world.pieces:
        x0 = min(piece.a[0], piece.b[0]) - piece.thickness - radius
        x1 = max(piece.a[0], piece.b[0]) + piece.thickness + radius
        y0 = min(piece.a[1], piece.b[1]) - piece.thickness - radius
        y1 = max(piece.a[1], piece.b[1]) + piece.thickness + radius
        for j in grid.span(y0, y1, 1):
            for i in grid.span(x0, x1, 0):
                k = j * grid.w + i
                height = heights[k]
                if height is None or blocked[k]:
                    continue
                if three_d and not (
                    piece.top > height + profile.step
                    and piece.base < height + profile.height
                ):
                    continue
                if piece.distance(*grid.centre(i, j)) < radius:
                    blocked[k] = True
    if not three_d:
        for platform in world.platforms:
            bx0, by0, bx1, by1 = platform.bounds
            for j in grid.span(by0 - radius, by1 + radius, 1):
                for i in grid.span(bx0 - radius, bx1 + radius, 0):
                    if platform.contains(*grid.centre(i, j), margin=-radius):
                        blocked[j * grid.w + i] = True

    def free(k: int) -> bool:
        return heights[k] is not None and not blocked[k]

    near = _near_edges(grid, free, max(1, math.ceil(radius / cell)))
    # Routes keep off hazards where there is another way.
    avoid = _around_hazards(world, grid, radius)
    assert world.spawn is not None
    start = _nearest_free(grid, free, world.spawn.at, radius * 2)
    reach = Reach(cell=cell)
    if start is None:
        reach.spawn_ok = False
        reach.spawn_reason = _spot_reason(
            world, world.spawn.at, "The spawn", heights, grid
        )
        start_k = -1
    else:
        start_k = start
    came: dict[int, tuple[int, bool]] = {}
    if start_k >= 0:
        came = _dijkstra(world, grid, heights, free, near, avoid, start_k)
    for marker in world.markers:
        if marker.kind not in TARGETS:
            continue
        target = Target(marker.id, marker.kind, marker.at)
        reach.targets.append(target)
        goal = _nearest_free(grid, free, marker.at, max(radius * 2, marker.size))
        if start_k < 0:
            target.reason = "The spawn is not on a floor the player can stand on."
        elif goal is None:
            target.reason = _spot_reason(
                world, marker.at, f"{marker.kind} {marker.id}", heights, grid
            )
        elif goal not in came:
            target.reason = _why_not(
                world, grid, came, marker.at, marker.id, marker.kind
            )
        else:
            target.reached = True
            target.route = _route(grid, heights, free, came, goal)
    return reach


def _around_hazards(world: World, grid: _Grid, radius: float) -> set[int]:
    cells: set[int] = set()
    for marker in world.markers:
        if marker.kind != "hazard":
            continue
        reach = marker.size / 2 + radius
        x, y = marker.at
        for j in grid.span(y - reach, y + reach, 1):
            for i in grid.span(x - reach, x + reach, 0):
                if math.dist(grid.centre(i, j), marker.at) <= reach:
                    cells.add(j * grid.w + i)
    return cells


def _paint(grid: _Grid, area: Area, heights: list[float | None], three_d: bool) -> None:
    x0, y0, x1, y1 = area.bounds
    for j in grid.span(y0, y1, 1):
        for i in grid.span(x0, x1, 0):
            x, y = grid.centre(i, j)
            if not area.contains(x, y):
                continue
            height = area.height_at(x, y) if three_d else 0.0
            k = j * grid.w + i
            current = heights[k]
            heights[k] = height if current is None else max(current, height)


def _near_edges(grid: _Grid, free: Free, steps: int) -> set[int]:
    """Cells within ``steps`` of a wall or a ledge, where a route should not hug."""
    frontier = [k for k in range(grid.w * grid.h) if not free(k)]
    seen = set(frontier)
    near: set[int] = set()
    for _ in range(steps):
        following: list[int] = []
        for k in frontier:
            i, j = k % grid.w, k // grid.w
            for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                ni, nj = i + di, j + dj
                if 0 <= ni < grid.w and 0 <= nj < grid.h:
                    nk = nj * grid.w + ni
                    if nk not in seen:
                        seen.add(nk)
                        following.append(nk)
                        near.add(nk)
        frontier = following
    return near


def _nearest_free(grid: _Grid, free: Free, at: XY, within: float) -> int | None:
    ci, cj = grid.index(*at)
    best: tuple[float, int] | None = None
    steps = max(1, math.ceil(within / grid.cell))
    for j in range(max(cj - steps, 0), min(cj + steps + 1, grid.h)):
        for i in range(max(ci - steps, 0), min(ci + steps + 1, grid.w)):
            k = j * grid.w + i
            if not free(k):
                continue
            distance = math.dist(grid.centre(i, j), at)
            if distance <= within and (best is None or distance < best[0]):
                best = (distance, k)
    return best[1] if best else None


def _dijkstra(
    world: World,
    grid: _Grid,
    heights: list[float | None],
    free: Free,
    near: set[int],
    avoid: set[int],
    start: int,
) -> dict[int, tuple[int, bool]]:
    """Every cell the player reaches from ``start``: {cell: (previous cell, jumped)}."""
    profile = world.profile
    cell = grid.cell
    across = math.floor(profile.across / cell) if profile.jumps else 0
    best = {start: 0.0}
    came: dict[int, tuple[int, bool]] = {start: (-1, False)}
    queue = [(0.0, start)]
    moves = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)]
    while queue:
        cost, k = heapq.heappop(queue)
        if cost > best.get(k, math.inf):
            continue
        i, j = k % grid.w, k // grid.w
        here = heights[k]
        assert here is not None
        for di, dj in moves:
            ni, nj = i + di, j + dj
            if not (0 <= ni < grid.w and 0 <= nj < grid.h):
                continue
            nk = nj * grid.w + ni
            if not free(nk):
                below = heights[nk]
                if across and (below is None or below < here - profile.step):
                    _gap_jumps(
                        grid,
                        heights,
                        free,
                        k,
                        di,
                        dj,
                        across,
                        profile,
                        best,
                        came,
                        queue,
                        cost,
                    )
                continue
            if di and dj and not (free(j * grid.w + ni) and free(nj * grid.w + i)):
                continue
            there = heights[nk]
            assert there is not None
            rise = there - here
            jumped = False
            if rise > profile.step + 1e-6:
                if not profile.jumps or rise > profile.jump:
                    continue
                jumped = True
            elif rise < -MAX_DROP:
                continue
            step = cell * (math.sqrt(2) if di and dj else 1.0)
            step += cell * 3 if jumped else 0.0
            step += cell * 1.5 if nk in near else 0.0
            step += cell * 60 if nk in avoid else 0.0
            total = cost + step
            if total < best.get(nk, math.inf):
                best[nk] = total
                came[nk] = (k, jumped)
                heapq.heappush(queue, (total, nk))
    return came


def _gap_jumps(
    grid: _Grid,
    heights: list[float | None],
    free: Free,
    k: int,
    di: int,
    dj: int,
    across: int,
    profile: Profile,
    best: dict[int, float],
    came: dict[int, tuple[int, bool]],
    queue: list[tuple[float, int]],
    cost: float,
) -> None:
    """A jump straight across a gap or a drop from the ledge at ``k``."""
    i, j = k % grid.w, k // grid.w
    here = heights[k]
    assert here is not None
    for reach in range(2, across + 1):
        ni, nj = i + di * reach, j + dj * reach
        if not (0 <= ni < grid.w and 0 <= nj < grid.h):
            return
        nk = nj * grid.w + ni
        there = heights[nk]
        if there is not None and there > here + profile.jump:
            return
        if not free(nk):
            continue
        assert there is not None
        total = (
            cost
            + grid.cell * reach * (math.sqrt(2) if di and dj else 1.0)
            + 4 * grid.cell
        )
        if total < best.get(nk, math.inf):
            best[nk] = total
            came[nk] = (k, True)
            heapq.heappush(queue, (total, nk))
        return


def _route(
    grid: _Grid,
    heights: list[float | None],
    free: Free,
    came: dict[int, tuple[int, bool]],
    goal: int,
) -> list[tuple[float, float, float, bool]]:
    chain: list[tuple[int, bool]] = []
    k = goal
    while k >= 0:
        previous, jumped = came[k]
        chain.append((k, jumped))
        k = previous
    chain.reverse()
    points = [
        (*grid.centre(k % grid.w, k // grid.w), heights[k] or 0.0, jumped)
        for k, jumped in chain
    ]
    return _simplify(grid, heights, free, points)


def _simplify(
    grid: _Grid,
    heights: list[float | None],
    free: Free,
    points: list[tuple[float, float, float, bool]],
) -> list[tuple[float, float, float, bool]]:
    """Keep the turns and jumps of a grid path, then drop turns a straight line skips."""
    if len(points) <= 2:
        return points
    kept = [points[0]]
    for index in range(1, len(points) - 1):
        before, here, after = points[index - 1], points[index], points[index + 1]
        turn = (here[0] - before[0], here[1] - before[1]) != (
            after[0] - here[0],
            after[1] - here[1],
        )
        if turn or here[3] or after[3] or abs(here[2] - before[2]) > 1e-6:
            kept.append(here)
    kept.append(points[-1])
    for _ in range(3):
        index = 1
        while index < len(kept) - 1:
            a, b, c = kept[index - 1], kept[index], kept[index + 1]
            if not (b[3] or c[3]) and _clear(grid, heights, free, a, c):
                kept.pop(index)
            else:
                index += 1
    return [(round(x, 3), round(y, 3), round(h, 3), j) for x, y, h, j in kept]


def _clear(
    grid: _Grid,
    heights: list[float | None],
    free: Free,
    a: tuple[float, float, float, bool],
    b: tuple[float, float, float, bool],
) -> bool:
    length = math.dist(a[:2], b[:2])
    steps = max(1, math.ceil(length / (grid.cell / 2)))
    last = a[2]
    for s in range(steps + 1):
        x = a[0] + (b[0] - a[0]) * s / steps
        y = a[1] + (b[1] - a[1]) * s / steps
        i, j = grid.index(x, y)
        k = j * grid.w + i
        if not free(k):
            return False
        height = heights[k] or 0.0
        if abs(height - last) > 0.31:
            return False
        last = height
    return True


def _spot_reason(
    world: World, at: XY, what: str, heights: list[float | None], grid: _Grid
) -> str:
    i, j = grid.index(*at)
    unit = world.unit
    where = f"at [{at[0] / unit:g}, {at[1] / unit:g}]"
    if heights[j * grid.w + i] is None:
        return (
            f"{what} {where} is not over any room, corridor, stairs or platform; "
            "move it onto a floor."
        )
    return (
        f"{what} {where} is inside or against a wall; move it at least "
        f"{world.profile.radius / unit:g} away from walls."
    )


def _why_not(
    world: World,
    grid: _Grid,
    came: dict[int, tuple[int, bool]],
    at: XY,
    ident: str,
    kind: str,
) -> str:
    unit = world.unit
    closest = min(
        (math.dist(grid.centre(k % grid.w, k // grid.w), at) for k in came),
        default=math.inf,
    )
    area = world.area_at(*at)
    where = f" in {area.kind} {area.id}" if area is not None else ""
    advice = (
        "Add a door or an open side, a corridor, stairs or a lower ledge on the way."
    )
    if area is not None and area.kind == "room":
        doors = [
            o for o in world.layout.openings if o.room == area.id and o.kind != "window"
        ]
        layout_room = next((r for r in world.layout.rooms if r.id == area.id), None)
        crossing = any(
            a.kind in {"corridor", "stairs"} and _touches(a.bounds, area.bounds)
            for a in world.areas
        )
        if not doors and not (layout_room and layout_room.open) and not crossing:
            advice = (
                f"Room {area.id} has no door, open side or corridor into it: add an "
                f"opening to one of its walls."
            )
    elif area is not None and area.kind == "platform" and world.three_d:
        advice = (
            f"Platform {area.id} is {area.top:g} m up; the player jumps "
            f"{world.profile.jump:g} m. Lower it or add stairs or a step."
        )
    return (
        f"{kind} {ident}{where} can't be reached from the spawn; the player gets within "
        f"{closest / unit:.1f} of it. {advice}"
    )


def _touches(
    a: tuple[float, float, float, float], b: tuple[float, float, float, float]
) -> bool:
    eps = 1e-6
    return not (
        a[2] < b[0] - eps or b[2] < a[0] - eps or a[3] < b[1] - eps or b[3] < a[1] - eps
    )


# --- Side views (2D platformers) -----------------------------------------------------


def _side(world: World) -> Reach:
    profile = world.profile
    cell = max(world.tile_px / 2, 2.0)
    grid = _Grid(world, cell)
    solid = [False] * (grid.w * grid.h)
    for piece in world.pieces:
        x0 = min(piece.a[0], piece.b[0]) - piece.thickness
        x1 = max(piece.a[0], piece.b[0]) + piece.thickness
        y0 = min(piece.a[1], piece.b[1]) - piece.thickness
        y1 = max(piece.a[1], piece.b[1]) + piece.thickness
        for j in grid.span(y0, y1, 1):
            for i in grid.span(x0, x1, 0):
                if piece.distance(*grid.centre(i, j)) <= cell * 0.25:
                    solid[j * grid.w + i] = True
    for area in [*world.platforms, *[a for a in world.areas if a.kind == "stairs"]]:
        x0, y0, x1, y1 = area.bounds
        for j in grid.span(y0, y1, 1):
            for i in grid.span(x0, x1, 0):
                x, y = grid.centre(i, j)
                if not area.contains(x, y):
                    continue
                if area.kind == "stairs" and not _under_slope(area, x, y):
                    continue
                solid[j * grid.w + i] = True
    tall = max(1, math.ceil(profile.height / cell))
    avoid = {
        (k % grid.w, k // grid.w)
        for k in _around_hazards(world, grid, profile.radius + cell)
    }
    rise = max(1, math.floor(profile.jump / cell))
    reach_x = max(1, math.floor(profile.across / cell))

    def empty(i: int, j: int) -> bool:
        return 0 <= i < grid.w and 0 <= j < grid.h and not solid[j * grid.w + i]

    def body(i: int, j: int) -> bool:
        return all(empty(i, j - r) for r in range(tall))

    def stand(i: int, j: int) -> bool:
        return body(i, j) and 0 <= j + 1 < grid.h and solid[(j + 1) * grid.w + i]

    stands: dict[int, list[int]] = {}
    for i in range(grid.w):
        stands[i] = [j for j in range(grid.h) if stand(i, j)]
    assert world.spawn is not None
    start = _drop_to_stand(grid, stand, empty, world.spawn.at)
    reach = Reach(cell=cell)
    if start is None:
        reach.spawn_ok = False
        reach.spawn_reason = (
            "The spawn has no ground under it the player can stand on; put it above "
            "a floor or platform."
        )
    came: dict[tuple[int, int], tuple[tuple[int, int] | None, bool]] = {}
    if start is not None:
        came = {start: (None, False)}
        best = {start: 0.0}
        queue: list[tuple[float, tuple[int, int]]] = [(0.0, start)]
        while queue:
            cost, (i, j) = heapq.heappop(queue)
            if cost > best.get((i, j), math.inf):
                continue
            for nxt, jumped in _side_moves(
                i, j, stands, stand, empty, body, rise, reach_x, tall
            ):
                dx = abs(nxt[0] - i)
                step = 4.0 + 2.0 * dx if jumped else dx + 0.2 * abs(nxt[1] - j)
                step += 200.0 if nxt in avoid else 0.0
                if cost + step < best.get(nxt, math.inf):
                    best[nxt] = cost + step
                    came[nxt] = ((i, j), jumped)
                    heapq.heappush(queue, (cost + step, nxt))
    for marker in world.markers:
        if marker.kind not in TARGETS:
            continue
        target = Target(marker.id, marker.kind, marker.at)
        reach.targets.append(target)
        mi, mj = grid.index(*marker.at)
        options = [
            (abs(di), j - mj, (mi + di, j))
            for di in (0, -1, 1)
            for j in stands.get(mi + di, [])
            if 0 <= j - mj <= rise + 1
        ]
        hit = next((o[2] for o in sorted(options) if o[2] in came), None)
        if start is None:
            target.reason = "The spawn has no ground under it."
        elif hit is None:
            where = world.area_at(*marker.at)
            inside = f" in {where.kind} {where.id}" if where else ""
            target.reason = (
                f"{marker.kind} {marker.id}{inside} can't be reached from the spawn: no "
                f"ground within a jump ({profile.jump / world.tile_px:g} tiles up, "
                f"{profile.across / world.tile_px:g} across) leads to it. Add a "
                "platform, lower a ledge or open a wall on the way."
            )
        else:
            target.reached = True
            target.route = _side_route(grid, came, hit)
    return reach


def _under_slope(area: Area, x: float, y: float) -> bool:
    x0, y0, x1, y1 = area.bounds
    part = (x - x0) / (x1 - x0) if area.up == "east" else (x1 - x) / (x1 - x0)
    if area.up not in {"east", "west"}:
        return True
    surface = y1 - (y1 - y0) * part
    return y >= surface


def _drop_to_stand(
    grid: _Grid, stand: Cell, empty: Cell, at: XY
) -> tuple[int, int] | None:
    i, j = grid.index(*at)
    for jj in range(j, grid.h):
        if stand(i, jj):
            return (i, jj)
        if not empty(i, jj):
            return None
    return None


def _side_moves(
    i: int,
    j: int,
    stands: dict[int, list[int]],
    stand: Cell,
    empty: Cell,
    body: Cell,
    rise: int,
    reach_x: int,
    tall: int,
) -> list[tuple[tuple[int, int], bool]]:
    moves: list[tuple[tuple[int, int], bool]] = []
    for di in (-1, 1):
        ni = i + di
        if stand(ni, j):
            moves.append(((ni, j), False))
        elif body(ni, j):
            # Walk off the edge and fall.
            for nj in range(j + 1, j + 400):
                if stand(ni, nj):
                    moves.append(((ni, nj), False))
                    break
                if not body(ni, nj):
                    break
        for dx in range(1, reach_x + 1):
            ti = i + di * dx
            for tj in stands.get(ti, []):
                if tj < j - rise or tj > j + rise * 4:
                    continue
                apex = min(j, tj) - 1
                if j - apex > rise + 1:
                    continue
                if _arc_clear(i, j, ti, tj, apex, empty, tall):
                    moves.append(((ti, tj), True))
    return moves


def _arc_clear(
    i: int, j: int, ti: int, tj: int, apex: int, empty: Cell, tall: int
) -> bool:
    top = apex - tall + 1
    for row in range(top, j + 1):
        if not empty(i, row):
            return False
    step = 1 if ti > i else -1
    for col in range(i, ti + step, step):
        for row in range(top, apex + 1):
            if not empty(col, row):
                return False
    for row in range(top, tj + 1):
        if not empty(ti, row):
            return False
    return True


def _side_route(
    grid: _Grid,
    came: dict[tuple[int, int], tuple[tuple[int, int] | None, bool]],
    goal: tuple[int, int],
) -> list[tuple[float, float, float, bool]]:
    chain: list[tuple[tuple[int, int], bool]] = []
    node: tuple[int, int] | None = goal
    while node is not None:
        previous, jumped = came[node]
        chain.append((node, jumped))
        node = previous
    chain.reverse()
    points: list[tuple[float, float, float, bool]] = []
    for (i, j), jumped in chain:
        x = grid.x0 + (i + 0.5) * grid.cell
        feet = grid.y0 + (j + 1) * grid.cell
        points.append((round(x, 2), round(feet, 2), 0.0, jumped))
    kept = [points[0]]
    for index in range(1, len(points) - 1):
        before, here, after = points[index - 1], points[index], points[index + 1]
        if here[3] or after[3] or here[1] != before[1] or after[1] != here[1]:
            kept.append(here)
    if len(points) > 1:
        kept.append(points[-1])
    return kept
