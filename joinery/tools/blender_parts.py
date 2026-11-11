"""Typed Blender operations on one part: a map's source, a material copy, a bone, a shape key.

Each builder returns operation code for an ordinary Blender edit, wrapped in the
scope guard (`blender.guarded`), so only the named part may change and the
recipe that records the edit replays it. The code is plain bpy: Blender does
the work, Joinery only names what to change.
"""

from __future__ import annotations

import math
from typing import Any

from joinery.tools.blender import PART_OPS, guarded

#: Bone operations and the parameters each one takes (all optional unless named).
BONE_OPS: dict[str, frozenset[str]] = {
    "rename": frozenset({"name"}),
    "set_rest": frozenset({"head", "tail", "roll_degrees"}),
    "set_parent": frozenset({"parent", "connected"}),
    "scale_chain": frozenset({"factor"}),
}
#: Shape key operations and their parameters.
SHAPE_KEY_OPS: dict[str, frozenset[str]] = {
    "set_value": frozenset({"value"}),
    "rename": frozenset({"name"}),
    "new_from_mix": frozenset(),
    "new_from_pose": frozenset(),
    "delete": frozenset(),
}

# Runs after PART_OPS (material and image lookups).
OPS = r"""
import os as _joinery_os

def _joinery_link_map(material, image, path, editable):
    np = _joinery_np
    material = _joinery_material(material)
    node, row = _joinery_image_node(material, image)
    picture = node.image
    roles = {use['role'] for use in row['uses']}
    if roles:
        # The colour space goes first: setting it later reloads the image.
        space = 'sRGB' if roles & _JOINERY_COLOUR_ROLES else 'Non-Color'
        if picture.colorspace_settings.name != space:
            picture.colorspace_settings.name = space
    loaded = bpy.data.images.load(_joinery_os.path.abspath(path), check_existing=False)
    try:
        loaded.colorspace_settings.name = picture.colorspace_settings.name
        width, height = loaded.size
        if width == 0 or height == 0:
            raise RuntimeError(f'{path} has no pixels.')
        pixels = np.empty(width * height * 4, dtype=np.float32)
        loaded.pixels.foreach_get(pixels)
    finally:
        bpy.data.images.remove(loaded)
    if tuple(picture.size) != (width, height):
        picture.scale(width, height)
    picture.pixels.foreach_set(pixels)
    picture.update()
    picture.pack()
    picture['joinery_map'] = path
    if editable:
        picture['joinery_source'] = editable
    print('JOINERY_PART_OP:' + _joinery_json.dumps({
        'material': material.name, 'image': picture.name, 'from': path,
        'size': [width, height], 'editable': editable,
    }))

def _joinery_split_material(material, objects, name, image_names):
    old = _joinery_material(material)
    if bpy.data.materials.get(name) is not None:
        raise RuntimeError(f'There is already a material named {name!r}. Pick another name.')
    users = [o for o in bpy.data.objects if any(s.material is old for s in o.material_slots)]
    for obj_name in objects:
        obj = bpy.data.objects.get(obj_name)
        if obj is None or obj not in users:
            raise RuntimeError(f'{obj_name!r} does not use {old.name!r}. It is used by: '
                               + ', '.join(o.name for o in users))
    if len(objects) == len(users):
        raise RuntimeError(f'Every object using {old.name!r} would get the copy. '
                           'Change the material itself instead.')
    new = old.copy()
    new.name = name
    for key in [k for k in new.keys() if k.startswith('joinery_')]:
        del new[key]
    copies = {}
    if image_names is not None and new.node_tree is not None:
        for node in new.node_tree.nodes:
            if node.type != 'TEX_IMAGE' or node.image is None:
                continue
            source = node.image
            if source.name not in copies:
                wanted = image_names.get(source.name)
                if wanted is None:
                    raise RuntimeError(f'No name was given for the copy of {source.name!r}.')
                if bpy.data.images.get(wanted) is not None:
                    raise RuntimeError(f'There is already an image named {wanted!r}.')
                width, height = source.size
                copy = bpy.data.images.new(wanted, width, height, alpha=True,
                                           float_buffer=source.is_float)
                copy.colorspace_settings.name = source.colorspace_settings.name
                values = _joinery_np.empty(width * height * 4, dtype=_joinery_np.float32)
                source.pixels.foreach_get(values)
                copy.pixels.foreach_set(values)
                copy.update()
                copy.pack()
                copies[source.name] = copy
            node.image = copies[source.name]
    for obj_name in objects:
        obj = bpy.data.objects[obj_name]
        shared = obj.data is not None and obj.data.users > 1
        for slot in obj.material_slots:
            if slot.material is old:
                if shared and slot.link == 'DATA':
                    # The mesh is shared with other objects: give only this
                    # object the copy, through an object-linked slot.
                    slot.link = 'OBJECT'
                slot.material = new
    print('JOINERY_PART_OP:' + _joinery_json.dumps({
        'material': old.name, 'copy': new.name, 'objects': list(objects),
        'images': {k: v.name for k, v in copies.items()},
    }))

def _joinery_armature(name):
    obj = bpy.data.objects.get(name)
    if obj is None or obj.type != 'ARMATURE':
        raise RuntimeError(f'There is no armature named {name!r} in this model.')
    return obj

def _joinery_edit_mode(obj):
    hidden = (obj.hide_get(), obj.hide_viewport)
    obj.hide_set(False)
    obj.hide_viewport = False
    if bpy.context.object is not None and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    for other in bpy.context.view_layer.objects:
        other.select_set(other is obj)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    return hidden

def _joinery_object_mode(obj, hidden):
    bpy.ops.object.mode_set(mode='OBJECT')
    obj.hide_set(hidden[0])
    obj.hide_viewport = hidden[1]
    obj.select_set(False)

def _joinery_edit_bone(armature, bone, op, params):
    import math
    rig = _joinery_armature(armature)
    if rig.data.bones.get(bone) is None:
        raise RuntimeError(f'{rig.name!r} has no bone named {bone!r}.')
    if op == 'rename':
        name = params['name']
        if rig.data.bones.get(name) is not None:
            raise RuntimeError(f'{rig.name!r} already has a bone named {name!r}.')
        # Blender renames the vertex groups that follow the bone, and the
        # constraints and animation that name it.
        for obj in bpy.data.objects:
            if obj.type == 'MESH' and obj.vertex_groups.get(bone) is not None and (
                obj.parent is rig or any(m.type == 'ARMATURE' and m.object is rig for m in obj.modifiers)
            ):
                _joinery_also.add('object:' + obj.name)
        rig.data.bones[bone].name = name
        if rig.data.bones.get(name) is None:
            raise RuntimeError(f'Blender did not rename {bone!r}.')
        result = {'bone': name, 'was': bone}
    else:
        hidden = _joinery_edit_mode(rig)
        try:
            bones = rig.data.edit_bones
            target = bones[bone]
            if op == 'set_rest':
                if 'head' in params:
                    target.head = params['head']
                if 'tail' in params:
                    target.tail = params['tail']
                if 'roll_degrees' in params:
                    target.roll = math.radians(params['roll_degrees'])
                if target.length < 1e-5:
                    raise RuntimeError(f'{bone!r} would have no length.')
            elif op == 'set_parent':
                parent = params.get('parent') or None
                if parent is not None:
                    if bones.get(parent) is None:
                        raise RuntimeError(f'{rig.name!r} has no bone named {parent!r}.')
                    if parent == bone or bones[parent] in target.children_recursive:
                        raise RuntimeError(f'{parent!r} is {bone!r} or one of its children.')
                    target.parent = bones[parent]
                else:
                    target.parent = None
                target.use_connect = bool(params.get('connected', False)) and parent is not None
            elif op == 'scale_chain':
                factor = params['factor']
                pivot = target.head.copy()
                for each in [target, *target.children_recursive]:
                    head = pivot + (each.head - pivot) * factor
                    tail = pivot + (each.tail - pivot) * factor
                    each.head, each.tail = head, tail
            else:
                raise RuntimeError(f'Unknown bone operation {op!r}.')
        finally:
            _joinery_object_mode(rig, hidden)
        moved = rig.data.bones[bone]
        result = {
            'bone': bone, 'head': [round(v, 6) for v in moved.head_local],
            'tail': [round(v, 6) for v in moved.tail_local],
            'parent': moved.parent.name if moved.parent else None,
        }
    print('JOINERY_PART_OP:' + _joinery_json.dumps({'armature': rig.name, 'op': op, **result}))

def _joinery_edit_shape_key(object_name, key, op, params):
    obj = bpy.data.objects.get(object_name)
    if obj is None or obj.type != 'MESH':
        raise RuntimeError(f'There is no mesh object named {object_name!r} in this model.')
    keys = obj.data.shape_keys
    blocks = keys.key_blocks if keys is not None else {}
    if op in ('new_from_mix', 'new_from_pose'):
        if blocks.get(key) is not None:
            raise RuntimeError(f'{obj.name!r} already has a shape key named {key!r}.')
        if keys is None:
            obj.shape_key_add(name='Basis', from_mix=False)
        if op == 'new_from_mix':
            made = obj.shape_key_add(name=key, from_mix=True)
        else:
            armatures = [m for m in obj.modifiers if m.type == 'ARMATURE' and m.object is not None]
            if not armatures:
                raise RuntimeError(f'{obj.name!r} has no armature, so it has no pose to keep.')
            before = {k.name for k in obj.data.shape_keys.key_blocks}
            for other in bpy.context.view_layer.objects:
                other.select_set(other is obj)
            bpy.context.view_layer.objects.active = obj
            with bpy.context.temp_override(object=obj, active_object=obj):
                bpy.ops.object.modifier_apply_as_shapekey(modifier=armatures[0].name, keep_modifier=True)
            added = [k for k in obj.data.shape_keys.key_blocks if k.name not in before]
            if len(added) != 1:
                raise RuntimeError('Blender did not make a shape key from the pose.')
            made = added[0]
            made.name = key
        made.value = 0.0
        result = {'shape_key': made.name, 'vertices': len(made.data)}
    else:
        block = blocks.get(key)
        if block is None:
            raise RuntimeError(f'{obj.name!r} has no shape key named {key!r}.')
        if block == keys.reference_key and op in ('delete', 'set_value'):
            raise RuntimeError(f'{key!r} is the basis of {obj.name!r}; it has no value to change and stays.')
        if op == 'set_value':
            value = params['value']
            if not block.slider_min <= value <= block.slider_max:
                raise RuntimeError(f'{key!r} goes from {block.slider_min} to {block.slider_max}.')
            block.value = value
            result = {'shape_key': key, 'value': value}
        elif op == 'rename':
            name = params['name']
            if blocks.get(name) is not None:
                raise RuntimeError(f'{obj.name!r} already has a shape key named {name!r}.')
            block.name = name
            result = {'shape_key': block.name, 'was': key}
        elif op == 'delete':
            obj.shape_key_remove(block)
            result = {'deleted': key}
        else:
            raise RuntimeError(f'Unknown shape key operation {op!r}.')
    print('JOINERY_PART_OP:' + _joinery_json.dumps({'object': obj.name, 'op': op, **result}))
"""


