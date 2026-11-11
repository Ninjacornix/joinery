"""Before/after evidence from the real staged documents, separate from game assets."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
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
# The second angle: the far side, so a change at the back shows too.
camera.location = center + Vector((-1, 1.5, 1)) * extent
camera.rotation_euler = (center - camera.location).to_track_quat('-Z', 'Y').to_euler()
scene.render.filepath = OUTPUT[:-4] + BACK_SUFFIX
bpy.ops.render.render(write_still=True)
"""
#: Name ending of the far-side render a full model view writes beside the front one.
BACK_SUFFIX = "-back.png"


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


# Plays one action and writes a strip of FRAMES square pictures side by side to
# OUTPUT, with what it measured (length, loop, bounce, foot slide) as JSON beside
# it, so the review loops it as a clip. Workbench, a three-quarter side view
# framed on the whole motion. An action the model does not have (a new one, on
# the original side) writes nothing. Set before it runs: OUTPUT, ACTION, FRAMES.
BLENDER_CLIP = r"""
import os, tempfile
import numpy as _np
action = bpy.data.actions.get(ACTION)
if action is None:
    print('JOINERY_NO_ACTION')
else:
    rig = _j_armature(_j_owner(action))
    st = _JRig(rig)
    row = _j_measure(st, action)
    data = rig.animation_data or rig.animation_data_create()
    data.use_nla = False
    _j_assign(rig, action)
    start, end = _j_frames(action)
    span = max(end - start, 1)
    loop = row['loop']
    moments = [start + span * i / (FRAMES if loop else FRAMES - 1) for i in range(FRAMES)]
    scene = st.scene
    # Visible meshes only: Rigify keeps its control shapes in a hidden collection.
    meshes = [o for o in scene.objects if o.type == 'MESH' and o.visible_get() and not o.hide_render]
    low, high = _JV((1e9,) * 3), _JV((-1e9,) * 3)
    for moment in moments[:: max(1, FRAMES // 6)]:
        scene.frame_set(int(moment), subframe=moment - int(moment))
        depsgraph = bpy.context.evaluated_depsgraph_get()
        for obj in meshes:
            evaluated = obj.evaluated_get(depsgraph)
            for corner in evaluated.bound_box:
                point = evaluated.matrix_world @ _JV(corner)
                low = _JV(min(a, b) for a, b in zip(low, point))
                high = _JV(max(a, b) for a, b in zip(high, point))
    if not meshes:
        raise RuntimeError('No visible geometry to preview')
    center = (low + high) / 2
    extent = max((high - low).length, 0.1)
    camera_data = bpy.data.cameras.new('JoineryReviewCamera')
    camera = bpy.data.objects.new('JoineryReviewCamera', camera_data)
    scene.collection.objects.link(camera)
    camera.location = center + (st.left * 1.5 + st.f * 0.9 + _J_UP * 0.35) * extent
    camera.rotation_euler = (center - camera.location).to_track_quat('-Z', 'Y').to_euler()
    camera_data.type = 'ORTHO'
    camera_data.ortho_scale = extent * 1.05
    scene.camera = camera
    scene.render.engine = 'BLENDER_WORKBENCH'
    scene.display.shading.light = 'STUDIO'
    scene.display.shading.color_type = 'MATERIAL'
    scene.display.shading.background_type = 'WORLD'
    scene.display.shading.background_color = (0.12, 0.12, 0.12)
    side = SIZE
    scene.render.resolution_x = side
    scene.render.resolution_y = side
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = 'PNG'
    scene.render.image_settings.color_mode = 'RGB'
    scene.render.film_transparent = False
    folder = tempfile.mkdtemp()
    strip = _np.zeros((side, side * FRAMES, 4), dtype=_np.float32)
    for index, moment in enumerate(moments):
        scene.frame_set(int(moment), subframe=moment - int(moment))
        scene.render.filepath = os.path.join(folder, f'{index}.png')
        bpy.ops.render.render(write_still=True)
        picture = bpy.data.images.load(scene.render.filepath)
        pixels = _np.empty(side * side * 4, dtype=_np.float32)
        picture.pixels.foreach_get(pixels)
        strip[:, index * side:(index + 1) * side, :] = pixels.reshape(side, side, 4)
        bpy.data.images.remove(picture)
    strip[:, :, 3] = 1.0
    out = bpy.data.images.new('joinery_clip', side * FRAMES, side, alpha=True)
    out.pixels.foreach_set(strip.ravel())
    out.filepath_raw = OUTPUT
    out.file_format = 'PNG'
    out.save()
    row.update({'frames': FRAMES, 'fps': round(FRAMES / max(row['seconds'], 1e-3), 3),
                'range': [start, end]})
    with open(OUTPUT[:-4] + '.json', 'w') as handle:
        _joinery_json.dump(row, handle)
"""
#: Pictures per clip strip, and their side in pixels: large enough to stay crisp
#: when a clip fills the review frame on a high-DPI screen.
CLIP_FRAMES = 12
CLIP_SIZE = 384
#: Where an animation operation lists the actions it touched in the stage.
ANIMATION_REVIEW = ".joinery/animation_review.json"


