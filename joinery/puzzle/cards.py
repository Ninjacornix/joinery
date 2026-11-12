"""The memory-cards starting point's deals: can a player clear them, and how hard are
they?

The game (``game/deck.gd``) shuffles pairs of symbols with Godot's
RandomNumberGenerator, so a deal is fixed by its seed. :func:`deal` repeats that
shuffle here exactly (PCG32, as Godot seeds and bounds it), so a deal can be checked
without running the game. Every deal can be cleared in as many moves as it has pairs
by a player who knows where the cards are; the honest measure is a player with a
perfect memory who doesn't (:func:`memory_moves`): it turns up a known pair when it
has one, else an unseen card, then the card that matches it if it has seen one, else
another unseen card. Over a sample of seeds that gives the moves deals take, how many
of them fit in the level's move limit, and the stars they earn.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Seeds checked: 1 to SAMPLE.
SAMPLE = 200
_MASK64 = (1 << 64) - 1
_PCG_INC = 1442695040888963407
_MULTIPLIER = 6364136223846793005


class _Pcg32:
    """Godot's RandomPCG with its default stream (``rng.seed = n``)."""

    def __init__(self, seed: int) -> None:
        self.inc = ((_PCG_INC << 1) | 1) & _MASK64
        self.state = 0
        self.next()
        self.state = (self.state + (seed & _MASK64)) & _MASK64
        self.next()

    def next(self) -> int:
        old = self.state
        self.state = (old * _MULTIPLIER + self.inc) & _MASK64
        shifted = (((old >> 18) ^ old) >> 27) & 0xFFFFFFFF
        rot = old >> 59
        return ((shifted >> rot) | (shifted << ((-rot) & 31))) & 0xFFFFFFFF

    def bounded(self, bound: int) -> int:
        threshold = ((-bound) & 0xFFFFFFFF) % bound
        while True:
            value = self.next()
            if value >= threshold:
                return value % bound

    def randi_range(self, low: int, high: int) -> int:
        if low == high:
            return low
        return self.bounded(abs(high - low) + 1) + min(low, high)


def deal(seed: int, pairs: int, symbols: int = 12) -> list[int]:
    """The layout ``Deck.deal(seed, pairs)`` gives: one symbol per slot, each twice."""
    count = max(1, min(pairs, symbols))
    layout = [symbol for symbol in range(count) for _ in (0, 1)]
    rng = _Pcg32(seed)
    for i in range(len(layout) - 1, 0, -1):
        j = rng.randi_range(0, i)
        layout[i], layout[j] = layout[j], layout[i]
    return layout


def memory_moves(layout: list[int]) -> tuple[int, float]:
    """Moves a perfect-memory player takes to clear ``layout``, and the mean number of
    unseen cards it had to choose from when it turned one (the deal's branching)."""
    #: Cards seen face up and not cleared: index -> symbol.
    seen: dict[int, int] = {}
    unseen = list(range(len(layout)))
    left = len(layout)
    moves = 0
    choices: list[int] = []

    def take() -> int:
        choices.append(len(unseen))
        return unseen.pop(0)

    while left:
        moves += 1
        by_symbol: dict[int, list[int]] = {}
        for index, symbol in seen.items():
            by_symbol.setdefault(symbol, []).append(index)
        known = next((pair for pair in by_symbol.values() if len(pair) == 2), None)
        if known is not None:
            for index in known:
                del seen[index]
            left -= 2
            continue
        first = take()
        partner = next((i for i, s in seen.items() if s == layout[first]), None)
        if partner is not None:
            del seen[partner]
            left -= 2
            continue
        second = take()
        if layout[second] == layout[first]:
            left -= 2
            continue
        seen[first] = layout[first]
        seen[second] = layout[second]
    return moves, (sum(choices) / len(choices) if choices else 0.0)


def stars_for(moves: int, pairs: int) -> int:
    """``Deck.stars_for``: three for close to perfect memory, two for a steady game."""
    if moves <= pairs + math.ceil(pairs / 2):
        return 3
    if moves <= pairs * 2 + 2:
        return 2
    return 1


@dataclass
class Board:
    """The level's settings as the game reads them."""

    pair_count: int
    columns: int
    move_limit: int
    symbols: int


@dataclass
class DealReport:
    """How the memory cards' deals play."""

    board: Board
    solvable: bool
    #: Fewest moves any deal can be cleared in (one per pair).
    shortest: int
    seeds: int
    moves_min: int = 0
    moves_mean: float = 0.0
    moves_max: int = 0
    #: Deals a perfect-memory player can't clear within the move limit.
    dead_deals: int = 0
    branching: float = 0.0
    stars: dict[int, int] = field(default_factory=dict)
    difficulty: str = ""
    reasons: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        if not self.solvable:
            return "Memory cards: unsolvable: " + (
                self.reasons[0] if self.reasons else ""
            )
        limit = self.board.move_limit
        within = (
            f"{self.seeds - self.dead_deals} of {self.seeds} deals within {limit} moves"
            if limit
            else "no move limit"
        )
        return (
            f"Memory cards: {self.board.pair_count} pairs, solvable in "
            f"{self.shortest} moves at best; a perfect memory takes "
            f"{self.moves_min}-{self.moves_max} (mean {self.moves_mean:.1f}), {within}: "
            f"{self.difficulty}"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": "memory-cards",
            "pair_count": self.board.pair_count,
            "columns": self.board.columns,
            "move_limit": self.board.move_limit,
            "symbols": self.board.symbols,
            "solvable": self.solvable,
            "shortest": self.shortest,
            "seeds": self.seeds,
            "memory_moves": {
                "min": self.moves_min,
                "mean": round(self.moves_mean, 2),
                "max": self.moves_max,
            },
            "dead_deals": self.dead_deals,
            "branching": round(self.branching, 2),
            "stars": {str(k): v for k, v in sorted(self.stars.items())},
            "difficulty": self.difficulty,
            "reasons": self.reasons,
            "notes": self.notes,
            "summary": self.summary(),
        }


