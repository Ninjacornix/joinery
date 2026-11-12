"""Native authoring contracts, with application execution replaced at its boundary.

These tests exercise staging, preimages, operation logs and failures. They do not
pretend that a mocked application has painted pixels or produced valid geometry.
"""

import asyncio
import json
import runpy
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from joinery import authoring
from joinery.recipe import Recipe
from joinery.runner import Result

DOCUMENTS = [
    pytest.param(
        "blender",
        "art/sources/hammer.blend",
        "art/models/hammer.glb",
        id="blender-model",
    ),
    pytest.param(
        "krita", "art/paint/icon.kra", "art/textures/icon.png", id="krita-texture"
    ),
    pytest.param(
        "supercollider",
        "audio/sources/impact.scd",
        "audio/sfx/impact.wav",
        id="supercollider-sfx",
    ),
    pytest.param(
        "surge-xt",
        "audio/sources/creak.json",
        "audio/sfx/creak.wav",
        id="surge-sfx",
    ),
]


@pytest.fixture
def stage(tmp_path: Path) -> Path:
    project = tmp_path / "transaction" / "stage"
    project.mkdir(parents=True)
    (project.parent / "journal.json").write_text(json.dumps({"state": "staging"}))
    (project / "joinery.toml").write_text(
        '[tools]\nenabled=["blender","krita","supercollider","material-maker","inkscape","surge-xt"]\n'
    )
    return project


@pytest.mark.parametrize("tool", sorted(authoring.SUPPORTED))
@pytest.mark.parametrize("state", ["review", "accepted", "rejected", "committing"])
def test_native_edit_requires_active_staging(
    stage: Path, tool: str, state: str
) -> None:
    (stage.parent / "journal.json").write_text(json.dumps({"state": state}))
    with pytest.raises(PermissionError, match="transaction"):
        authoring.enabled(stage, tool)


@pytest.mark.parametrize("tool", sorted(authoring.SUPPORTED))
def test_unselected_owner_cannot_author(stage: Path, tool: str) -> None:
    (stage / "joinery.toml").write_text('[tools]\nenabled=["godot"]\n')
    with pytest.raises(PermissionError, match="not selected"):
        authoring.enabled(stage, tool)


# Removed from the catalog; they must never come back through an old project's
# tool list.
@pytest.mark.parametrize("tool", ["aseprite", "audacity", "reaper", "musescore"])
def test_desktop_only_or_planned_tools_do_not_claim_native_authoring(
    stage: Path, tool: str
) -> None:
    with pytest.raises(ValueError, match="No native document adapter"):
        authoring.enabled(stage, tool)


@pytest.mark.parametrize("tool,source,export", DOCUMENTS)
@pytest.mark.parametrize("existing", [False, True], ids=["create", "edit-existing"])
def test_document_edit_retains_preimage_and_records_every_export(
    stage: Path,
    monkeypatch: pytest.MonkeyPatch,
    tool: str,
    source: str,
    export: str,
    existing: bool,
) -> None:
    native = stage / source
    native.parent.mkdir(parents=True)
    original = b"original native document, named parts/layers retained"
    if existing:
        native.write_bytes(original)
    execute = AsyncMock(return_value=Result(True, "saved", "", 0.01))
    record = AsyncMock(return_value={"status": "staged", "requires_review": True})
    monkeypatch.setattr(authoring, "run_offline", execute)
    monkeypatch.setattr(authoring, "_record", record)
    code = "# user operation: preserve the existing named parts"
    result = asyncio.run(
        authoring.edit_document(stage, tool, source, code, [export], "requested edit")
    )
    assert result == {"status": "staged", "requires_review": True}
    recipe: Recipe = record.await_args.args[1]
    assert recipe.tool == tool
    assert recipe.outputs == (source, export)
    assert recipe.note == "requested edit"
    script = stage / recipe.script
    assert code in script.read_text()
    assert (stage / export).parent.is_dir()
    assert execute.await_args.args[1] == stage
    if existing:
        assert len(recipe.sources) == 1
        assert (stage / recipe.sources[0]).read_bytes() == original
        assert (stage / recipe.sources[0]).parent.joinpath(".gdignore").is_file()
        # Text sources (SuperCollider, Surge) are written as the edit itself.
        if tool not in {"supercollider", "surge-xt"}:
            assert native.read_bytes() == original  # the fake app never saves
    else:
        assert recipe.sources == ()
    if tool in {"blender", "krita"}:
        compile(script.read_text(), str(script), "exec")
    if tool == "blender":
        assert (native.parent / ".gdignore").is_file()


