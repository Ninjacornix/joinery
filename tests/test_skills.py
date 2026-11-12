"""find_recipe: the project's accepted recipes, found by what they made (ADR 0064)."""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest

from joinery import skills
from joinery.catalog import ROOT
from joinery.cli import main
from joinery.gateway import ProjectGateway
from joinery.gateway_rpc import GatewayRPC, rpc
from joinery.roles import offered_tools
from joinery.transactions import FileTransaction


@pytest.fixture
def testbed(tmp_path: Path) -> Path:
    root = tmp_path / "testbed"
    shutil.copytree(
        ROOT / "testbed", root, ignore=shutil.ignore_patterns(".joinery", ".godot")
    )
    return root


def journal(
    root: Path, ident: str, summary: str, paths: list[str], state: str = "accepted"
) -> None:
    folder = root / ".joinery/transactions" / ident
    folder.mkdir(parents=True)
    (folder / "journal.json").write_text(
        json.dumps(
            {
                "state": state,
                "summary": summary,
                "changes": [
                    {"path": p, "before": "", "after": "x", "change": "created"}
                    for p in paths
                ],
            }
        )
    )


def best(root: Path, request: str) -> str:
    found = skills.find(root, request)["recipes"]
    return str(found[0]["recipe"]) if found else ""


def test_words_fold_case_plurals_and_code_names() -> None:
    assert skills.words("Creaky doors_creaked DoorCreak") == [
        "creak",
        "door",
        "creak",
        "door",
        "creak",
    ]
    assert "the" not in skills.words("the door")


def test_requests_find_what_the_recipes_made(testbed: Path) -> None:
    assert best(testbed, "a creaky gate sound") == "recipes/sfx_door.toml"
    assert best(testbed, "stone floor texture") == "recipes/material_stone.toml"
    assert best(testbed, "wooden chest with brass hinges") == "recipes/door.toml"
    assert best(testbed, "calm exploration music") == "recipes/music_explore.toml"
    assert best(testbed, "evening sky") == "recipes/sky.toml"
    assert skills.find(testbed, "spaceship hyperdrive")["recipes"] == []


def test_a_match_says_how_to_start_from_it(testbed: Path) -> None:
    found = skills.find(testbed, "creaky gate sound")
    top = found["recipes"][0]
    assert top["tool"] == "supercollider" and top["script"] == "recipes/sfx_door.scd"
    assert "audio/sfx/door_creak.wav" in top["outputs"]
    assert "creak" in top["matched"] and top["ops"]
    assert found["indexed"] == 6
    assert "changed parameters" in found["next"]


def test_accepted_change_summaries_are_indexed_and_kept(testbed: Path) -> None:
    journal(
        testbed, "a" * 32, "Add a dusty crate for the cellar", ["recipes/room.toml"]
    )
    journal(
        testbed,
        "b" * 32,
        "Barrel nobody accepted",
        ["recipes/door.toml"],
        state="rejected",
    )
    assert best(testbed, "dusty crate") == "recipes/room.toml"
    assert best(testbed, "barrel") == ""
    shutil.rmtree(testbed / ".joinery/transactions")
    # History retention removed the journal; the index still knows the summary.
    assert best(testbed, "dusty crate") == "recipes/room.toml"
    index = json.loads((testbed / skills.SKILLS / skills.INDEX).read_text())
    room = next(s for s in index["skills"] if s["recipe"] == "recipes/room.toml")
    assert room["summaries"] == ["Add a dusty crate for the cellar"]


def test_the_index_is_rebuilt_only_for_changed_recipes(testbed: Path) -> None:
    skills.refresh(testbed)
    path = testbed / skills.SKILLS / skills.INDEX
    stamp = path.stat().st_mtime_ns
    skills.refresh(testbed)
    assert path.stat().st_mtime_ns == stamp
    # A changed note is indexed again.
    sky = testbed / "recipes/sky.toml"
    assert 'note = "' in sky.read_text()
    sky.write_text(sky.read_text().replace('note = "', 'note = "Lantern glow. ', 1))
    assert best(testbed, "lantern glow") == "recipes/sky.toml"
    # An unreadable recipe is skipped, the rest still found.
    sky.write_text("tool = [")
    assert best(testbed, "stone floor") == "recipes/material_stone.toml"
    assert skills.find(testbed, "stone")["indexed"] == 5


def test_kit_parameters_are_shown(tmp_path: Path) -> None:
    (tmp_path / "recipes").mkdir()
    (tmp_path / "recipes/fence.toml").write_text(
        'version = 1\ntool = "blender"\nscript = "recipes/fence.py"\noutputs = ["art/kits/fence.blend"]\n'
    )
    (tmp_path / "recipes/fence.py").write_text(
        'import bpy\n# joinery-kit: {"kit": "fence", "params": {"posts": 6, "height": 1.2}}\n'
    )
    top = skills.find(tmp_path, "a taller fence")["recipes"][0]
    assert top["params"] == {"posts": 6, "height": 1.2}


def test_accepting_a_change_indexes_its_recipe(tmp_path: Path) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    tx = FileTransaction(tmp_path)
    tx.begin("Make a mossy well")
    (tx.stage / "recipes").mkdir()
    (tx.stage / "recipes/well.toml").write_text(
        'version = 1\ntool = "blender"\nscript = "recipes/well.py"\noutputs = ["art/well.blend"]\n'
    )
    (tx.stage / "recipes/well.py").write_text(
        "import bpy\nbpy.ops.mesh.primitive_cylinder_add(radius=0.8)\n"
    )
    tx.review()
    tx.accept()
    index = json.loads((tmp_path / skills.SKILLS / skills.INDEX).read_text())
    assert index["skills"][0]["summaries"] == ["Make a mossy well"]
    assert index["skills"][0]["params"] == {"radius": "0.8"}


def test_the_lead_and_workers_are_offered_find_recipe_through_the_gateway(
    testbed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert "find_recipe" in offered_tools("lead") and "find_recipe" in offered_tools(
        "worker"
    )
    assert "find_recipe" not in offered_tools("reviewer")
    monkeypatch.setattr("joinery.gateway.configured", lambda _: ([], []))

    async def run() -> None:
        gateway = ProjectGateway(testbed)
        lead = GatewayRPC(gateway)
        reviewer = GatewayRPC(gateway, role="reviewer")
        await lead.start()
        await reviewer.start()
        try:
            found = await rpc(
                lead.path, "find_recipe", {"request": "creaky gate sound"}
            )
            assert found["recipes"][0]["recipe"] == "recipes/sfx_door.toml"
            with pytest.raises(RuntimeError):
                await rpc(reviewer.path, "find_recipe", {"request": "door"})
            with pytest.raises(RuntimeError, match="few words"):
                await rpc(lead.path, "find_recipe", {"request": " "})
        finally:
            await reviewer.close()
            await lead.close()

    asyncio.run(run())


def test_the_command_finds_and_lists(
    testbed: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        main(["skills", "--project", str(testbed), "--find", "creaky gate", "--json"])
        == 0
    )
    assert (
        json.loads(capsys.readouterr().out)["recipes"][0]["recipe"]
        == "recipes/sfx_door.toml"
    )
    assert main(["skills", "--project", str(testbed)]) == 0
    assert "6 recipes indexed" in capsys.readouterr().out
