"""`/tidy`: what the game never uses, with evidence; removal as one reviewed change."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from pathlib import Path

import pytest

from bridge import tidy as host_tidy
from joinery import (
    lockfile,
    roles,
    tidy,
    tidy_caches,
    tidy_cli,
    tidy_remove,
    tidy_report,
)
from joinery.cli import main as cli_main
from joinery.gateway import ProjectGateway
from joinery.gateway_rpc import GatewayRPC, rpc
from joinery.policy import user_only_violation
from joinery.transactions import FileTransaction
from tests.test_crew import CrewTest

PROJECT = """config_version=5

[application]

config/name="Tidy fixture"
run/main_scene="res://game/main.tscn"

[autoload]

Used="*res://game/used_auto.gd"
Idle="*res://game/idle_auto.gd"
Gone="*res://game/gone_auto.gd"

[input]

jump={
"deadzone": 0.2,
"events": []
}
dash={
"deadzone": 0.2,
"events": []
}
move_left={
"deadzone": 0.2,
"events": []
}
ui_accept={
"deadzone": 0.2,
"events": []
}

[internationalization]

locale/translations_pot_files=PackedStringArray("res://game/main.gd", "res://game/old_hud.gd")
"""

MAIN = """[gd_scene load_steps=5 format=3 uid="uid://main"]

[ext_resource type="Script" path="res://game/main.gd" id="1_main"]
[ext_resource type="PackedScene" path="res://game/player.tscn" id="2_player"]
[ext_resource type="Material" path="res://materials/floor.tres" id="3_floor"]
[ext_resource type="Texture2D" path="res://art/textures/deco.png" id="4_deco"]

[sub_resource type="BoxMesh" id="BoxMesh_deco"]

[node name="Main" type="Node3D"]
script = ExtResource("1_main")

[node name="Player" parent="." instance=ExtResource("2_player")]

[node name="Floor" type="MeshInstance3D" parent="."]
material_override = ExtResource("3_floor")

[node name="HiddenDeco" type="MeshInstance3D" parent="."]
visible = false
mesh = SubResource("BoxMesh_deco")

[node name="Glow" type="Sprite3D" parent="HiddenDeco"]
texture = ExtResource("4_deco")

[node name="Shown" type="MeshInstance3D" parent="."]
visible = false

[node name="Node3D" type="Node3D" parent="."]

[node name="Spawn" type="Node3D" parent="."]

[node name="Markers" type="Node3D" parent="."]

[node name="A" type="Marker3D" parent="Markers"]
"""

MAIN_GD = """extends Node3D

func _ready() -> void:
\t$Shown.show()
\tvar spawn: Node3D = $Spawn
\tvar music := load("res://audio/music/theme.ogg")
\tvar level := load("res://game/levels/level_%d.tscn" % 1)
\tif Input.is_action_pressed("jump"):
\t\tprint(spawn, music, level)
\tvar side := "left"
\tif Input.is_action_pressed("move_" + side):
\t\tprint(side)
"""

PLAYER = """[gd_scene load_steps=3 format=3]

[ext_resource type="Script" path="res://game/player.gd" id="1_p"]
[ext_resource type="Script" path="res://game/empty_extra.gd" id="2_e"]

[node name="Player" type="CharacterBody3D"]
script = ExtResource("1_p")

[node name="Extra" type="Node3D" parent="."]
script = ExtResource("2_e")
"""

CRATE = """[gd_scene load_steps=4 format=3]

[ext_resource type="PackedScene" path="res://art/models/crate.glb" id="1_c"]
[ext_resource type="Texture2D" path="res://art/textures/crate.png" id="2_t"]
[ext_resource type="Script" path="res://game/crate.gd" id="3_s"]

[node name="Crate" instance=ExtResource("1_c")]
script = ExtResource("3_s")
"""

SHARED_USER = """[gd_scene load_steps=2 format=3]

[ext_resource type="Texture2D" path="res://art/textures/shared.png" id="1_t"]

