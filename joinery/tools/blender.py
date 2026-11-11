"""Blender: native .blend sources edited through bpy in the offline worker."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from joinery.tools.base import (
    BLENDER_WORKER,
    CREATIVE_WORKER,
    SANDBOX_ENTRYPOINT,
    Feature,
    ImageCheck,
    InstallCheck,
    McpProbe,
    Run,
    ToolModule,
    WorkerExec,
)
from joinery.tools.blender_export import (
    BAKE_FOR_EXPORT,
    BAKE_MACRO_KEYS,
    macro_call,
)

# Follows each image node's links forward to the shader socket it feeds, so a
# map is known by its role: straight into the BSDF, through Normal Map (normal),
# through Bump or Displacement (height) and through Separate Color (with the
# channel, as in an ORM map). A glTF Settings group's Occlusion input is ao.
PART_WALK = r"""
import json as _joinery_json
_JOINERY_ROLES = {
    'Base Color': 'base_color', 'Roughness': 'roughness', 'Metallic': 'metallic',
    'Normal': 'normal', 'Alpha': 'alpha', 'Emission Color': 'emission',
    'Emission': 'emission', 'Displacement': 'height', 'Occlusion': 'ao',
}
_JOINERY_SPLIT = {'SEPARATE_COLOR', 'SEPRGB', 'SEPARATE_RGB'}

def _joinery_terminal(node):
    if node.type == 'OUTPUT_MATERIAL' or node.type.startswith('BSDF_'):
        return True
    tree = getattr(node, 'node_tree', None)
    return node.type == 'GROUP' and tree is not None and tree.name.lower().startswith('gltf')

def _joinery_follow(socket, via, seen):
    rows = []
    for link in socket.links:
        if not link.is_valid or link.is_muted:
            continue
        node, to = link.to_node, link.to_socket
        hop = via + [(node.type, to.name, link.from_socket.name)]
        if _joinery_terminal(node):
            first = hop[0]
            if first[0] in ('BUMP', 'DISPLACEMENT') and first[1] == 'Height':
                role = 'height'
            elif first[0] == 'NORMAL_MAP':
                role = 'normal'
            else:
                role = _JOINERY_ROLES.get(to.name, to.name.lower().replace(' ', '_'))
            channel = ''
            for index, step in enumerate(hop[:-1]):
                if step[0] in _JOINERY_SPLIT:
                    channel = hop[index + 1][2]
            rows.append({
                'shader': node.name, 'socket': to.name, 'role': role,
                'channel': channel, 'via': [step[0] for step in hop[:-1]],
            })
            continue
        key = (node.name, to.identifier)
        if key in seen:
            continue
        seen.add(key)
        for output in node.outputs:
            rows += _joinery_follow(output, hop, seen)
    return rows

def _joinery_image_nodes(material):
    rows = []
    if material.node_tree is None:
        return rows
    for node in material.node_tree.nodes:
        if node.type != 'TEX_IMAGE' or node.image is None:
            continue
        uses = []
        for output in node.outputs:
            uses += _joinery_follow(output, [], set())
        rows.append({'node': node.name, 'label': node.label, 'image': node.image.name, 'uses': uses})
    return rows

def _joinery_maps(material):
    maps = {}
    for row in _joinery_image_nodes(material):
        for use in row['uses']:
            maps.setdefault(use['role'], row['image'])
    return maps

def _joinery_texture_files():
    files, used = {}, set()
    for image in sorted(bpy.data.images, key=lambda i: i.name):
        if image.type not in {'IMAGE', 'UV_TEST'} or image.size[0] == 0 or image.size[1] == 0:
            continue
        name = bpy.path.clean_name(image.name) or 'texture'
        while name in used:
            name += '_'
        used.add(name)
        files[image.name] = name + '.png'
    return files
"""

# Reports what an edit needs to target by name: objects with their parents,
# modifiers, vertex groups, material slots and shape keys, armatures with their
# bones, materials with every image node and the socket it feeds, and actions.
# Large rigs (Rigify, MPFB2) list deform bones only, with a total.
INSPECT_BLEND = (
    PART_WALK
    + """
import bpy, json
MAX_BONES = 256"""
)
INSPECT_BLEND += """
def bones(armature):
    every = list(armature.bones)
    shown = every if len(every) <= MAX_BONES else [b for b in every if b.use_deform]
    return {
        'bone_count': len(every),
        'deform_bone_count': sum(b.use_deform for b in every),
        'bones': [
            {'name': b.name, 'parent': b.parent.name if b.parent else None, 'deform': b.use_deform}
            for b in shown
        ],
    }
