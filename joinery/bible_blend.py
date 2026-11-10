"""Measure `.blend` files for the art bible's checked rules, inside Blender.

The art bible checks models where they are made, not only where they are
exported: triangle and bone budgets, applied scale, pivots, texel density,
material factors and names. Blender is the only reader of its own format that
can be trusted, so the measuring runs in the offline creative worker, read-only,
once for every changed `.blend` file:

    sh -c 'for f; do echo "JOINERY_FILE:$f"; blender -b --disable-autoexec "$f" ... "$0"; done' <script> <files...>

Each file prints one `JOINERY_MEASURE:<json>` line. A file Blender cannot open
prints none, and the caller reports it as "could not be measured", never as a
pass. Coordinates are reported in Godot's frame (+Y up, metres): Blender's
(x, y, z) becomes (x, z, -y).
"""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict

#: Runs inside Blender. Read-only: it never saves, and the worker mounts the
#: project read-only as well.
MEASURE_BLEND = r"""
import bpy, json, math

def principled(material):
    tree = getattr(material, 'node_tree', None)
    if tree is None:
        return None
    for node in tree.nodes:
        if node.type == 'BSDF_PRINCIPLED':
            return node
    return None

def base_image(material):
    node = principled(material)
    if node is None or 'Base Color' not in node.inputs:
        return None
    seen = set()
    stack = [link.from_node for link in node.inputs['Base Color'].links]
    while stack:
        current = stack.pop()
        if current.name in seen:
            continue
        seen.add(current.name)
        if current.type == 'TEX_IMAGE' and current.image is not None:
            width, height = current.image.size[0], current.image.size[1]
            if width and height:
                return (width, height)
        for socket in current.inputs:
            stack.extend(link.from_node for link in socket.links)
    return None

def to_y_up(v):
    return [v[0], v[2], -v[1]]

def measure_mesh(obj, depsgraph):
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        mesh.calc_loop_triangles()
        triangles = len(mesh.loop_triangles)
        uv = mesh.uv_layers.active
        sizes = []
        for slot in obj.material_slots:
            sizes.append(base_image(slot.material) if slot.material else None)
        matrix = obj.matrix_world
        uv_px = 0.0
        world = 0.0
        if uv is not None:
            for tri in mesh.loop_triangles:
                size = sizes[tri.material_index] if tri.material_index < len(sizes) else None
                if size is None:
                    continue
                a, b, c = (matrix @ mesh.vertices[i].co for i in tri.vertices)
                world += (b - a).cross(c - a).length / 2
                p, q, r = (uv.data[i].uv for i in tri.loops)
                area = abs((q[0] - p[0]) * (r[1] - p[1]) - (r[0] - p[0]) * (q[1] - p[1])) / 2
                uv_px += area * size[0] * size[1]
        density = math.sqrt(uv_px / world) if world > 0 and uv_px > 0 else None
        return triangles, density
    finally:
        evaluated.to_mesh_clear()

def measure():
    depsgraph = bpy.context.evaluated_depsgraph_get()
    objects = []
    armatures = []
    for obj in bpy.data.objects:
        if obj.type not in {'MESH', 'ARMATURE'}:
            continue
        corners = [[c[i] * obj.scale[i] for i in range(3)] for c in obj.bound_box]
        low = [min(c[i] for c in corners) for i in range(3)]
        high = [max(c[i] for c in corners) for i in range(3)]
        # Z-up to Y-up flips the sign of Blender's y, which swaps min and max.
        bbox_min = [low[0], low[2], -high[1]]
        bbox_max = [high[0], high[2], -low[1]]
        row = {
            'name': obj.name,
            'type': obj.type,
            'parent': obj.parent.name if obj.parent else None,
            'scale': list(obj.scale),
            'bbox_min': bbox_min,
            'bbox_max': bbox_max,
            'triangles': 0,
            'mesh': None,
            'texel_density': None,
        }
        if obj.type == 'MESH':
            row['mesh'] = obj.data.name
            row['triangles'], row['texel_density'] = measure_mesh(obj, depsgraph)
        else:
            armatures.append({
                'name': obj.name,
                'deform_bones': sum(1 for b in obj.data.bones if b.use_deform),
            })
        objects.append(row)
    materials = []
    for material in bpy.data.materials:
        if material.users == 0:
            continue
        node = principled(material)
        row = {'name': material.name, 'roughness': None, 'metallic': None,
               'base_color': None, 'linked': []}
        if node is not None:
            for key, socket in (('roughness', 'Roughness'), ('metallic', 'Metallic'),
                                ('base_color', 'Base Color')):
                if socket not in node.inputs:
                    continue
                value = node.inputs[socket]
                if value.is_linked:
                    row['linked'].append(key)
                elif key == 'base_color':
                    row[key] = list(value.default_value)[:3]
                else:
                    row[key] = float(value.default_value)
        materials.append(row)
    print('JOINERY_MEASURE:' + json.dumps({
        'path': bpy.data.filepath,
        'objects': objects,
        'armatures': armatures,
        'materials': materials,
    }))

# A file Blender could not open leaves the factory scene loaded, with no file
# path. Measuring that would report the default cube, so print nothing.
if bpy.data.filepath:
    measure()
"""

#: One worker run for every changed `.blend`: `$0` is the script, `$@` the files.
#: `--disable-autoexec` keeps scripts and drivers stored in a `.blend` (an
#: imported one included) from running while it is measured.
MEASURE_LOOP = (
    'for f; do echo "JOINERY_FILE:$f"; '
    'blender -b --disable-autoexec "$f" --python-exit-code 1 --python-expr "$0"; done'
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BlendObject(_Strict):
    name: str
    type: str
    parent: str | None = None
    scale: tuple[float, float, float]
    #: Local bounding box after object scale, in Godot's +Y-up frame.
    bbox_min: tuple[float, float, float]
    bbox_max: tuple[float, float, float]
    triangles: int = 0
    mesh: str | None = None
    #: Texture pixels per metre, from faces whose material has a base colour image.
    texel_density: float | None = None


class BlendArmature(_Strict):
    name: str
    deform_bones: int


class BlendMaterial(_Strict):
    name: str
    roughness: float | None = None
    metallic: float | None = None
    #: Linear RGB, as Blender stores the Principled BSDF default value.
    base_color: tuple[float, float, float] | None = None
    #: Inputs driven by a texture or node: checked through the texture instead.
    linked: list[str] = []


class BlendMeasure(_Strict):
    path: str = ""
    objects: list[BlendObject] = []
    armatures: list[BlendArmature] = []
    materials: list[BlendMaterial] = []


def measure_argv(paths: list[str]) -> list[str]:
    """The worker command that measures every file in one run."""
    return ["sh", "-c", MEASURE_LOOP, MEASURE_BLEND, *paths]


def parse_measures(output: str) -> dict[str, BlendMeasure]:
    """Measurements per file from the worker's output; unmeasured files are absent."""
    results: dict[str, BlendMeasure] = {}
    current = ""
    for line in output.splitlines():
        if line.startswith("JOINERY_FILE:"):
            current = line.removeprefix("JOINERY_FILE:").strip()
            continue
        if current and line.startswith("JOINERY_MEASURE:"):
            try:
                raw = json.loads(line.removeprefix("JOINERY_MEASURE:"))
                results[current] = BlendMeasure.model_validate(raw)
            except ValueError:
                continue
    return results
