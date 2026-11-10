"""Actual source resolution and art-direction visibility through the MCP SDK."""

import asyncio
import json
from pathlib import Path

import pytest
from mcp import Client

from servers.joinery_registry.server import make_server

SOURCES = [
    ("blender", "art/sources/hammer.blend", "art/models/hammer.glb"),
    ("krita", "art/icon.kra", "art/icon.png"),
    ("gimp", "art/portrait.xcf", "art/portrait.png"),
    ("inkscape", "art/icon.svg", "art/vector.png"),
    ("material-maker", "art/stone.ptex", "art/stone.tres"),
    ("supercollider", "audio/impact.scd", "audio/impact.wav"),
]


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    (tmp_path / "joinery.toml").write_text('[tools]\nenabled=["godot"]\n')
    (tmp_path / "joinery.lock").write_text("version=1\n")
    (tmp_path / "recipes").mkdir()
    (tmp_path / "art").mkdir()

    def unavailable(_tool: str, _settings: dict):
        raise ValueError("Application intentionally absent from this unit fixture")

    monkeypatch.setattr("joinery.workspace.launcher", unavailable)
    return tmp_path


def call(project: Path, tool: str, arguments: dict):
    async def run():
        async with Client(make_server(project)) as client:
            return await client.call_tool(tool, arguments)

    return asyncio.run(run())


@pytest.mark.parametrize("owner,source,export", SOURCES)
@pytest.mark.parametrize(
    "resource_suffix", ["", "::NamedMaterial_1"], ids=["file", "selected-subresource"]
)
def test_selected_game_resource_resolves_its_real_owner_and_native_source(
    project: Path, owner: str, source: str, export: str, resource_suffix: str
) -> None:
    for relative in (source, export):
        path = project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"opaque native unit fixture")
    (project / "recipes/op.py").write_text("# source operation\n")
    (project / "recipes/asset.toml").write_text(
        f'tool={json.dumps(owner)}\nscript="recipes/op.py"\noutputs={json.dumps([source, export])}\n'
    )
    before = {p: p.read_bytes() for p in project.rglob("*") if p.is_file()}
    result = call(
        project, "resolve_source", {"resource": "res://" + export + resource_suffix}
    )
    assert not result.is_error
    asset = json.loads(result.content[0].text)
    assert asset["path"] == export and asset["tool"] == owner
    assert source in asset["sources"] and asset["recipe"] == "recipes/asset.toml"
    assert asset["verification"] == "Not checked by replay"
    assert {p: p.read_bytes() for p in project.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize(
    "resource",
    [
        "../outside.blend",
        "res://../outside.kra",
        "/outside.svg",
        "res://art/nonexistent.png",
    ],
)
def test_unregistered_or_outside_selection_is_an_actionable_error_not_an_invented_source(
    project: Path, resource: str
) -> None:
    result = call(project, "resolve_source", {"resource": resource})
    assert result.is_error
    assert any(
        reason in result.content[0].text for reason in ("outside", "not registered")
    )


@pytest.mark.parametrize("exists", [False, True], ids=["missing-bible", "draft-bible"])
def test_art_direction_remains_unapproved_and_has_no_model_approval_tool(
    project: Path, exists: bool
) -> None:
    if exists:
        (project / "art/bible.md").write_text("# User draft\nSteel and painted wood.\n")
    result = call(project, "art_direction", {})
    data = json.loads(result.content[0].text)
    assert not data["approved"]
    assert ("Steel" in data["text"]) if exists else ("No art direction" in data["text"])
    assert bool(data["sha256"]) is exists
    assert "/art" in data["approval_action"]

    async def schema():
        async with Client(make_server(project)) as client:
            tools = (await client.list_tools()).tools
            assert all(t.annotations.read_only_hint for t in tools)
            assert not any("approve" in t.name or "accept" in t.name for t in tools)

    asyncio.run(schema())


def hammer_with_parts(project: Path) -> None:
    from joinery import parts

    for relative in ("art/sources/hammer.blend", "art/models/hammer.glb"):
        path = project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"opaque native unit fixture")
    (project / "recipes/op.py").write_text("# source operation\n")
    (project / "recipes/asset.toml").write_text(
        'tool="blender"\nscript="recipes/op.py"\n'
        'outputs=["art/sources/hammer.blend", "art/models/hammer.glb"]\n'
    )
    inspection = json.loads(
        (Path(__file__).parent / "fixtures/parts/parts.json").read_text()
    )
    digest = parts._sha256(project / "art/sources/hammer.blend")
    cached = parts.cache_path(project, digest)
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text(json.dumps({"sha256": digest, "inspection": inspection}))


@pytest.mark.parametrize(
    "arguments",
    [
        {
            "resource": "res://art/models/hammer.glb",
            "part": {"material": "Steel", "image": "steel_rough"},
        },
        {"resource": "res://art/models/hammer.glb#material=Steel&image=steel_rough"},
        {
            "resource": "res://art/models/hammer.glb",
            "part": {"object": "Head.001", "material": "Steel", "image": "steel_rough"},
        },
    ],
    ids=["fields", "part-id", "picked-on-head"],
)
def test_a_selected_part_resolves_to_its_owning_tool(
    project: Path, arguments: dict
) -> None:
    hammer_with_parts(project)
    result = call(project, "resolve_source", arguments)
    assert not result.is_error, result.content[0].text
    asset = json.loads(result.content[0].text)
    assert asset["path"] == "art/models/hammer.glb" and asset["tool"] == "blender"
    part = asset["part"]
    assert part["owner"] == "blender"
    assert part["owner_source"] == "art/sources/hammer.blend"
    assert part["role"] == "roughness" and part["image"] == "steel_rough"
    assert part["texture"] == "art/textures/hammer/steel_rough.png"


@pytest.mark.parametrize(
    "part",
    [
        {"material": "Steel", "image": "steel_gloss"},
        {"material": "Gold"},
        {"object": "Handle", "material": "Steel", "image": "wood_orm"},
    ],
)
def test_an_unknown_part_is_an_error_not_an_invented_owner(
    project: Path, part: dict
) -> None:
    hammer_with_parts(project)
    result = call(
        project,
        "resolve_source",
        {"resource": "res://art/models/hammer.glb", "part": part},
    )
    assert result.is_error and "has no" in result.content[0].text