def describe(o):
    row = {
        'name': o.name,
        'type': o.type,
        'parent': o.parent.name if o.parent else None,
        'position': list(o.location),
        'scale': list(o.scale),
        'rotation': list(o.rotation_euler),
        'vertices': len(o.data.vertices) if o.type == 'MESH' else 0,
        'modifiers': [{'name': m.name, 'type': m.type} for m in o.modifiers],
    }
    if o.type == 'MESH':
        row['vertex_groups'] = len(o.vertex_groups)
        keys = o.data.shape_keys
        row['shape_keys'] = [k.name for k in keys.key_blocks] if keys else []
        faces = [0] * len(o.material_slots)
        for polygon in o.data.polygons:
            if polygon.material_index < len(faces):
                faces[polygon.material_index] += 1
        row['slots'] = [
            {'index': i, 'material': s.material.name if s.material else None, 'faces': faces[i]}
            for i, s in enumerate(o.material_slots)
        ]
    if o.type == 'ARMATURE':
        row.update(bones(o.data))
    if o.animation_data and o.animation_data.action:
        row['action'] = o.animation_data.action.name
    return row
_files = _joinery_texture_files()
def describe_material(m):
    images = []
    for row in _joinery_image_nodes(m):
        image = bpy.data.images[row['image']]
        row.update({
            'size': list(image.size),
            'colorspace': image.colorspace_settings.name,
            'packed': image.packed_file is not None,
            'source': image.source,
            'file': _files.get(image.name, ''),
            # The Krita or Material Maker source a map is painted in, once made.
            'editable': str(image.get('joinery_source', '')),
        })
        images.append(row)
    users = [
        o.name for o in bpy.data.objects
        if any(s.material is m for s in o.material_slots)
    ]
    return {'name': m.name, 'users': users, 'images': images}
print('JOINERY_DOCUMENT:' + json.dumps({
    'objects': [describe(o) for o in bpy.data.objects],
    'materials': [describe_material(m) for m in bpy.data.materials],
    'images': [
        {
            'name': i.name, 'size': list(i.size), 'colorspace': i.colorspace_settings.name,
            'file': _files.get(i.name, ''), 'users': i.users,
        }
        for i in bpy.data.images if i.type in {'IMAGE', 'UV_TEST'}
    ],
    'actions': [
        {'name': a.name, 'frames': [a.frame_range[0], a.frame_range[1]]}
        for a in bpy.data.actions
    ],
}))
"""

# Writes each image that has pixels as its own PNG. The .blend keeps its packed
# copy, so a recipe still replays from the .blend alone.
EXPORT_TEXTURES = """
def _joinery_export_textures(directory):
    os.makedirs(directory, exist_ok=True)
    used = set()
    for image in sorted(bpy.data.images, key=lambda i: i.name):
        if image.type != 'IMAGE' or image.size[0] == 0 or image.size[1] == 0:
            continue
        name = bpy.path.clean_name(image.name) or 'texture'
        while name in used:
            name += '_'
        used.add(name)
        width, height = image.size
        pixels = [0.0] * (width * height * 4)
        image.pixels.foreach_get(pixels)
        copy = bpy.data.images.new('joinery_texture_export', width, height, alpha=True)
        copy.colorspace_settings.name = image.colorspace_settings.name
        copy.pixels.foreach_set(pixels)
        copy.filepath_raw = os.path.join(directory, name + '.png')
        copy.file_format = 'PNG'
        copy.save()
        bpy.data.images.remove(copy)
"""


# Blender prints an error raised by a --python script and still exits 0, so a
# failed operation would look like a successful one that changed nothing. An
# error that reaches the top of the recipe exits 1 instead. Errors reported
# from elsewhere (a driver, a handler) only print, as before.
FAIL_ON_ERROR = """
import sys, traceback
def _joinery_fail(kind, value, tb, _top=globals()):
    traceback.print_exception(kind, value, tb)
    if tb is not None and tb.tb_frame.f_globals is _top:
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(1)
sys.excepthook = _joinery_fail
"""

# Packs every image whose pixels changed in memory: a bake target made with
# images.new (source GENERATED), or a packed image that was painted or baked
# again. pack_all() packs only images read from files, and never repacks one
# that is already packed, so without this the pixels are lost on save and the
# texture PNG is never written. Packing turns a generated image into a packed
# IMAGE, which the texture export then writes. Images that never changed
# regenerate identically and stay as they are.
PACK_CHANGED_IMAGES = """
for _joinery_image in bpy.data.images:
    if _joinery_image.source in {'GENERATED', 'FILE'} and _joinery_image.has_data and _joinery_image.is_dirty:
        _joinery_image.pack()
"""

# Runs after the save, so only the export carries it. Godot sanitises node
# names (Head.001 arrives as Head_001), so every object and material carries its
# Blender name as joinery_name, and each material lists its maps by role as
# joinery_maps, including maps glTF cannot carry, such as height. They reach
# Godot as the "extras" meta of the node and the material (export_extras).
EXPORT_NAMES = (
    PART_WALK
    + """
def _joinery_name_parts():
    for obj in bpy.data.objects:
        obj['joinery_name'] = obj.name
    for material in bpy.data.materials:
        material['joinery_name'] = material.name
        material['joinery_maps'] = _joinery_json.dumps(_joinery_maps(material), sort_keys=True)