@pytest.mark.parametrize("tool,source,export", DOCUMENTS)
def test_native_application_failure_never_records_success(
    stage: Path, monkeypatch: pytest.MonkeyPatch, tool: str, source: str, export: str
) -> None:
    record = AsyncMock()
    monkeypatch.setattr(authoring, "_record", record)
    monkeypatch.setattr(
        authoring,
        "run_offline",
        AsyncMock(return_value=Result(False, "", "native save failed", 0.01)),
    )
    with pytest.raises(RuntimeError, match="native save failed"):
        asyncio.run(authoring.edit_document(stage, tool, source, "operation", [export]))
    record.assert_not_awaited()


@pytest.mark.parametrize(
    "source,exports",
    [
        ("art/models/hammer.blend", ["art/models/hammer.glb"]),
        ("art/sources/hammer.blend", ["art/sources/hammer.glb"]),
        ("art/sources/hammer.blend", ["../escape.glb"]),
        ("art/sources/hammer.blend", ["scripts/hammer.glb"]),
        ("../hammer.blend", ["art/models/hammer.glb"]),
        ("art/sources/hammer.fbx", ["art/models/hammer.glb"]),
    ],
)
def test_invalid_blender_destinations_fail_before_launch(
    stage: Path, monkeypatch: pytest.MonkeyPatch, source: str, exports: list[str]
) -> None:
    execute = AsyncMock()
    monkeypatch.setattr(authoring, "run_offline", execute)
    with pytest.raises(ValueError):
        asyncio.run(authoring.edit_document(stage, "blender", source, "pass", exports))
    execute.assert_not_awaited()


def test_blender_automatic_export_refuses_fbx_before_launch(
    stage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    execute = AsyncMock()
    monkeypatch.setattr(authoring, "run_offline", execute)
    with pytest.raises(ValueError, match="uses GLB"):
        asyncio.run(
            authoring.edit_document(
                stage,
                "blender",
                "art/sources/hammer.blend",
                "pass",
                ["art/models/hammer.fbx"],
            )
        )
    execute.assert_not_awaited()


def test_krita_layer_inspection_is_read_only_and_preserves_nested_layers(
    stage: Path,
) -> None:
    path = stage / "art/portrait.kra"
    path.parent.mkdir()
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "maindoc.xml",
            '<DOC xmlns="urn:krita"><IMAGE><layers><layer name="Ink" nodetype="paintlayer"/><layer name="Group" nodetype="grouplayer"><layer name="Colour"/></layer></layers></IMAGE></DOC>',
        )
    before = path.read_bytes()
    result = asyncio.run(authoring.inspect_document(stage, "krita", "art/portrait.kra"))
    assert [layer["name"] for layer in result["layers"]] == ["Ink", "Group", "Colour"]
    assert result["layers"][0]["nodetype"] == "paintlayer"
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "tool,source",
    [
        ("supercollider", "audio/source.scd"),
        ("supercollider", "audio/source.sc"),
        ("material-maker", "art/stone.ptex"),
        ("inkscape", "art/icon.svg"),
    ],
)
def test_native_text_inspection_preserves_unicode_and_source(
    stage: Path, tool: str, source: str
) -> None:
    path = stage / source
    path.parent.mkdir(parents=True)
    content = "native source — café / Čekić / 音楽"
    path.write_text(content, encoding="utf-8")
    assert asyncio.run(authoring.inspect_document(stage, tool, source)) == {
        "source": source,
        "content": content,
    }
    assert path.read_text(encoding="utf-8") == content


def test_large_graph_inspection_has_bounded_response(stage: Path) -> None:
    path = stage / "art/large.ptex"
    path.parent.mkdir()
    path.write_text("x" * 2_000_001)
    with pytest.raises(ValueError, match="text limit"):
        asyncio.run(
            authoring.inspect_document(stage, "material-maker", "art/large.ptex")
        )