[node name="{name}" type="Sprite3D"]
texture = ExtResource("1_t")
"""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write(root: Path, rel: str, data: str | bytes) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        path.write_bytes(data)
    else:
        path.write_text(data, encoding="utf-8")


def make_fixture(root: Path) -> Path:
    project = root / "game"
    files: dict[str, str | bytes] = {
        "project.godot": PROJECT,
        "game/main.tscn": MAIN,
        "game/main.gd": MAIN_GD,
        "game/player.tscn": PLAYER,
        "game/player.gd": "extends CharacterBody3D\n\nfunc _physics_process(_d: float) -> void:\n\tmove_and_slide()\n",
        "game/empty_extra.gd": "extends Node3D\n",
        "game/used_auto.gd": "extends Node\n\nfunc _ready() -> void:\n\tprint('up')\n",
        "game/idle_auto.gd": "extends Node\n\nfunc helper() -> int:\n\treturn 1\n",
        "game/old_hud.gd": "extends Control\n\nfunc _ready() -> void:\n\tpass\n",
        "game/levels/level_1.tscn": '[gd_scene format=3]\n\n[node name="L1" type="Node3D"]\n',
        "game/levels/level_2.tscn": '[gd_scene format=3]\n\n[node name="L2" type="Node3D"]\n',
        "game/crate.tscn": CRATE,
        "game/crate.gd": "extends Node3D\n\nfunc _ready() -> void:\n\tpass\n",
        "game/a.tscn": SHARED_USER.replace("{name}", "A"),
        "game/b.tscn": SHARED_USER.replace("{name}", "B"),
        "game/blockout_level.tscn": '[gd_scene format=3]\n\n[node name="B" type="Node3D"]\n',
        "materials/floor.tres": '[gd_resource type="StandardMaterial3D" format=3]\n\n[resource]\n',
        "materials/old_mat.tres": '[gd_resource type="StandardMaterial3D" format=3]\n\n[resource]\n',
        "data/old.tres": '[gd_resource type="Resource" format=3]\n\n[resource]\n',
        "shaders/old.gdshader": "shader_type spatial;\n",
        "fonts/unused.ttf": b"\0" * 3000,
        "fonts/kept.ttf": b"\0" * 1000,
        "art/bible.md": "# Art bible\n",
        "art/reference/.gdignore": "",
        "art/reference/mood.png": b"\x89PNG" + b"\0" * 100,
        "art/sources/.gdignore": "",
        "art/sources/crate.blend": b"BLENDER" + b"\0" * 5000,
        "art/models/crate.glb": b"glTF" + b"\0" * 20000,
        "art/models/crate.glb.import": '[remap]\nuid="uid://crateglb"\n',
        "art/models/crate_lod1.glb": b"glTF" + b"\0" * 8000,
        "art/textures/crate.png": b"\x89PNG" + b"\0" * 4000,
        "art/textures/deco.png": b"\x89PNG" + b"\0" * 900,
        "art/textures/shared.png": b"\x89PNG" + b"\0" * 700,
        "art/textures/final.png": b"\x89PNG" + b"\0" * 600,
        "art/textures/test_only.png": b"\x89PNG" + b"\0" * 500,
        "audio/sfx/beep.wav": b"RIFF" + b"\0" * 1500,
        "audio/music/theme.ogg": b"OggS" + b"\0" * 2000,
        "audio/music/old_theme.ogg": b"OggS" + b"\0" * 2500,
        "recipes/crate.toml": (
            'tool = "blender"\nscript = "recipes/crate.py"\n'
            'outputs = ["art/sources/crate.blend", "art/models/crate.glb", '
            '"art/models/crate_lod1.glb"]\n'
        ),
        "recipes/crate.py": "# kit\n",
        "design/brief.md": "# Brief\n",
        "design/blockouts/arena.toml": 'scene = "res://game/blockout_level.tscn"\n',
        "tests/test_thing.gd": 'extends Node\nvar t := load("res://art/textures/test_only.png")\n',
        "joinery.toml": (
            '[tools]\nenabled = ["godot"]\n\n[tidy]\nkeep = ["fonts/kept.ttf"]\n\n'
            '[match]\nfinal = ["art/textures/final.png"]\n'
        ),
    }
    for rel, data in files.items():
        _write(project, rel, data)
    entries: list[lockfile.Entry] = []
    for rel in sorted(lockfile.asset_files(project)):
        digest = _sha((project / rel).read_bytes())
        if rel in {
            "art/sources/crate.blend",
            "art/models/crate.glb",
            "art/models/crate_lod1.glb",
        }:
            entries.append(
                lockfile.ToolCreated(
                    path=rel,
                    sha256=digest,
                    origin="tool-created",
                    tool="blender",
                    recipe="recipes/crate.toml",
                )
            )
        else:
            entries.append(
                lockfile.HumanImport(
                    path=rel,
                    sha256=digest,
                    origin="human-import",
                    imported_by="me",
                    licence="CC0",
                )
            )
    (project / "joinery.lock").write_text(lockfile.LOCK_HEADER)
    lockfile.write_assets(project, set(), entries)
    return project


@pytest.fixture
def project(tmp_path: Path) -> Path:
    return make_fixture(tmp_path)


def _by(report: tidy.Report) -> dict[str, tidy.Finding]:
    return {f.key: f for f in report.findings}


def test_finds_unused_files_of_every_kind_with_evidence(project: Path) -> None:
    report = tidy_report.check(project)
    found = _by(report)
    sure = {k for k, f in found.items() if f.confidence == "unused"}
    assert {
        "game/crate.tscn",
        "art/textures/shared.png",
        "game/a.tscn",
        "game/b.tscn",
        "materials/old_mat.tres",
        "data/old.tres",
        "shaders/old.gdshader",
        "fonts/unused.ttf",
        "audio/sfx/beep.wav",
        "audio/music/old_theme.ogg",
        "game/old_hud.gd",
    } <= sure
    assert found["materials/old_mat.tres"].kind == "material"
    assert found["data/old.tres"].kind == "resource"
    assert found["audio/music/old_theme.ogg"].kind == "music"
    assert found["audio/sfx/beep.wav"].kind == "sound"
    assert found["fonts/unused.ttf"].kind == "font"
    assert found["shaders/old.gdshader"].kind == "shader"
    assert found["game/old_hud.gd"].kind == "script"
    # What only the crate scene uses goes with it, with its provenance.
    crate = found["game/crate.tscn"]
    goes = {c.path: c.what for c in crate.goes_with}
    assert goes["art/models/crate.glb"] == "only it uses it"
    assert goes["art/textures/crate.png"] == "only it uses it"
    assert goes["game/crate.gd"] == "only it uses it"
    assert goes["art/models/crate.glb.import"] == "import settings"
    assert goes["recipes/crate.toml"] == "recipe"
    assert goes["recipes/crate.py"] == "recipe script"
    assert goes["art/sources/crate.blend"] == "native source"
    # The LOD is unused too, and goes with its model rather than standing alone.
    assert "art/models/crate_lod1.glb" not in found
    assert goes["art/models/crate_lod1.glb"] == "LOD"
    assert crate.size > 30000
    assert "Nothing in the game loads it" in crate.why
    # A texture two unused scenes share is its own finding, with why.
    assert "game/a.tscn" in found["art/textures/shared.png"].why
    # Used, test-only, kept, final, protected and the user's references never are.
    for never in (
        "game/main.tscn",
        "game/player.tscn",
        "materials/floor.tres",
        "audio/music/theme.ogg",
        "game/blockout_level.tscn",
        "art/textures/deco.png",
        "art/textures/test_only.png",
        "fonts/kept.ttf",
        "art/textures/final.png",
        "art/reference/mood.png",
        "art/bible.md",
        "design/brief.md",
    ):
        assert never not in found, never
    assert "art/textures/test_only.png" in report.used_by_tests
    assert "fonts/kept.ttf" in report.kept
    assert report.summary.startswith(f"{report.count} unused things · ")


def test_built_paths_are_maybe_used_never_sure(project: Path) -> None:
    report = tidy_report.check(project)
    found = _by(report)
    for level in ("game/levels/level_1.tscn", "game/levels/level_2.tscn"):
        finding = found[level]
        assert finding.confidence == "maybe"
        assert "game/main.gd:7 builds res://game/levels/level_" in finding.evidence[0]
    assert found["input:move_left"].confidence == "maybe"
    removal = tidy_remove.plan(tidy.build(project), report)
    assert not any(c.path.startswith("game/levels/") for c in removal.delete)


def test_actions_autoloads_nodes_and_empty_scripts(project: Path) -> None:
    found = _by(tidy_report.check(project))
    assert found["input:dash"].confidence == "unused"
    assert "input:jump" not in found and "input:ui_accept" not in found
    assert found["autoload:Idle"].confidence == "unused"
    assert "is gone" in found["autoload:Gone"].why
    assert "autoload:Used" not in found
    assert found["game/main.tscn::HiddenDeco"].title.startswith(
        "Hidden MeshInstance3D HiddenDeco"
    )
    assert found["game/main.tscn::Node3D"].title.startswith("Empty Node3D")
    for kept_node in ("Shown", "Spawn", "Markers", "Markers/A", "Player", "Floor"):
        assert f"game/main.tscn::{kept_node}" not in found
    assert found["game/empty_extra.gd::empty"].kind == "script"


def test_remove_is_one_reviewed_change_and_discard_restores(project: Path) -> None:
    before = {
        p: p.read_bytes()
        for p in project.rglob("*")
        if p.is_file() and ".joinery" not in p.parts
    }
    graph = tidy.build(project)
    report = tidy_report.check(project, graph)
    removal = tidy_remove.plan(graph, report)
    assert not removal.problems
    tx = tidy_cli.stage(project, removal)
    assert tx.state == "review"
    deleted = {c["path"] for c in tx.changes if c["change"] == "deleted"}
    assert {
        "game/crate.tscn",
        "art/models/crate.glb",
        "recipes/crate.toml",
        "art/sources/crate.blend",
    } <= deleted
    assert "Remove" in tx.summary and "unused" in tx.summary
    # Nothing in the project moved while it waits.
    assert (project / "game/crate.tscn").is_file()
    tx.reject()
    after = {
        p: p.read_bytes()
        for p in project.rglob("*")
        if p.is_file() and ".joinery" not in p.parts
    }
    assert after == before


def test_accept_removes_together_and_revert_restores(project: Path) -> None:
    before = {
        p: p.read_bytes()
        for p in project.rglob("*")
        if p.is_file() and ".joinery" not in p.parts
    }
    graph = tidy.build(project)
    report = tidy_report.check(project, graph)
    tx = tidy_cli.stage(project, tidy_remove.plan(graph, report))
    tx.accept()
    for gone in (
        "game/crate.tscn",
        "art/models/crate.glb",
        "art/models/crate_lod1.glb",
        "recipes/crate.toml",
        "recipes/crate.py",
        "art/sources/crate.blend",
        "game/empty_extra.gd",
        "audio/sfx/beep.wav",
    ):
        assert not (project / gone).exists(), gone
    locked = {e.path for e in lockfile.read_assets(project)}
    assert "art/models/crate.glb" not in locked and "audio/sfx/beep.wav" not in locked
    assert "audio/music/theme.ogg" in locked
    settings = (project / "project.godot").read_text()
    assert (
        "dash=" not in settings and "Idle=" not in settings and "Gone=" not in settings
    )
    assert "jump=" in settings and "Used=" in settings
    assert "old_hud.gd" not in settings and "res://game/main.gd" in settings
    main = (project / "game/main.tscn").read_text()
    assert (
        "HiddenDeco" not in main and "Glow" not in main and 'name="Node3D"' not in main
    )
    assert "deco.png" not in main and "BoxMesh_deco" not in main
    assert "Shown" in main and "Spawn" in main
    assert "empty_extra" not in (project / "game/player.tscn").read_text()
    # Still used and maybe-used things stay.
    assert (project / "game/levels/level_1.tscn").is_file()
    assert (project / "art/reference/mood.png").is_file()
    assert (project / "fonts/kept.ttf").is_file()
    # The Timeline's revert brings everything back.
    FileTransaction.load(project, tx.id).revert()
    after = {
        p: p.read_bytes()
        for p in project.rglob("*")
        if p.is_file() and ".joinery" not in p.parts
    }
    assert after == before


def test_removing_a_file_another_finding_still_uses_is_refused(project: Path) -> None:
    graph = tidy.build(project)
    report = tidy_report.check(project, graph)
    shared = _by(report)["art/textures/shared.png"]
    removal = tidy_remove.plan(graph, report, [shared.id])
    assert any(
        "game/a.tscn still uses art/textures/shared.png" in p for p in removal.problems
    )
    with pytest.raises(ValueError):
        tidy_remove.apply(project, removal)


def test_keep_list_leaves_a_finding_alone(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        cli_main(
            [
                "tidy",
                "--project",
                str(project),
                "--keep",
                "audio/sfx/beep.wav",
                "--json",
            ]
        )
        == 0
    )
    capsys.readouterr()
    report = tidy_report.check(project)
    assert "audio/sfx/beep.wav" not in _by(report)
    assert "audio/sfx/beep.wav" in report.kept
    assert (
        'keep = ["audio/sfx/beep.wav", "fonts/kept.ttf"]'
        in (project / "joinery.toml").read_text()
    )
    assert (
        cli_main(["tidy", "--project", str(project), "--keep", "input:dash", "--json"])
        == 0
    )
    assert "input:dash" not in _by(tidy_report.check(project))
    assert (
        cli_main(
            ["tidy", "--project", str(project), "--unkeep", "input:dash", "--json"]
        )
        == 0
    )
    assert "input:dash" in _by(tidy_report.check(project))


def test_cli_json_plan_and_remove(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli_main(["tidy", "--project", str(project), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["count"] >= 10 and data["size"] > 0
    assert {g["kind"] for g in data["groups"]} >= {
        "model",
        "texture",
        "scene",
        "input",
        "node",
    } - {"model"}
    assert cli_main(["tidy", "--project", str(project), "--plan", "--json"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["size"] == data["size"] and not plan["problems"]
    assert cli_main(["tidy", "--project", str(project), "--remove", "--json"]) == 0
    staged = json.loads(capsys.readouterr().out)
    assert staged["state"] == "review" and staged["transaction_id"]
    assert (project / "game/crate.tscn").is_file()


def test_the_check_writes_nothing(project: Path) -> None:
    before = {
        p: (p.stat().st_mtime_ns, p.read_bytes())
        for p in project.rglob("*")
        if p.is_file()
    }
    tidy_report.check(project)
    after = {
        p: (p.stat().st_mtime_ns, p.read_bytes())
        for p in project.rglob("*")
        if p.is_file()
    }
    assert after == before


def test_caches_are_cleared_quietly(project: Path) -> None:
    old = time.time() - 30 * 24 * 3600
    _write(project, ".joinery/render_cache/a.png", b"\0" * 4096)
    _write(project, ".joinery/results/old.json", "{}")
    _write(project, ".joinery/results/new.json", "{}")
    os.utime(project / ".joinery/results/old.json", (old, old))
    _write(project, ".joinery/transactions/x/journal.json", "{}")
    found = {c.path: c for c in tidy_caches.caches(project)}
    assert found[".joinery/render_cache"].size == 4096
    assert found[".joinery/results"].files == 1
    cleared = tidy_caches.clear(project)
    assert sum(c.size for c in cleared) >= 4096
    assert not (project / ".joinery/render_cache/a.png").exists()
    assert (project / ".joinery/results/new.json").exists()
    assert (project / ".joinery/transactions/x/journal.json").exists()


def test_read_only_roles_check_but_never_remove() -> None:
    assert roles.registry_allowed("designer", roles.TIDY_CHECK)
    assert roles.registry_allowed("reviewer", roles.TIDY_CHECK)
    assert not roles.registry_allowed("reviewer", roles.TIDY_REMOVE)
    assert roles.registry_allowed("worker", roles.TIDY_REMOVE)
    denied = roles.role_violation(
        "reviewer", "mcp", "joinery-registry", roles.TIDY_REMOVE
    )
    assert denied is not None
    assert roles.role_violation(
        "reviewer", "bash", "", "", "joinery tidy --project . --remove --yes"
    )
    # Writers remove through the reviewed tool, never the CLI's --yes or keep list.
    for command in (
        "uv run joinery tidy --project . --remove --yes",
        "joinery tidy --project . --keep audio/x.wav",
        "joinery tidy --project . --clear-caches",
    ):
        assert user_only_violation(command) is not None, command
    assert user_only_violation("uv run joinery tidy --project . --json") is None


@pytest.fixture
def hosted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The fixture project with the chat host's fakes (tests/test_crew.py)."""
    from joinery.runner import Result
    from tests.test_crew import FakeConnection

    project = make_fixture(tmp_path)
    monkeypatch.setattr("joinery.gateway.configured", lambda _: ([], []))
    monkeypatch.setattr("joinery.gateway.approved", lambda _project: True)
    monkeypatch.setattr("joinery.gateway.MCPConnection", FakeConnection)
    monkeypatch.setattr("joinery.submission.approved_in", lambda *_: True)

    async def offline(
        _argv: list[str], _root: Path, *_a: object, **_k: object
    ) -> Result:
        return Result(True, "", "", 0.0)

    monkeypatch.setattr("joinery.submission.run_offline", offline)
    return project