def _call(name: str, *args: object) -> str:
    return PART_OPS + "\n" + OPS + "\n" + f"{name}({', '.join(repr(a) for a in args)})"


def link_map_code(material: str, image: str, path: str, editable: str = "") -> str:
    """Blender code that loads a tool-made PNG into one map, scope-guarded to that image."""
    return guarded(
        _call("_joinery_link_map", material, image, path, editable), images=(image,)
    )


def split_names(
    material: str, objects: list[str], maps: list[str], name: str = ""
) -> tuple[str, dict[str, str]]:
    """The copy's material name and a new name for each map it uses."""
    copy = name.strip() or f"{material}_{objects[0]}"
    return copy, {image: f"{image}_{copy}" for image in maps}


def split_material_code(
    material: str, objects: list[str], name: str, images: dict[str, str] | None
) -> str:
    """Blender code that gives `objects` their own copy of a material.

    `images` names a copy of each map the material uses; None shares the maps,
    so a later map edit still changes every object.
    """
    return guarded(
        _call("_joinery_split_material", material, list(objects), name, images),
        materials=(name,),
        images=tuple((images or {}).values()),
        objects=tuple(objects),
    )


def edit_bone_code(armature: str, bone: str, op: str, params: dict[str, Any]) -> str:
    """Blender code for one bone operation; only the armature may change."""
    if op not in BONE_OPS:
        raise ValueError(
            f"Unknown bone operation {op!r}. Use one of: {', '.join(BONE_OPS)}."
        )
    return guarded(
        _call("_joinery_edit_bone", armature, bone, op, dict(params)),
        objects=(armature,),
    )


