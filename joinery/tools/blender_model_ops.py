"""Typed whole-model operations in Blender, for bringing a model in line with the bible.

* ``apply_scale``: make named objects' scale 1 without changing how they look.
* ``set_pivot``: move the model so its origin is at the bottom-centre (or the
  centre) of its bounds, as the bible's pivot rule measures it.
* ``reduce_triangles``: a Decimate (collapse) on every mesh, applied, so the
  model fits a triangle budget.
* ``set_texel_density``: bring named objects to a texel density: a texture too
  dense is resized in Blender; one too sparse has its UVs scaled about their
  centre, so it repeats more often.

Each runs as the code of an ordinary Blender edit inside the scope guard
(`blender.guarded`), so the snapshot, recipe, exports and review all apply and
nothing it doesn't name may change.
"""

from __future__ import annotations

import math

from joinery.tools.blender import guarded

MODEL_OPS = r"""
import math as _joinery_math
from mathutils import Matrix as _JoineryMatrix, Vector as _JoineryVector

def _joinery_object(name):
    obj = bpy.data.objects.get(name)
    if obj is None:
        raise RuntimeError(f'There is no object named {name!r} in this model.')
    return obj

def _joinery_report(**values):
    print('JOINERY_MODEL_OP:' + _joinery_json.dumps(values))

def _joinery_select(objects):
    for obj in bpy.context.view_layer.objects:
        obj.select_set(False)
    for obj in objects:
        obj.hide_set(False)
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]

def _joinery_apply_scale(names):
    objects = [_joinery_object(n) for n in names]
    for obj in objects:
        if obj.type == 'MESH' and obj.data.users > 1:
            raise RuntimeError(f'{obj.name} shares its mesh with other objects, so its scale cannot be applied alone.')
        _joinery_also.add('object:' + obj.name)
        for child in obj.children_recursive:
            _joinery_also.add('object:' + child.name)
    before = {o.name: list(o.scale) for o in objects}
    _joinery_select(objects)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    _joinery_report(applied_scale=before)

def _joinery_bounds():
    meshes = [o for o in bpy.data.objects if o.type == 'MESH' and o.users_scene]
    corners = [o.matrix_world @ _JoineryVector(c) for o in meshes for c in o.bound_box]
    if not corners:
        raise RuntimeError('This model has no mesh to place.')
    low = [min(c[i] for c in corners) for i in range(3)]
    high = [max(c[i] for c in corners) for i in range(3)]
    return meshes, low, high

def _joinery_set_pivot(mode):
    meshes, low, high = _joinery_bounds()
    centre = [(low[i] + high[i]) / 2 for i in range(3)]
    # Blender is Z up: the bottom is the lowest z, centred in x and y.
    anchor = _JoineryVector((centre[0], centre[1], low[2] if mode == 'bottom-centre' else centre[2]))
    roots = [o for o in bpy.data.objects if o.parent is None and o.users_scene]
    single = roots[0] if len(roots) == 1 else None
    if single is not None and single.type == 'MESH' and single.data.users == 1 and not single.children:
        # One mesh: its origin moves into the geometry, and the object to 0.
        local = single.matrix_world.inverted() @ anchor
        single.data.transform(_JoineryMatrix.Translation(-local))
        single.location = (0.0, 0.0, 0.0)
        moved = [single]
    else:
        for root in roots:
            root.location = root.location - anchor
        moved = roots
    for obj in [*moved, *meshes]:
        _joinery_also.add('object:' + obj.name)
        for child in obj.children_recursive:
            _joinery_also.add('object:' + child.name)
    _joinery_report(pivot=mode, moved_by=[-v for v in anchor])

def _joinery_triangles(obj, depsgraph):
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        mesh.calc_loop_triangles()
        return len(mesh.loop_triangles)
    finally:
        evaluated.to_mesh_clear()

def _joinery_reduce_triangles(budget):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    meshes = [o for o in bpy.data.objects if o.type == 'MESH' and o.users_scene]
    counts = {o.name: _joinery_triangles(o, depsgraph) for o in meshes}
    total = sum(counts.values())
    if total <= budget:
        _joinery_report(triangles=total, budget=budget)
        return
    ratio = max(0.01, budget / total * 0.97)
    for obj in meshes:
        if not counts[obj.name]:
            continue
        if obj.data.users > 1:
            raise RuntimeError(f'{obj.name} shares its mesh with other objects; decimate it in Blender.')
        modifier = obj.modifiers.new('Joinery decimate', 'DECIMATE')
        modifier.decimate_type = 'COLLAPSE'
        modifier.ratio = ratio
        _joinery_select([obj])
        bpy.ops.object.modifier_apply(modifier=modifier.name)
        _joinery_also.add('object:' + obj.name)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    after = sum(_joinery_triangles(o, depsgraph) for o in meshes)
    _joinery_report(triangles_before=total, triangles=after, budget=budget, ratio=ratio)

def _joinery_base_image(material):
    tree = getattr(material, 'node_tree', None)
    if tree is None:
        return None
    for node in tree.nodes:
        if node.type != 'BSDF_PRINCIPLED' or 'Base Color' not in node.inputs:
            continue
        stack = [link.from_node for link in node.inputs['Base Color'].links]
        seen = set()
        while stack:
            current = stack.pop()
            if current.name in seen:
                continue
            seen.add(current.name)
            if current.type == 'TEX_IMAGE' and current.image is not None and current.image.size[0]:
                return current.image
            for socket in current.inputs:
                stack.extend(link.from_node for link in socket.links)
    return None

def _joinery_density(obj):
    mesh = obj.data
    uv = mesh.uv_layers.active
    if uv is None:
        return None, None
    mesh.calc_loop_triangles()
    images = [_joinery_base_image(s.material) if s.material else None for s in obj.material_slots]
    matrix = obj.matrix_world
    uv_px = world = 0.0
    used = None
    for tri in mesh.loop_triangles:
        image = images[tri.material_index] if tri.material_index < len(images) else None
        if image is None:
            continue
        used = used or image
        a, b, c = (matrix @ mesh.vertices[i].co for i in tri.vertices)
        world += (b - a).cross(c - a).length / 2
        p, q, r = (uv.data[i].uv for i in tri.loops)
        area = abs((q[0] - p[0]) * (r[1] - p[1]) - (r[0] - p[0]) * (q[1] - p[1])) / 2
        uv_px += area * image.size[0] * image.size[1]
    if world <= 0 or uv_px <= 0:
        return None, None
    return _joinery_math.sqrt(uv_px / world), used

def _joinery_set_texel_density(names, target, power_of_two):
    done = {}
    for name in names:
        obj = _joinery_object(name)
        if obj.type != 'MESH':
            raise RuntimeError(f'{name} is not a mesh.')
        density, image = _joinery_density(obj)
        if density is None:
            raise RuntimeError(f'{name} has no UVs with a base colour texture to measure.')
        factor = target / density
        if factor < 1:
            width, height = image.size
            new = [max(1, round(width * factor)), max(1, round(height * factor))]
            if power_of_two:
                new = [2 ** max(0, round(_joinery_math.log2(v))) for v in new]
            if new != [width, height]:
                image.scale(new[0], new[1])
                _joinery_also.add('image:' + image.name)
            done[name] = {'image': image.name, 'size': new, 'was': [width, height]}
        else:
            uv = obj.data.uv_layers.active
            us = [d.uv[0] for d in uv.data]
            vs = [d.uv[1] for d in uv.data]
            cu, cv = (min(us) + max(us)) / 2, (min(vs) + max(vs)) / 2
            for d in uv.data:
                d.uv = ((d.uv[0] - cu) * factor + cu, (d.uv[1] - cv) * factor + cv)
            _joinery_also.add('object:' + obj.name)
            done[name] = {'uv_scale': factor}
        after, _ = _joinery_density(obj)
        done[name]['density'] = [density, after]
    _joinery_report(texel_density=done, target=target)
"""


