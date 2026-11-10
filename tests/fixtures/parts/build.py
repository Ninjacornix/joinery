"""Rebuild the part-selection fixture in the offline creative worker.

    uv run python tests/fixtures/parts/build.py

Wraps hammer_model.py in Joinery's own Blender recipe script, so the GLB carries
the same part names (extras.joinery_name, extras.joinery_maps) as a real edit,
then records Blender's part inspection of the result as parts.json.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from joinery.authoring import run_offline
from joinery.tools import blender

HERE = Path(__file__).resolve().parent
SOURCE = "art/sources/hammer.blend"
EXPORT = "art/models/hammer.glb"


async def build() -> None:
    with tempfile.TemporaryDirectory(prefix="joinery-parts-fixture-") as directory:
        work = Path(directory)
        (work / "art/sources").mkdir(parents=True)
        (work / "art/models").mkdir(parents=True)
        code = (HERE / "hammer_model.py").read_text()
        script = work / "build_hammer.py"
        script.write_text(blender.MODULE.script(SOURCE, "", code, [EXPORT]))
        got = await run_offline(
            ["blender", "--background", "--factory-startup", "--python", script.name],
            work,
        )
        if not got.ok:
            raise SystemExit("Blender failed:\n" + got.output[-3000:])
        inspected = await run_offline(
            ["blender", "--background", SOURCE, "--python-expr", blender.INSPECT_BLEND],
            work,
            readonly=True,
        )
        line = next(
            row
            for row in inspected.output.splitlines()
            if row.startswith("JOINERY_DOCUMENT:")
        )
        report = json.loads(line.removeprefix("JOINERY_DOCUMENT:"))
        shutil.copyfile(work / SOURCE, HERE / "hammer.blend")
        shutil.copyfile(work / EXPORT, HERE / "hammer.glb")
        # The texture PNGs Joinery writes beside the model (art/textures/hammer/).
        textures = HERE / "textures"
        shutil.rmtree(textures, ignore_errors=True)
        shutil.copytree(work / blender.texture_dir(SOURCE), textures)
        (HERE / "parts.json").write_text(json.dumps(report, indent=1) + "\n")
        print("wrote", *(p.name for p in sorted(HERE.glob("hammer.*"))), "parts.json")


if __name__ == "__main__":
    asyncio.run(build())