def test_blender_inspection_uses_readonly_worker_and_named_geometry(
    stage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = stage / "art/sources/hammer.blend"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"native fixture")
    expected = {"objects": [{"name": "Head", "vertices": 8}], "materials": ["Steel"]}
    execute = AsyncMock(
        return_value=Result(
            True, "Blender startup\nJOINERY_DOCUMENT:" + json.dumps(expected), "", 0.01
        )
    )
    monkeypatch.setattr(authoring, "run_offline", execute)
    assert (
        asyncio.run(
            authoring.inspect_document(stage, "blender", "art/sources/hammer.blend")
        )
        == expected
    )
    assert execute.await_args.kwargs == {"readonly": True}
    assert execute.await_args.args[0][:3] == [
        "blender",
        "--background",
        "art/sources/hammer.blend",
    ]
    assert path.read_bytes() == b"native fixture"


@pytest.mark.parametrize("output", ["", "Blender could not read this file"])
def test_blender_inspection_never_reports_fake_empty_success(
    stage: Path, monkeypatch: pytest.MonkeyPatch, output: str
) -> None:
    path = stage / "art/test.blend"
    path.parent.mkdir()
    path.write_bytes(b"invalid native document")
    monkeypatch.setattr(
        authoring,
        "run_offline",
        AsyncMock(return_value=Result(False, output, "", 0.01)),
    )
    with pytest.raises(ValueError, match="could not inspect"):
        asyncio.run(authoring.inspect_document(stage, "blender", "art/test.blend"))


@pytest.mark.parametrize(
    "svg",
    [
        "<svg><script>alert(1)</script></svg>",
        '<!DOCTYPE svg [<!ENTITY ext SYSTEM "file:///etc/passwd">]><svg/>',
        '<svg><image href="https://example.com/a.png"/></svg>',
        '<svg><path style="fill:url(https://example.com/a.svg)"/></svg>',
        '<svg><image href="file:///tmp/texture.png"/></svg>',
    ],
)
def test_vector_edit_rejects_external_content_before_app_execution(
    stage: Path, monkeypatch: pytest.MonkeyPatch, svg: str
) -> None:
    execute = AsyncMock()
    monkeypatch.setattr(authoring, "run_offline", execute)
    with pytest.raises(ValueError, match="self-contained"):
        asyncio.run(authoring.edit_vector(stage, "art/icon.svg", svg, "art/icon.png"))
    execute.assert_not_awaited()


