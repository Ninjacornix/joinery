"""A recorded project image feeding a Material Maker graph, in the real offline worker.

Technical fixture only: the input is a gradient written by this script and
declared as a human import, standing in for a texture that came with a model.
Run: `uv run python tests/integration/materialmaker_parts.py`.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import struct
import tempfile
import zlib
from pathlib import Path
from typing import Any

from joinery.authoring import edit_graph
from joinery.catalog import load_catalog
from joinery.lockfile import check_assets
from joinery.recipe import load_recipe, rebuild_one
from joinery.runner import SandboxRunner
from joinery.transactions import EXCLUDED, FileTransaction

HEIGHT = "art/imported/rock_height.png"
SOURCE = "art/materials/rock.ptex"
EXPORTS = "art/materials/export"


def gradient_png(size: int = 32) -> bytes:
    """An 8-bit greyscale left-to-right ramp."""
    rows = b"".join(
        b"\x00" + bytes(x * 255 // (size - 1) for x in range(size)) for _ in range(size)
    )

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", size, size, 8, 0, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def project_at(directory: str) -> Path:
    project = Path(directory)
    (project / "project.godot").write_text("config_version=5\n")
    (project / "joinery.toml").write_text(
        '[tools]\nenabled=["godot","material-maker"]\n'
    )
    (project / Path(HEIGHT).parent).mkdir(parents=True)
    data = gradient_png()
    (project / HEIGHT).write_bytes(data)
    (project / "joinery.lock").write_text(
        "version = 1\n\n[[asset]]\n"
        f'path = "{HEIGHT}"\nsha256 = "{hashlib.sha256(data).hexdigest()}"\n'
        'origin = "human-import"\nimported_by = "integration fixture"\n'
        'licence = "test fixture"\n'
    )
    return project


def graph(image: str) -> dict[str, Any]:
    return {
        "type": "graph",
        "name": "FromImage",
        "nodes": [
            {"type": "material", "name": "Material", "parameters": {"size": 5}},
            {"type": "image", "name": "Height", "parameters": {"image": image}},
            {
                "type": "normal_map2",
                "name": "Normal",
                "parameters": {"param0": 10, "param1": 1.0, "param2": 0},
            },
        ],
        "connections": [
            {"from": "Height", "from_port": 0, "to": "Normal", "to_port": 0},
            {"from": "Normal", "from_port": 0, "to": "Material", "to_port": 4},
            {"from": "Height", "from_port": 0, "to": "Material", "to_port": 6},
        ],
    }


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="joinery-material-image-") as directory:
        project = project_at(directory)
        tx = FileTransaction(project)
        tx.begin("Feed a recorded height image into a material graph")
        # The agent may name the worker path; the source stores it relative.
        result = await edit_graph(tx.stage, SOURCE, graph("/work/" + HEIGHT), EXPORTS)
        recipe = load_recipe(tx.stage / result["recipe"])
        assert recipe.sources == (HEIGHT,), recipe.sources
        assert HEIGHT not in recipe.outputs
        stored = json.loads((tx.stage / SOURCE).read_text())
        assert stored["nodes"][1]["parameters"]["image"] == HEIGHT
        normal = [p for p in recipe.outputs if p.endswith("_normal.png")]
        assert normal and (tx.stage / normal[0]).stat().st_size > 0, recipe.outputs
        assert (tx.stage / HEIGHT).read_bytes() == gradient_png(), "input untouched"
        problems = check_assets(tx.stage, load_catalog())
        assert not problems, problems
        with tempfile.TemporaryDirectory(prefix="joinery-material-replay-") as copy:
            scratch = Path(copy)
            shutil.copytree(
                tx.stage,
                scratch,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns(*EXCLUDED),
            )
            rebuilt = await asyncio.to_thread(
                rebuild_one, scratch / result["recipe"], scratch, SandboxRunner()
            )
            assert rebuilt.ok, rebuilt.problems
        # An image nobody recorded never reaches the worker.
        (tx.stage / "art/imported/found.png").write_bytes(gradient_png(8))
        try:
            await edit_graph(tx.stage, SOURCE, graph("art/imported/found.png"), EXPORTS)
        except ValueError as error:
            assert "no joinery.lock entry" in str(error), error
        else:
            raise AssertionError("an unrecorded image input was accepted")
        tx.review()
        tx.reject()
        print(
            "Material Maker recorded image input/relative .ptex path/recipe sources/"
            "replay/unrecorded input refused: PASS"
        )


if __name__ == "__main__":
    asyncio.run(main())