def clip_script(action: str) -> str:
    """The Blender script that renders one action as a looping strip."""
    from joinery.tools import blender_anim

    return (
        "import bpy\nimport json as _joinery_json\n"
        + blender_anim.library()
        + f"\nACTION={action!r}\nFRAMES={CLIP_FRAMES}\nSIZE={CLIP_SIZE}\n"
        + BLENDER_CLIP
    )


def _order(relative: str) -> int:
    """Map before/after first, then the selected part on the model, then the rest."""
    if Path(relative).parts[:2] == ("art", "textures"):
        return 0
    return 2


async def _render(root: Path, relative: str, name: str, script: str) -> Path:
    output = root / ".joinery/previews" / name
    output.parent.mkdir(parents=True, exist_ok=True)
    code = (
        f"OUTPUT={'/work/.joinery/previews/' + name!r}\n"
        f"BACK_SUFFIX={BACK_SUFFIX!r}\n" + script
    )
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


@dataclass
class Render:
    """One Blender render a review waits for: it writes ``output`` for ``item[side]``."""

    item: dict[str, Any]
    side: str
    root: Path
    relative: str
    name: str
    script: str
    #: The side may have nothing to show (an action new in this change).
    optional: bool = False

    @property
    def output(self) -> Path:
        return self.root / ".joinery/previews" / self.name

    @property
    def meta(self) -> Path:
        """What a clip render measured, beside its strip."""
        return self.output.with_suffix(".json")

    @property
    def back(self) -> Path:
        """The far-side render a full model view writes beside ``output``."""
        return self.output.with_name(self.output.stem + BACK_SUFFIX)


def plan(
    tx: FileTransaction,
    changes: list[dict[str, str]],
    parts: list[dict[str, str]] | None = None,
) -> tuple[list[dict[str, Any]], list[Render]]:
    """Before/after media for the changed art and audio, and the renders they need.

    Every item already names the file each side shows: an image or audio file
    as it is, or the render's output, which appears once :func:`render` has
    run. ``parts`` are the prompt's part targets: a material or map target adds
    a texture view of that part on its model before the full model.
    """
    media: list[dict[str, Any]] = []
    renders: list[Render] = []
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
                        renders.append(
                            Render(
                                view,
                                side,
                                root,
                                relative,
                                f"{digest}-{key}.png",
                                part_thumbnail_script(part),
                            )
                        )
                media.append(view)
            for anim in _animations_in(tx, relative, parts or []):
                clip: dict[str, Any] = {
                    "path": relative,
                    "media": "clip",
                    "part": anim["id"],
                    "label": anim["action"] or anim["was"],
                }
                key = hashlib.sha256(anim["id"].encode()).hexdigest()[:8]
                for side, root, action in (
                    ("before", tx.original, anim["was"]),
                    ("after", tx.stage, anim["action"]),
                ):
                    if action and (root / relative).is_file():
                        renders.append(
                            Render(
                                clip,
                                side,
                                root,
                                relative,
                                f"{digest}-{key}-clip.png",
                                clip_script(action),
                                optional=True,
                            )
                        )
                media.append(clip)
        for side, root in (("before", tx.original), ("after", tx.stage)):
            path = root / relative
            if not path.is_file():
                continue
            if suffix == ".blend":
                renders.append(
                    Render(
                        item, side, root, relative, digest + ".png", BLENDER_THUMBNAIL
                    )
                )
            else:
                item[side] = str(path)
        media.append(item)
    for job in renders:
        # A file left by an earlier review would show before this render is done.
        job.output.unlink(missing_ok=True)
        job.back.unlink(missing_ok=True)
        job.meta.unlink(missing_ok=True)
        job.item[job.side] = str(job.output)
    return media, renders


