"""What the GLB export of a Blender model does to its meshes before glTF writes it.

All of this runs after the .blend is saved, so it shapes the game's copy only:
the native model keeps every modifier and shape key.
"""

from __future__ import annotations

import json
import re
import struct
from pathlib import Path
from typing import Any

#: MPFB2 names its macro targets (age, gender, weight, proportions) "$md-…".
#: They shape the body; they are not expressions a game animates.
MACRO_PREFIX = "$md-"
#: The export option a recipe carries, and its default for a new model.
KEEP_MACROS = "keep_macro_shape_keys"
#: The line of a recipe script that records the option.
_MACRO_CALL = re.compile(r"_joinery_bake_macro_keys\(keep=(True|False)\)")
#: Printed once per mesh with shape keys, as JSON after the prefix.
SHAPES_PREFIX = "JOINERY_EXPORT_SHAPES:"


def is_macro_key(name: str) -> bool:
    """True for an MPFB2 body-shape macro target, which the export bakes in."""
    return name.startswith(MACRO_PREFIX)


def macro_split(names: list[str]) -> tuple[list[str], list[str]]:
    """(macro keys, kept keys) of a mesh's shape keys, without the basis."""
    macros = [name for name in names if is_macro_key(name)]
    return macros, [name for name in names if not is_macro_key(name)]


def macro_call(keep: bool) -> str:
    """The script line that bakes (or keeps) macro keys for the export."""
    return f"_joinery_bake_macro_keys(keep={keep!r})"


def recorded_keep(script: str) -> bool | None:
    """The macro option a recipe script was made with, or None if it predates it."""
    found = _MACRO_CALL.search(script)
    return None if found is None else found.group(1) == "True"


# Bakes the current mix of MPFB2's macro shape keys into the base mesh and
# removes them, keeping every other key (expressions, a Dent) as its own morph
# target. Each kept key gets the same offset as the base, so what it changes
# stays the same. The mix comes from Blender itself (a key added from the mix of
# the macro keys alone), so values, relative keys and vertex groups all count.
BAKE_MACRO_KEYS = """
def _joinery_bake_macro_keys(keep):
    import json
    import numpy as _np
    for obj in [o for o in bpy.data.objects if o.type == 'MESH']:
        keys = obj.data.shape_keys
        if keys is None or len(keys.key_blocks) < 2:
            continue
        blocks = list(keys.key_blocks)
        macros = [k for k in blocks[1:] if k.name.startswith(MACRO_PREFIX)]
        row = {
            'object': obj.name, 'vertices': len(obj.data.vertices),
            'shape_keys': len(blocks) - 1, 'macro_keys': len(macros),
            'baked': 0, 'kept': len(blocks) - 1,
        }
        if macros and not keep:
            names = {k.name for k in macros}
            mutes = [(k, k.mute) for k in blocks]
            for k in blocks[1:]:
                k.mute = k.name not in names
            obj.show_only_shape_key = False
            mix = obj.shape_key_add(name='joinery_macro_mix', from_mix=True)
            for k, mute in mutes:
                k.mute = mute
            count = len(obj.data.vertices) * 3
            base = _np.empty(count, dtype=_np.float64)
            mixed = _np.empty(count, dtype=_np.float64)
            blocks[0].data.foreach_get('co', base)
            mix.data.foreach_get('co', mixed)
            offset = mixed - base
            obj.shape_key_remove(mix)
            for k in macros:
                obj.shape_key_remove(k)
            for k in obj.data.shape_keys.key_blocks:
                co = _np.empty(count, dtype=_np.float64)
                k.data.foreach_get('co', co)
                k.data.foreach_set('co', co + offset)
            obj.data.vertices.foreach_set('co', base + offset)
            obj.data.update()
            row['baked'] = len(macros)
            row['kept'] = len(obj.data.shape_keys.key_blocks) - 1
            if row['kept'] == 0:
                obj.shape_key_clear()
        print('JOINERY_EXPORT_SHAPES:' + json.dumps(row))
""".replace("MACRO_PREFIX", repr(MACRO_PREFIX))