"""
)

# Hashes what an edit could touch: each object (transform, slots, modifiers,
# mesh geometry, UVs and shape keys), each material (settings, nodes, links)
# and each image (size, colour space, pixels). A typed operation compares the
# hashes before and after its code and fails when anything outside its target
# changed, so a part edit can never quietly change the rest of the model.
SCOPE_GUARD = r"""
import array as _joinery_array, hashlib as _joinery_hashlib
_JOINERY_UI = {
    'rna_type', 'select', 'location', 'location_absolute', 'width', 'height', 'dimensions',
    'hide', 'show_options', 'show_preview', 'show_texture', 'show_expanded', 'is_active',
    'use_custom_color', 'color', 'color_tag', 'warning_propagation', 'is_override_data_editable',
    'persistent_uid', 'is_active_output', 'name', 'bl_idname', 'bl_label', 'bl_description',
    'bl_icon', 'bl_static_type', 'bl_width_default', 'bl_width_min', 'bl_width_max',
    'bl_height_default', 'bl_height_min', 'bl_height_max', 'type', 'internal_links', 'tag',
    'users', 'preview', 'is_evaluated', 'original', 'session_uid', 'paint_active_slot',
    'paint_clone_slot',
}

def _joinery_plain(value):
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, (int, bool, str)) or value is None:
        return value
    if hasattr(value, 'name') and not hasattr(value, '__len__'):
        return ('ref', value.name)
    try:
        return tuple(_joinery_plain(v) for v in value)
    except TypeError:
        return repr(type(value))

def _joinery_props(block):
    values = []
    for prop in block.bl_rna.properties:
        key = prop.identifier
        if key in _JOINERY_UI or prop.type == 'COLLECTION':
            continue
        try:
            value = getattr(block, key)
        except Exception:
            continue
        if prop.type == 'POINTER':
            value = getattr(value, 'name', None) if value is not None else None
        values.append((key, _joinery_plain(value)))
    return values

def _joinery_floats(collection, attribute, width):
    data = _joinery_array.array('f', [0.0]) * (len(collection) * width)
    if len(collection):
        collection.foreach_get(attribute, data)
    return data.tobytes()

def _joinery_ints(collection, attribute):
    data = _joinery_array.array('i', [0]) * len(collection)
    if len(collection):
        collection.foreach_get(attribute, data)
    return data.tobytes()

def _joinery_hash(parts):
    digest = _joinery_hashlib.sha256()
    for part in parts:
        digest.update(part if isinstance(part, bytes) else repr(part).encode())
    return digest.hexdigest()

def _joinery_object_hash(obj):
    parts = [obj.type, [list(row) for row in obj.matrix_basis], getattr(obj.data, 'name', None),
             obj.parent.name if obj.parent else None, obj.hide_render, obj.hide_viewport,
             [(s.link, s.material.name if s.material else None) for s in obj.material_slots],
             [g.name for g in obj.vertex_groups]]
    for modifier in obj.modifiers:
        parts.append((modifier.name, modifier.type, _joinery_props(modifier)))
    mesh = obj.data if obj.type == 'MESH' else None
    if mesh is not None:
        parts += [
            _joinery_floats(mesh.vertices, 'co', 3),
            _joinery_ints(mesh.loops, 'vertex_index'),
            _joinery_ints(mesh.polygons, 'material_index'),
        ]
        for layer in mesh.uv_layers:
            parts += [layer.name, _joinery_floats(layer.data, 'uv', 2)]
        if mesh.shape_keys:
            for key in mesh.shape_keys.key_blocks:
                parts += [key.name, key.value, key.mute, _joinery_floats(key.data, 'co', 3)]
    if obj.type == 'ARMATURE':
        for bone in obj.data.bones:
            parts.append((bone.name, bone.parent.name if bone.parent else None,
                          bone.use_connect, bone.use_deform,
                          _joinery_plain([list(row) for row in bone.matrix_local]),
                          round(bone.length, 6)))
    return _joinery_hash(parts)

def _joinery_material_hash(material):
    parts = _joinery_props(material)
    tree = material.node_tree
    if tree is not None:
        for node in sorted(tree.nodes, key=lambda n: n.name):
            inputs = [
                (s.identifier, _joinery_plain(getattr(s, 'default_value', None)))
                for s in node.inputs
            ]
            parts.append((node.name, node.bl_idname, node.label, _joinery_props(node), inputs))
        parts.append(sorted(
            (l.from_node.name, l.from_socket.identifier, l.to_node.name, l.to_socket.identifier, l.is_muted)
            for l in tree.links
        ))
    return _joinery_hash(parts)

def _joinery_image_hash(image):
    parts = [image.source, tuple(image.size), image.colorspace_settings.name, image.filepath]
    if image.size[0] and image.size[1]:
        parts.append(_joinery_pixels(image))
    return _joinery_hash(parts)