def test_agents_check_and_writers_stage_a_removal(hosted: Path) -> None:
    async def go() -> None:
        gateway = ProjectGateway(hosted)
        lead = GatewayRPC(gateway)
        reviewer = GatewayRPC(gateway, role="reviewer")
        await lead.start()
        await reviewer.start()
        try:
            seen = await rpc(reviewer.path, "tidy_check", {})
            assert seen["sure"] >= 10 and seen["findings"][0]["confidence"] == "unused"
            crate = next(f for f in seen["findings"] if f["path"] == "game/crate.tscn")
            with pytest.raises(RuntimeError, match="only reads"):
                await rpc(reviewer.path, "tidy_remove", {"ids": [crate["id"]]})
            data = await rpc(lead.path, "tidy_remove", {"ids": [crate["id"]]})
            assert data["status"] == "staged"
            assert "art/models/crate.glb" in data["removed"]
            tx = gateway.transaction
            assert tx is not None
            assert not (tx.stage / "game/crate.tscn").exists()
            assert (hosted / "game/crate.tscn").is_file()
        finally:
            await reviewer.close()
            await lead.close()
            await gateway.close()

    asyncio.run(go())


def test_the_card_remove_is_a_reviewed_change_without_an_agent(hosted: Path) -> None:
    test = CrewTest(hosted)

    async def go() -> None:
        crew = test.crew()
        task = await host_tidy.start(
            crew, hosted, {"kind": "tidy_remove"}, test.prompt()
        )
        assert task.job is not None
        await task.job
        assert task.state == "in_review", task.message
        assert (hosted / "game/crate.tscn").is_file()
        preview = next(e for e in test.events if e["kind"] == "file_preview")
        assert any(f.startswith("Frees ") for f in preview["facts"])
        await crew.decide(task.transaction_id, False)
        assert (hosted / "game/crate.tscn").is_file()
        assert "dash=" in (hosted / "project.godot").read_text()
        again = await host_tidy.start(
            crew, hosted, {"kind": "tidy_remove"}, test.prompt()
        )
        assert again.job is not None
        await again.job
        assert again.state == "in_review", again.message
        await crew.decide(again.transaction_id, True)
        assert not (hosted / "game/crate.tscn").exists()
        assert not (hosted / "art/sources/crate.blend").exists()
        assert "dash=" not in (hosted / "project.godot").read_text()
        await crew.close()
        await test.gateway.close()

    asyncio.run(go())
