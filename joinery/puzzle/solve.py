"""Solve a grid puzzle level by searching its states, and estimate how hard it is.

A state is where the player stands, where every box is, which keys have been picked
up and which doors opened. The search is breadth first over single moves, so the
first win it meets is a shortest one (in moves; pushes are counted too). It keeps
going until every reachable state is known or it reaches ``MAX_STATES``; with the
whole space known it also counts the dead ends: states the player can get into from
the start from which the level can no longer be won (a box pushed into a corner, a
key spent on the wrong door). A box pushed onto a square from which it can never
reach a goal is such a state and is not searched further.

Difficulty is an estimate from those numbers (``rate``): how long the shortest win
is, what share of reachable states are dead ends, and how many moves each state
offers. It says what kind of level this is; it doesn't say whether it is fun.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Literal

from joinery.puzzle.grid import Cell, Level, where

#: States searched at most; beyond it the answer may be "unknown".
MAX_STATES = 200_000
DIRECTIONS: tuple[tuple[str, int, int], ...] = (
    ("u", 0, -1),
    ("d", 0, 1),
    ("l", -1, 0),
    ("r", 1, 0),
)
Difficulty = Literal["easy", "medium", "hard"]

#: player, boxes (sorted cell indices), keys picked up (bits), doors opened (bits)
State = tuple[int, tuple[int, ...], int, int]


@dataclass
class Solution:
    """What the search found for one level."""

    name: str
    #: True, False, or None when the search stopped at its bound without an answer.
    solvable: bool | None
    #: The shortest win in moves (u d l r, upper case for a push); "" when none.
    moves: str = ""
    states: int = 0
    #: Every reachable state was searched (so dead ends are counted exactly).
    complete: bool = False
    dead_ends: int = 0
    #: Moves on offer per searched state.
    branching: float = 0.0
    difficulty: Difficulty | None = None
    #: Why the level can't be won (or why the answer is unknown), in words.
    reasons: list[str] = field(default_factory=list)
    #: Things worth a look that don't stop the level being won.
    notes: list[str] = field(default_factory=list)
    move_limit: int = 0

    @property
    def length(self) -> int:
        return len(self.moves)

    @property
    def pushes(self) -> int:
        return sum(1 for move in self.moves if move.isupper())

    @property
    def dead_share(self) -> float:
        return self.dead_ends / self.states if self.states else 0.0

    def summary(self) -> str:
        if self.solvable is None:
            return f"{self.name}: unknown ({self.reasons[0] if self.reasons else ''})"
        if not self.solvable:
            return f"{self.name}: unsolvable: {self.reasons[0] if self.reasons else ''}"
        dead = (
            f"{self.dead_ends} dead ends of {self.states} states"
            if self.complete
            else f"{self.states}+ states (dead ends not counted)"
        )
        return (
            f"{self.name}: solvable in {self.length} moves ({self.pushes} pushes), "
            f"{dead}, {self.branching:.1f} moves per state: {self.difficulty}"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "solvable": self.solvable,
            "shortest": self.length if self.solvable else None,
            "pushes": self.pushes if self.solvable else None,
            "solution": self.moves,
            "states": self.states,
            "complete": self.complete,
            "dead_ends": self.dead_ends if self.complete else None,
            "dead_end_share": round(self.dead_share, 3) if self.complete else None,
            "branching": round(self.branching, 2),
            "difficulty": self.difficulty,
            "move_limit": self.move_limit,
            "reasons": self.reasons,
            "notes": self.notes,
            "summary": self.summary(),
        }


def rate(length: int, dead_share: float | None, branching: float) -> Difficulty:
    """A rough difficulty from the shortest win, the share of dead ends and the
    moves per state: long wins and many traps make a level hard."""
    trap = dead_share or 0.0
    score = length / 20 + trap * 4 + max(branching - 2.0, 0.0) * 0.25
    if score < 1.5:
        return "easy"
    if score < 3.5:
        return "medium"
    return "hard"


class _Board:
    """The level's fixed parts as flat lists, for a fast search."""

    def __init__(self, level: Level) -> None:
        self.level = level
        self.w = level.width
        self.h = level.height
        size = self.w * self.h
        self.wall = [False] * size
        for x, y in level.walls:
            self.wall[self.at((x, y))] = True
        self.goals = frozenset(self.at(c) for c in level.goals)
        self.exits = frozenset(self.at(c) for c in level.exits)
        self.switches = tuple(self.at(c) for c in sorted(level.switches))
        self.gates = frozenset(self.at(c) for c in level.gates)
        key_cells = sorted(level.keys)
        door_cells = sorted(level.doors)
        self.key_bit = {self.at(c): 1 << i for i, c in enumerate(key_cells)}
        self.key_letter = [level.keys[c] for c in key_cells]
        self.door_bit = {self.at(c): 1 << i for i, c in enumerate(door_cells)}
        self.door_letter = [level.doors[c] for c in door_cells]
        self.kept = level.keys_mode == "kept"
        self.live = self._live_squares()

    def at(self, cell: Cell) -> int:
        return cell[1] * self.w + cell[0]

    def cell(self, index: int) -> Cell:
        return (index % self.w, index // self.w)

    def step(self, index: int, dx: int, dy: int) -> int | None:
        x, y = index % self.w + dx, index // self.w + dy
        if 0 <= x < self.w and 0 <= y < self.h:
            return y * self.w + x
        return None

    def _live_squares(self) -> list[bool]:
        """Squares from which a box can still be pushed to a goal (doors and keys
        counted as open floor: an optimistic answer, so nothing live is called dead)."""
        size = self.w * self.h
        if not self.goals:
            return [True] * size
        live = [False] * size
        queue = deque(self.goals)
        for goal in self.goals:
            live[goal] = True
        while queue:
            box = queue.popleft()
            for _, dx, dy in DIRECTIONS:
                to = self.step(box, dx, dy)
                stand = self.step(box, 2 * dx, 2 * dy)
                if to is None or stand is None or live[to]:
                    continue
                if self._box_blocked(to) or self.wall[stand]:
                    continue
                live[to] = True
                queue.append(to)
        return live

    def _box_blocked(self, index: int) -> bool:
        return self.wall[index] or index in self.gates

    def held(self, letter: str, got: int, opened: int) -> bool:
        have = sum(
            1 for i, k in enumerate(self.key_letter) if k == letter and got >> i & 1
        )
        if self.kept:
            return have > 0
        spent = sum(
            1 for i, d in enumerate(self.door_letter) if d == letter and opened >> i & 1
        )
        return have > spent

    def pressed(self, player: int, boxes: tuple[int, ...]) -> bool:
        return all(s == player or s in boxes for s in self.switches)

    def won(self, state: State) -> bool:
        player, boxes, _, _ = state
        if not self.goals <= set(boxes):
            return False
        return not self.exits or player in self.exits

    def moves(self, state: State) -> list[tuple[str, State]]:
        """Every move from ``state``: (u d l r, upper case for a push; the new state)."""
        player, boxes, got, opened = state
        out: list[tuple[str, State]] = []
        for name, dx, dy in DIRECTIONS:
            to = self.step(player, dx, dy)
            if to is None or self.wall[to]:
                continue
            new_got, new_opened = got, opened
            door = self.door_bit.get(to)
            if door is not None and not opened & door:
                letter = self.door_letter[door.bit_length() - 1]
                if not self.held(letter, got, opened):
                    continue
                new_opened = opened | door
            new_boxes = boxes
            pushed = False
            if to in boxes:
                beyond = self.step(to, dx, dy)
                if beyond is None or not self._box_may_enter(
                    beyond, boxes, got, opened
                ):
                    continue
                new_boxes = tuple(sorted(beyond if b == to else b for b in boxes))
                pushed = True
            if to in self.gates and not self.pressed(to, new_boxes):
                continue
            key = self.key_bit.get(to)
            if key is not None:
                new_got |= key
            out.append(
                (name.upper() if pushed else name, (to, new_boxes, new_got, new_opened))
            )
        return out

    def _box_may_enter(
        self, index: int, boxes: tuple[int, ...], got: int, opened: int
    ) -> bool:
        if self._box_blocked(index) or index in boxes:
            return False
        key = self.key_bit.get(index)
        if key is not None and not got & key:
            return False
        door = self.door_bit.get(index)
        return door is None or bool(opened & door)

    def dead(self, boxes: tuple[int, ...]) -> bool:
        return any(not self.live[b] for b in boxes if b not in self.goals) and bool(
            self.goals
        )


def _static_reasons(level: Level, board: _Board) -> tuple[list[str], list[str]]:
    """Why a level can't be won, seen without searching: (certain reasons, hints)."""
    fatal: list[str] = []
    hints: list[str] = []
    if len(level.boxes) < len(level.goals):
        fatal.append(
            f"There are {len(level.goals)} goals but only {len(level.boxes)} "
            f"{'box' if len(level.boxes) == 1 else 'boxes'}: "
            "every goal needs a box on it."
        )
    letters = set(level.keys.values())
    for cell, letter in sorted(level.doors.items()):
        if letter not in letters:
            hints.append(
                f"Door {letter.upper()} at {where(cell)} has no key {letter} anywhere "
                "in the level."
            )
    # Where the player could ever stand: through every door that has a key, every
    # gate, over keys and boxes.
    seen = {board.at(level.player)}
    queue = deque(seen)
    while queue:
        here = queue.popleft()
        for _, dx, dy in DIRECTIONS:
            to = board.step(here, dx, dy)
            if to is None or to in seen or board.wall[to]:
                continue
            door = board.door_bit.get(to)
            if (
                door is not None
                and board.door_letter[door.bit_length() - 1] not in letters
            ):
                continue
            seen.add(to)
            queue.append(to)
    if level.exits and all(board.at(c) not in seen for c in level.exits):
        cells = ", ".join(where(c) for c in sorted(level.exits))
        fatal.append(f"The exit ({cells}) is walled off from the player.")
    boxed = {board.at(b) for b in level.boxes}
    for cell in sorted(level.goals):
        index = board.at(cell)
        if index not in seen and index not in boxed:
            fatal.append(f"The goal at {where(cell)} is walled off from the player.")
    stuck = [b for b in level.boxes if not board.live[board.at(b)]]
    if level.goals and len(level.boxes) - len(stuck) < len(level.goals):
        for cell in stuck:
            fatal.append(
                f"The box at {where(cell)} is against a wall where it can never be "
                "pushed onto a goal."
            )
    return fatal, hints


def replay(level: Level, moves: str) -> bool:
    """Play ``moves`` (u d l r; case ignored) from the start: does it win?"""
    board = _Board(level)
    state: State = (
        board.at(level.player),
        tuple(sorted(board.at(b) for b in level.boxes)),
        0,
        0,
    )
    for move in moves.lower():
        options = {name.lower(): after for name, after in board.moves(state)}
        if move not in options:
            return False
        state = options[move]
    return board.won(state)


def solve(level: Level, max_states: int = MAX_STATES) -> Solution:
    """Search ``level``'s states; see the module docstring."""
    board = _Board(level)
    result = Solution(level.name, None, move_limit=level.move_limit)
    fatal, hints = _static_reasons(level, board)
    start: State = (
        board.at(level.player),
        tuple(sorted(board.at(b) for b in level.boxes)),
        0,
        0,
    )
    index: dict[State, int] = {start: 0}
    states: list[State] = [start]
    parent: list[tuple[int, str]] = [(-1, "")]
    preds: list[list[int]] = [[]]
    wins: list[int] = []
    edges = 0
    expanded = 0
    complete = True
    best_on_goals = 0
    queue: deque[int] = deque([0])
    while queue:
        here = queue.popleft()
        state = states[here]
        on_goals = sum(1 for b in state[1] if b in board.goals)
        best_on_goals = max(best_on_goals, on_goals)
        if board.won(state):
            wins.append(here)
            continue
        if board.dead(state[1]):
            continue
        expanded += 1
        for move, after in board.moves(state):
            edges += 1
            known = index.get(after)
            if known is None:
                if len(states) >= max_states:
                    complete = False
                    continue
                known = len(states)
                index[after] = known
                states.append(after)
                parent.append((here, move))
                preds.append([])
                queue.append(known)
            preds[known].append(here)
    result.states = len(states)
    result.complete = complete
    result.branching = edges / expanded if expanded else 0.0
    if wins:
        first = wins[0]
        path: list[str] = []
        while parent[first][0] >= 0:
            path.append(parent[first][1])
            first = parent[first][0]
        result.moves = "".join(reversed(path))
        result.solvable = True
    if complete:
        can_win = [False] * len(states)
        back = deque(wins)
        for win in wins:
            can_win[win] = True
        while back:
            here = back.popleft()
            for before in preds[here]:
                if not can_win[before]:
                    can_win[before] = True
                    back.append(before)
        result.dead_ends = sum(1 for ok in can_win if not ok)
    if result.solvable:
        result.difficulty = rate(
            result.length, result.dead_share if complete else None, result.branching
        )
        if level.move_limit and result.length > level.move_limit:
            result.solvable = False
            result.reasons.append(
                f"The shortest win takes {result.length} moves but the level allows "
                f"{level.move_limit}."
            )
        result.notes += hints
        if complete and result.dead_share >= 0.5:
            result.notes.append(
                f"{result.dead_ends} of {result.states} reachable states are dead ends: "
                "most mistakes can't be undone, so the player will restart often."
            )
        return result
    if complete:
        result.solvable = False
        closest = []
        if level.goals:
            closest.append(
                f"the closest got {best_on_goals} of {len(level.goals)} boxes onto goals"
            )
        if level.exits:
            closest.append("the exit stayed out of reach")
        result.reasons += fatal or [
            *hints,
            (
                f"No sequence of moves wins: all {result.states} reachable states "
                f"were tried; {' and '.join(closest)}."
            ),
        ]
        return result
    if fatal:
        result.solvable = False
        result.reasons += fatal
        return result
    result.reasons.append(
        f"Too many states to decide: stopped after {max_states} without finding a win."
    )
    return result
