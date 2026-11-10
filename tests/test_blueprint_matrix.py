"""Every blueprint, alone, with each feature and with all of them, played in Godot.

Each cell applies a blueprint and features into a fresh project through
`joinery.blueprints`, imports it in stock Godot (headless), boots its main menu,
runs the blueprint's play test and every applied feature's own headless test,
and, with online play, the two-process host and join test. See
`tests/blueprints/matrix.py` for the steps.

The Godot cells are slow (several minutes in all), so they carry the
`blueprint_matrix` marker and run only on request:

    JOINERY_BLUEPRINT_MATRIX=1 uv run pytest tests/test_blueprint_matrix.py
    uv run pytest -m blueprint_matrix

They skip with a reason when no Godot binary is found (`JOINERY_TEST_GODOT`,
then the stock binary under `.tools/`). With `JOINERY_MATRIX_REPORT=<dir>`
each cell writes `<cell>.json` there and the run writes `matrix.md`, the
pass/fail table with timings (`python -m tests.blueprints.matrix <dir>` prints
it again, also across parallel runs).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from joinery import blueprints
from tests.blueprints.godot_run import godot_binary
from tests.blueprints.matrix import (
    Cell,
    all_compatible,
    cells,
    load_results,
    render,
    run_cell,
    supported_features,
    write_result,
)

MARKER = "blueprint_matrix"
REPORT = os.environ.get("JOINERY_MATRIX_REPORT", "")
CELLS = cells()


def _requested(config: pytest.Config) -> bool:
    if os.environ.get("JOINERY_BLUEPRINT_MATRIX") == "1":
        return True
    expression = str(getattr(config.option, "markexpr", "") or "")
    return MARKER in expression and f"not {MARKER}" not in expression


# --- static: what the matrix covers --------------------------------------------


def test_matrix_covers_every_blueprint_and_feature() -> None:
    ids = {c.id for c in CELLS}
    for blueprint in blueprints.list_blueprints():
        assert Cell(blueprint.id, (), "alone").id in ids
        assert Cell(blueprint.id, all_compatible(blueprint.id), "all").id in ids
        for feature in supported_features(blueprint.id):
            assert Cell(blueprint.id, (feature,), "single").id in ids, feature
        left_out = set(supported_features(blueprint.id)) - set(all_compatible(blueprint.id))
        for feature in left_out:
            problems = blueprints.resolve(blueprint.id, [*all_compatible(blueprint.id), feature])
            assert problems.problems, f"{feature} left out of {blueprint.id}'s full set"
    assert len(ids) == len(CELLS), "every cell is distinct"


@pytest.mark.parametrize("cell", CELLS, ids=lambda c: c.id)
def test_cell_resolves_and_plans_statically(cell: Cell) -> None:
    assert blueprints.check_static(cell.blueprint, cell.features) == []


# --- in Godot ------------------------------------------------------------------


@pytest.fixture(scope="module")
def report_dir() -> Iterator[Path | None]:
    directory = Path(REPORT) if REPORT else None
    yield directory
    if directory is not None and directory.is_dir():
        (directory / "matrix.md").write_text(render(load_results(directory)), encoding="utf-8")


@pytest.mark.blueprint_matrix
@pytest.mark.skipif(godot_binary() is None, reason="needs a Godot 4.7 binary (JOINERY_TEST_GODOT)")
@pytest.mark.parametrize("cell", CELLS, ids=lambda c: c.id)
def test_cell_plays_in_godot(
    cell: Cell, tmp_path: Path, request: pytest.FixtureRequest, report_dir: Path | None
) -> None:
    if not _requested(request.config):
        pytest.skip(
            "the Godot matrix runs on request: JOINERY_BLUEPRINT_MATRIX=1 or -m blueprint_matrix"
        )
    result = run_cell(cell, tmp_path)
    if report_dir is not None:
        write_result(result, report_dir)
    failures = result.failures()
    assert not failures, "\n\n".join(f"[{f.name}] {f.detail}" for f in failures)
