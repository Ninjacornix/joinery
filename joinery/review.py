"""Before/after evidence from the real staged documents, separate from game assets."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Coroutine
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


# Shows the selected map on the model: Workbench draws the active image node of
# each material in TEXTURE mode, so the target material's map node is made
# active, and the camera frames only the objects the part is on. Set before it
# runs: OUTPUT, MATERIAL, IMAGE (empty for a material part) and OBJECTS.
BLENDER_PART_THUMBNAIL = r"""
import bpy
from mathutils import Vector
material = bpy.data.materials.get(MATERIAL)
if material is None or material.node_tree is None:
    raise RuntimeError('The selected material is not in this model: ' + MATERIAL)
tree = material.node_tree
nodes = [n for n in tree.nodes if n.type == 'TEX_IMAGE' and n.image is not None]
chosen = next((n for n in nodes if n.image.name == IMAGE), None)
if chosen is None and nodes:
    chosen = next((n for n in nodes for l in n.outputs['Color'].links if l.to_socket.name == 'Base Color'), nodes[0])
if chosen is not None:
    tree.nodes.active = chosen
shown = [o for o in bpy.context.scene.objects if o.type == 'MESH' and (
    o.name in OBJECTS if OBJECTS else any(s.material is material for s in o.material_slots))]
for obj in bpy.context.scene.objects:
    if obj.type == 'MESH':
        obj.hide_render = obj not in shown
points = [o.matrix_world @ Vector(c) for o in shown for c in o.bound_box]
if not points:
    raise RuntimeError('No object shows the selected part')
low = Vector([min(v[i] for v in points) for i in range(3)])
high = Vector([max(v[i] for v in points) for i in range(3)])
center = (low + high) / 2
extent = max((high - low).length, 0.05)
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
scene.display.shading.light = 'FLAT' if IMAGE else 'STUDIO'
scene.display.shading.color_type = 'TEXTURE'
scene.display.shading.background_type = 'WORLD'
scene.display.shading.background_color = (0.12, 0.12, 0.12)
scene.render.resolution_x = 320
scene.render.resolution_y = 320
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = 'PNG'
scene.render.filepath = OUTPUT
bpy.ops.render.render(write_still=True)
"""


def _order(relative: str) -> int:
    """Map before/after first, then the selected part on the model, then the rest."""
    if Path(relative).parts[:2] == ("art", "textures"):
        return 0
    return 2


async def _render(root: Path, relative: str, name: str, script: str) -> Path:
    output = root / ".joinery/previews" / name
    output.parent.mkdir(parents=True, exist_ok=True)
    code = "OUTPUT=" + repr("/work/.joinery/previews/" + name) + "\n" + script
    got = await run_offline(
        ["blender", "--background", relative, "--python-expr", code], root, 120
    )
    if not got.ok or not output.is_file():
        raise ValueError(
            "Could not render the native document for review: "
            + relative
            + "\n"
            + got.output[-1000:]
        )
    return output


def part_thumbnail_script(part: dict[str, str]) -> str:
    """The Blender script that shows one material or map part on its model."""
    objects = [part["object"]] if part.get("object") else []
    return (
        f"MATERIAL={part.get('material', '')!r}\n"
        f"IMAGE={part.get('image', '') if part.get('part_kind') == 'texture' else ''!r}\n"
        f"OBJECTS={objects!r}\n" + BLENDER_PART_THUMBNAIL
    )


async def evidence(
    tx: FileTransaction,
    changes: list[dict[str, str]],
    parts: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Before/after media for the changed art and audio.

    ``parts`` are the prompt's part targets: a material or map target adds a
    texture view of that part on its model before the full model. Every render
    runs at once: each is its own offline Blender, and the review waits for all.
    """
    media: list[dict[str, Any]] = []
    #: (item, side, render) for every view that needs Blender.
    renders: list[tuple[dict[str, Any], str, Coroutine[Any, Any, Path]]] = []
    for change in sorted(changes, key=lambda c: _order(c["path"])):
        relative = change["path"]
        suffix = Path(relative).suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".wav", ".ogg", ".blend"}:
            continue
        # Recipe snapshots (recipes/inputs) and Joinery's own files are
        # bookkeeping, not the asset under review.
        if Path(relative).parts[0] not in {"art", "audio"}:
            continue
        item: dict[str, Any] = {
            "path": relative,
            "media": "audio" if suffix in {".wav", ".ogg"} else "image",
        }
        digest = hashlib.sha256(relative.encode()).hexdigest()[:12]
        if suffix == ".blend":
            for part in _parts_in(relative, parts or []):
                view: dict[str, Any] = {
                    "path": relative,
                    "media": "image",
                    "part": part.get("id", ""),
                    "label": part.get("label", ""),
                }
                key = hashlib.sha256(part.get("id", "").encode()).hexdigest()[:8]
                for side, root in (("before", tx.original), ("after", tx.stage)):
                    if (root / relative).is_file():
                        script = part_thumbnail_script(part)
                        name = f"{digest}-{key}.png"
                        renders.append(
                            (view, side, _render(root, relative, name, script))
                        )
                media.append(view)
        for side, root in (("before", tx.original), ("after", tx.stage)):
            path = root / relative
            if not path.is_file():
                continue
            if suffix == ".blend":
                renders.append(
                    (
                        item,
                        side,
                        _render(root, relative, digest + ".png", BLENDER_THUMBNAIL),
                    )
                )
            else:
                item[side] = str(path)
        media.append(item)
    done = await asyncio.gather(
        *(render for _item, _side, render in renders), return_exceptions=True
    )
    for (target, side, _render_call), result in zip(renders, done, strict=True):
        if isinstance(result, BaseException):
            raise result
        target[side] = str(result)
    return media


def _parts_in(source: str, parts: list[dict[str, str]]) -> list[dict[str, str]]:
    """Material and map targets inside one source document, one view per part."""
    found: dict[str, dict[str, str]] = {}
    for part in parts:
        if part.get("part_kind") not in {"material", "texture"} or not part.get(
            "material"
        ):
            continue
        if source not in {
            part.get("owner_source", ""),
            part.get("source", "").removeprefix("res://"),
        }:
            continue
        found.setdefault(part.get("id", "") or part["material"], part)
    return list(found.values())