def _joinery_pixels(image):
    data = _joinery_array.array('f', [0.0]) * len(image.pixels)
    image.pixels.foreach_get(data)
    return data.tobytes()

def _joinery_scope_hashes():
    found = {}
    for obj in bpy.data.objects:
        found['object:' + obj.name] = _joinery_object_hash(obj)
    for material in bpy.data.materials:
        found['material:' + material.name] = _joinery_material_hash(material)
    for image in bpy.data.images:
        if image.type in {'IMAGE', 'UV_TEST'}:
            found['image:' + image.name] = _joinery_image_hash(image)
    return found

def _joinery_scope_check(before, allowed):
    after = _joinery_scope_hashes()
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    outside = [k for k in changed if k not in allowed]
    print('JOINERY_SCOPE:' + _joinery_json.dumps({'changed': changed, 'outside': outside}))
    if outside:
        names = ', '.join(k.replace(':', ' ', 1) for k in outside)
        raise RuntimeError(
            'This edit also changed ' + names + ', outside the selected part. Nothing was saved.'
        )
"""

# Typed part operations. Each runs as the code of an ordinary Blender edit, so
# snapshot, preimage, recipe, lock, texture export, GLB export and review all
# apply. Pixels are rewritten rather than adding nodes, because glTF exports
# the raw image and drops nodes such as Map Range between it and the shader.
PART_OPS = r"""
import numpy as _joinery_np
_JOINERY_COLOUR_ROLES = {'base_color', 'emission'}
_JOINERY_CHANNELS = {'R': 0, 'RED': 0, 'G': 1, 'GREEN': 1, 'B': 2, 'BLUE': 2, 'A': 3, 'ALPHA': 3}

def _joinery_material(name):
    material = bpy.data.materials.get(name)
    if material is None or material.node_tree is None:
        raise RuntimeError(f'There is no node material named {name!r} in this model.')
    return material

def _joinery_bsdf(material):
    for node in material.node_tree.nodes:
        if node.type == 'BSDF_PRINCIPLED':
            return node
    raise RuntimeError(f'{material.name!r} has no Principled BSDF.')

def _joinery_image_node(material, image):
    for row in _joinery_image_nodes(material):
        if row['image'] == image:
            return material.node_tree.nodes[row['node']], row
    raise RuntimeError(f'{material.name!r} does not use an image named {image!r}.')

def _joinery_set_material_value(material, socket, value):
    material = _joinery_material(material)
    bsdf = _joinery_bsdf(material)
    target = bsdf.inputs.get(socket)
    if target is None or not hasattr(target, 'default_value'):
        names = ', '.join(s.name for s in bsdf.inputs if hasattr(s, 'default_value'))
        raise RuntimeError(f'{socket!r} is not a value of {material.name!r}. Values: {names}')
    if target.is_linked:
        raise RuntimeError(f'{socket} of {material.name!r} comes from a map. Change the map with adjust_map.')
    current = target.default_value
    if isinstance(value, (int, float)):
        if hasattr(current, '__len__'):
            raise RuntimeError(f'{socket} needs a list of {len(current)} numbers.')
        target.default_value = float(value)
    else:
        if not hasattr(current, '__len__'):
            raise RuntimeError(f'{socket} needs one number.')
        values = list(value) + [1.0] * (len(current) - len(value))
        if len(values) != len(current):
            raise RuntimeError(f'{socket} needs {len(current)} numbers.')
        target.default_value = values
    print('JOINERY_PART_OP:' + _joinery_json.dumps({'material': material.name, 'socket': socket}))

def _joinery_rgb_to_hsv(rgb):
    np = _joinery_np
    r, g, b = rgb[:, 0], rgb[:, 1], rgb[:, 2]
    high, low = rgb.max(1), rgb.min(1)
    delta = high - low
    safe = np.where(delta > 0, delta, 1.0)
    hue = np.where(high == r, ((g - b) / safe) % 6, np.where(high == g, (b - r) / safe + 2, (r - g) / safe + 4)) / 6.0
    hue = np.where(delta > 0, hue, 0.0)
    saturation = np.where(high > 0, delta / np.where(high > 0, high, 1.0), 0.0)
    return hue, saturation, high

def _joinery_hsv_to_rgb(hue, saturation, value):
    np = _joinery_np
    sector = np.floor(hue * 6.0)
    f = hue * 6.0 - sector
    sector = sector.astype(int) % 6
    p = value * (1 - saturation)
    q = value * (1 - saturation * f)
    t = value * (1 - saturation * (1 - f))
    r = np.choose(sector, [value, q, p, p, t, value])
    g = np.choose(sector, [t, value, value, q, p, p])
    b = np.choose(sector, [p, p, t, value, value, q])
    return np.stack([r, g, b], axis=1)

