# Joinery kits: shared Blender helpers. MIT licence, see kits/LICENSE.
#
# Runs inside Blender's Python. Every kit builds one mesh object from bmesh
# shapes, in metres with +Z up (glTF export turns that into Godot's +Y up),
# with scale 1 and flat-coloured materials. All randomness comes from the
# random.Random the recipe passes in, so the same seed builds the same model.

import json
import math
import random

import bmesh
import bpy
from mathutils import Matrix, Vector


def _linear(channel):
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


def colour(hex_colour):
    """A '#rrggbb' sRGB colour as linear RGBA, the way Blender stores it."""
    value = hex_colour.lstrip("#")
    rgb = [int(value[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    return [_linear(c) for c in rgb] + [1.0]


def material(name, look):
    """A flat-coloured Principled material from a look {colour, roughness, metallic}."""
    found = bpy.data.materials.get(name)
    if found is not None:
        return found
    made = bpy.data.materials.new(name)
    if made.node_tree is None:
        made.use_nodes = True
    tree = made.node_tree
    shader = next((n for n in tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if shader is None:
        shader = tree.nodes.new("ShaderNodeBsdfPrincipled")
        output = next((n for n in tree.nodes if n.type == "OUTPUT_MATERIAL"), None)
        if output is None:
            output = tree.nodes.new("ShaderNodeOutputMaterial")
        tree.links.new(shader.outputs["BSDF"], output.inputs["Surface"])
    rgba = colour(look["colour"])
    shader.inputs["Base Color"].default_value = rgba
    shader.inputs["Roughness"].default_value = float(look["roughness"])
    shader.inputs["Metallic"].default_value = float(look["metallic"])
    made.diffuse_color = rgba
    made.roughness = float(look["roughness"])
    made.metallic = float(look["metallic"])
    # glTF's doubleSided: leaf cards are seen from both sides.
    made.use_backface_culling = not look.get("double_sided", False)
    return made


class Shapes:
    """A bmesh being built, with each shape's faces given a material slot."""

    def __init__(self):
        self.bm = bmesh.new()

    def _paint(self, verts, slot):
        faces = {f for v in verts for f in v.link_faces}
        for face in faces:
            face.material_index = slot
        return faces

    def box(self, centre, size, slot=0, rotation=None):
        """A box of `size` (x, y, z metres) centred on `centre`."""
        matrix = Matrix.Translation(Vector(centre))
        if rotation is not None:
            matrix = matrix @ rotation.to_4x4()
        matrix = matrix @ Matrix.Diagonal(Vector((*size, 1.0)))
        made = bmesh.ops.create_cube(self.bm, size=1.0, matrix=matrix)
        return self._paint(made["verts"], slot)

    def tube(self, start, end, radius_start, radius_end, sides, slot=0, caps=True):
        """A tapered cylinder (or cone, with radius_end 0) from start to end."""
        start, end = Vector(start), Vector(end)
        axis = end - start
        length = axis.length
        if length <= 1e-6:
            return set()
        rotation = Vector((0.0, 0.0, 1.0)).rotation_difference(axis.normalized())
        matrix = Matrix.Translation((start + end) / 2) @ rotation.to_matrix().to_4x4()
        made = bmesh.ops.create_cone(
            self.bm,
            cap_ends=caps,
            cap_tris=False,
            segments=max(3, int(sides)),
            radius1=radius_start,
            radius2=radius_end,
            depth=length,
            matrix=matrix,
        )
        return self._paint(made["verts"], slot)

    def blob(self, centre, radius, subdivisions, slot=0, scale=(1.0, 1.0, 1.0)):
        """An icosphere, returned as its vertices for the caller to shape."""
        matrix = Matrix.Translation(Vector(centre)) @ Matrix.Diagonal(
            Vector((*scale, 1.0))
        )
        made = bmesh.ops.create_icosphere(
            self.bm, subdivisions=int(subdivisions), radius=radius, matrix=matrix
        )
        self._paint(made["verts"], slot)
        return made["verts"]

    def quad(self, centre, normal, size, slot=0, spin=0.0):
        """A square card facing `normal`, `size` metres across."""
        normal = Vector(normal).normalized()
        rotation = Vector((0.0, 0.0, 1.0)).rotation_difference(normal).to_matrix()
        rotation = rotation @ Matrix.Rotation(spin, 3, "Z")
        half = size / 2
        corners = [
            Vector(centre) + rotation @ Vector((x, y, 0.0))
            for x, y in ((-half, -half), (half, -half), (half, half), (-half, half))
        ]
        verts = [self.bm.verts.new(c) for c in corners]
        face = self.bm.faces.new(verts)
        face.material_index = slot
        return {face}

    def polygon(self, points, slot=0):
        """One face through the given points (a lid, a gable)."""
        verts = [self.bm.verts.new(Vector(p)) for p in points]
        face = self.bm.faces.new(verts)
        face.material_index = slot
        return face

    def finish(self, name, materials):
        """The mesh object, with only the material slots its faces use."""
        bm = self.bm
        used = sorted({f.material_index for f in bm.faces})
        remap = {old: new for new, old in enumerate(used)}
        for face in bm.faces:
            face.material_index = remap[face.material_index]
        bm.normal_update()
        mesh = bpy.data.meshes.new(name)
        bm.to_mesh(mesh)
        bm.free()
        for old in used:
            mesh.materials.append(materials[old])
        obj = bpy.data.objects.new(name, mesh)
        bpy.context.scene.collection.objects.link(obj)
        return obj


def ring(count, rng, jitter=0.0):
    """Evenly spread angles with a little random jitter."""
    start = rng.uniform(0, math.tau)
    step = math.tau / max(1, count)
    return [start + i * step + rng.uniform(-jitter, jitter) * step for i in range(count)]


def shape_blob(shapes, rng, centre, radius, scale, slot, rough):
    """A lumpy low-poly blob (a canopy, a bush clump)."""
    verts = shapes.blob(centre, radius, 2, slot, scale)
    middle = Vector(centre)
    for vert in verts:
        vert.co = middle + (vert.co - middle) * (1.0 + rng.uniform(-rough, rough))
    return verts


def leaf_cards(shapes, rng, blobs, count, slot):
    """Square leaf cards scattered over the surface of the given (centre, radius) blobs."""
    if not blobs:
        return
    for i in range(count):
        centre, radius = blobs[i % len(blobs)]
        direction = Vector((rng.gauss(0, 1), rng.gauss(0, 1), rng.gauss(0, 1) * 0.7))
        if direction.length < 1e-6:
            direction = Vector((0.0, 0.0, 1.0))
        direction.normalize()
        where = Vector(centre) + direction * radius * rng.uniform(0.85, 1.1)
        facing = direction + Vector([rng.uniform(-0.6, 0.6) for _ in range(3)])
        if facing.length < 1e-6:
            facing = direction
        shapes.quad(where, facing, radius * rng.uniform(0.35, 0.55), slot, rng.uniform(0, math.tau))


def triangles(obj):
    return sum(len(p.vertices) - 2 for p in obj.data.polygons)


def bounds(obj):
    xs = [v.co.x for v in obj.data.vertices]
    ys = [v.co.y for v in obj.data.vertices]
    zs = [v.co.z for v in obj.data.vertices]
    return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))


def set_pivot(obj, mode):
    """Move the geometry so the origin is where the art bible puts pivots."""
    low, high = bounds(obj)
    centre = [(a + b) / 2 for a, b in zip(low, high)]
    if mode == "bottom-centre":
        offset = (-centre[0], -centre[1], -low[2])
    elif mode == "centre":
        offset = (-centre[0], -centre[1], -centre[2])
    else:
        return
    obj.data.transform(Matrix.Translation(Vector(offset)))
    obj.data.update()


def _apply_decimate(obj, ratio):
    modifier = obj.modifiers.new("Budget", "DECIMATE")
    modifier.decimate_type = "COLLAPSE"
    modifier.ratio = max(0.01, min(1.0, ratio))
    graph = bpy.context.evaluated_depsgraph_get()
    reduced = bpy.data.meshes.new_from_object(obj.evaluated_get(graph))
    obj.modifiers.clear()
    old = obj.data
    name = old.name
    obj.data = reduced
    bpy.data.meshes.remove(old)
    reduced.name = name


def fit_budget(obj, budget):
    """Reduce the mesh to the art bible's triangle budget; the count it started at."""
    start = triangles(obj)
    if not budget or start <= budget:
        return start
    for _attempt in range(6):
        count = triangles(obj)
        if count <= budget:
            break
        _apply_decimate(obj, budget / count * 0.97)
    if triangles(obj) > budget:
        raise RuntimeError(
            f"{obj.name} has {triangles(obj)} triangles even reduced, over the art "
            f"bible's budget of {budget}. Use less detail."
        )
    if triangles(obj) < start * 0.25:
        raise RuntimeError(
            f"{obj.name} needs {start} triangles, far over the art bible's budget of "
            f"{budget}. Use less detail."
        )
    return start


def lod_copy(obj, level, pivot):
    """A copy of the object with about half the triangles per level, pivot kept."""
    mesh = obj.data.copy()
    copy = bpy.data.objects.new(f"{obj.name}_LOD{level}", mesh)
    mesh.name = copy.name
    bpy.context.scene.collection.objects.link(copy)
    _apply_decimate(copy, 0.5**level)
    set_pivot(copy, pivot)
    return copy


def name_parts():
    """Godot sanitises names, so each part carries its Blender name (glTF extras)."""
    for obj in bpy.data.objects:
        obj["joinery_name"] = obj.name
    for item in bpy.data.materials:
        item["joinery_name"] = item.name
        item["joinery_maps"] = json.dumps({}, sort_keys=True)


def export(obj, path):
    for other in bpy.data.objects:
        other.select_set(other is obj)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.export_scene.gltf(
        filepath=path,
        export_format="GLB",
        use_selection=True,
        export_yup=True,
        export_apply=True,
        export_extras=True,
    )


def reset():
    """An empty scene in metres, for the next model."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.preferences.filepaths.save_version = 0
    bpy.context.scene.unit_settings.system = "METRIC"
    bpy.context.scene.unit_settings.scale_length = 1.0


def make_variant(build, variant, context):
    """Build one model, fit it to the art bible, save its .blend and export it."""
    import os

    reset()
    rng = random.Random(variant["seed"])
    looks = context["looks"]
    materials = {
        role: material(f"{variant['prefix']}_{role.title().replace('_', '')}", look)
        for role, look in looks.items()
    }
    obj = build(variant["params"], rng, materials, variant["object"])
    if obj.scale[:] != (1.0, 1.0, 1.0):
        raise RuntimeError("A kit must build its model at scale 1.")
    start = fit_budget(obj, context["budget"])
    set_pivot(obj, context["pivot"])
    low, high = bounds(obj)
    for path in (variant["blend"], variant["glb"], *variant["lods"]):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(variant["blend"]))
    name_parts()
    export(obj, os.path.abspath(variant["glb"]))
    lods = []
    for level, path in enumerate(variant["lods"], start=1):
        copy = lod_copy(obj, level, context["pivot"])
        copy["joinery_name"] = copy.name
        export(copy, os.path.abspath(path))
        lods.append(triangles(copy))
        bpy.data.objects.remove(copy)
    print(
        "JOINERY_KIT:"
        + json.dumps(
            {
                "name": variant["object"],
                "glb": variant["glb"],
                "triangles": triangles(obj),
                "built_triangles": start,
                "lod_triangles": lods,
                "size": [round(h - l, 4) for l, h in zip(low, high)],
                "materials": sorted(m.name for m in obj.data.materials),
            }
        )
    )
