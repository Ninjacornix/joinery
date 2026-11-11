"""Kit previews: one model drawn in Blender in the offline worker, cached by its values.

The picture is not an asset: it lives in ``.joinery/kits/previews`` and only the
Kits section of the asset workspace shows it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from joinery.authoring import run_offline
from joinery.kits import _reason, common_source, plan
from joinery.tools.blender import FAIL_ON_ERROR

PREVIEW_DIR = ".joinery/kits/previews"
PREVIEW_SIZE = 384

_PREVIEW = r"""
import math
from mathutils import Vector
reset()
_model = MODELS[0]
_obj = build(_model['params'], random.Random(_model['seed']), {
    role: material(f"{_model['prefix']}_{role}", look) for role, look in CONTEXT['looks'].items()
}, _model['object'])
fit_budget(_obj, CONTEXT['budget'])
set_pivot(_obj, CONTEXT['pivot'])
_low, _high = bounds(_obj)
_centre = Vector([(a + b) / 2 for a, b in zip(_low, _high)])
_radius = max(0.05, (Vector(_high) - Vector(_low)).length / 2)
_scene = bpy.context.scene
_camera = bpy.data.objects.new('Camera', bpy.data.cameras.new('Camera'))
_scene.collection.objects.link(_camera)
_scene.camera = _camera
_camera.data.lens = 50
_fov = _camera.data.angle
_view = Vector((1.0, -1.35, 0.85)).normalized()
_camera.location = _centre + _view * (_radius / math.sin(_fov / 2) * 1.08)
_camera.rotation_euler = (-_view).to_track_quat('-Z', 'Y').to_euler()
_scene.render.engine = 'BLENDER_WORKBENCH'
_scene.display.shading.light = 'STUDIO'
_scene.display.shading.color_type = 'MATERIAL'
_scene.display.shading.show_cavity = True
_scene.render.film_transparent = True
_scene.render.resolution_x = _scene.render.resolution_y = SIZE
_scene.render.filepath = os.path.abspath(OUT)
bpy.ops.render.render(write_still=True)
print('JOINERY_PREVIEW:' + json.dumps({'triangles': triangles(_obj), 'size': [round(h - l, 3) for l, h in zip(_low, _high)]}))
"""


async def preview(
    project: Path,
    kit_id: str,
    params: Mapping[str, Any] | None = None,
    *,
    seed: int = 1,
    colours: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """A picture of one model with these values, rendered in Blender (cached)."""
    made = plan(project, kit_id, params, seed=seed, name="preview", colours=colours)
    context = {"looks": made.looks, "budget": made.budget, "pivot": made.pivot}
    model = made.models[0]
    job = {key: model[key] for key in ("object", "prefix", "seed", "params")}
    key = hashlib.sha256(
        json.dumps(
            [made.kit.source, common_source(), context, job], sort_keys=True
        ).encode()
    ).hexdigest()[:24]
    folder = project / PREVIEW_DIR
    folder.mkdir(parents=True, exist_ok=True)
    image = folder / f"{made.kit.id}-{key}.png"
    facts = folder / f"{made.kit.id}-{key}.json"
    if image.is_file() and facts.is_file():
        return {**json.loads(facts.read_text()), "image": str(image), "cached": True}
    code = "\n".join(
        [
            "import bpy, os, math, json, random",
            FAIL_ON_ERROR,
            "os.chdir('/work')",
            common_source(),
            made.kit.source,
            f"CONTEXT = json.loads({json.dumps(context)!r})",
            f"MODELS = [json.loads({json.dumps(job)!r})]",
            f"SIZE = {PREVIEW_SIZE}",
            f"OUT = {image.name!r}",
            _PREVIEW,
        ]
    )
    work = folder / f"{key}.py"
    work.write_text(code, encoding="utf-8")
    try:
        result = await run_offline(
            ["blender", "--background", "--factory-startup", "--python", work.name],
            folder,
            300,
        )
    finally:
        work.unlink(missing_ok=True)
    row = next(
        (
            json.loads(line.removeprefix("JOINERY_PREVIEW:"))
            for line in result.output.splitlines()
            if line.startswith("JOINERY_PREVIEW:")
        ),
        None,
    )
    if not result.ok or row is None or not image.is_file():
        raise RuntimeError(
            "Blender could not draw the preview: " + _reason(result.output)
        )
    data = {
        "kit": made.kit.id,
        "triangles": row["triangles"],
        "size_m": row["size"],
        "budget": made.budget,
        "seconds": round(result.seconds, 1),
    }
    facts.write_text(json.dumps(data))
    _prune(folder)
    return {**data, "image": str(image), "cached": False}


def _prune(folder: Path, keep: int = 64) -> None:
    """Keep the newest previews only."""
    pictures = sorted(
        folder.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    for old in pictures[keep:]:
        old.unlink(missing_ok=True)
        old.with_suffix(".json").unlink(missing_ok=True)
