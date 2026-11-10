"""Material Maker graphs: image inputs are checked, recorded and kept project-relative."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from joinery import authoring
from joinery.mcp_runtime import PolicyStop
from joinery.recipe import Recipe
from joinery.runner import Result
from joinery.tools import get
from joinery.tools.material_maker import HOME, graph_code, image_inputs

MM = get("material-maker")
LOCK = 'version = 1\n\n[[asset]]\npath = "art/imported/rock.png"\nsha256 = "00"\norigin = "human-import"\n'


@pytest.fixture
def stage(tmp_path: Path) -> Path:
    project = tmp_path / "transaction" / "stage"
    project.mkdir(parents=True)
    (project.parent / "journal.json").write_text(json.dumps({"state": "staging"}))
    (project / "joinery.toml").write_text(
        '[tools]\nenabled=["godot","material-maker"]\n'
    )
    (project / "joinery.lock").write_text(LOCK)
    (project / "art/imported").mkdir(parents=True)
    (project / "art/imported/rock.png").write_bytes(b"png")
    return project


def graph(path: Any, nested: bool = False) -> dict[str, Any]:
    image = {"type": "image", "name": "Height", "parameters": {"image": path}}
    nodes: list[dict[str, Any]] = [
        {"type": "material", "name": "Material", "parameters": {}}
    ]
    nodes.append(
        {"type": "graph", "name": "Group", "nodes": [image], "connections": []}
        if nested
        else image
    )
    return {"type": "graph", "name": "Rock", "nodes": nodes, "connections": []}


def image_of(document: dict[str, Any], nested: bool = False) -> str:
    node = document["nodes"][1]
    node = node["nodes"][0] if nested else node
    return str(node["parameters"]["image"])


@pytest.mark.parametrize(
    "path",
    [
        "art/imported/rock.png",
        "res://art/imported/rock.png",
        "/work/art/imported/rock.png",
        "art\\imported\\rock.png",
    ],
)
@pytest.mark.parametrize("nested", [False, True], ids=["top", "subgraph"])
def test_image_inputs_are_stored_project_relative_and_run_from_work(
    stage: Path, path: str, nested: bool
) -> None:
    original = graph(path, nested)
    stored, runtime, inputs = image_inputs(
        stage, original, "art/materials/rock.ptex", "art/materials/export"
    )
    assert inputs == ("art/imported/rock.png",)
    assert image_of(stored, nested) == "art/imported/rock.png"
    assert image_of(runtime, nested) == "/work/art/imported/rock.png"
    assert image_of(original, nested) == path, "the caller's graph is not changed"


@pytest.mark.parametrize(
    ("path", "message"),
    [
        ("/etc/passwd", "under art/"),
        ("/work/../etc/passwd", "under art/"),
        ("art/../joinery.toml", "under art/"),
        ("C:/Users/me/rock.png", "under art/"),
        ("file:///work/art/imported/rock.png", "under art/"),
        ("audio/rock.png", "under art/"),
        ("", "needs an image file"),
        (None, "needs an image file"),
        ("art/imported/missing.png", "not a file"),
        ("art/materials/export/rock_albedo.png", "export directory"),
    ],
)
def test_image_inputs_outside_the_project_or_unrecorded_are_refused(
    stage: Path, path: Any, message: str
) -> None:
    (stage / "art/materials/export").mkdir(parents=True)
    (stage / "art/materials/export/rock_albedo.png").write_bytes(b"png")
    with pytest.raises(ValueError, match=message):
        image_inputs(
            stage, graph(path), "art/materials/rock.ptex", "art/materials/export"
        )


def test_symlink_out_of_the_project_is_refused(stage: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"png")
    (stage / "art/imported/link.png").symlink_to(outside)
    with pytest.raises(ValueError, match="not a file in this project"):
        image_inputs(stage, graph("art/imported/link.png"), "art/m.ptex", "art/export")


def test_image_without_provenance_is_refused(stage: Path) -> None:
    (stage / "art/imported/found.png").write_bytes(b"png")
    with pytest.raises(ValueError, match="no joinery.lock entry"):
        image_inputs(stage, graph("art/imported/found.png"), "art/m.ptex", "art/export")


def test_image_made_by_a_recipe_counts_as_known(stage: Path) -> None:
    (stage / "art/painted.png").write_bytes(b"png")
    (stage / "recipes").mkdir()
    (stage / "recipes/paint.toml").write_text(
        'version = 1\ntool = "krita"\nscript = "recipes/paint.py"\n'
        'outputs = ["art/painted.kra", "art/painted.png"]\n'
    )
    _, _, inputs = image_inputs(
        stage, graph("art/painted.png"), "art/m.ptex", "art/export"
    )
    assert inputs == ("art/painted.png",)


def test_graph_without_images_keeps_its_recipe_script_unchanged() -> None:
    plain = {"type": "graph", "nodes": [{"type": "material", "name": "M"}]}
    assert graph_code("art/a.ptex", plain, "art/out", plain) == graph_code(
        "art/a.ptex", plain, "art/out"
    )
    assert ".run.ptex" not in graph_code("art/a.ptex", plain, "art/out", plain)


def test_runtime_copy_is_loaded_but_exports_keep_the_source_name(stage: Path) -> None:
    stored, runtime, _ = image_inputs(
        stage, graph("art/imported/rock.png"), "art/rock.ptex", "art/out"
    )
    code = graph_code("art/rock.ptex", stored, "art/out", runtime)
    namespace: dict[str, Any] = {}
    writes: dict[str, str] = {}

    class FakePath:
        def __init__(self, path: str) -> None:
            self.path = path

        def write_text(self, text: str) -> None:
            writes[self.path] = text

        def __str__(self) -> str:
            return self.path

    calls: list[list[str]] = []
    exec(  # noqa: S102 - run the generated recipe with its side effects captured
        code.replace("import json, pathlib, subprocess\n", ""),
        {
            "pathlib": type("P", (), {"Path": FakePath}),
            "subprocess": type(
                "S", (), {"run": lambda argv, check: calls.append(argv)}
            ),
            "json": json,
        },
        namespace,
    )
    native = json.loads(writes["art/rock.ptex"])
    assert image_of(native) == "art/imported/rock.png"
    run_copy = next(p for p in writes if p.endswith(".run.ptex"))
    assert run_copy.startswith("recipes/")
    assert image_of(json.loads(writes[run_copy])) == "/work/art/imported/rock.png"
    driver = next(text for p, text in writes.items() if p.endswith(".export.gd"))
    assert json.dumps("/work/" + run_copy) in driver
    assert json.dumps("/work/art/out/rock") in driver
    assert calls and calls[0][0] == f"{HOME}/joinery-godot"


def test_edit_graph_records_image_inputs_as_recipe_sources(
    stage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def export(argv: list[str], project: Path, *_: Any, **__: Any) -> Result:
        (project / "art/out/rock_albedo.png").write_bytes(b"albedo")
        return Result(True, "", "", 0.0)

    record = AsyncMock(return_value={"status": "staged", "requires_review": True})
    monkeypatch.setattr(authoring, "run_offline", export)
    monkeypatch.setattr(authoring, "_record", record)
    asyncio.run(
        authoring.edit_graph(
            stage, "art/rock.ptex", graph("/work/art/imported/rock.png"), "art/out"
        )
    )
    recipe: Recipe = record.await_args.args[1]
    assert recipe.sources == ("art/imported/rock.png",)
    assert recipe.outputs == ("art/rock.ptex", "art/out/rock_albedo.png")
    script = (stage / recipe.script).read_text()
    assert '\\"image\\": \\"art/imported/rock.png\\"' in script


def test_edit_graph_applies_the_argument_policy(
    stage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker = AsyncMock()
    monkeypatch.setattr(authoring, "run_offline", worker)
    hostile = graph("art/imported/rock.png")
    hostile["nodes"][0]["parameters"]["note"] = "pip install diffusers"
    with pytest.raises(PolicyStop):
        asyncio.run(authoring.edit_graph(stage, "art/rock.ptex", hostile, "art/out"))
    worker.assert_not_awaited()


def test_edit_graph_refuses_unrecorded_image_before_the_worker_runs(
    stage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker = AsyncMock()
    monkeypatch.setattr(authoring, "run_offline", worker)
    (stage / "art/imported/found.png").write_bytes(b"png")
    with pytest.raises(ValueError, match="no joinery.lock entry"):
        asyncio.run(
            authoring.edit_graph(
                stage, "art/rock.ptex", graph("art/imported/found.png"), "art/out"
            )
        )
    worker.assert_not_awaited()
    assert not (stage / "recipes").exists()


def test_capability_check_covers_the_exporter_and_the_pack() -> None:
    assert MM.capability_requirements() == {
        "material-maker": (
            "material-maker",
            f"{HOME}/material_maker.x86_64",
            f"{HOME}/joinery-godot",
        )
    }
    assert get("krita").capability_requirements() == {
        "krita": ("krita", "joinery-krita")
    }


@pytest.mark.skipif(
    os.name == "nt", reason="the snippet runs in the Linux worker, not on a Windows host"
)
@pytest.mark.parametrize(
    ("present", "installed"),
    [
        ({"material-maker", "material_maker.x86_64", "joinery-godot"}, True),
        ({"material-maker", "material_maker.x86_64"}, False),
        ({"material-maker"}, False),
    ],
)
def test_capability_snippet_requires_every_program(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    present: set[str],
    installed: bool,
) -> None:
    """Run the exact worker snippet against a fake filesystem."""
    from servers import authoring as server

    project = tmp_path / "p"
    project.mkdir()
    (project / "joinery.toml").write_text('[tools]\nenabled=["material-maker"]\n')
    home = tmp_path / "opt"
    bin_dir = tmp_path / "bin"
    home.mkdir()
    bin_dir.mkdir()
    for name in present:
        target = (bin_dir if name == "material-maker" else home) / name
        target.write_text("#!/bin/sh\n")
        target.chmod(0o755)
    captured: list[list[str]] = []

    async def worker(argv: list[str], *_: Any, **__: Any) -> Result:
        captured.append(argv)
        code = argv[2].replace(HOME, str(home))
        got = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env={"PATH": str(bin_dir)},
            check=True,
        )
        return Result(True, got.stdout, got.stderr, 0.0)

    monkeypatch.setattr(server, "run_offline", worker)

    async def call() -> Any:
        from mcp import Client

        async with Client(
            server.make_server(project, "joinery-materialmaker")
        ) as client:
            return await client.call_tool("document_capabilities", {})

    result = asyncio.run(call())
    data = json.loads(result.content[0].text)
    assert data["tools"] == [
        {"id": "material-maker", "application_installed": installed}
    ]
    assert captured
