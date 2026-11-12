"""Scene variants (Godot inherited scenes): making one, reading what it overrides,
checking it fits its base, the review item, the make_variant tool for every agent and
the /variant command (ADR 0066)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from bridge import scene_variant
from bridge.instructions import REVIEW_GUIDE, SCENE_VARIANT_GUIDE, expand_command
from joinery import reasons, scene_variants
from joinery.gateway import ProjectGateway
from joinery.gateway_rpc import GatewayRPC, rpc
from joinery.mcp_runtime import ServerSpec
from joinery.roles import offered_tools, registry_allowed
from joinery.scene_text import Scene
from joinery.scene_variants import VariantError
from tests.test_crew import CrewTest
from tests.test_crew import project as crew_project  # noqa: F401 - the fixture
from tests.test_mixer_save import GodotConnection

GOBLIN = """[gd_scene load_steps=3 format=3 uid="uid://goblinbase"]

[ext_resource type="Script" path="res://enemies/goblin.gd" id="1_script"]
[ext_resource type="PackedScene" uid="uid://hatscene" path="res://props/hat.tscn" id="2_hat"]

[node name="Goblin" type="CharacterBody2D"]
script = ExtResource("1_script")
speed = 120.0

[node name="Sprite" type="Sprite2D" parent="."]
modulate = Color(1, 1, 1, 1)

[node name="Hat" parent="Sprite" instance=ExtResource("2_hat")]
"""
HAT = """[gd_scene format=3 uid="uid://hatscene"]

[node name="Hat" type="Node2D"]