def _joinery_adjust(values, op, params):
    np = _joinery_np
    if op == 'invert':
        return 1.0 - values
    if op == 'gamma':
        return np.power(np.clip(values, 0, 1), 1.0 / params.get('gamma', 1.0))
    if op == 'levels':
        low, high = params.get('in_low', 0.0), params.get('in_high', 1.0)
        span = max(high - low, 1e-6)
        scaled = np.power(np.clip((values - low) / span, 0, 1), 1.0 / params.get('gamma', 1.0))
        out_low, out_high = params.get('out_low', 0.0), params.get('out_high', 1.0)
        return out_low + scaled * (out_high - out_low)
    if op == 'contrast':
        return (values - 0.5) * params.get('factor', 1.0) + 0.5
    if op == 'tint':
        colour = np.array(params['color'][:3], dtype=np.float32)
        strength = params.get('strength', 1.0)
        return values * (1.0 - strength) + values * colour * strength
    if op == 'hue_sat_value':
        hue, saturation, value = _joinery_rgb_to_hsv(values)
        hue = (hue + params.get('hue', 0.0)) % 1.0
        saturation = np.clip(saturation * params.get('saturation', 1.0), 0, 1)
        value = value * params.get('value', 1.0)
        return _joinery_hsv_to_rgb(hue, saturation, value)
    raise RuntimeError(f'Unknown map adjustment {op!r}.')

def _joinery_adjust_map(material, image, op, params):
    np = _joinery_np
    material = _joinery_material(material)
    node, row = _joinery_image_node(material, image)
    picture = node.image
    roles = {use['role'] for use in row['uses']}
    if roles:
        # The colour space goes first: setting it later reloads the image and
        # throws the new pixels away.
        space = 'sRGB' if roles & _JOINERY_COLOUR_ROLES else 'Non-Color'
        if picture.colorspace_settings.name != space:
            picture.colorspace_settings.name = space
    width, height = picture.size
    if width == 0 or height == 0:
        raise RuntimeError(f'{image!r} has no pixels to adjust.')
    pixels = np.empty(width * height * 4, dtype=np.float32)
    picture.pixels.foreach_get(pixels)
    pixels = pixels.reshape(-1, 4)
    channel = str(params.get('channel', '')).upper()
    if channel:
        index = _JOINERY_CHANNELS[channel]
        column = pixels[:, index:index + 1]
        pixels[:, index:index + 1] = np.clip(_joinery_adjust(np.repeat(column, 3, 1), op, params)[:, :1], 0, 1)
    else:
        pixels[:, :3] = np.clip(_joinery_adjust(pixels[:, :3].copy(), op, params), 0, 1)
    picture.pixels.foreach_set(pixels.ravel())
    picture.update()
    picture.pack()
    print('JOINERY_PART_OP:' + _joinery_json.dumps({
        'material': material.name, 'image': picture.name, 'op': op,
        'colorspace': picture.colorspace_settings.name, 'mean': float(pixels[:, :3].mean()),
    }))

