"""The combination matrix: every blueprint with every feature subset.

Static checks run on every combination. Godot boots each blueprint alone, with
each single feature, and with all features (`JOINERY_COMBO_MATRIX=full` boots
every combination). The real catalogue is checked once it is complete; while
blueprints or features are still being written, those tests skip and say why.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from joinery import blueprints
from tests.blueprints.conftest import FixtureCatalogue
from tests.blueprints.godot_run import boot, make_project, needs_godot


def test_fixture_matrix_resolves_and_plans(
    fixture_catalogue: FixtureCatalogue, tmp_path: Path
) -> None:
    combos = blueprints.combinations()
    assert len(combos) == 2**5 + 2**4  # alpha supports all five, beta four
    refused = 0
    for blueprint_id, subset in combos:
        problems = blueprints.check_static(blueprint_id, subset)
        if problems:
            assert all("conflicts with" in p for p in problems), (blueprint_id, subset, problems)
            refused += 1
    # Every subset holding solo with net, or with lobby (which pulls net in).
    assert refused == 8 + 4 + 4
    project = make_project(tmp_path, "alpha")
    for _, subset in combos[:8]:
        planned = blueprints.plan(project, "alpha", subset)
        assert planned.conflicts == [] or all("conflicts with" in c for c in planned.conflicts)


def _real_catalogue_gaps() -> list[str]:
    """What the real catalogue is still missing (empty once it is complete)."""
    errors: list[str] = []
    try:
        listed = blueprints._load(errors)
    except ValueError as exc:  # pragma: no cover - _load(errors) does not raise
        return [str(exc)]
    gaps: list[str] = []
    if listed.core is None:
        gaps.append("the shared game core")
    if not listed.blueprints:
        gaps.append("any blueprint")
    wanted = {f for b in listed.blueprints.values() for f in b.default_features}
    gaps += [f"feature {f}" for f in sorted(wanted - set(listed.features))]
    return gaps


def _real_combos() -> list[tuple[str, tuple[str, ...]]]:
    if _real_catalogue_gaps():
        return []
    catalogue = blueprints._load([])
    if os.environ.get("JOINERY_COMBO_MATRIX") == "full":
        return [
            combo for combo in blueprints.combinations(catalogue)
            if not blueprints._resolve(catalogue, *combo).problems
        ]
    picked: list[tuple[str, tuple[str, ...]]] = []
    for blueprint_id in sorted(catalogue.blueprints):
        supported = tuple(
            sorted(f.id for f in catalogue.features.values() if f.supports(blueprint_id))
        )
        options = [(), *((f,) for f in supported), supported]
        for subset in dict.fromkeys(options):
            if not blueprints._resolve(catalogue, blueprint_id, subset).problems:
                picked.append((blueprint_id, subset))
    return picked


def test_real_catalogue_is_valid() -> None:
    gaps = _real_catalogue_gaps()
    if gaps:
        pytest.skip("catalogue still being built: missing " + ", ".join(gaps))
    assert blueprints.validate_catalogue() == []


def test_real_matrix_resolves_statically() -> None:
    gaps = _real_catalogue_gaps()
    if gaps:
        pytest.skip("catalogue still being built: missing " + ", ".join(gaps))
    combos = blueprints.combinations()
    clashes: list[str] = []
    for blueprint_id, subset in combos:
        problems = blueprints.check_static(blueprint_id, subset)
        clashes += [p for p in problems if "conflicts with" not in p]
    assert clashes == []


@needs_godot
@pytest.mark.parametrize(
    ("blueprint_id", "features"),
    _real_combos() or [pytest.param("", (), marks=pytest.mark.skip(reason="catalogue still being built"))],
    ids=lambda value: "+".join(value) if isinstance(value, tuple) else (value or "none"),
)
def test_real_combo_boots(blueprint_id: str, features: tuple[str, ...], tmp_path: Path) -> None:
    project = make_project(tmp_path, blueprint_id, features)
    boot(project, frames=120)
