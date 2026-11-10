"""GIMP's Python-Fu adapter: recipe scripts, replay, inspection and wiring.

The recipe script itself runs here against a small stand-in for GIMP's Python
API, so the export and refusal logic is exercised without pretending that GIMP
painted anything. Real pixels, .xcf saves and byte-identical replay are proven
in the worker by tests/integration/gimp_native.py.
"""

from __future__ import annotations

import asyncio
import copy
import itertools
import json
import os
import runpy
import sys
import types
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from mcp import Client

from joinery import authoring, tools
from joinery.recipe import Recipe
from joinery.runner import Result
from joinery.tools.base import McpProbe, WorkerExec
from joinery.tools.gimp import MODULE, PYTHON_FU, failure
from servers import authoring as authoring_server

_ids = itertools.count(1)


class FakeLayer:
    def __init__(
        self, name: str, children: list[FakeLayer] | None = None, visible: bool = True
    ) -> None:
        self.name, self.children, self.visible = name, children, visible
        self.id = next(_ids)

    def get_name(self) -> str:
        return self.name

    def get_id(self) -> int:
        return self.id

    def is_group(self) -> bool:
        return self.children is not None

    def get_children(self) -> list[FakeLayer]:
        return self.children or []

    def get_visible(self) -> bool:
        return self.visible

    def set_visible(self, visible: bool) -> None:
        self.visible = visible


class FakeImage:
    def __init__(self, layers: list[FakeLayer], world: World) -> None:
        self.layers, self.world, self.merged = layers, world, ""

    def get_layers(self) -> list[FakeLayer]:
        return self.layers

    def get_channel_by_name(self, name: str) -> object | None:
        return None

    def duplicate(self) -> FakeImage:
        return FakeImage(copy.deepcopy(self.layers), self.world)

    def merge_visible_layers(self, mode: object) -> None:
        def shown(items: list[FakeLayer], prefix: str = "") -> list[str]:
            names: list[str] = []
            for item in items:
                if item.visible:
                    names.append(prefix + item.name)
                    names += shown(item.get_children(), prefix + item.name + "/")
            return names

        self.merged = ",".join(shown(self.layers))

    def delete(self) -> None:
        self.world.deleted += 1


class World:
    """What the stand-in GIMP was asked to do."""

    def __init__(self, document: list[FakeLayer]) -> None:
        self.document = document
        self.loaded: list[str] = []
        self.saved: list[str] = []
        self.exported: dict[str, str] = {}
        self.settings: dict[str, Any] = {}
        self.deleted = 0

    def module(self) -> types.ModuleType:
        world = self

        class Config:
            def __init__(self) -> None:
                self.values: dict[str, Any] = {}

            def set_property(self, key: str, value: Any) -> None:
                self.values[key] = value

        class Procedure:
            def create_config(self) -> Config:
                return Config()

            def run(self, config: Config) -> Any:
                image = config.values["image"]
                world.exported[config.values["file"]] = image.merged
                world.settings = config.values
                return types.SimpleNamespace(index=lambda _: "SUCCESS")

        def file_load(mode: object, path: str) -> FakeImage:
            world.loaded.append(path)
            return FakeImage(world.document, world)

        def file_save(mode: object, image: FakeImage, path: str, options: None) -> bool:
            world.saved.append(path)
            return True

        gimp = types.SimpleNamespace(
            RunMode=types.SimpleNamespace(NONINTERACTIVE="noninteractive"),
            MergeType=types.SimpleNamespace(CLIP_TO_IMAGE="clip"),
            PDBStatusType=types.SimpleNamespace(SUCCESS="SUCCESS"),
            file_load=file_load,
            file_save=file_save,
            get_pdb=lambda: types.SimpleNamespace(
                lookup_procedure=lambda _: Procedure()
            ),
        )
        gio = types.SimpleNamespace(
            File=types.SimpleNamespace(new_for_path=lambda path: path)
        )
        repository = types.ModuleType("gi.repository")
        repository.Gimp = gimp  # type: ignore[attr-defined]
        repository.Gio = gio  # type: ignore[attr-defined]
        repository.Gegl = types.SimpleNamespace()  # type: ignore[attr-defined]
        return repository


def document() -> list[FakeLayer]:
    return [
        FakeLayer("height"),
        FakeLayer("details", [FakeLayer("cracks"), FakeLayer("moss", visible=False)]),
        FakeLayer("albedo"),
    ]


