"""Puzzle solvability and difficulty (ADR 0069): the grid level format, the solver on
hand-made solvable and unsolvable levels, the memory-cards deals, the check on a
staged change and ``joinery puzzle check``."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from joinery import blueprints
from joinery.cli import main
from joinery.puzzle import cards
from joinery.puzzle import check as puzzle_check
from joinery.puzzle.grid import PuzzleError, parse
from joinery.puzzle.solve import rate, replay, solve
from tests.test_submit_for_review import Session

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "puzzles"
CARDS = blueprints.BLUEPRINTS_DIR / "puzzle-cards" / "overlay"
#: Shortest wins of the hand-made solvable levels, counted by hand.
SHORTEST = {
    "first_push": 3,
    "key_and_door": 8,
    "hold_the_switch": 7,
    "two_boxes": 18,
    "kept_key": 8,
}
#: Why each hand-made unsolvable level can't be won.
WHY = {
    "box_in_corner": "against a wall where it can never be pushed onto a goal",
    "too_few_boxes": "2 goals but only 1 box",
    "walled_exit": "walled off from the player",
    "one_key_two_doors": "No sequence of moves wins",
    "switch_needs_a_box": "No sequence of moves wins",
    "too_tight": "takes 3 moves but the level allows 2",
}
#: Deals Godot's own Deck.deal(seed, 8) gave (deck.gd run in Godot 4.7).
GODOT_DEALS = {
    0: [6, 3, 2, 7, 4, 5, 2, 5, 6, 3, 0, 4, 0, 1, 1, 7],
    1: [5, 6, 0, 1, 3, 0, 3, 7, 5, 6, 2, 7, 4, 2, 1, 4],
    17: [2, 5, 3, 7, 5, 2, 0, 1, 0, 1, 7, 4, 4, 3, 6, 6],
    123456: [0, 6, 3, 1, 4, 5, 3, 0, 2, 5, 1, 6, 2, 4, 7, 7],
    2147483647: [4, 6, 6, 2, 7, 2, 5, 1, 3, 1, 7, 0, 3, 0, 5, 4],
}
BIG = """
map = '''
  #####
###   #
#.@$  #
### $.#
#.##$ #
# # . ##
#$ *$$.#
#   .  #
########
'''
"""


def _levels(name: str) -> dict[str, object]:
    text = (FIXTURES / f"{name}.toml").read_text(encoding="utf-8")
    return {level.name: level for level in parse(text)}


# --- the format -------------------------------------------------------------------


def test_a_map_reads_every_kind_of_cell() -> None:
    (level,) = parse(
        "map = '''\n#####\n#@$.#\n#a A#\n#_=>#\n#####\n'''\nmove_limit = 9\n"
    )
    assert (level.width, level.height) == (5, 5)
    assert level.player == (1, 1) and level.boxes == ((2, 1),)
    assert level.goals == {(3, 1)} and level.exits == {(3, 3)}
    assert level.keys == {(1, 2): "a"} and level.doors == {(3, 2): "a"}
    assert level.switches == {(1, 3)} and level.gates == {(2, 3)}
    assert level.move_limit == 9 and level.keys_mode == "consumed"


@pytest.mark.parametrize(
    ("text", "said"),
    [
        ("map = '''\n#####\n#  .#\n#####\n'''", "exactly one player"),
        ("map = '''\n#####\n#@  #\n#####\n'''", "nothing to win"),
        ("map = '''\n#####\n#@?>#\n#####\n'''", "not in the legend"),
        ("map = '''\n#####\n#@=>#\n#####\n'''", "need at least one switch"),
        ("name = 'x'\n[[levels]]\nmap = '#@>#'", ""),
        ("version = 2\nmap = '#@>#'", "only version 1"),
        ("map = '#@>#'\nkeys = 'lost'", "consumed"),
    ],
)
def test_a_broken_level_says_what_to_fix(text: str, said: str) -> None:
    if not said:
        assert parse(text)[0].name == "level_1"
        return
    with pytest.raises(PuzzleError) as caught:
        parse(text)
    assert said in str(caught.value)


# --- the solver -------------------------------------------------------------------


def test_hand_made_levels_are_solved_in_their_shortest_number_of_moves() -> None:
    for name, level in _levels("solvable").items():
        solution = solve(level)  # type: ignore[arg-type]
        assert solution.solvable is True, (name, solution.reasons)
        assert solution.length == SHORTEST[name], (name, solution.moves)
        assert replay(level, solution.moves), name  # type: ignore[arg-type]
        assert not replay(level, solution.moves[:-1]), name  # type: ignore[arg-type]
        assert solution.complete and solution.difficulty in {"easy", "medium", "hard"}
        assert solution.pushes == sum(1 for m in solution.moves if m.isupper())


def test_hand_made_unsolvable_levels_say_why() -> None:
    levels = _levels("unsolvable")
    assert set(levels) == set(WHY)
    for name, level in levels.items():
        solution = solve(level)  # type: ignore[arg-type]
        assert solution.solvable is False, name
        assert any(WHY[name] in reason for reason in solution.reasons), (
            name,
            solution.reasons,
        )


def test_solving_is_deterministic_and_counts_dead_ends() -> None:
    level = _levels("solvable")["two_boxes"]
    first = solve(level).as_dict()  # type: ignore[arg-type]
    assert first == solve(level).as_dict()  # type: ignore[arg-type]
    assert first["dead_ends"] > 0 and 0 < first["dead_end_share"] < 1
    assert first["branching"] > 1
    # A corridor with nothing to get wrong has no dead ends.
    assert solve(_levels("solvable")["first_push"]).dead_ends == 0  # type: ignore[arg-type]


def test_the_search_is_bounded() -> None:
    (level,) = parse(BIG)
    small = solve(level, max_states=300)
    assert small.solvable is None and not small.complete
    assert "Too many states" in small.reasons[0]
    assert small.states == 300
    whole = solve(level)
    assert whole.solvable is True and replay(level, whole.moves)


def test_difficulty_grows_with_length_and_traps() -> None:
    assert rate(5, 0.0, 2.0) == "easy"
    assert rate(40, 0.3, 2.5) == "medium"
    assert rate(90, 0.7, 3.0) == "hard"
    assert rate(10, 0.9, 2.0) != "easy"


# --- memory cards -----------------------------------------------------------------


def test_deals_match_godot_exactly() -> None:
    for seed, layout in GODOT_DEALS.items():
        assert cards.deal(seed, 8) == layout, seed


def test_a_perfect_memory_clears_any_deal_in_bounded_moves() -> None:
    for seed in range(1, 60):
        layout = cards.deal(seed, 8)
        assert sorted(layout) == sorted(list(range(8)) * 2)
        moves, choices = cards.memory_moves(layout)
        assert 8 <= moves <= 15 and choices > 0
    # Pairs laid side by side: every second flip matches.
    assert cards.memory_moves([0, 0, 1, 1, 2, 2])[0] == 3


def test_the_blueprints_deals_are_solvable_and_easy() -> None:
    board = cards.read_board(CARDS)
    assert board == cards.Board(pair_count=8, columns=4, move_limit=30, symbols=12)
    report = cards.check(board)
    assert report.solvable and report.shortest == 8 and report.dead_deals == 0
    assert report.moves_max <= 30 and report.difficulty == "easy"
    assert sum(report.stars.values()) == cards.SAMPLE
    assert "200 of 200 deals within 30 moves" in report.summary()


@pytest.mark.parametrize(
    ("change", "said"),
    [
        ({"move_limit": 6}, "no deal can be won"),
        ({"pair_count": 14}, "the deck has 12 symbols"),
    ],
)
def test_impossible_card_settings_are_unsolvable(
    change: dict[str, int], said: str
) -> None:
    board = cards.Board(pair_count=8, columns=4, move_limit=30, symbols=12)
    for key, value in change.items():
        setattr(board, key, value)
    report = cards.check(board, seeds=20)
    assert not report.solvable and said in report.reasons[0]


def test_a_tight_limit_is_a_luck_warning_not_unsolvable() -> None:
    report = cards.check(cards.Board(8, 4, 11, 12), seeds=50)
    assert report.solvable and report.dead_deals > 25
    assert "mostly luck" in report.notes[0] and report.difficulty == "hard"


# --- the check on a change, and the command ---------------------------------------


def _cards_game(root: Path, move_limit: int | None = None) -> None:
    shutil.copytree(CARDS / "game", root / "game")
    if move_limit is not None:
        scene = root / "game/level.tscn"
        text = scene.read_text(encoding="utf-8")
        scene.write_text(
            text.replace(
                'script = ExtResource("1_level")\n',
                f'script = ExtResource("1_level")\nmove_limit = {move_limit}\n',
                1,
            ),
            encoding="utf-8",
        )


def test_a_staged_unsolvable_level_is_sent_back_with_the_reason(tmp_path: Path) -> None:
    folder = tmp_path / "design/puzzles"
    folder.mkdir(parents=True)
    shutil.copy(FIXTURES / "unsolvable.toml", folder / "pack.toml")
    shutil.copy(FIXTURES / "solvable.toml", folder / "good.toml")
    report = puzzle_check.staged(tmp_path, ["design/puzzles/pack.toml"])
    assert report is not None and not report.ok
    assert {lv["file"] for lv in report.levels} == {"design/puzzles/pack.toml"}
    errors = [f for f in report.findings if f.severity == "error"]
    assert len(errors) == len(WHY)
    rules = {f.reason().rule for f in errors}
    assert rules == {"puzzles.unsolvable", "puzzles.move_limit"}
    assert all(f.reason().file == "design/puzzles/pack.toml" for f in errors)
    assert puzzle_check.staged(tmp_path, ["design/puzzles/good.toml"]).ok  # type: ignore[union-attr]
    assert puzzle_check.staged(tmp_path, ["game/player.gd"]) is None


def test_a_staged_card_change_is_checked(tmp_path: Path) -> None:
    _cards_game(tmp_path)
    fine = puzzle_check.staged(tmp_path, ["game/level.tscn"])
    assert fine is not None and fine.ok and fine.cards is not None
    _cards_game(tmp_path / "tight", move_limit=5)
    tight = puzzle_check.staged(tmp_path / "tight", ["game/level.tscn"])
    assert tight is not None and not tight.ok
    (finding,) = [f for f in tight.findings if f.severity == "error"]
    assert (
        finding.rule == "puzzles.cards_unsolvable" and finding.file == "game/level.tscn"
    )
    # Another game's level scene isn't a card game.
    other = tmp_path / "other"
    (other / "game").mkdir(parents=True)
    (other / "game/level.tscn").write_text("[gd_scene format=3]\n")
    assert puzzle_check.staged(other, ["game/level.tscn"]) is None


def test_precheck_sends_back_an_unsolvable_level(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = Session(tmp_path, monkeypatch)
    session.turn.begin("Add a puzzle level")
    session.stage(
        {"design/puzzles/corner.toml": "map = '''\n#####\n#$  #\n# @.#\n#####\n'''\n"}
    )
    checked = session.run(session.turn.precheck)
    assert checked["status"] == "problems"
    (problem,) = [p for p in checked["problems"] if p["check"] == "puzzles"]
    assert "can never be pushed onto a goal" in problem["message"]
    assert problem["items"][0]["rule"] == "puzzles.unsolvable"
    assert "puzzles" in checked["ran"]


def test_puzzle_check_command(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = tmp_path / "design/puzzles"
    folder.mkdir(parents=True)
    shutil.copy(FIXTURES / "solvable.toml", folder / "good.toml")
    _cards_game(tmp_path)
    assert main(["puzzle", "check", "--project", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "first_push: solvable in 3 moves" in out and "Memory cards: 8 pairs" in out
    shutil.copy(FIXTURES / "unsolvable.toml", folder / "bad.toml")
    assert main(["puzzle", "check", "--project", str(tmp_path), "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert not data["ok"] and len(data["levels"]) == len(SHORTEST) + len(WHY)
    assert data["cards"]["solvable"] is True
    assert (
        main(["puzzle", "check", str(folder / "good.toml"), "--project", str(tmp_path)])
        == 0
    )