def _number(text: str, name: str) -> int | None:
    found = re.search(rf"^\s*{name}\s*=\s*(-?\d+)\s*$", text, re.MULTILINE)
    return int(found.group(1)) if found else None


def _default(script: str, name: str) -> int | None:
    found = re.search(
        rf"^@export[^\n]*\bvar\s+{name}\s*:\s*int\s*=\s*(-?\d+)", script, re.MULTILINE
    )
    return int(found.group(1)) if found else None


def read_board(root: Path) -> Board | None:
    """The memory-cards settings of the game at ``root`` (a project or a stage), or
    None when it isn't one: ``game/level.tscn`` over ``game/level.gd``'s defaults,
    and the symbols in ``game/deck.gd``."""
    try:
        deck = (root / "game/deck.gd").read_text(encoding="utf-8")
        script = (root / "game/level.gd").read_text(encoding="utf-8")
        scene = (root / "game/level.tscn").read_text(encoding="utf-8")
    except OSError:
        return None
    if "static func deal(" not in deck or "pair_count" not in script:
        return None
    symbols = re.search(r"const SYMBOLS[^=]*=\s*\[([^\]]*)\]", deck)
    count = len(re.findall(r'"[^"]*"', symbols.group(1))) if symbols else 12
    # Only the root node's properties: the first [node] block.
    root_block = scene.split("[node ", 2)
    props = root_block[1] if len(root_block) > 1 else ""

    def value(name: str, fallback: int) -> int:
        found = _number(props, name)
        if found is None:
            found = _default(script, name)
        return fallback if found is None else found

    return Board(
        pair_count=value("pair_count", 8),
        columns=value("columns", 4),
        move_limit=value("move_limit", 30),
        symbols=count,
    )


def check(board: Board, seeds: int = SAMPLE) -> DealReport:
    """Every deal for seeds 1..``seeds`` played by a perfect memory."""
    report = DealReport(
        board, True, max(1, min(board.pair_count, board.symbols)), seeds
    )
    if board.pair_count < 1:
        report.solvable = False
        report.reasons.append(f"pair_count is {board.pair_count}: a deal needs a pair.")
    if board.pair_count > board.symbols:
        report.solvable = False
        report.reasons.append(
            f"pair_count is {board.pair_count} but the deck has {board.symbols} "
            f"symbols, so the game deals only {board.symbols} pairs and the board "
            "doesn't match its settings."
        )
    pairs = report.shortest
    if board.move_limit and board.move_limit < pairs:
        report.solvable = False
        report.reasons.append(
            f"The move limit is {board.move_limit} but clearing {pairs} pairs takes "
            f"at least {pairs} moves: no deal can be won."
        )
    results: list[int] = []
    branching: list[float] = []
    for seed in range(1, seeds + 1):
        moves, choice = memory_moves(deal(seed, pairs, board.symbols))
        results.append(moves)
        branching.append(choice)
        stars = stars_for(moves, pairs)
        report.stars[stars] = report.stars.get(stars, 0) + 1
    report.moves_min = min(results)
    report.moves_max = max(results)
    report.moves_mean = sum(results) / len(results)
    report.branching = sum(branching) / len(branching)
    if board.move_limit:
        report.dead_deals = sum(1 for m in results if m > board.move_limit)
    share = report.dead_deals / seeds
    if report.solvable and share > 0.5:
        report.notes.append(
            f"{report.dead_deals} of {seeds} deals can't be cleared within "
            f"{board.move_limit} moves even with a perfect memory: winning is mostly "
            "luck. Raise move_limit or deal fewer pairs."
        )
    elif report.solvable and report.dead_deals:
        report.notes.append(
            f"{report.dead_deals} of {seeds} deals need some luck: a perfect memory "
            f"takes up to {report.moves_max} moves against a limit of {board.move_limit}."
        )
    if board.columns > 0 and (pairs * 2) % board.columns:
        report.notes.append(
            f"{pairs * 2} cards in {board.columns} columns leave the last row short."
        )
    slack = (board.move_limit - report.moves_mean) if board.move_limit else 99.0
    if share > 0.5 or slack < 2:
        report.difficulty = "hard"
    elif slack < pairs or pairs >= 10:
        report.difficulty = "medium"
    else:
        report.difficulty = "easy"
    return report
