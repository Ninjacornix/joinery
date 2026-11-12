"""MPFB2 macro shape keys: baked into the exported mesh by default, kept on request.

Blender is replaced at its boundary; tests/integration/blender_rig.py exports a
real MPFB2 human both ways.
"""

from __future__ import annotations

import asyncio
import json
import struct
from pathlib import Path
from typing import Any

import pytest

from joinery import authoring
from joinery.tools import blender, blender_export

SOURCE = "art/sources/hammer.blend"
MODEL = "art/models/hammer.glb"


def test_macro_keys_are_the_mpfb2_body_shape_targets_only() -> None:
    names = [
        "$md-$as-$fe-$yn",
        "$md-universal-$ma-$yn-$av$mu-$av$wg",
        "Dent",
        "smile",
        "md-x",
    ]
    assert blender_export.macro_split(names) == (names[:2], ["Dent", "smile", "md-x"])
    assert not blender_export.is_macro_key("Basis")


def test_the_export_bakes_macros_by_default_and_records_the_choice() -> None:
    script = blender.MODULE.script(SOURCE, "", "pass", [MODEL])
    assert "_joinery_bake_shape_keys(keep_macros=False, keep=None)" in script
    # After the save (the .blend keeps every key) and before the per-key
    # modifier bake, so fewer keys are baked.
    call = "_joinery_bake_shape_keys(keep_macros="
    assert script.index("save_as_mainfile") < script.index(call)
    assert script.index(call) < script.index("_joinery_bake_for_export()\n")
    assert "'$md-'" in script
    kept = blender.MODULE.script(
        SOURCE, "", "pass", [MODEL], keep_macro_shape_keys=True
    )
    assert blender_export.recorded_keep(kept) is True
    assert blender_export.recorded_keep(script) is False
    assert blender_export.recorded_keep("print(1)") is None
    # Recipes made before the game's keys could be chosen still read.
    assert blender_export.recorded_keep("_joinery_bake_macro_keys(keep=True)") is True
    assert "_joinery_bake_shape_keys(" not in blender.MODULE.script(
        SOURCE, "", "pass", []
    )


def _stage(tmp_path: Path) -> Path:
    stage = tmp_path / "tx" / "stage"
    stage.mkdir(parents=True)
    (stage.parent / "journal.json").write_text(json.dumps({"state": "staging"}))
    (stage / "joinery.toml").write_text(
        '[tools]\nenabled=["blender","krita","material-maker"]\n'
    )
    return stage


@pytest.mark.parametrize(
    ("given", "recorded", "wanted"),
    [
        (None, None, False),
        (None, True, True),
        (False, True, False),
        (True, None, True),
    ],
)
def test_a_model_keeps_its_macro_choice_until_told_otherwise(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    given: bool | None,
    recorded: bool | None,
    wanted: bool,
) -> None:
    stage = _stage(tmp_path)
    if recorded is not None:
        (stage / "recipes").mkdir()
        (stage / "recipes/old.py").write_text(
            blender.MODULE.script(
                SOURCE, "", "pass", [MODEL], keep_macro_shape_keys=recorded
            )
        )
        (stage / "recipes/old.toml").write_text(
            'version = 1\ntool = "blender"\nscript = "recipes/old.py"\n'
            f'outputs = ["{SOURCE}", "{MODEL}"]\n'
        )
    seen: list[str] = []

    async def run(argv: list[str], project: Path, *_args: Any, **_kw: Any) -> Any:
        seen.append((project / argv[-1]).read_text())
        raise RuntimeError("stop before recording")

    monkeypatch.setattr(authoring, "run_offline", run)
    with pytest.raises(RuntimeError, match="stop"):
        asyncio.run(
            authoring.edit_document(
                stage, "blender", SOURCE, "pass", [MODEL], keep_macro_shape_keys=given
            )
        )
    assert blender_export.recorded_keep(seen[0]) is wanted


