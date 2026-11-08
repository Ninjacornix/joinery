"""Before/after evidence from the real staged documents, separate from game assets."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from joinery.authoring import run_offline
from joinery.transactions import FileTransaction

BLENDER_THUMBNAIL = r"""
import bpy, math
from mathutils import Vector
points = [o.matrix_world @ Vector(c) for o in bpy.context.scene.objects if o.type == 'MESH' for c in o.bound_box]
if not points:
    raise RuntimeError('No visible geometry to preview')
low = Vector([min(v[i] for v in points) for i in range(3)])
high = Vector([max(v[i] for v in points) for i in range(3)])
center = (low + high) / 2
extent = max((high - low).length, 0.1)
camera_data = bpy.data.cameras.new('JoineryReviewCamera')
camera = bpy.data.objects.new('JoineryReviewCamera', camera_data)
bpy.context.scene.collection.objects.link(camera)
camera.location = center + Vector((1, -1.5, 1)) * extent
camera.rotation_euler = (center - camera.location).to_track_quat('-Z', 'Y').to_euler()
camera_data.type = 'ORTHO'
camera_data.ortho_scale = extent * 1.2
scene = bpy.context.scene
scene.camera = camera
scene.render.engine = 'BLENDER_WORKBENCH'
scene.display.shading.light = 'STUDIO'
scene.display.shading.color_type = 'MATERIAL'
scene.display.shading.background_type = 'WORLD'
scene.display.shading.background_color = (0.12, 0.12, 0.12)
scene.render.resolution_x = 320
scene.render.resolution_y = 320
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = 'PNG'
scene.render.filepath = OUTPUT
bpy.ops.render.render(write_still=True)
"""


async def evidence(
    tx: FileTransaction, changes: list[dict[str, str]]
) -> list[dict[str, Any]]:
    media: list[dict[str, Any]] = []
    for change in changes:
        relative = change["path"]
        suffix = Path(relative).suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".wav", ".ogg", ".blend"}:
            continue
        item: dict[str, Any] = {
            "path": relative,
            "media": "audio" if suffix in {".wav", ".ogg"} else "image",
        }
        for side, root in (("before", tx.original), ("after", tx.stage)):
            path = root / relative
            if not path.is_file():
                continue
            if suffix == ".blend":
                name = hashlib.sha256(relative.encode()).hexdigest()[:12] + ".png"
                output = root / ".joinery/previews" / name
                output.parent.mkdir(parents=True, exist_ok=True)
                script = (
                    "OUTPUT="
                    + repr("/work/.joinery/previews/" + name)
                    + "\n"
                    + BLENDER_THUMBNAIL
                )
                got = await run_offline(
                    ["blender", "--background", relative, "--python-expr", script],
                    root,
                    120,
                )
                if not got.ok or not output.is_file():
                    raise ValueError(
                        "Could not render the native document for review: "
                        + relative
                        + "\n"
                        + got.output[-1000:]
                    )
                path = output
            item[side] = str(path)
        media.append(item)
    return media