def _joinery_bake_height_to_normal(material, height, strength):
    material = _joinery_material(material)
    tree = material.node_tree
    bsdf = _joinery_bsdf(material)
    height_node, _row = _joinery_image_node(material, height)
    users = [
        o for o in bpy.data.objects
        if o.type == 'MESH' and any(s.material is material for s in o.material_slots)
    ]
    if not users:
        raise RuntimeError(f'No object uses {material.name!r}, so there is nothing to bake on.')
    for obj in users:
        if not obj.data.uv_layers:
            raise RuntimeError(f'{obj.name!r} has no UV map, so its normal map cannot be baked.')
    name = material.name + '_normal_baked'
    side = max(256, min(4096, max(height_node.image.size)))
    old = bpy.data.images.get(name)
    if old is not None:
        for node in [n for n in tree.nodes if n.type == 'TEX_IMAGE' and n.image is old]:
            tree.nodes.remove(node)
        bpy.data.images.remove(old)
    baked = bpy.data.images.new(name, side, side, alpha=False, is_data=True)
    # Non-Color before any pixel is written (a later change drops them).
    baked.colorspace_settings.name = 'Non-Color'
    baked.generated_color = (0.5, 0.5, 1.0, 1.0)
    target = tree.nodes.new('ShaderNodeTexImage')
    target.name = target.label = 'Baked Normal'
    target.image = baked
    target.location = (bsdf.location.x - 700, bsdf.location.y - 500)
    # Bake through a temporary Bump fed by the height map, keeping any detail
    # normal that already reached the shader.
    previous = bsdf.inputs['Normal'].links[0].from_socket if bsdf.inputs['Normal'].is_linked else None
    bump = tree.nodes.new('ShaderNodeBump')
    bump.inputs['Strength'].default_value = strength
    tree.links.new(height_node.outputs['Color'], bump.inputs['Height'])
    if previous is not None:
        source = previous.node
        if source.type == 'BUMP' and source.inputs['Normal'].is_linked:
            tree.links.new(source.inputs['Normal'].links[0].from_socket, bump.inputs['Normal'])
        elif source.type == 'NORMAL_MAP':
            tree.links.new(previous, bump.inputs['Normal'])
    tree.links.new(bump.outputs['Normal'], bsdf.inputs['Normal'])
    for node in tree.nodes:
        node.select = node is target
    tree.nodes.active = target
    # Cycles needs an active image in every material of a baked object; the
    # other materials get a throwaway one, removed again right after.
    spare = []
    for obj in users:
        for slot in obj.material_slots:
            other = slot.material
            if other is None or other is material or other.node_tree is None:
                continue
            if any(m is other for m, _n, _i in spare):
                continue
            scratch = bpy.data.images.new('joinery_bake_scratch', 8, 8)
            node = other.node_tree.nodes.new('ShaderNodeTexImage')
            node.image = scratch
            active = other.node_tree.nodes.active
            other.node_tree.nodes.active = node
            spare.append((other, node, (scratch, active)))
    scene = bpy.context.scene
    scene.render.engine = 'CYCLES'
    scene.cycles.device = 'CPU'
    scene.cycles.samples = 1
    scene.render.bake.normal_space = 'TANGENT'
    scene.render.bake.margin = 4
    if bpy.context.object is not None and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    for obj in bpy.context.view_layer.objects:
        obj.select_set(obj in users)
    bpy.context.view_layer.objects.active = users[0]
    try:
        bpy.ops.object.bake(type='NORMAL', normal_space='TANGENT', use_clear=True, margin=4)
    finally:
        for other, node, (scratch, active) in spare:
            other.node_tree.nodes.remove(node)
            other.node_tree.nodes.active = active
            bpy.data.images.remove(scratch)
        tree.nodes.remove(bump)
    # Packing straight after a bake keeps the image GENERATED (Blender does not
    # see the baked buffer as changed), so it would come back flat on load.
    # Writing the pixels back marks them changed, and then they pack.
    pixels = _joinery_np.empty(side * side * 4, dtype=_joinery_np.float32)
    baked.pixels.foreach_get(pixels)
    baked.pixels.foreach_set(pixels)
    baked.update()
    baked.pack()
    if baked.packed_file is None:
        raise RuntimeError('The baked normal map could not be stored in the model.')
    # The game reads the baked map: baked image -> Normal Map -> BSDF Normal.
    # The height map keeps driving Displacement in Blender only.
    normal_map = tree.nodes.new('ShaderNodeNormalMap')
    normal_map.name = normal_map.label = 'Baked Normal Map'
    normal_map.location = (bsdf.location.x - 300, bsdf.location.y - 500)
    tree.links.new(target.outputs['Color'], normal_map.inputs['Color'])
    tree.links.new(normal_map.outputs['Normal'], bsdf.inputs['Normal'])
    rgb = pixels.reshape(-1, 4)[:, :3]
    print('JOINERY_PART_OP:' + _joinery_json.dumps({
        'material': material.name, 'image': baked.name,
        'mean': [float(v) for v in rgb.mean(0)], 'spread': [float(v) for v in rgb.std(0)],
    }))
"""

#: Map adjustments adjust_map knows, with the parameters each one takes.
MAP_OPS: dict[str, frozenset[str]] = {
    "invert": frozenset({"channel"}),
    "gamma": frozenset({"gamma", "channel"}),
    "levels": frozenset(
        {"in_low", "in_high", "gamma", "out_low", "out_high", "channel"}
    ),
    "contrast": frozenset({"factor", "channel"}),
    "tint": frozenset({"color", "strength"}),
    "hue_sat_value": frozenset({"hue", "saturation", "value"}),
}


def guarded(
    code: str,
    *,
    materials: tuple[str, ...] = (),
    images: tuple[str, ...] = (),
    objects: tuple[str, ...] = (),
) -> str:
    """Wrap operation code in the scope guard: only the named parts may change."""
    allowed = sorted(
        [f"material:{name}" for name in materials]
        + [f"image:{name}" for name in images]
        + [f"object:{name}" for name in objects]
    )
    return "\n".join(
        [
            PART_WALK,
            SCOPE_GUARD,
            "_joinery_before = _joinery_scope_hashes()",
            # An operation adds the parts Blender changes along with its
            # target (a renamed bone's vertex groups) to this set.
            "_joinery_also = set()",
            code,
            f"_joinery_scope_check(_joinery_before, set({allowed!r}) | _joinery_also)",
        ]
    )


def material_value_code(material: str, socket: str, value: float | list[float]) -> str:
    """Blender code that sets one unlinked shader value of a material, scope-guarded."""
    call = f"_joinery_set_material_value({material!r}, {socket!r}, {value!r})"
    return guarded(PART_OPS + "\n" + call, materials=(material,))


def adjust_map_code(material: str, image: str, op: str, params: dict[str, Any]) -> str:
    """Blender code that rewrites one map's pixels, scope-guarded to that image."""
    if op not in MAP_OPS:
        raise ValueError(
            f"Unknown map adjustment {op!r}. Use one of: {', '.join(MAP_OPS)}."
        )
    call = f"_joinery_adjust_map({material!r}, {image!r}, {op!r}, {params!r})"
    return guarded(PART_OPS + "\n" + call, images=(image,))


