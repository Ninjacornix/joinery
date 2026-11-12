"""Grown levels in stock Godot (ADR 0069): a level grown from a hand-drawn sample is
built into a game made from its starting point, loads with collision and a floor under
the spawn, and the playtest bot walks from the spawn to every goal."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from joinery import blockout, playtest
from joinery.blockout import grow
from tests.blueprints.godot_run import (
    import_project,
    make_project,
    needs_godot,
    run_godot,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "blockout"
#: (starting point, sample, seed)
CASES = [
    ("third-person-3d", "rooms_sample", 3),
    ("top-down", "rooms_sample", 5),
    ("platformer-2d", "ledges_sample", 2),
]


@needs_godot
@pytest.mark.parametrize(("blueprint", "sample", "seed"), CASES)
def test_a_grown_level_loads_and_the_bot_walks_to_every_goal(
    tmp_path: Path, blueprint: str, sample: str, seed: int
) -> None:
    project = make_project(tmp_path, blueprint, [])
    made = grow.make(
        project,
        str(FIXTURES / f"{sample}.toml"),
        width=24,
        height=16,
        seed=seed,
        name="grown",
    )
    built = made.built
    assert built.ok, [p.text for p in built.checked.problems]
    for relative, data in built.files.items():
        target = project / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    import_project(project)
    checked = run_godot(
        project,
        str(FIXTURES / "scene_check.gd"),
        [f"--scene={built.scene}"],
        marker="blockout_check",
    )
    line = next(x for x in checked.output.splitlines() if x.startswith("BLOCKOUT "))
    found = json.loads(line[len("BLOCKOUT ") :])
    assert found["solids"] >= 4 and found["floor_under_spawn"], found
    markers = built.checked.world.markers
    assert found["groups"]["goal"] == sum(1 for m in markers if m.kind == "goal")
    route = tmp_path / "route.json"
    route.write_text(json.dumps(blockout.route(built)))
    report = playtest.run(
        playtest.Options(
            project=project,
            seconds=150,
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