def run_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: str, exports: list[str]
) -> World:
    world = World(document())
    monkeypatch.setitem(sys.modules, "gi", types.ModuleType("gi"))
    monkeypatch.setitem(sys.modules, "gi.repository", world.module())
    monkeypatch.setattr(os, "chdir", lambda _: None)
    script = tmp_path / "recipe.py"
    script.write_text(
        MODULE.script("art/rock.xcf", "recipes/inputs/before.xcf", code, exports)
    )
    runpy.run_path(str(script))
    return world


def test_script_saves_xcf_then_exports_composite_and_single_layers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = run_script(
        tmp_path,
        monkeypatch,
        "layer_exports['art/rock_cracks.png'] = 'cracks'\n"
        "layer_exports['art/rock_height.png'] = 'height'",
        ["art/rock.png", "art/rock_cracks.png", "art/rock_height.png"],
    )
    assert world.loaded == [os.path.abspath("recipes/inputs/before.xcf")]
    assert world.saved == [os.path.abspath("art/rock.xcf")]
    assert world.exported == {
        # The composite keeps the document's own visibility.
        os.path.abspath("art/rock.png"): "height,details,details/cracks,albedo",
        # A layer inside a group is shown with its group, nothing else.
        os.path.abspath("art/rock_cracks.png"): "details,details/cracks",
        os.path.abspath("art/rock_height.png"): "height",
    }
    # Exports are reproducible: no timestamp or metadata in the PNG.
    for key in ("time", "include-exif", "include-xmp", "include-comment"):
        assert world.settings[key] is False
    # Every duplicate and the image itself are released.
    assert world.deleted == 4
    # The document itself was never re-shown or hidden by an export.
    assert [layer.visible for layer in world.document] == [True, True, True]


def test_unknown_layer_is_refused_before_anything_is_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(KeyError, match="No layer named 'heigth'"):
        run_script(
            tmp_path,
            monkeypatch,
            "layer_exports['art/rock.png'] = 'heigth'",
            ["art/rock.png"],
        )


def test_layer_export_must_be_a_declared_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(AssertionError, match="listed in exports"):
        run_script(
            tmp_path,
            monkeypatch,
            "layer_exports['art/other.png'] = 'height'",
            ["art/rock.png"],
        )


def test_new_image_script_requires_the_code_to_create_one() -> None:
    script = MODULE.script("art/new.xcf", "", "pass", [])
    compile(script, "recipe.py", "exec")
    assert "image = None" in script
    assert "file_load" not in script.split("image = None")[1]


def test_replay_runs_headless_gimp_with_python_and_a_private_profile() -> None:
    argv = MODULE.replay_argv("recipes/abc.py")
    assert argv[:3] == ["env", "GIMP3_DIRECTORY=/tmp/joinery-gimp", "gimp-console"]
    assert "--batch-interpreter=python-fu-eval" in argv and argv[-1] == "--quit"
    assert (
        argv[-2]
        == "import runpy; runpy.run_path('recipes/abc.py', run_name='__main__')"
    )


@pytest.mark.parametrize(
    "source,exports",
    [
        ("audio/rock.xcf", []),
        ("art/rock.xcf", ["art/rock.jpg"]),
        ("art/rock.xcf", ["audio/rock.png"]),
    ],
)
def test_destinations_are_checked_before_gimp_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str, exports: list[str]
) -> None:
    stage = staged(tmp_path)
    execute = AsyncMock()
    monkeypatch.setattr(authoring, "run_offline", execute)
    with pytest.raises(ValueError):
        asyncio.run(authoring.edit_document(stage, "gimp", source, "pass", exports))
    execute.assert_not_awaited()


def staged(tmp_path: Path) -> Path:
    stage = tmp_path / "transaction" / "stage"
    stage.mkdir(parents=True)
    (stage.parent / "journal.json").write_text(json.dumps({"state": "staging"}))
    (stage / "joinery.toml").write_text('[tools]\nenabled=["gimp"]\n')
    return stage


