"""Blockouts in stock Godot (ADR 0043): each sample layout is built into a game made from its
starting point, the scene loads with collision and a floor under the spawn, and the
playtest bot walks from the spawn to every goal with the game's own player and controls."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from joinery import blockout, playtest
from tests.blueprints.godot_run import (
    import_project,
    make_project,
    needs_godot,
    run_godot,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "blockout"
#: (starting point, sample layout): the first-person keep climbs its stairs without a jump.
CASES = [
    ("third-person-3d", "keep"),
    ("first-person-3d", "house"),
    ("first-person-3d", "keep"),
    ("platformer-2d", "cliffs"),
    ("top-down", "dungeon"),
]


def _built(tmp_path: Path, blueprint: str, name: str) -> tuple[Path, blockout.Built]:
    project = make_project(tmp_path, blueprint, [])
    layout = (FIXTURES / f"{name}.toml").read_text(encoding="utf-8")
    built = blockout.make(project, layout)
    assert built.ok, [p.text for p in built.checked.problems]
    for relative, data in built.files.items():
        target = project / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return project, built


@needs_godot
@pytest.mark.parametrize(("blueprint", "name"), CASES)
def test_the_blockout_loads_with_collision_and_the_bot_walks_to_every_goal(
    tmp_path: Path, blueprint: str, name: str
) -> None:
    project, built = _built(tmp_path, blueprint, name)
    import_project(project)
    checked = run_godot(
        project,
        str(FIXTURES / "scene_check.gd"),
        [f"--scene={built.scene}"],
        marker="blockout_check",
    )
    line = next(x for x in checked.output.splitlines() if x.startswith("BLOCKOUT "))
    found = json.loads(line[len("BLOCKOUT ") :])
    assert found["solids"] >= 8 and found["floor_under_spawn"], found
    markers = built.checked.world.markers
    for kind in ("goal", "coin", "pickup", "checkpoint", "hazard"):
        assert found["groups"][kind] == sum(1 for m in markers if m.kind == kind), kind
    route = tmp_path / "route.json"
    route.write_text(json.dumps(blockout.route(built)))
    report = playtest.run(
        playtest.Options(
            project=project,
            seconds=120,
            seed=1,
            scene=built.scene,
            route=route,
            out=tmp_path / "walk",
        )
    )
    assert report["status"] == "played", report
    walked = report["route"]
    reach = built.checked.reach
    assert reach is not None
    assert [w["id"] for w in walked] == [t.id for t in reach.targets]
    assert all(w["reached"] for w in walked), walked
    serious = [i for i in report["items"] if i["severity"] in {"critical", "high"}]
    assert serious == [], serious
