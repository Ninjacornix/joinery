"""The memory-cards deals checked by Joinery (ADR 0069) are the deals the game makes:
`Deck.deal` run in stock Godot gives the same layouts as `joinery.puzzle.cards.deal`,
and a project made from the starting point passes `joinery puzzle check`."""

from __future__ import annotations

import json
from pathlib import Path

from joinery.puzzle import cards
from joinery.puzzle.check import check_project
from tests.blueprints.godot_run import make_project, needs_godot, run_godot

SEEDS = range(1, 41)
PAIRS = (1, 4, 8, 12)
SCRIPT = """extends SceneTree

const Deck := preload("res://game/deck.gd")


func _init() -> void:
	var found: Dictionary = {}
	for pairs: int in %s:
		for seed_value: int in range(%d, %d):
			found["%%d:%%d" %% [pairs, seed_value]] = Array(Deck.deal(seed_value, pairs))
	print("DEALS " + JSON.stringify(found))
	print("deals: ok")
	quit()
"""


def test_a_new_card_game_passes_the_puzzle_check(tmp_path: Path) -> None:
    project = make_project(tmp_path, "puzzle-cards")
    report = check_project(project)
    assert report.ok and report.cards is not None
    assert report.cards["solvable"] and report.cards["dead_deals"] == 0


@needs_godot
def test_godot_deals_the_cards_the_check_expects(tmp_path: Path) -> None:
    project = make_project(tmp_path, "puzzle-cards")
    (project / "deals_check.gd").write_text(
        SCRIPT % (list(PAIRS), SEEDS.start, SEEDS.stop), encoding="utf-8"
    )
    result = run_godot(project, "res://deals_check.gd", marker="deals")
    line = next(x for x in result.output.splitlines() if x.startswith("DEALS "))
    dealt = json.loads(line[len("DEALS ") :])
    for pairs in PAIRS:
        for seed in SEEDS:
            got = [int(v) for v in dealt[f"{pairs}:{seed}"]]
            assert got == cards.deal(seed, pairs), (pairs, seed)
