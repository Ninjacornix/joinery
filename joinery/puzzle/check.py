"""Check a project's puzzles: every grid level in ``design/puzzles/`` and, in a
memory-cards game, its deals.

``joinery puzzle check`` runs it on a project; the submit runs it on the staged
change (only the puzzle files it touches) and sends back a level that can't be won,
with the reason (``joinery.submission``). A level too large to decide within the
search bound is a warning, never a send-back.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from joinery.puzzle import cards
from joinery.puzzle.grid import PuzzleError, parse
from joinery.puzzle.solve import MAX_STATES, solve
from joinery.reasons import Reason

PUZZLE_DIR = "design/puzzles"
#: The memory-cards files whose change can change how its deals play.
CARD_FILES = frozenset({"game/level.tscn", "game/level.gd", "game/deck.gd"})
CARDS_FILE = "game/level.tscn"


@dataclass
class Finding:
    severity: Literal["error", "warning"]
    rule: str
    file: str
    text: str
    level: str = ""
    measured: str = ""
    expected: str = ""
    fix: str = ""

    def reason(self) -> Reason:
        return Reason(
            self.rule,
            self.file,
            node=self.level,
            measured=self.measured or self.text,
            expected=self.expected,
            fix=self.fix,
        )

    def as_dict(self) -> dict[str, str]:
        return {
            "severity": self.severity,
            "rule": self.rule,
            "file": self.file,
            "level": self.level,
            "text": self.text,
        }


@dataclass
class Report:
    levels: list[dict[str, Any]] = field(default_factory=list)
    cards: dict[str, Any] | None = None
    findings: list[Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(f.severity == "error" for f in self.findings)

    @property
    def checked(self) -> bool:
        return bool(self.levels) or self.cards is not None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "levels": self.levels,
            "cards": self.cards,
            "findings": [f.as_dict() for f in self.findings],
        }


def is_puzzle_file(path: str) -> bool:
    pure = PurePosixPath(path.replace("\\", "/").removeprefix("res://"))
    return (
        ".." not in pure.parts
        and pure.suffix == ".toml"
        and pure.parent == PurePosixPath(PUZZLE_DIR)
    )


def check_level_file(
    root: Path, relative: str, report: Report, max_states: int = MAX_STATES
) -> None:
    """Solve every level in one puzzle file and add what it found to ``report``."""
    try:
        text = (root / relative).read_text(encoding="utf-8")
        levels = parse(text, PurePosixPath(relative).stem)
    except OSError as exc:
        report.findings.append(
            Finding("error", "puzzles.unreadable", relative, f"Can't read it: {exc}")
        )
        return
    except PuzzleError as exc:
        for problem in exc.problems:
            report.findings.append(
                Finding(
                    "error",
                    "puzzles.unreadable",
                    relative,
                    problem,
                    expected="a level in the puzzle format (ADR 0069)",
                    fix="Fix the map or the field named; the legend is in the file "
                    "format's notes.",
                )
            )
        return
    for level in levels:
        solution = solve(level, max_states)
        data = solution.as_dict()
        data["file"] = relative
        report.levels.append(data)
        if solution.solvable is False:
            limited = bool(level.move_limit) and solution.moves != ""
            report.findings.append(
                Finding(
                    "error",
                    "puzzles.move_limit" if limited else "puzzles.unsolvable",
                    relative,
                    f"{level.name} can't be won: " + "; ".join(solution.reasons),
                    level=level.name,
                    measured="; ".join(solution.reasons),
                    expected=(
                        f"a win within {level.move_limit} moves"
                        if limited
                        else "a sequence of moves that wins"
                    ),
                    fix=(
                        f"Raise move_limit to at least {solution.length}, or shorten "
                        "the way."
                        if limited
                        else "Change the map so it can be won: open the way, add the "
                        "missing box or key, or move the box off the wall."
                    ),
                )
            )
        elif solution.solvable is None:
            report.findings.append(
                Finding(
                    "warning",
                    "puzzles.undecided",
                    relative,
                    f"{level.name}: " + "; ".join(solution.reasons),
                    level=level.name,
                )
            )
        for note in solution.notes:
            report.findings.append(
                Finding("warning", "puzzles.note", relative, f"{level.name}: {note}")
            )


def check_cards(root: Path, report: Report) -> None:
    board = cards.read_board(root)
    if board is None:
        return
    dealt = cards.check(board)
    report.cards = dealt.as_dict()
    if not dealt.solvable:
        report.findings.append(
            Finding(
                "error",
                "puzzles.cards_unsolvable",
                CARDS_FILE,
                "The memory cards can't be won: " + "; ".join(dealt.reasons),
                level="Level",
                measured="; ".join(dealt.reasons),
                expected=(
                    f"pair_count at most {board.symbols}, and a move_limit of at "
                    "least pair_count (or 0 for none)"
                ),
                fix="Set the Level node's pair_count and move_limit so every deal can "
                "be cleared.",
            )
        )
    for note in dealt.notes:
        report.findings.append(
            Finding("warning", "puzzles.cards_note", CARDS_FILE, note, level="Level")
        )


def check_project(root: Path, files: list[str] | None = None) -> Report:
    """Check the puzzles at ``root``: all of them, or only those ``files`` touch."""
    report = Report()
    folder = root / PUZZLE_DIR
    if files is None:
        puzzle_files = (
            sorted(
                p.relative_to(root).as_posix()
                for p in folder.glob("*.toml")
                if p.is_file()
            )
            if folder.is_dir()
            else []
        )
        card_change = True
    else:
        puzzle_files = sorted(
            f for f in files if is_puzzle_file(f) and (root / f).is_file()
        )
        card_change = any(f in CARD_FILES for f in files)
    for relative in puzzle_files:
        check_level_file(root, relative, report)
    if card_change:
        check_cards(root, report)
    return report


def staged(stage: Path, changed: list[str]) -> Report | None:
    """The check of a staged change, or None when it touches no puzzle."""
    if not any(is_puzzle_file(f) or f in CARD_FILES for f in changed):
        return None
    report = check_project(stage, changed)
    return report if report.checked or report.findings else None
