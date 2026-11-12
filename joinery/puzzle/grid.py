"""The grid puzzle level format: a map drawn in text, one character per cell.

A file in ``design/puzzles/`` holds one level (a top-level ``map``) or several
(``[[levels]]``, each with its own ``name`` and ``map``)::

    version = 1
    name = "first_push"
    move_limit = 0          # optional: the most moves the level allows (0: none)
    keys = "consumed"       # or "kept": a key opens every door of its letter
    map = '''
    #######
    #@ $ .#
    #######
    '''

Legend (``LEGEND``): ``#`` wall, space or ``-`` floor, ``@`` the player (``+`` on a
goal), ``$`` a box (``*`` on a goal), ``.`` a goal a box must cover, ``>`` an exit
the player must reach, ``a``-``z`` a key, ``A``-``Z`` the door its key opens, ``_``
a switch (a pressure plate) and ``=`` a gate that is open while every switch is held
down by the player or a box. The level is won when every goal has a box on it and,
if there are exits, the player stands on one.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from typing import Any, Literal

Cell = tuple[int, int]
LEGEND: dict[str, str] = {
    "#": "wall",
    " ": "floor",
    "-": "floor",
    "@": "player",
    "+": "player on a goal",
    "$": "box",
    "*": "box on a goal",
    ".": "goal",
    ">": "exit",
    "a-z": "key",
    "A-Z": "door (opened by the key of its letter)",
    "_": "switch",
    "=": "gate (open while every switch is held down)",
}
#: The most cells a level may have; larger maps are not hand-made puzzle levels.
MAX_CELLS = 64 * 64
NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_ .-]{0,47}$")


class PuzzleError(ValueError):
    """A level that can't be read: one line per problem."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass
class Level:
    """One grid puzzle level, read from its map."""

    name: str
    width: int
    height: int
    walls: frozenset[Cell]
    player: Cell
    boxes: tuple[Cell, ...]
    goals: frozenset[Cell]
    exits: frozenset[Cell]
    #: Key cell -> its letter (lower case).
    keys: dict[Cell, str] = field(default_factory=dict)
    #: Door cell -> the letter of the key that opens it (lower case).
    doors: dict[Cell, str] = field(default_factory=dict)
    switches: frozenset[Cell] = frozenset()
    gates: frozenset[Cell] = frozenset()
    keys_mode: Literal["consumed", "kept"] = "consumed"
    move_limit: int = 0

    def inside(self, cell: Cell) -> bool:
        return 0 <= cell[0] < self.width and 0 <= cell[1] < self.height


def where(cell: Cell) -> str:
    """A cell in words: column and row, counted from 1 at the top left."""
    return f"column {cell[0] + 1}, row {cell[1] + 1}"


def _rows(text: str) -> list[str]:
    lines = text.split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return [line.rstrip() for line in lines]


def read_level(data: dict[str, Any], fallback: str = "level") -> Level:
    """A level from one table (``name``, ``map``, ``move_limit``, ``keys``)."""
    problems: list[str] = []
    name = str(data.get("name", fallback) or fallback)
    if not NAME.match(name):
        problems.append(f"name {name!r}: letters, digits, spaces, _ . - (48 at most)")
    text = data.get("map")
    if not isinstance(text, str) or not text.strip():
        raise PuzzleError([f"{name}: map is missing (the level drawn in text)."])
    limit = data.get("move_limit", 0)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
        problems.append(f"{name}: move_limit must be a whole number, 0 for none.")
        limit = 0
    keys_mode = data.get("keys", "consumed")
    if keys_mode not in {"consumed", "kept"}:
        problems.append(f'{name}: keys is "consumed" or "kept".')
        keys_mode = "consumed"
    unknown = sorted(set(data) - {"name", "map", "move_limit", "keys"})
    if unknown:
        problems.append(f"{name}: unknown keys {', '.join(unknown)}.")
    rows = _rows(text)
    height = len(rows)
    width = max((len(row) for row in rows), default=0)
    if width * height > MAX_CELLS:
        problems.append(
            f"{name}: the map is {width}x{height}; at most {MAX_CELLS} cells."
        )
    walls: set[Cell] = set()
    players: list[Cell] = []
    boxes: list[Cell] = []
    goals: set[Cell] = set()
    exits: set[Cell] = set()
    keys: dict[Cell, str] = {}
    doors: dict[Cell, str] = {}
    switches: set[Cell] = set()
    gates: set[Cell] = set()
    for y, row in enumerate(rows):
        for x, char in enumerate(row.ljust(width)):
            cell = (x, y)
            if char == "#":
                walls.add(cell)
            elif char in " -":
                pass
            elif char in "@+":
                players.append(cell)
                if char == "+":
                    goals.add(cell)
            elif char in "$*":
                boxes.append(cell)
                if char == "*":
                    goals.add(cell)
            elif char == ".":
                goals.add(cell)
            elif char == ">":
                exits.add(cell)
            elif char == "_":
                switches.add(cell)
            elif char == "=":
                gates.add(cell)
            elif "a" <= char <= "z":
                keys[cell] = char
            elif "A" <= char <= "Z":
                doors[cell] = char.lower()
            else:
                problems.append(
                    f"{name}: {char!r} at {where(cell)} is not in the legend "
                    "(# wall, space floor, @ player, $ box, . goal, > exit, a-z key, "
                    "A-Z door, _ switch, = gate)."
                )
    if len(players) != 1:
        problems.append(
            f"{name}: the map needs exactly one player (@ or +); it has {len(players)}."
        )
    if not goals and not exits:
        problems.append(f"{name}: nothing to win: add a goal (.) or an exit (>).")
    if gates and not switches:
        problems.append(f"{name}: gates (=) need at least one switch (_).")
    if problems:
        raise PuzzleError(problems)
    return Level(
        name=name,
        width=width,
        height=height,
        walls=frozenset(walls),
        player=players[0],
        boxes=tuple(sorted(boxes)),
        goals=frozenset(goals),
        exits=frozenset(exits),
        keys=keys,
        doors=doors,
        switches=frozenset(switches),
        gates=frozenset(gates),
        keys_mode=keys_mode,
        move_limit=limit,
    )


def parse(text: str, fallback: str = "level") -> list[Level]:
    """Every level in a puzzle file (TOML text)."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise PuzzleError([f"The puzzle file is not valid TOML: {exc}"]) from None
    version = data.pop("version", 1)
    if version != 1:
        raise PuzzleError([f"version {version!r}: only version 1 is known."])
    tables = data.pop("levels", None)
    if tables is None:
        return [read_level(data, fallback)]
    if data.keys() - {"name"}:
        raise PuzzleError(
            ["A file with [[levels]] keeps each map inside its level, not at the top."]
        )
    if not isinstance(tables, list) or not tables:
        raise PuzzleError(["levels must be a list of tables ([[levels]])."])
    levels: list[Level] = []
    problems: list[str] = []
    for index, table in enumerate(tables):
        if not isinstance(table, dict):
            problems.append(f"levels[{index}] is not a table.")
            continue
        try:
            levels.append(read_level(table, f"{fallback}_{index + 1}"))
        except PuzzleError as exc:
            problems += exc.problems
    if problems:
        raise PuzzleError(problems)
    return levels