def edit_shape_key_code(obj: str, key: str, op: str, params: dict[str, Any]) -> str:
    """Blender code for one shape key operation; only that object may change."""
    if op not in SHAPE_KEY_OPS:
        raise ValueError(
            f"Unknown shape key operation {op!r}. Use one of: {', '.join(SHAPE_KEY_OPS)}."
        )
    return guarded(
        _call("_joinery_edit_shape_key", obj, key, op, dict(params)), objects=(obj,)
    )


def _vector(value: Any, what: str) -> list[float]:
    if (
        not isinstance(value, list)
        or len(value) != 3
        or not all(_finite(x) for x in value)
    ):
        raise ValueError(f"{what} is three numbers in metres, in the armature's space.")
    return [float(x) for x in value]


def _finite(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _new_name(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 63:
        raise ValueError(f"{what} needs a new name of 1 to 63 characters.")
    return value.strip()


def check_params(
    ops: dict[str, frozenset[str]], op: str, params: dict[str, Any], what: str
) -> dict[str, Any]:
    """Refuse unknown operations and parameters, and check each value, before Blender starts."""
    if op not in ops:
        raise ValueError(
            f"Unknown {what} operation {op!r}. Use one of: {', '.join(ops)}."
        )
    unknown = set(params) - ops[op]
    if unknown:
        raise ValueError(
            f"{op} does not take {', '.join(sorted(unknown))}. "
            f"It takes: {', '.join(sorted(ops[op])) or 'nothing'}."
        )
    checked: dict[str, Any] = {}
    for key, value in params.items():
        if key == "name":
            checked[key] = _new_name(value, op)
        elif key in {"head", "tail"}:
            checked[key] = _vector(value, key)
        elif key == "parent":
            if value is not None and (not isinstance(value, str) or len(value) > 63):
                raise ValueError("parent is a bone name, or null for no parent.")
            checked[key] = value or None
        elif key == "connected":
            if not isinstance(value, bool):
                raise ValueError("connected is true or false.")
            checked[key] = value
        elif not _finite(value):
            raise ValueError(f"{key} must be a finite number.")
        else:
            checked[key] = float(value)
    if op == "rename" and "name" not in checked:
        raise ValueError("rename needs a name.")
    if op == "set_rest" and not checked:
        raise ValueError("set_rest needs head, tail or roll_degrees.")
    if op == "set_parent" and "parent" not in checked:
        raise ValueError("set_parent needs a parent (a bone name, or null).")
    if op == "scale_chain" and not 0.01 <= checked.get("factor", 0) <= 100:
        raise ValueError("scale_chain needs a factor from 0.01 to 100.")
    if op == "set_value" and not -10 <= checked.get("value", math.inf) <= 10:
        raise ValueError("set_value needs a value (usually 0 to 1).")
    return checked