def baked_normal_name(material: str) -> str:
    """The image a height map is baked into for a material."""
    return material + "_normal_baked"


def bake_height_code(material: str, height_image: str, strength: float) -> str:
    """Blender code that bakes a height map into a tangent normal map for the game."""
    call = (
        f"_joinery_bake_height_to_normal({material!r}, {height_image!r}, {strength!r})"
    )
    return guarded(
        PART_OPS + "\n" + call,
        materials=(material,),
        images=(baked_normal_name(material),),
    )


#: Extensions a recipe turns on when its code names them: (word in the code,
#: module, label). Recipes start from factory settings, which enable neither.
EXTENSIONS = (
    ("rigify", "rigify", "Rigify"),
    ("mpfb", "bl_ext.system.mpfb", "MPFB2"),
)

#: How an agent turns MPFB2 on. Shown wherever Blender code is written.
MPFB_HINT = (
    "Blender runs in the background from factory settings, so enable extensions "
    "first: MPFB2 is addon_utils.enable('bl_ext.system.mpfb', default_set=True) "
    "and Rigify is addon_utils.enable('rigify', default_set=True)."
)

# Builds one MPFB2 human. Passes when MPFB2 is absent (the tier-2 check reports
# that as an old image), so this only fails when MPFB2 loads but cannot work.
MPFB_HUMAN = """
import addon_utils
if not addon_utils.enable('bl_ext.system.mpfb', default_set=True):
    print('JOINERY_MPFB_ABSENT')
else:
    from bl_ext.system.mpfb.services.humanservice import HumanService
    human = HumanService.create_human()
    print('JOINERY_MPFB_HUMAN', len(human.data.vertices))
"""


def texture_dir(source: str) -> str:
    """Where a Blender source's textures are written as separate image files."""
    return f"art/textures/{Path(source).stem}"


def _blender_expr(expr: str) -> tuple[str, ...]:
    return (
        "blender",
        "-b",
        "--factory-startup",
        "--python-exit-code",
        "1",
        "--python-expr",
        expr,
    )


def _enable(addon: str, marker: str) -> tuple[str, ...]:
    return _blender_expr(
        "import addon_utils; "
        f"assert addon_utils.enable({addon!r}, default_set=True); "
        f"print({marker!r})"
    )


def extension_lines(code: str) -> list[str]:
    """Enable each extension the operation code names, or stop with a clear reason."""
    lines: list[str] = []
    for word, module, label in EXTENSIONS:
        if re.search(word, code, re.IGNORECASE):
            reason = (
                f"{label} is not available in this Blender worker. "
                "Run Joinery setup to rebuild the worker image."
            )
            lines += [
                "import addon_utils",
                f"if not addon_utils.enable({module!r}, default_set=True):",
                f"    raise RuntimeError({reason!r})",
            ]
    return lines


