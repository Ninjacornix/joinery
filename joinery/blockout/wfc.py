"""Wave Function Collapse, the overlapping model, on small grids of tile numbers.

Joinery's own implementation (MIT, like the rest of this package) of the method
Maxim Gumin described: every N x N window of the sample (and, if asked, its rotations
and mirror images) is a pattern, counted by how often it occurs. The output starts
with every pattern possible at every place; repeatedly the place with the least
uncertainty is fixed to one pattern, drawn by those counts, and every neighbour loses
the patterns that no longer overlap consistently. When a place has no pattern left
the run fails and the caller tries again with the next seed.

Everything is decided by the seed: the same sample, size and seed always give the
same grid. Only ``random.Random(...).random()`` is used, whose sequence Python keeps
stable across versions.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass

Grid = list[list[int]]
Pattern = tuple[int, ...]
#: (dx, dy) to the neighbour on each side.
SIDES: tuple[tuple[int, int], ...] = ((0, -1), (1, 0), (0, 1), (-1, 0))


class Contradiction(Exception):
    """A place was left with no pattern: try another seed."""


@dataclass
class Model:
    """The patterns of one sample and which may sit beside which."""

    n: int
    patterns: list[Pattern]
    weights: list[float]
    #: allowed[side][p]: bit mask of the patterns that may sit on ``side`` of ``p``.
    allowed: list[list[int]]
    #: Patterns that may sit away from the output's sides: all but the solid blocks
    #: the sample only has against its plain sides (``learn``'s ``plain``).
    inner: int = -1

    @property
    def count(self) -> int:
        return len(self.patterns)


def _rotate(block: Pattern, n: int) -> Pattern:
    return tuple(block[(n - 1 - x) * n + y] for y in range(n) for x in range(n))


def _mirror(block: Pattern, n: int) -> Pattern:
    return tuple(block[y * n + (n - 1 - x)] for y in range(n) for x in range(n))


def _variants(block: Pattern, n: int, symmetry: int) -> list[Pattern]:
    if symmetry <= 1:
        return [block]
    if symmetry == 2:
        return [block, _mirror(block, n)]
    out: list[Pattern] = []
    turned = block
    for _ in range(4):
        out += [turned, _mirror(turned, n)]
        turned = _rotate(turned, n)
    return out


def _agrees(a: Pattern, b: Pattern, dx: int, dy: int, n: int) -> bool:
    """Do ``a`` and ``b`` overlap consistently with ``b`` moved by (dx, dy)?"""
    for y in range(max(0, dy), min(n, n + dy)):
        for x in range(max(0, dx), min(n, n + dx)):
            if a[y * n + x] != b[(y - dy) * n + (x - dx)]:
                return False
    return True


def learn(
    sample: Grid,
    n: int = 3,
    symmetry: int = 8,
    plain: Sequence[bool] = (False, False, False, False),
) -> Model:
    """The patterns of ``sample`` (rows of tile numbers, not wrapped at its edges).

    ``plain`` marks the sides (top, right, bottom, left) that are one tile all along,
    such as an outer wall or the ground. A solid block of one tile seen only against
    them is kept for the output's sides (``Model.inner``): otherwise a thick border
    spreads into the middle and fills it.
    """
    height, width = len(sample), len(sample[0]) if sample else 0
    if width < n or height < n:
        raise ValueError(f"The sample is {width}x{height}; it needs at least {n}x{n}.")
    counts: dict[Pattern, int] = {}
    inside: set[Pattern] = set()
    for y in range(height - n + 1):
        for x in range(width - n + 1):
            block = tuple(sample[y + j][x + i] for j in range(n) for i in range(n))
            touches = (
                (plain[0] and y == 0)
                or (plain[1] and x == width - n)
                or (plain[2] and y == height - n)
                or (plain[3] and x == 0)
            )
            for variant in _variants(block, n, symmetry):
                counts[variant] = counts.get(variant, 0) + 1
                if not touches or len(set(variant)) > 1:
                    inside.add(variant)
    patterns = list(counts)
    weights = [float(counts[p]) for p in patterns]
    allowed = [
        [
            sum(
                1 << q
                for q, other in enumerate(patterns)
                if _agrees(pattern, other, dx, dy, n)
            )
            for pattern in patterns
        ]
        for dx, dy in SIDES
    ]
    inner = sum(1 << p for p, pattern in enumerate(patterns) if pattern in inside)
    return Model(n, patterns, weights, allowed, inner or -1)


def _bits(mask: int) -> list[int]:
    out = []
    while mask:
        low = mask & -mask
        out.append(low.bit_length() - 1)
        mask ^= low
    return out


def run(
    model: Model,
    width: int,
    height: int,
    rng: random.Random,
    edges: Sequence[int | None] = (None, None, None, None),
) -> Grid:
    """A ``width`` x ``height`` grid grown from ``model``.

    ``edges`` (top, right, bottom, left) may name a tile that whole side of the output
    must be, as the sample's own side is. Raises :class:`Contradiction`.
    """
    n = model.n
    cols, rows = width - n + 1, height - n + 1
    if cols < 1 or rows < 1:
        raise ValueError(f"The level must be at least {n}x{n}.")
    everything = (1 << model.count) - 1
    wave = [everything] * (cols * rows)
    for side, tile in enumerate(edges):
        if tile is None:
            continue
        keep = 0
        for p, pattern in enumerate(model.patterns):
            line = {
                0: pattern[:n],
                1: pattern[n - 1 :: n],
                2: pattern[n * (n - 1) :],
                3: pattern[::n],
            }[side]
            if all(t == tile for t in line):
                keep |= 1 << p
        if not keep:
            continue
        for y in range(rows):
            for x in range(cols):
                on_side = (y == 0, x == cols - 1, y == rows - 1, x == 0)[side]
                if on_side:
                    wave[y * cols + x] &= keep
    constrained = [tile is not None for tile in edges]
    if model.inner != -1:
        for y in range(rows):
            for x in range(cols):
                sides = (y == 0, x == cols - 1, y == rows - 1, x == 0)
                if not any(c and s for c, s in zip(constrained, sides, strict=True)):
                    wave[y * cols + x] &= model.inner
    weights = model.weights
    log_weights = [w * math.log(w) for w in weights]
    union_cache: list[dict[int, int]] = [{} for _ in SIDES]

    def entropy(mask: int) -> float:
        total = 0.0
        weighted = 0.0
        for p in _bits(mask):
            total += weights[p]
            weighted += log_weights[p]
        return math.log(total) - weighted / total

    def union(side: int, mask: int) -> int:
        cache = union_cache[side]
        found = cache.get(mask)
        if found is None:
            found = 0
            table = model.allowed[side]
            for p in _bits(mask):
                found |= table[p]
            cache[mask] = found
        return found

    def propagate(stack: list[int]) -> None:
        while stack:
            here = stack.pop()
            x, y = here % cols, here // cols
            mask = wave[here]
            for side, (dx, dy) in enumerate(SIDES):
                nx, ny = x + dx, y + dy
                if not (0 <= nx < cols and 0 <= ny < rows):
                    continue
                there = ny * cols + nx
                narrowed = wave[there] & union(side, mask)
                if narrowed != wave[there]:
                    if not narrowed:
                        raise Contradiction()
                    wave[there] = narrowed
                    stack.append(there)

    propagate([i for i, mask in enumerate(wave) if mask != everything])
    if any(not mask for mask in wave):
        raise Contradiction()
    cached: dict[int, float] = {}
    while True:
        best = -1
        best_score = math.inf
        for i, mask in enumerate(wave):
            if mask & (mask - 1) == 0:
                continue
            score = cached.get(mask)
            if score is None:
                score = cached[mask] = entropy(mask)
            score += rng.random() * 1e-6
            if score < best_score:
                best, best_score = i, score
        if best < 0:
            break
        options = _bits(wave[best])
        pick = rng.random() * sum(weights[p] for p in options)
        chosen = options[-1]
        for p in options:
            pick -= weights[p]
            if pick <= 0:
                chosen = p
                break
        wave[best] = 1 << chosen
        propagate([best])
    grid: Grid = [[0] * width for _ in range(height)]
    for y in range(height):
        for x in range(width):
            cx, cy = min(x, cols - 1), min(y, rows - 1)
            pattern = model.patterns[_bits(wave[cy * cols + cx])[0]]
            grid[y][x] = pattern[(y - cy) * n + (x - cx)]
    return grid