def test_vector_edit_keeps_element_ids_and_uses_literal_export_argv(
    stage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    export = AsyncMock(return_value={"status": "staged"})
    monkeypatch.setattr(authoring, "_scripted_export", export)
    svg = '<svg xmlns="http://www.w3.org/2000/svg"><rect id="user-outline" fill="#a00"/></svg>'
    result = asyncio.run(
        authoring.edit_vector(
            stage, "art/icon's outline.svg", svg, "art/icon export.png"
        )
    )
    assert result == {"status": "staged"}
    code = export.await_args.args[3]
    compile(code, "<vector export>", "exec")
    assert export.await_args.args[-1] == ["art/icon export.png"]
    # Execute only the generated file-write and subprocess boundary; no app mock
    # is allowed to claim that actual Inkscape pixels have been checked here.
    import os
    from unittest.mock import patch

    monkeypatch.chdir(stage)
    script = stage / "vector-operation.py"
    script.write_text(code)
    with patch("subprocess.run") as run:
        runpy.run_path(str(script))
    assert (stage / "art/icon's outline.svg").read_text() == svg
    assert run.call_args.args[0] == [
        "inkscape",
        "art/icon's outline.svg",
        "--export-filename=art/icon export.png",
    ]
    assert run.call_args.kwargs == {"check": True}
    assert os.getcwd() == str(stage)


def test_material_graph_round_trip_preserves_ids_links_and_parameters(
    stage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    export = AsyncMock(return_value={"status": "staged"})
    monkeypatch.setattr(authoring, "_scripted_export", export)
    graph = {
        "nodes": [
            {"id": "paint", "type": "uniform", "colour": [1, 0.2, 0.1, 1]},
            {"id": "material", "roughness": 0.25},
        ],
        "connections": [{"from": "paint", "to": "material"}],
    }
    original = json.loads(json.dumps(graph))
    asyncio.run(
        authoring.edit_graph(stage, "art/materials/steel.ptex", graph, "art/textures")
    )
    code = export.await_args.args[3]
    monkeypatch.chdir(stage)
    (stage / "recipes").mkdir()
    from unittest.mock import patch

    script = stage / "graph-operation.py"
    script.write_text(code)
    with patch("subprocess.run") as run:
        runpy.run_path(str(script))
    assert json.loads((stage / "art/materials/steel.ptex").read_text()) == original
    assert graph == original
    assert run.call_args.kwargs == {"check": True}
    assert "--main-pack" in run.call_args.args[0]
    driver = next((stage / "recipes").glob("*.export.gd")).read_text()
    assert "prompt_overwrite" in driver and "Godot/Godot 4 Standard" in driver


def test_no_material_export_is_an_error_not_a_staged_success(
    stage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (stage / "art/exports").mkdir(parents=True)
    monkeypatch.setattr(
        authoring, "run_offline", AsyncMock(return_value=Result(True, "", "", 0.01))
    )
    record = AsyncMock()
    monkeypatch.setattr(authoring, "_record", record)
    with pytest.raises(ValueError, match="no exports"):
        asyncio.run(
            authoring._scripted_export(
                stage, "material-maker", "art/stone.ptex", "pass", "art/exports"
            )
        )
    record.assert_not_awaited()


def test_blender_textures_are_separate_recorded_outputs(
    stage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    textures = stage / "art/textures/hammer"
    textures.mkdir(parents=True)
    (textures / "Removed_Image.png").write_bytes(b"stale")

    async def run(_argv: list[str], project: Path, *_args: object) -> Result:
        # The worker writes the images the model still has.
        (project / "art/textures/hammer/Head_BaseColor.png").write_bytes(b"png")
        return Result(True, "saved", "", 0.01)

    record = AsyncMock(return_value={"status": "staged", "requires_review": True})
    monkeypatch.setattr(authoring, "run_offline", run)
    monkeypatch.setattr(authoring, "_record", record)
    asyncio.run(
        authoring.edit_document(
            stage,
            "blender",
            "art/sources/hammer.blend",
            "pass",
            ["art/models/hammer.glb"],
        )
    )
    recipe: Recipe = record.await_args.args[1]
    assert recipe.outputs == (
        "art/sources/hammer.blend",
        "art/models/hammer.glb",
        "art/textures/hammer/Head_BaseColor.png",
    )
    assert not (textures / "Removed_Image.png").exists()
    script = (stage / recipe.script).read_text()
    assert "_joinery_export_textures" in script and "art/textures/hammer" in script


def test_godot_paths_name_the_same_project_files(
    stage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An agent copies a model's path from a part's resource (res://...). The
    # edit must take it, not refuse it as outside art/ and audio/.
    async def run(_argv: list[str], _project: Path, *_args: object) -> Result:
        return Result(True, "saved", "", 0.01)

    record = AsyncMock(return_value={"status": "staged", "requires_review": True})
    monkeypatch.setattr(authoring, "run_offline", run)
    monkeypatch.setattr(authoring, "_record", record)
    asyncio.run(
        authoring.edit_document(
            stage,
            "blender",
            "res://art/sources/hammer.blend",
            "pass",
            ["res://art/models/hammer.glb"],
        )
    )
    recipe: Recipe = record.await_args.args[1]
    assert recipe.outputs[:2] == ("art/sources/hammer.blend", "art/models/hammer.glb")
    script = (stage / recipe.script).read_text()
    assert "res://" not in script


def test_edit_action_takes_godot_paths_on_its_first_call(
    stage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from servers.blender_ops import animation

    (stage / "art/sources").mkdir(parents=True)
    (stage / "art/sources/hero.blend").write_bytes(b"BLENDER")

    async def run(_argv: list[str], _project: Path, *_args: object) -> Result:
        return Result(True, "saved", "", 0.01)

    record = AsyncMock(return_value={"status": "staged", "requires_review": True})
    monkeypatch.setattr(authoring, "run_offline", run)
    monkeypatch.setattr(authoring, "_record", record)
    asyncio.run(
        animation.edit_action(
            stage,
            "res://art/sources/hero.blend",
            "run",
            [{"op": "retime", "speed": 1.25}],
            "",
            ["res://art/models/hero.glb"],
        )
    )
    recipe: Recipe = record.await_args.args[1]
    assert recipe.outputs[:2] == ("art/sources/hero.blend", "art/models/hero.glb")


# --- part selection: export names, typed part edits, scope guard ---------------


def test_blender_export_carries_part_names_and_maps() -> None:
    from joinery.tools import blender

    script = blender.MODULE.script(
        "art/sources/hammer.blend", "", "pass", ["art/models/hammer.glb"]
    )
    assert "export_extras=True" in script
    assert "joinery_maps" in script and "joinery_name" in script
    # Names are written after the source is saved, so only the export has them.
    assert script.index("save_as_mainfile") < script.index("_joinery_name_parts()")
    assert script.index("_joinery_name_parts()") < script.index("export_scene.gltf")


def test_inspection_reports_slots_and_what_each_map_feeds() -> None:
    from joinery.tools import blender

    assert "'slots'" in blender.INSPECT_BLEND
    assert "'uses': uses" in blender.PART_WALK
    for via in ("'BUMP'", "'DISPLACEMENT'", "'NORMAL_MAP'", "SEPARATE_COLOR"):
        assert via in blender.PART_WALK


def test_adjust_map_sets_the_colour_space_before_touching_pixels() -> None:
    from joinery.tools import blender

    code = blender.adjust_map_code("Steel", "steel_rough", "invert", {})
    body = code[code.index("def _joinery_adjust_map") :]
    assert body.index("colorspace_settings.name = space") < body.index(
        "pixels.foreach_get"
    )
    assert body.index("pixels.foreach_set") < body.index("picture.pack()")
    # The scope guard wraps the operation and only the map may change.
    assert code.index("_joinery_scope_hashes()") < code.index("_joinery_adjust_map(")
    assert "set(['image:steel_rough'])" in code


def test_bake_writes_a_non_color_image_before_baking() -> None:
    from joinery.tools import blender

    code = blender.bake_height_code("Steel", "steel_height", 1.0)
    body = code[code.index("def _joinery_bake_height_to_normal") :]
    assert body.index("colorspace_settings.name = 'Non-Color'") < body.index(
        "object.bake("
    )
    assert "normal_space='TANGENT'" in body and "ShaderNodeNormalMap" in body
    assert "set(['image:Steel_normal_baked', 'material:Steel'])" in code


def test_scope_guard_lists_only_the_named_parts() -> None:
    from joinery.tools import blender

    code = blender.guarded("pass", materials=("Steel",), objects=("Head.001",))
    assert "set(['material:Steel', 'object:Head.001'])" in code
    assert "outside the selected part. Nothing was saved." in code


def part_stage(stage: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    from joinery import parts

    (stage / "recipes").mkdir()
    (stage / "recipes/hammer.toml").write_text(
        'version = 1\ntool = "blender"\nscript = "recipes/hammer.py"\n'
        'outputs = ["art/sources/hammer.blend", "art/models/hammer.glb"]\n'
        'sources = []\nnote = ""\n'
    )
    parts.write_targets(
        stage,
        [
            {
                "id": "res://art/models/hammer.glb#material=Steel&image=steel_rough",
                "label": "Roughness · Steel",
                "part_kind": "texture",
                "source": "res://art/sources/hammer.blend",
                "owner_source": "art/sources/hammer.blend",
                "material": "Steel",
                "image": "steel_rough",
            }
        ],
    )
    edit = AsyncMock(return_value={"status": "staged", "requires_review": True})
    monkeypatch.setattr("servers.authoring.edit_document", edit)
    return edit


def call_op(stage: Path, tool: str, arguments: dict[str, object]) -> object:
    from mcp import Client

    from servers import authoring as server

    async def run() -> object:
        async with Client(server.make_server(stage, "joinery-blender-ops")) as client:
            return await client.call_tool(tool, arguments)

    return asyncio.run(run())


def test_adjust_map_edits_the_selected_map_and_keeps_the_glb_export(
    stage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    edit = part_stage(stage, monkeypatch)
    result = call_op(
        stage,
        "adjust_map",
        {
            "source": "art/sources/hammer.blend",
            "material": "Steel",
            "image": "steel_rough",
            "op": "levels",
            "params": {"in_low": 0.1, "gamma": 1.2},
        },
    )
    assert not result.is_error, result.content[0].text
    args = edit.await_args.args
    assert args[1:3] == ("blender", "art/sources/hammer.blend")
    assert "_joinery_adjust_map('Steel', 'steel_rough', 'levels'" in args[3]
    assert args[4] == ["art/models/hammer.glb"]


@pytest.mark.parametrize(
    ("arguments", "reason"),
    [
        (
            {"material": "Steel", "image": "steel_base", "op": "invert"},
            "not part of the selection",
        ),
        (
            {"material": "Wood", "image": "wood_orm", "op": "invert"},
            "not part of the selection",
        ),
        (
            {"material": "Steel", "image": "steel_rough", "op": "blur"},
            "Unknown map adjustment",
        ),
        (
            {
                "material": "Steel",
                "image": "steel_rough",
                "op": "invert",
                "params": {"radius": 2},
            },
            "does not take radius",
        ),
        (
            {
                "material": "Steel",
                "image": "steel_rough",
                "op": "gamma",
                "params": {"gamma": 0},
            },
            "above 0",
        ),
        (
            {
                "material": "Steel",
                "image": "steel_rough",
                "op": "tint",
                "params": {"color": [2, 0, 0]},
            },
            "0 to 1",
        ),
    ],
)
def test_adjust_map_refuses_other_parts_and_unknown_adjustments(
    stage: Path,
    monkeypatch: pytest.MonkeyPatch,
    arguments: dict[str, object],
    reason: str,
) -> None:
    edit = part_stage(stage, monkeypatch)
    result = call_op(
        stage, "adjust_map", {"source": "art/sources/hammer.blend", **arguments}
    )
    assert result.is_error and reason in result.content[0].text
    edit.assert_not_awaited()


def test_material_value_and_bake_respect_the_selection(
    stage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    edit = part_stage(stage, monkeypatch)
    refused = call_op(
        stage,
        "set_material_value",
        {
            "source": "art/sources/hammer.blend",
            "material": "Wood",
            "socket": "Roughness",
            "value": 0.4,
        },
    )
    assert refused.is_error and "not part of the selection" in refused.content[0].text
    refused = call_op(
        stage,
        "bake_height_to_normal",
        {
            "source": "art/sources/hammer.blend",
            "material": "Steel",
            "height_image": "steel_height",
        },
    )
    assert refused.is_error and "'steel_height' is not part" in refused.content[0].text
    edit.assert_not_awaited()
    accepted = call_op(
        stage,
        "set_material_value",
        {
            "source": "art/sources/hammer.blend",
            "material": "Steel",
            "socket": "Roughness",
            "value": 0.4,
        },
    )
    assert not accepted.is_error
    assert (
        "_joinery_set_material_value('Steel', 'Roughness', 0.4)"
        in edit.await_args.args[3]
    )


@pytest.mark.parametrize("fails", ["refused", "raises"])
def test_a_failed_blender_edit_leaves_the_texture_files_in_place(
    stage: Path, monkeypatch: pytest.MonkeyPatch, fails: str
) -> None:
    textures = stage / "art/textures/hammer"
    textures.mkdir(parents=True)
    (textures / "steel_rough.png").write_bytes(b"pixels")

    async def run(_argv: object, _project: Path) -> Result:
        # The old maps are cleared before Blender runs.
        assert not (textures / "steel_rough.png").exists()
        if fails == "raises":
            raise RuntimeError("This edit also changed material Wood.")
        return Result(False, "", "This edit also changed material Wood.", 0.01)

    monkeypatch.setattr(authoring, "run_offline", run)
    with pytest.raises(RuntimeError, match="material Wood"):
        asyncio.run(
            authoring.edit_document(
                stage,
                "blender",
                "art/sources/hammer.blend",
                "pass",
                ["art/models/hammer.glb"],
            )
        )
    assert (textures / "steel_rough.png").read_bytes() == b"pixels"