def _glb(meshes: list[dict[str, Any]], accessors: list[dict[str, Any]]) -> bytes:
    body = json.dumps({"meshes": meshes, "accessors": accessors}).encode()
    body += b" " * (-len(body) % 4)
    return (
        b"glTF"
        + struct.pack("<II", 2, 20 + len(body))
        + struct.pack("<I4s", len(body), b"JSON")
        + body
    )


def test_the_export_reports_vertices_and_morph_targets_per_mesh(tmp_path: Path) -> None:
    path = tmp_path / "human.glb"
    path.write_bytes(
        _glb(
            [
                {
                    "name": "Hero",
                    "extras": {"targetNames": ["smile"]},
                    "primitives": [
                        {"attributes": {"POSITION": 0}, "targets": [{"POSITION": 1}]}
                    ],
                }
            ],
            [{"count": 13380}, {"count": 13380}],
        )
    )
    assert blender_export.glb_meshes(path) == [
        {
            "mesh": "Hero",
            "vertices": 13380,
            "morph_targets": 1,
            "target_names": ["smile"],
        }
    ]
    output = 'x\nJOINERY_EXPORT_SHAPES:{"object": "Hero", "baked": 8, "kept": 0}\n'
    assert blender_export.shape_rows(output) == [
        {"object": "Hero", "baked": 8, "kept": 0}
    ]


def test_operate_document_takes_the_macro_option_for_blender_only(
    tmp_path: Path,
) -> None:
    from mcp import Client

    from servers.authoring import make_server

    async def call() -> Any:
        async with Client(make_server(tmp_path, "joinery-paint")) as client:
            return await client.call_tool(
                "operate_document",
                {
                    "tool": "krita",
                    "source": "art/a.kra",
                    "code": "pass",
                    "exports": [],
                    "keep_macro_shape_keys": True,
                },
            )

    result = asyncio.run(call())
    assert result.is_error and "Blender export option" in result.content[0].text


def test_the_game_keys_are_recorded_and_read_back() -> None:
    chosen = blender_export.ExportShapeKeys(keep=("smile", "viseme_aa"))
    script = blender.MODULE.script(SOURCE, "", "pass", [MODEL], shape_keys=chosen)
    assert (
        "_joinery_bake_shape_keys(keep_macros=False, keep=('smile', 'viseme_aa'))"
        in script
    )
    assert blender_export.recorded_shape_keys(script) == chosen
    none = blender_export.ExportShapeKeys(keep=())
    assert blender_export.recorded_shape_keys(none.call()) == none
    # Detail targets are known from MPFB2's own target files, not guessed.
    assert "_joinery_mpfb_targets()" in script and "'expression'" in script


def test_a_model_keeps_its_game_keys_until_told_otherwise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage = _stage(tmp_path)
    chosen = blender_export.ExportShapeKeys(keep=("smile",))
    (stage / "recipes").mkdir()
    (stage / "recipes/old.py").write_text(
        blender.MODULE.script(SOURCE, "", "pass", [MODEL], shape_keys=chosen)
    )
    (stage / "recipes/old.toml").write_text(
        'version = 1\ntool = "blender"\nscript = "recipes/old.py"\n'
        f'outputs = ["{SOURCE}", "{MODEL}"]\n'
    )
    seen: list[str] = []

    async def run(argv: list[str], project: Path, *_args: Any, **_kw: Any) -> Any:
        seen.append((project / argv[-1]).read_text())
        raise RuntimeError("stop before recording")

    monkeypatch.setattr(authoring, "run_offline", run)
    for keep in (None, True):
        with pytest.raises(RuntimeError, match="stop"):
            asyncio.run(
                authoring.edit_document(
                    stage,
                    "blender",
                    SOURCE,
                    "pass",
                    [MODEL],
                    keep_macro_shape_keys=keep,
                )
            )
    assert blender_export.recorded_shape_keys(seen[0]) == chosen
    assert blender_export.recorded_shape_keys(
        seen[1]
    ) == blender_export.ExportShapeKeys(keep_macros=True, keep=("smile",))