async def render(renders: list[Render], *, strict: bool = False) -> None:
    """Run every render at once, each in its own offline Blender.

    A render that fails marks its item with ``error`` (the review still stands)
    and drops that side; with ``strict`` the first failure is raised instead.
    """
    done = await asyncio.gather(
        *(_render(job.root, job.relative, job.name, job.script) for job in renders),
        return_exceptions=True,
    )
    for job, result in zip(renders, done, strict=True):
        if isinstance(result, Exception) and job.optional and not job.output.is_file():
            # Nothing to show on this side (the action is new, or was deleted).
            job.item.pop(job.side, None)
            continue
        if isinstance(result, BaseException):
            if strict or not isinstance(result, Exception):
                raise result
            job.item.pop(job.side, None)
            job.item["error"] = str(result).split("\n", 1)[0]
        else:
            job.item[job.side] = str(result)
            if job.back.is_file():
                # A full model shows from two angles: the front and the far side.
                job.item[job.side + "_back"] = str(job.back)
            if job.meta.is_file():
                job.item[job.side + "_clip"] = json.loads(job.meta.read_text())


async def evidence(
    tx: FileTransaction,
    changes: list[dict[str, str]],
    parts: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Before/after media with every render done; a failed render raises."""
    media, renders = plan(tx, changes, parts)
    await render(renders, strict=True)
    return media


def _animations_in(
    tx: FileTransaction, source: str, parts: list[dict[str, str]]
) -> list[dict[str, str]]:
    """Actions of one model to show as clips: picked ones, and those this change touched.

    Each is {id, action, was}: ``was`` names it in the original (empty when it
    is new), ``action`` in the change (empty when it was deleted).
    """
    found: dict[tuple[str, str], dict[str, str]] = {}
    for part in parts:
        if part.get("part_kind") != "animation" or not part.get("action"):
            continue
        if source not in {
            part.get("owner_source", ""),
            part.get("source", "").removeprefix("res://"),
        }:
            continue
        name = part["action"]
        found[(name, name)] = {
            "id": part.get("id", "") or name,
            "action": name,
            "was": name,
        }
    path = tx.stage / ANIMATION_REVIEW
    rows: list[Any] = []
    if path.is_file():
        try:
            rows = list(json.loads(path.read_text()))
        except (ValueError, TypeError):
            rows = []
    for row in rows:
        if not isinstance(row, dict) or row.get("source") != source:
            continue
        recipe = str(row.get("recipe", ""))
        # Only what this change made: its recipe is new in the stage.
        if (
            not recipe
            or not (tx.stage / recipe).is_file()
            or (tx.original / recipe).is_file()
        ):
            continue
        action, was = str(row.get("action", "")), str(row.get("was", ""))
        if (action, was) in found or (action and (action, action) in found):
            continue
        if was and was != action and (was, was) in found:
            del found[(was, was)]
        found[(action, was)] = {
            "id": f"res://{source}#action={action or was}",
            "action": action,
            "was": was,
        }
    return list(found.values())


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
