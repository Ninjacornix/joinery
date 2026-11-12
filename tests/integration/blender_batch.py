"""blender_batch against the same operations one by one, in the real Blender worker.

A small model (a scaled, raised subdivided cube body with a textured Paint
material and a cube lid with a Metal material) is made once and copied into
three stages. One gets six typed operations as six calls of joinery-blender-ops,
the next gets them as one blender_batch call, both through the MCP server
exactly as an agent calls it:

  transform_parts, apply_scale, set_pivot, reduce_triangles,
  set_material_value, adjust_map

Then:

1. both stages hold the same recipe log: one recipe per operation with the
   same note, outputs and script (preimage names aside, since Blender writes
   a different .blend byte for byte each run), and the same recorded
   fingerprints (the .blend's semantic signature, the GLB and texture bytes);
2. the final GLB and textures are byte-identical, and every recipe in the
   batched stage, the replaced ones in history too, replays (`joinery rebuild`)
   to its recorded fingerprints;
3. a batch whose third step names a missing object stops there, says so, and
   leaves the first two steps applied and replayable.

Both ways are timed, and the size of what the agent reads back is measured.
Technical fixtures only. Run: `uv run python tests/integration/blender_batch.py`.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from joinery.authoring import edit_document
from joinery.recipe import HISTORY_DIR, load_recipe, rebuild_one, recipe_paths
from joinery.runner import SandboxRunner
from joinery.transactions import FileTransaction

SOURCE = "art/sources/box.blend"
MODEL = "art/models/box.glb"
MAKE = """
bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 1.0))
body = bpy.context.object
body.name = body.data.name = 'Body'
bpy.ops.object.mode_set(mode='EDIT')
bpy.ops.mesh.subdivide(number_cuts=8)
bpy.ops.object.mode_set(mode='OBJECT')
body.scale = (2, 2, 1)
bpy.ops.mesh.primitive_cube_add(size=0.4, location=(0, 0, 1.6))
lid = bpy.context.object
lid.name = lid.data.name = 'Lid'
paint = bpy.data.materials.new('Paint')
paint.use_nodes = True
image = bpy.data.images.new('paint_base', 16, 16, alpha=False)
image.pixels.foreach_set([v for i in range(256) for v in (i / 255, 0.4, 0.2, 1.0)])
image.pack()
node = paint.node_tree.nodes.new('ShaderNodeTexImage')
node.image = image
paint.node_tree.links.new(node.outputs['Color'], paint.node_tree.nodes['Principled BSDF'].inputs['Base Color'])
body.data.materials.append(paint)
metal = bpy.data.materials.new('Metal')
metal.use_nodes = True
lid.data.materials.append(metal)
"""
STEPS: list[dict[str, Any]] = [
    {
        "op": "transform_parts",
        "arguments": {"transforms": [{"name": "Lid", "rotation_degrees": [0, 0, 30]}]},
    },
    {"op": "apply_scale", "arguments": {"objects": ["Body"]}},
    {"op": "set_pivot", "arguments": {"pivot": "bottom-centre"}},
    {"op": "reduce_triangles", "arguments": {"max_triangles": 500}},
    {
        "op": "set_material_value",
        "arguments": {"material": "Metal", "socket": "Roughness", "value": 0.3},
    },
    {
        "op": "adjust_map",
        "arguments": {"material": "Paint", "image": "paint_base", "op": "invert"},
    },
]
PREIMAGE = re.compile(r"recipes/inputs/[0-9a-f]{64}\.blend")


async def call(stage: Path, tool: str, arguments: dict[str, Any]) -> tuple[Any, str]:
    from mcp import Client

    from servers.authoring import make_server

    async with Client(make_server(stage, "joinery-blender-ops")) as client:
        result = await client.call_tool(tool, arguments)
    text = result.content[0].text
    return result, text


def stage_of(root: Path, name: str) -> tuple[FileTransaction, Path]:
    project = root / name
    project.mkdir()
    (project / "project.godot").write_text("config_version=5\n")
    (project / "joinery.toml").write_text('[tools]\nenabled=["godot", "blender"]\n')
    (project / "joinery.lock").write_text("version=1\n")
    tx = FileTransaction(project)
    tx.begin("Batch fixture")
    return tx, tx.stage


def log(stage: Path) -> list[dict[str, Any]]:
    """Every recipe, current and replaced, with preimage names masked."""
    rows = []
    paths = [*recipe_paths(stage), *sorted((stage / HISTORY_DIR).glob("*.toml"))]
    for path in paths:
        recipe = load_recipe(path)
        rows.append(
            {
                "current": path.parent.name == "recipes",
                "note": recipe.note,
                "outputs": recipe.outputs,
                "sources": [PREIMAGE.sub("<preimage>", s) for s in recipe.sources],
                "expect": recipe.expect,
                "script": PREIMAGE.sub(
                    "<preimage>", (stage / recipe.script).read_text()
                ),
            }
        )
    return sorted(rows, key=lambda row: (row["current"], row["note"]))


def outputs(stage: Path) -> dict[str, bytes]:
    return {
        p.relative_to(stage).as_posix(): p.read_bytes()
        for p in sorted((stage / "art").rglob("*"))
        if p.is_file() and p.suffix in {".glb", ".png"}
    }


async def replay(stage: Path, paths: list[Path]) -> list[tuple[str, tuple[str, ...]]]:
    done = [
        await asyncio.to_thread(rebuild_one, path, stage, SandboxRunner())
        for path in paths
    ]
    notes = {path.name: load_recipe(path).note for path in paths}
    return [(notes[r.recipe], r.problems) for r in done if not r.ok]


async def main() -> dict[str, Any]:
    # Reuse one worker container per stage, as the gateway does for an agent
    # (COLD=1 starts a container per run instead).
    if not os.environ.get("COLD"):
        os.environ.setdefault("JOINERY_WORKER_SCOPE", uuid.uuid4().hex)
    facts: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="joinery-blender-batch-") as directory:
        root = Path(directory)
        single_tx, single = stage_of(root, "single")
        batch_tx, batched = stage_of(root, "batched")
        failing_tx, failing = stage_of(root, "failing")
        await edit_document(single, "blender", SOURCE, MAKE, [MODEL], "Make the box")
        for other in (batched, failing):
            for folder in ("art", "recipes"):
                shutil.copytree(single / folder, other / folder)
            shutil.copy2(single / "joinery.lock", other / "joinery.lock")

        start = time.monotonic()
        single_text = 0
        for step in STEPS:
            result, text = await call(
                single,
                step["op"],
                {"source": SOURCE, "exports": [MODEL], **step["arguments"]},
            )
            assert not result.is_error, f"{step['op']}: {text}"
            single_text += len(text)
        facts["one_by_one_s"] = round(time.monotonic() - start, 1)
        facts["one_by_one_result_chars"] = single_text

        start = time.monotonic()
        result, text = await call(
            batched, "blender_batch", {"source": SOURCE, "steps": STEPS}
        )
        assert not result.is_error, text
        facts["batch_s"] = round(time.monotonic() - start, 1)
        facts["batch_result_chars"] = len(text)
        found = json.loads(text)
        facts["batch_blender_s"] = found["seconds"]
        facts["batch_steps"] = found["steps"]

        assert log(batched) == log(single), "the recipe logs differ"
        assert outputs(batched) == outputs(single), "the exports differ"
        history = sorted((batched / HISTORY_DIR).glob("*.toml"))
        assert len(history) == len(STEPS), [p.name for p in history]
        failed = await replay(batched, [*recipe_paths(batched), *history])
        assert not failed, failed
        failed = await replay(single, recipe_paths(single))
        assert not failed, failed
        facts["replayed"] = len(history) + 1

        broken = [*STEPS[:2], {"op": "apply_scale", "arguments": {"objects": ["Nope"]}}]
        result, text = await call(
            failing, "blender_batch", {"source": SOURCE, "steps": [*broken, STEPS[3]]}
        )
        assert result.is_error, text
        assert "Step 3 of 4 (apply_scale) failed" in text and "Nope" in text, text
        assert "Steps 1-2 are applied and staged" in text, text
        current = [load_recipe(p) for p in recipe_paths(failing)]
        assert [r.note for r in current] == ["Apply scale on Body"], current
        failed = await replay(failing, recipe_paths(failing))
        assert not failed, failed
        facts["failure"] = text[:300]
        for tx in (single_tx, batch_tx, failing_tx):
            tx.reject()
    from joinery.warm_workers import close_blenders

    await close_blenders()
    return facts


if __name__ == "__main__":
    print(json.dumps(asyncio.run(main()), indent=1))
    print("BLENDER_BATCH_OK")