def test_edit_records_a_replayable_recipe_from_the_preimage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage = staged(tmp_path)
    (stage / "art").mkdir()
    (stage / "art/rock.xcf").write_bytes(b"gimp xcf v011")
    execute = AsyncMock(return_value=Result(True, "", "", 0.1))
    record = AsyncMock(return_value={"status": "staged"})
    monkeypatch.setattr(authoring, "run_offline", execute)
    monkeypatch.setattr(authoring, "_record", record)
    asyncio.run(
        authoring.edit_document(
            stage, "gimp", "art/rock.xcf", "pass", ["art/rock.png"], "note"
        )
    )
    recipe: Recipe = record.await_args.args[1]
    assert recipe.tool == "gimp" and recipe.script.endswith(".py")
    assert recipe.outputs == ("art/rock.xcf", "art/rock.png")
    (base,) = recipe.sources
    assert (stage / base).read_bytes() == b"gimp xcf v011"
    assert execute.await_args.args[0] == MODULE.replay_argv(recipe.script)
    compile((stage / recipe.script).read_text(), recipe.script, "exec")


NOISY = (
    "gimp_ui_theme_changed: error loading /tmp/joinery-gimp/theme.css\n"
    "GIMP-Warning: Welcome to GIMP 3.0.4!\n"
)


def test_inspection_is_read_only_and_reads_the_layer_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage = staged(tmp_path)
    (stage / "art").mkdir()
    (stage / "art/rock.xcf").write_bytes(b"gimp xcf v011")
    data = {"size": [64, 64], "layers": [{"name": "height"}], "channels": []}
    execute = AsyncMock(
        return_value=Result(
            True, "JOINERY_DOCUMENT:" + json.dumps(data) + "\n", NOISY, 1.0
        )
    )
    monkeypatch.setattr(authoring, "run_offline", execute)
    assert (
        asyncio.run(authoring.inspect_document(stage, "gimp", "art/rock.xcf")) == data
    )
    argv = execute.await_args.args[0]
    assert execute.await_args.kwargs == {"readonly": True}
    assert "gimp-console" in argv and "SOURCE = 'art/rock.xcf'" in argv[-2]
    assert (stage / "art/rock.xcf").read_bytes() == b"gimp xcf v011"


def test_failures_report_gimps_verdict_and_traceback_not_its_noise() -> None:
    output = (
        NOISY
        + "batch command experienced a calling error:\n"
        + "Traceback (most recent call last):\n"
        + '  File "<string>", line 1, in <module>\n'
        + "KeyError: 'No layer named heigth'\n"
    )
    reported = failure(output)
    assert reported.startswith("batch command experienced a calling error")
    assert "KeyError: 'No layer named heigth'" in reported
    assert "theme.css" not in reported


def test_readiness_requires_python_fu_and_the_paint_server() -> None:
    worker = [c for c in MODULE.checks if isinstance(c, WorkerExec)]
    scripting = next(c for c in worker if c.argv == PYTHON_FU)
    assert scripting.tier == 2 and not scripting.feature
    # The probe runs the adapter's own interpreter flags.
    assert "--batch-interpreter=python-fu-eval" in scripting.argv
    (probe,) = [c for c in MODULE.checks if isinstance(c, McpProbe)]
    assert (probe.server, probe.tool, probe.capability) == (
        "joinery-paint",
        "document_capabilities",
        "gimp",
    )
    assert MODULE.features == ()


def test_paint_server_operates_gimp_documents_and_checks_gimp_console(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "joinery.toml").write_text('[tools]\nenabled=["gimp"]\n')
    operation = AsyncMock(return_value={"status": "staged"})
    monkeypatch.setattr(authoring_server, "edit_document", operation)
    probe = AsyncMock(return_value=Result(True, '{"gimp": true}', "", 0.1))
    monkeypatch.setattr(authoring_server, "run_offline", probe)

    async def run() -> tuple[Any, Any]:
        server = authoring_server.make_server(tmp_path, "joinery-paint")
        async with Client(server) as client:
            edited = await client.call_tool(
                "operate_document",
                {
                    "tool": "gimp",
                    "source": "art/rock.xcf",
                    "code": "pass",
                    "exports": ["art/rock.png"],
                },
            )
            capabilities = await client.call_tool("document_capabilities", {})
            return edited, capabilities

    edited, capabilities = asyncio.run(run())
    assert not edited.is_error
    assert operation.await_args.args[1:4] == ("gimp", "art/rock.xcf", "pass")
    assert "'gimp': 'gimp-console'" in probe.await_args.args[0][-1]
    assert not capabilities.is_error
    assert "gimp" in tools.script_tools("joinery-paint")