# Godot cannot run Blender modifiers, so the GLB is exported with them applied
# (export_apply). The glTF exporter drops every shape key of a mesh whose
# modifiers it applies, so such a mesh gets its modifiers applied here first,
# once per shape key, keeping every key. An MPFB2 human is the common case: a
# Mask modifier hides its helper geometry and it carries shape keys; without
# this, the helpers would reach the game as visible, unweighted geometry.
# Armature modifiers stay for the exporter's skinning.
BAKE_FOR_EXPORT = """
def _joinery_evaluated_coordinates(obj):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    depsgraph.update()
    mesh = bpy.data.meshes.new_from_object(obj.evaluated_get(depsgraph), depsgraph=depsgraph)
    coordinates = [0.0] * (len(mesh.vertices) * 3)
    mesh.vertices.foreach_get('co', coordinates)
    bpy.data.meshes.remove(mesh)
    return coordinates

def _joinery_bake_for_export():
    for obj in [o for o in bpy.data.objects if o.type == 'MESH']:
        keys = obj.data.shape_keys
        applied = [m for m in obj.modifiers if m.type != 'ARMATURE' and m.show_viewport]
        if keys is None or len(keys.key_blocks) < 2 or not applied:
            continue
        armatures = [m for m in obj.modifiers if m.type == 'ARMATURE' and m.show_viewport]
        for modifier in armatures:
            modifier.show_viewport = False
        blocks = list(keys.key_blocks)
        settings = [(k.name, k.value, k.mute, k.slider_min, k.slider_max) for k in blocks]
        obj.show_only_shape_key = True
        shapes = []
        for index in range(len(blocks)):
            obj.active_shape_key_index = index
            shapes.append(_joinery_evaluated_coordinates(obj))
        if len({len(shape) for shape in shapes}) != 1:
            raise RuntimeError(
                f'{obj.name}: its modifiers change the vertex count per shape key, so the '
                'GLB cannot keep both. Apply the modifiers or remove the shape keys first.'
            )
        obj.active_shape_key_index = 0
        depsgraph = bpy.context.evaluated_depsgraph_get()
        depsgraph.update()
        baked = bpy.data.meshes.new_from_object(
            obj.evaluated_get(depsgraph), preserve_all_data_layers=True, depsgraph=depsgraph
        )
        obj.show_only_shape_key = False
        obj.data = baked
        for modifier in applied:
            obj.modifiers.remove(modifier)
        for modifier in armatures:
            modifier.show_viewport = True
        for (name, value, mute, low, high), shape in zip(settings, shapes):
            block = obj.shape_key_add(name=name, from_mix=False)
            block.data.foreach_set('co', shape)
            block.slider_min, block.slider_max = low, high
            block.value, block.mute = value, mute
        print('JOINERY_EXPORT_BAKED', obj.name, len(settings) - 1, 'shape keys')
"""


# A Rigify rig draws its controls with hundreds of face-less WGT- meshes, used
# as bone custom shapes. glTF writes each as an empty node, so the game got
# hundreds of stray nodes under the scene root. The export removes them (the
# .blend, already saved, keeps them to draw the rig). A custom shape with faces
# is a real mesh someone reused, and is exported like any other.
DROP_RIG_WIDGETS = """
def _joinery_drop_rig_widgets():
    shapes = set()
    for rig in [o for o in bpy.data.objects if o.type == 'ARMATURE' and o.pose]:
        for bone in rig.pose.bones:
            if bone.custom_shape is not None:
                shapes.add(bone.custom_shape)
                bone.custom_shape = None
    dropped = 0
    for obj in shapes:
        if obj.type == 'MESH' and not obj.data.polygons:
            bpy.data.objects.remove(obj)
            dropped += 1
    print('JOINERY_EXPORT_WIDGETS', dropped)
"""


def shape_rows(output: str) -> list[dict[str, Any]]:
    """What the export did to each mesh's shape keys, from the worker's output."""
    rows: list[dict[str, Any]] = []
    for line in output.splitlines():
        if line.startswith(SHAPES_PREFIX):
            rows.append(json.loads(line.removeprefix(SHAPES_PREFIX)))
    return rows


def glb_meshes(path: Path) -> list[dict[str, Any]]:
    """Each mesh of a GLB with its vertex count and morph targets, read from its JSON."""
    data = path.read_bytes()
    if data[:4] != b"glTF" or len(data) < 20:
        raise ValueError(f"{path.name} is not a GLB.")
    length, kind = struct.unpack("<I4s", data[12:20])
    if kind != b"JSON":
        raise ValueError(f"{path.name} has no JSON chunk.")
    gltf = json.loads(data[20 : 20 + length])
    accessors = gltf.get("accessors", [])
    found = []
    for mesh in gltf.get("meshes", []):
        primitives = mesh.get("primitives", [])
        vertices = sum(
            int(accessors[p["attributes"]["POSITION"]]["count"])
            for p in primitives
            if "POSITION" in p.get("attributes", {})
        )
        targets = max((len(p.get("targets", [])) for p in primitives), default=0)
        found.append(
            {
                "mesh": str(mesh.get("name", "")),
                "vertices": vertices,
                "morph_targets": targets,
                "target_names": list(mesh.get("extras", {}).get("targetNames", [])),
            }
        )
    return found