[node name="Brim" type="Sprite2D" parent="."]
scale = Vector2(1, 1)
"""


def _project(root: Path) -> Path:
    (root / "enemies").mkdir(parents=True)
    (root / "props").mkdir()
    (root / "project.godot").write_text("config_version=5\n")
    (root / "enemies/goblin.tscn").write_text(GOBLIN)
    (root / "enemies/goblin.gd").write_text("extends CharacterBody2D\n@export var speed := 100.0\n")
    (root / "props/hat.tscn").write_text(HAT)
    return root


def test_make_writes_an_inherited_scene_with_only_the_overrides(tmp_path: Path) -> None:
    root = _project(tmp_path)
    made = scene_variants.make(
        root,
        "res://enemies/goblin.tscn",
        overrides=[
            {"node": ".", "property": "speed", "value": 180},
            {"node": "Sprite", "property": "modulate", "value": "Color(1, 0.3, 0.3, 1)"},
            {"node": "Sprite/Hat/Brim", "property": "scale", "value": "Vector2(1.5, 1.5)"},
            {"node": ".", "property": "metadata/title", "value": "Red goblin"},
        ],
    )
    assert made.path == "enemies/goblin_variant.tscn"
    lines = made.text.splitlines()
    assert lines[0].startswith('[gd_scene format=3 uid="uid://')
    assert (
        '[ext_resource type="PackedScene" uid="uid://goblinbase" '
        'path="res://enemies/goblin.tscn" id="1_base"]'
    ) in lines
    root_at = lines.index('[node name="Goblin" instance=ExtResource("1_base")]')
    assert lines[root_at + 1 : root_at + 3] == ["speed = 180", 'metadata/title = "Red goblin"']
    assert '[node name="Sprite" parent="."]' in lines
    assert '[node name="Brim" parent="Sprite/Hat"]' in lines
    assert "type=" not in "\n".join(lines[root_at:])
    assert [o.line() for o in made.variant.overrides] == [
        "root · speed: 120.0 → 180",
        "root · metadata/title: default → \"Red goblin\"",
        "Sprite · modulate: Color(1, 1, 1, 1) → Color(1, 0.3, 0.3, 1)",
        "Sprite/Hat/Brim · scale: Vector2(1, 1) → Vector2(1.5, 1.5)",
    ]
    assert made.variant.fact() == "variant of goblin.tscn, 4 overrides"
    # The next one beside it gets its own name.
    (root / made.path).write_text(made.text)
    again = scene_variants.make(root, "enemies/goblin.tscn")
    assert again.path == "enemies/goblin_variant_2.tscn"
    assert again.variant.fact() == "variant of goblin.tscn, same as its base so far"


def test_make_refuses_what_godot_couldnt_open(tmp_path: Path) -> None:
    root = _project(tmp_path)
    base = "res://enemies/goblin.tscn"
    cases: list[tuple[dict[str, Any], str]] = [
        ({"base": "res://enemies/orc.tscn"}, "There is no scene res://enemies/orc.tscn"),
        ({"base": "res://enemies/goblin.gd"}, "made from .tscn scenes"),
        ({"base": base, "path": "res://enemies/goblin.tscn"}, "already exists"),
        ({"base": base, "path": "res://addons/x.tscn"}, "aren't written under addons/"),
        ({"base": base, "path": "res://../x.tscn"}, "Not a project file"),
        ({"base": base, "path": "res://enemies/red.tres"}, "is a .tscn scene"),
        (
            {"base": base, "overrides": [{"node": "Arm", "property": "x", "value": 1}]},
            "has no node Arm (it has: Sprite, Sprite/Hat, Sprite/Hat/Brim)",
        ),
        (
            {"base": base, "overrides": [{"node": ".", "property": "script", "value": "x"}]},
            "isn't a property make_variant can set",
        ),
        (
            {
                "base": base,
                "overrides": [
                    {"node": ".", "property": "texture", "value": 'ExtResource("9")'}
                ],
            },
            "change resources",
        ),
        (
            {"base": base, "overrides": [{"node": ".", "property": "a", "value": "Vector2(1, 2"}]},
            "isn't a whole Godot value",
        ),
    ]
    for arguments, words in cases:
        with pytest.raises(VariantError, match=words.replace("(", r"\(").replace(")", r"\)")):
            scene_variants.make(
                root,
                arguments["base"],
                arguments.get("path", ""),
                arguments.get("overrides"),
            )


def test_plain_words_become_a_string_and_numbers_stay_numbers(tmp_path: Path) -> None:
    root = _project(tmp_path)
    made = scene_variants.make(
        root,
        "enemies/goblin.tscn",
        overrides=[
            {"node": ".", "property": "metadata/name", "value": "Grim"},
            {"node": ".", "property": "visible", "value": False},
            {"node": ".", "property": "z_index", "value": "-2"},
        ],
    )
    assert 'metadata/name = "Grim"' in made.text
    assert "visible = false" in made.text and "z_index = -2" in made.text


def test_values_read_as_places_sizes_and_files() -> None:
    said = scene_variants.said
    assert said("Transform3D(1.6, 0, 0, 0, 1.6, 0, 0, 0, 1.6, 0, 0.8, 0)") == "at (0, 0.8, 0), ×1.6"
    assert said("Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, 2, 0, -1)") == "at (2, 0, -1)"
    assert said("Transform3D(0, 0, -1, 0, 1, 0, 1, 0, 0, 0, 0, 0)") == "at (0, 0, 0), turned"
    assert said("Transform2D(2, 0, 0, 3, 10, 4)") == "at (10, 4), ×(2, 3)"
    scene = Scene(GOBLIN)
    assert said('ExtResource("1_script")', scene) == "goblin.gd"
    assert said('SubResource("nope")', scene) == "its own resource"
    assert said("Color(1, 0, 0, 1)") == "Color(1, 0, 0, 1)"


def test_read_lists_overrides_added_nodes_and_connections_against_the_base_chain(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    first = scene_variants.make(
        root,
        "enemies/goblin.tscn",
        "enemies/red_goblin.tscn",
        [{"node": "Sprite", "property": "modulate", "value": "Color(1, 0, 0, 1)"}],
    )
    (root / first.path).write_text(first.text)
    # A variant of the variant: overrides compare with the red goblin's own values.
    text = """[gd_scene format=3 uid="uid://boss"]

[ext_resource type="PackedScene" path="res://enemies/red_goblin.tscn" id="1_base"]

[node name="Goblin" instance=ExtResource("1_base")]
speed = 120.0

[node name="Sprite" parent="."]
modulate = Color(0.5, 0, 0, 1)

[node name="Crown" type="Sprite2D" parent="Sprite"]
scale = Vector2(2, 2)

[node name="Jewel" type="Sprite2D" parent="Sprite/Crown"]

