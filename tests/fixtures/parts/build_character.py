"""Rebuild the character fixture in the offline creative worker.

    uv run python tests/fixtures/parts/build_character.py

Wraps character_model.py (an MPFB2 human with a generated Rigify rig and two
expression keys) in Joinery's own Blender recipe script, so the GLB carries the
part names and body regions (extras joinery_name, joinery_role,
joinery_regions) as a real edit does, then records Blender's part inspection
of the result as character_parts.json. The .blend is not kept: the widget tests
read the GLB and the recorded inspection only.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from joinery.authoring import run_offline
from joinery.tools import blender

HERE = Path(__file__).resolve().parent
SOURCE = "art/sources/character.blend"
EXPORT = "art/models/character.glb"


async def build() -> None:
    with tempfile.TemporaryDirectory(prefix="joinery-character-fixture-") as directory:
        work = Path(directory)
        (work / "art/sources").mkdir(parents=True)
        (work / "art/models").mkdir(parents=True)
        code = (HERE / "character_model.py").read_text()
        script = work / "build_character.py"
        script.write_text(blender.MODULE.script(SOURCE, "", code, [EXPORT]))
        got = await run_offline(
            ["blender", "--background", "--factory-startup", "--python", script.name],
            work,
            900,
        )
        if not got.ok:
            raise SystemExit("Blender failed:\n" + got.output[-3000:])
        inspected = await run_offline(
            ["blender", "--background", SOURCE, "--python-expr", blender.INSPECT_BLEND],
            work,
            600,
            readonly=True,
        )
        line = next(
            row
            for row in inspected.output.splitlines()
            if row.startswith("JOINERY_DOCUMENT:")
        )
        report = json.loads(line.removeprefix("JOINERY_DOCUMENT:"))
        # Rigify's control shapes are rig internals; the parts list never shows them.
        report["objects"] = [
            o for o in report["objects"] if not str(o["name"]).startswith("WGT-")
        ]
        (HERE / "character.glb").write_bytes((work / EXPORT).read_bytes())
        (HERE / "character_parts.json").write_text(json.dumps(report, indent=1) + "\n")
        print("wrote character.glb", (HERE / "character.glb").stat().st_size, "bytes")


if __name__ == "__main__":
    asyncio.run(build())