class Blender(ToolModule):
    id = "blender"
    name = "Blender"
    family = "joinery-blender-ops"
    suffix = ".blend"
    script_ext = ".py"
    authoring = True
    binary = "blender"
    desktop = "blender"
    parts_provider = "blender"
    ready_summary = "{name} {version} ready (offline worker)"
    needs_image = "Blender worker image not built. Rebuild the worker."
    broken = "Blender failed to start"
    mcp_notes = (("execute_blender_code", "Joinery: " + MPFB_HINT),)
    features = (
        Feature(
            "rigify",
            "Rigify",
            needs_image="Rigify unavailable: the worker image is out of date. Rebuild the worker.",
            broken="Rigify failed to load",
        ),
        Feature(
            "mpfb",
            "MPFB2 humans",
            needs_image=(
                "MPFB2 humans unavailable: worker image predates the fix. Run Joinery "
                "setup to rebuild (about 1 h, about 6 GB)."
            ),
            broken="MPFB2 humans failed to load",
        ),
        Feature(
            "mcp",
            "official Blender MCP connected",
            needs_image=(
                "Blender MCP worker not built. Rebuild the worker. Editing still works "
                "through Joinery's Blender adapter."
            ),
            needs_install=(
                "Blender MCP not installed. Rebuild the worker. Editing still works "
                "through Joinery's Blender adapter."
            ),
            broken="Blender MCP error",
        ),
    )
    checks = (
        ImageCheck(CREATIVE_WORKER),
        WorkerExec(
            CREATIVE_WORKER,
            ("blender", "--version"),
            expect=r"Blender \d",
            version=r"Blender (\d+\.\d+(?:\.\d+)?)",
            on_failure="needs_image",
            failure="Blender is missing from the creative worker",
        ),
        WorkerExec(
            CREATIVE_WORKER,
            _enable("rigify", "JOINERY_RIGIFY_OK"),
            expect="JOINERY_RIGIFY_OK",
            tier=2,
            timeout=120,
            feature="rigify",
            on_failure="needs_image",
        ),
        WorkerExec(
            CREATIVE_WORKER,
            _enable("bl_ext.system.mpfb", "JOINERY_MPFB_OK"),
            expect="JOINERY_MPFB_OK",
            tier=2,
            timeout=120,
            feature="mpfb",
            on_failure="needs_image",
        ),
        WorkerExec(
            CREATIVE_WORKER,
            _blender_expr(MPFB_HUMAN),
            expect=r"JOINERY_MPFB_(ABSENT|HUMAN [1-9]\d*)",
            tier="deep",
            timeout=300,
            feature="mpfb",
            failure="could not build a human",
        ),
        ImageCheck(BLENDER_WORKER, feature="mcp"),
        WorkerExec(
            BLENDER_WORKER,
            ("blender", "--version"),
            expect=r"Blender \d",
            feature="mcp",
            entrypoint=SANDBOX_ENTRYPOINT,
            on_failure="needs_image",
        ),
        InstallCheck("blender", feature="mcp"),
        McpProbe(
            "blender",
            "get_blendfile_summary_datablocks",
            feature="mcp",
            image=BLENDER_WORKER,
        ),
    )

    def check_destinations(self, source: str, exports: list[str]) -> None:
        if not source.startswith("art/sources/") or any(
            Path(path).is_relative_to(Path(source).parent) for path in exports
        ):
            raise ValueError(
                "Keep native Blender sources under art/sources/ and exports outside that source directory."
            )

    def script(
        self,
        source: str,
        base: str,
        code: str,
        exports: list[str],
        *,
        keep_macro_shape_keys: bool = False,
    ) -> str:
        lines = [
            "import bpy, os, math",
            FAIL_ON_ERROR,
            "os.chdir('/work')",
            "bpy.ops.wm.read_factory_settings(use_empty=True)",
            "bpy.context.preferences.filepaths.save_version = 0",
        ]
        if base:
            lines.append(
                f"bpy.ops.wm.open_mainfile(filepath=os.path.abspath({base!r}))"
            )
        lines += [
            "bpy.context.scene.unit_settings.system = 'METRIC'",
            "bpy.context.scene.unit_settings.scale_length = 1.0",
            *extension_lines(code),
            code,
        ]
        lines += [
            "assert all(all(math.isfinite(x) for row in o.matrix_world for x in row) for o in bpy.data.objects), 'Nonfinite object transform'",
            PACK_CHANGED_IMAGES,
            "bpy.ops.file.pack_all()",
            f"bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath({source!r}))",
            EXPORT_TEXTURES,
            f"_joinery_export_textures(os.path.abspath({texture_dir(source)!r}))",
        ]
        if exports:
            # A generated Rigify rig carries hundreds of control and mechanism
            # bones; the game needs only its deform bones. Hand-made rigs keep
            # every bone, since non-deform bones there are often sockets.
            lines += [
                "_joinery_deform_only = any('rig_id' in a for a in bpy.data.armatures)",
                # MPFB2's body-shape macros go into the base mesh unless the
                # model keeps them; the choice is part of this recipe.
                BAKE_MACRO_KEYS,
                macro_call(keep_macro_shape_keys),
                BAKE_FOR_EXPORT,
                "_joinery_bake_for_export()",
                EXPORT_NAMES,
                "_joinery_name_parts()",
            ]
        for export in exports:
            if Path(export).suffix != ".glb":
                raise ValueError(
                    "The automatic Blender export uses GLB. Render images explicitly in the operation script."
                )
            lines.append(
                f"bpy.ops.export_scene.gltf(filepath=os.path.abspath({export!r}), export_format='GLB', export_yup=True, export_apply=True, export_extras=True, export_def_bones=_joinery_deform_only)"
            )
        return "\n".join(lines) + "\n"

    def replay_argv(self, script: str) -> list[str]:
        # --factory-startup so a user's preferences cannot change the result.
        return ["blender", "--background", "--factory-startup", "--python", script]

    async def inspect(
        self, project: Path, path: Path, source: str, run: Run
    ) -> dict[str, Any] | None:
        got = await run(
            ["blender", "--background", source, "--python-expr", INSPECT_BLEND],
            project,
            readonly=True,
        )
        for line in got.output.splitlines():
            if line.startswith("JOINERY_DOCUMENT:"):
                data: dict[str, Any] = json.loads(
                    line.removeprefix("JOINERY_DOCUMENT:")
                )
                return data
        raise ValueError(
            "Blender could not inspect this document: " + got.output[-800:]
        )


MODULE = Blender()