[connection signal="ready" from="." to="." method="_on_ready"]
"""
    (root / "enemies/boss.tscn").write_text(text)
    variant = scene_variants.read(root, "enemies/boss.tscn")
    assert variant is not None
    assert variant.base == "res://enemies/red_goblin.tscn"
    # speed = 120.0 is what the base has already: not an override.
    assert [o.as_dict() for o in variant.overrides] == [
        {
            "node": "Sprite",
            "property": "modulate",
            "base": "Color(1, 0, 0, 1)",
            "value": "Color(0.5, 0, 0, 1)",
            "base_said": "Color(1, 0, 0, 1)",
            "said": "Color(0.5, 0, 0, 1)",
        }
    ]
    assert variant.added == [
        {"node": "Sprite/Crown", "type": "Sprite2D"},
        {"node": "Sprite/Crown/Jewel", "type": "Sprite2D"},
    ]
    assert variant.connections == ["ready: . → _on_ready()"]
    assert variant.fact() == "variant of red_goblin.tscn, 1 override, 2 added nodes, 1 connection"
    assert scene_variants.read(root, "enemies/goblin.tscn") is None


def test_check_finds_missing_bases_unknown_nodes_and_cycles(tmp_path: Path) -> None:
    root = _project(tmp_path)

    def variant(name: str, base: str, body: str = "") -> str:
        (root / f"enemies/{name}.tscn").write_text(
            f'[gd_scene format=3]\n\n[ext_resource type="PackedScene" path="{base}" id="1"]\n\n'
            f'[node name="Goblin" instance=ExtResource("1")]\n{body}'
        )
        return f"enemies/{name}.tscn"

    good = variant("ok", "res://enemies/goblin.tscn")
    gone = variant("gone", "res://enemies/orc.tscn")
    stray = variant(
        "stray", "res://enemies/goblin.tscn", '\n[node name="Arm" parent="."]\nscale = Vector2(2, 2)\n'
    )
    loop_a = variant("loop_a", "res://enemies/loop_b.tscn")
    variant("loop_b", "res://enemies/loop_a.tscn")
    found = scene_variants.check(root, [good, gone, stray, loop_a, "enemies/goblin.gd"])
    assert found[0] == "enemies/gone.tscn: its base scene res://enemies/orc.tscn doesn't exist."
    assert found[1].startswith("enemies/stray.tscn: node Arm isn't in res://enemies/goblin.tscn")
    assert found[2].startswith("enemies/loop_a.tscn: A scene can't inherit from itself")
    assert len(found) == 3
    assert [reasons.variant(line).rule for line in found] == [
        "variants.missing_base",
        "variants.unknown_node",
        "variants.cycle",
    ]
    assert reasons.variant(found[1]).node == "Arm"


def test_review_items_show_overrides_and_render_base_against_variant(tmp_path: Path) -> None:
    stage = _project(tmp_path / "stage")
    original = _project(tmp_path / "original")
    made = scene_variants.make(
        stage,
        "enemies/goblin.tscn",
        overrides=[{"node": "Sprite", "property": "modulate", "value": "Color(1, 0, 0, 1)"}],
    )
    (stage / made.path).write_text(made.text)
    changes = [
        {"path": made.path, "before": "", "after": "abc", "change": "created"},
        {"path": "enemies/goblin.gd", "before": "a", "after": "b", "change": "modified"},
    ]
    items, facts = scene_variants.review_items(stage, original, changes)
    (item,) = items
    assert facts == ["variant of goblin.tscn, 1 override"]
    assert item["media"] == "image" and item["label"] == "Variant"
    assert item["variant"]["overrides"] == [
        {
            "node": "Sprite",
            "property": "modulate",
            "base": "Color(1, 1, 1, 1)",
            "value": "Color(1, 0, 0, 1)",
            "base_said": "Color(1, 1, 1, 1)",
            "said": "Color(1, 0, 0, 1)",
        }
    ]
    assert item["scenes"] == {
        "before": "res://enemies/goblin.tscn",
        "after": str(stage / made.path),
    }
    assert item["before"].endswith("variant-enemies-goblin_variant-before.png")
    assert item["after"].startswith(str(stage / ".joinery/previews"))
    # A changed variant compares with itself as it was.
    (original / made.path).write_text(made.text)
    changes[0]["before"] = "old"
    (changed,), _facts = scene_variants.review_items(stage, original, changes)
    assert changed["scenes"]["before"] == str(original / made.path)


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from tests.test_crew import FakeConnection

    _project(tmp_path)
    (tmp_path / "joinery.toml").write_text('[tools]\nenabled=["godot"]\n')
    monkeypatch.setattr("joinery.gateway.configured", lambda _: ([], []))
    monkeypatch.setattr("joinery.gateway.approved", lambda _project: True)
    monkeypatch.setattr("joinery.gateway.MCPConnection", FakeConnection)
    return tmp_path


def test_make_variant_stages_the_scene_and_the_review_lists_its_overrides(
    project: Path,
) -> None:
    async def go() -> None:
        gateway = ProjectGateway(project)
        lead = GatewayRPC(gateway)
        reviewer = GatewayRPC(gateway, role="reviewer")
        await lead.start()
        await reviewer.start()
        try:
            with pytest.raises(RuntimeError, match="Specialists cannot make variants"):
                await rpc(reviewer.path, "make_variant", {"base": "res://enemies/goblin.tscn"})
            bad = await rpc(
                lead.path,
                "make_variant",
                {"base": "res://enemies/goblin.tscn", "overrides": [{"node": "Arm", "property": "x", "value": 1}]},
            )
            assert bad["status"] == "problems" and "has no node Arm" in bad["problems"][0]
            data = await rpc(
                lead.path,
                "make_variant",
                {
                    "base": "res://enemies/goblin.tscn",
                    "path": "res://enemies/red_goblin.tscn",
                    "overrides": [
                        {"node": "Sprite", "property": "modulate", "value": "Color(1, 0, 0, 1)"}
                    ],
                },
            )
            assert data["status"] == "staged" and data["path"] == "res://enemies/red_goblin.tscn"
            assert data["overrides"] == ["Sprite · modulate: Color(1, 1, 1, 1) → Color(1, 0, 0, 1)"]
            tx = gateway.transaction
            assert tx is not None
            assert (tx.stage / "enemies/red_goblin.tscn").is_file()
            assert not (project / "enemies/red_goblin.tscn").exists()
            preview = gateway.open_review("A red goblin")
            assert "variant of goblin.tscn, 1 override" in preview["joinery_facts"]
            (item,) = [m for m in preview["media"] if m.get("label") == "Variant"]
            assert item["path"] == "enemies/red_goblin.tscn"
            assert item["variant"]["base"] == "res://enemies/goblin.tscn"
        finally:
            await reviewer.close()
            await lead.close()
            await gateway.close()

    asyncio.run(go())


def test_every_changing_agent_is_offered_make_variant() -> None:
    assert registry_allowed("lead", "make_variant")
    assert registry_allowed("worker", "make_variant")
    assert "make_variant" in offered_tools("lead")
    assert "make_variant" not in offered_tools("reviewer")
    assert SCENE_VARIANT_GUIDE in REVIEW_GUIDE and "make_variant(" in SCENE_VARIANT_GUIDE
    request = expand_command("/variant red, faster")
    assert "make_variant" in request and request.endswith("red, faster")
    assert expand_command("/variants 3 a door") == "/variants 3 a door"
    assert expand_command("/variantly") == "/variantly"


def test_the_registry_tool_passes_overrides_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mcp import Client

    from servers.joinery_registry.proxy import make_server

    asked: list[tuple[str, dict[str, Any]]] = []

    async def fake(_socket: Path, method: str, params: dict[str, Any]) -> dict[str, Any]:
        asked.append((method, params))
        return {"status": "staged"}

    monkeypatch.setattr("servers.joinery_registry.proxy.rpc", fake)

    async def go() -> None:
        async with Client(make_server(tmp_path / "s")) as client:
            result = await client.call_tool(
                "make_variant",
                {
                    "base": "res://enemies/goblin.tscn",
                    "overrides": [{"node": "Sprite", "property": "visible", "value": False}],
                },
            )
            assert not result.is_error
            assert json.loads(result.content[0].text)["status"] == "staged"  # type: ignore[union-attr]

    asyncio.run(go())
    assert asked == [
        (
            "make_variant",
            {
                "base": "res://enemies/goblin.tscn",
                "path": "",
                "overrides": [{"node": "Sprite", "property": "visible", "value": False}],
            },
        )
    ]


def test_a_bare_variant_command_is_a_reviewed_change_without_an_agent(
    crew_project: Path,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = crew_project
    (project / "enemies").mkdir()
    (project / "props").mkdir()
    (project / "enemies/goblin.tscn").write_text(GOBLIN)
    (project / "props/hat.tscn").write_text(HAT)
    with pytest.raises(ValueError, match="Open the scene"):
        scene_variant.plan(project, {"kind": "variant_make"})
    test = CrewTest(project)

    async def connection(self: Any, server: str) -> GodotConnection:
        return GodotConnection(
            ServerSpec(name=server, command="x", source="first-party"), project
        )

    monkeypatch.setattr("joinery.gateway.ProjectGateway.connection", connection)
    monkeypatch.setattr("joinery.gateway.MCPConnection", GodotConnection)

    class Installed:
        revision = "test"

    monkeypatch.setattr("joinery.server_install.load_installed", lambda _tool: Installed())

    async def go() -> None:
        crew = test.crew()
        message = {"kind": "variant_make", "base": "res://enemies/goblin.tscn"}
        task = await scene_variant.start(crew, project, message, test.prompt())
        assert task.job is not None
        await task.job
        assert task.state == "in_review", task.message
        assert not (project / "enemies/goblin_variant.tscn").exists()
        preview = next(e for e in test.events if e["kind"] == "file_preview")
        assert "variant of goblin.tscn, same as its base so far" in preview["facts"]
        assert any(m.get("label") == "Variant" for m in preview["media"])
        await crew.decide(task.transaction_id, True)
        text = (project / "enemies/goblin_variant.tscn").read_text()
        assert scene_variants.base_of(text) == "res://enemies/goblin.tscn"
        await crew.close()
        await test.gateway.close()

    asyncio.run(go())