def _code(call: str, objects: tuple[str, ...] = ()) -> str:
    return guarded(MODEL_OPS + "\n" + call, objects=objects)


def apply_scale_code(objects: list[str]) -> str:
    return _code(f"_joinery_apply_scale({objects!r})", tuple(objects))


def set_pivot_code(pivot: str) -> str:
    if pivot not in {"bottom-centre", "centre"}:
        raise ValueError('pivot is "bottom-centre" or "centre".')
    return _code(f"_joinery_set_pivot({pivot!r})")


def reduce_triangles_code(max_triangles: int) -> str:
    if (
        not isinstance(max_triangles, int)
        or isinstance(max_triangles, bool)
        or max_triangles < 4
    ):
        raise ValueError("max_triangles is a whole number of at least 4.")
    return _code(f"_joinery_reduce_triangles({max_triangles!r})")


def texel_density_code(objects: list[str], px_per_m: float, power_of_two: bool) -> str:
    if (
        not isinstance(px_per_m, (int, float))
        or not math.isfinite(px_per_m)
        or px_per_m <= 0
    ):
        raise ValueError("px_per_m is a number above 0.")
    return _code(
        f"_joinery_set_texel_density({objects!r}, {float(px_per_m)!r}, {bool(power_of_two)!r})",
        tuple(objects),
    )
