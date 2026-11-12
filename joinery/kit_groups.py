"""The user's own Geometry Nodes group as a kit: `joinery kits from-blend`.

The user made a node group in their own .blend. Joinery copies the .blend into
the project (art/sources/groups/<id>.blend, a `human-import` in joinery.lock:
the user's own work), reads the group in Blender in the offline worker, and
writes a project kit (kits/blender/<id>.py) whose typed parameters are the
group's inputs:

* a Float input is a float parameter with the input's range (unbounded ends
  get a sensible span around the default), an Int input an int parameter, a
  Boolean input a bool; an Int input called "Seed" takes the kit's seed, so
  every model of a set differs; other inputs (vectors, colours, objects,
  menus, …) stay at the group's own values and are listed as fixed;
* each material the group sets becomes a material role, its colour taken from
  the material's base colour, so the art bible places it on the palette.

Only plain Geometry Nodes are accepted: a node group of another kind, or one
using a node Blender itself does not define (an add-on's node, such as
Sverchok's, GPL and absent from the worker), a group linked from another file
or an image that is not packed in, is refused with what to change.

The kit's build appends the group from the copied .blend, runs it on an empty
mesh (or a cube or plane, `--base`) through a Realize Instances node, applies
the result and hands it to the shared kit code (budget, pivot, materials,
GLB). The .blend is a recipe source, so `joinery rebuild` replays the models.
"""

from __future__ import annotations

import hashlib
import json
import math
import pprint
import re
from pathlib import Path
from typing import Any

from joinery.authoring import run_offline

#: Where a group's .blend is kept in the game project.
GROUP_DIR = "art/sources/groups"
#: The kit's own generator file.
KIT_DIR = "kits/blender"
BASES = ("none", "plane", "cube")
IMPORTED_BY = "user (joinery kits from-blend)"
_ID = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
#: Socket types that become parameters, and their kit kind.
KINDS = {"NodeSocketFloat": "float", "NodeSocketInt": "int", "NodeSocketBool": "bool"}
#: An input with no real bound gets this many times its default (or 1) either side.
SPAN = 10.0
LIMIT = 1e4

#: Runs in Blender with the user's .blend open: prints what the group is.
READ_GROUP = r"""
import bpy, json, math
GROUP = json.loads(GROUP_JSON)

def srgb(channel):
    channel = max(0.0, min(1.0, channel))
    return channel * 12.92 if channel <= 0.0031308 else 1.055 * channel ** (1 / 2.4) - 0.055

def look(material):
    colour, roughness, metallic = list(material.diffuse_color)[:3], material.roughness, material.metallic
    tree = material.node_tree
    shader = next((n for n in tree.nodes if n.type == 'BSDF_PRINCIPLED'), None) if tree else None
    if shader is not None:
        colour = list(shader.inputs['Base Color'].default_value)[:3]
        roughness = shader.inputs['Roughness'].default_value
        metallic = shader.inputs['Metallic'].default_value
    return {
        'colour': '#' + ''.join(f'{round(srgb(c) * 255):02x}' for c in colour),
        'roughness': round(float(roughness), 3),
        'metallic': round(float(metallic), 3),
    }

def plain(value):
    if isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    try:
        return [plain(v) for v in value]
    except TypeError:
        return getattr(value, 'name', None)

found = {
    'groups': sorted(g.name for g in bpy.data.node_groups if g.bl_idname == 'GeometryNodeTree'),
    'problems': [],
}
group = bpy.data.node_groups.get(GROUP)
if group is None:
    found['error'] = 'missing'
elif group.bl_idname != 'GeometryNodeTree':
    found['error'] = 'kind:' + group.bl_idname
else:
    seen, materials, problems = set(), {}, found['problems']
    def material_from(socket):
        value = getattr(socket, 'default_value', None)
        if isinstance(value, bpy.types.Material) and value.name not in materials:
            materials[value.name] = look(value)
    def walk(tree):
        if tree.name in seen:
            return
        seen.add(tree.name)
        if tree.library is not None:
            problems.append(f'{tree.name} is linked from {tree.library.filepath}; make it local (Make Local) first.')
        if tree.bl_idname != 'GeometryNodeTree':
            problems.append(f'{tree.name} is a {tree.bl_idname}, not a Geometry Nodes group.')
            return
        for link in tree.links:
            if not link.is_valid and not link.is_muted:
                problems.append(
                    f'{tree.name}: the link from {link.from_node.name} to {link.to_node.name} '
                    f'({link.to_socket.name}) is red in Blender, so the group would not '
                    'make what it shows; fix or remove it.'
                )
        for node in tree.nodes:
            idname = node.bl_idname
            if idname == 'NodeUndefined' or not hasattr(bpy.types, idname):
                problems.append(f'{tree.name}: node {node.name} ({idname}) is not a node Blender itself defines.')
                continue
            if getattr(node, 'node_tree', None) is not None:
                walk(node.node_tree)
            for socket in node.inputs:
                material_from(socket)
                image = getattr(socket, 'default_value', None)
                if isinstance(image, bpy.types.Image) and image.source == 'FILE' and image.packed_file is None:
                    problems.append(f'{tree.name}: image {image.name} is not packed into the .blend (File > External Data > Pack Resources).')
            for attribute in ('material', 'image'):
                value = getattr(node, attribute, None)
                if isinstance(value, bpy.types.Material) and value.name not in materials:
                    materials[value.name] = look(value)
                if isinstance(value, bpy.types.Image) and value.source == 'FILE' and value.packed_file is None:
                    problems.append(f'{tree.name}: image {value.name} is not packed into the .blend.')
    walk(group)
    inputs, outputs = [], []
    for item in group.interface.items_tree:
        if item.item_type != 'SOCKET':
            continue
        row = {
            'identifier': item.identifier, 'name': item.name, 'socket_type': item.socket_type,
            'description': item.description, 'default': plain(getattr(item, 'default_value', None)),
            'subtype': getattr(item, 'subtype', ''), 'hide_value': bool(getattr(item, 'hide_value', False)),
        }
        for key in ('min_value', 'max_value'):
            if hasattr(item, key):
                row[key] = plain(getattr(item, key))
        if row['socket_type'] == 'NodeSocketMaterial' and item.default_value is not None:
            materials.setdefault(item.default_value.name, look(item.default_value))
        (inputs if item.in_out == 'INPUT' else outputs).append(row)
    found.update(name=group.name, description=group.description, inputs=inputs, outputs=outputs, materials=materials)
print('JOINERY_GROUP:' + json.dumps(found, sort_keys=True))
"""


def read_code(group: str) -> str:
    return f"GROUP_JSON = {json.dumps(json.dumps(group))}\n" + READ_GROUP


async def read_group(project: Path, blend: str, group: str) -> dict[str, Any]:
    """What Blender says about the group in the project's copy of the .blend."""
    got = await run_offline(
        [
            "blender",
            "--background",
            "--factory-startup",
            blend,
            "--python-expr",
            read_code(group),
        ],
        project,
        300,
        readonly=True,
    )
    for line in got.stdout.splitlines():
        if line.startswith("JOINERY_GROUP:"):
            found: dict[str, Any] = json.loads(line.removeprefix("JOINERY_GROUP:"))
            return found
    raise RuntimeError("Blender could not read the .blend: " + got.output[-600:])


def check_group(found: dict[str, Any], group: str) -> None:
    """Refuse a group that is not plain, self-contained Geometry Nodes."""
    error = found.get("error", "")
    if error == "missing":
        groups = found.get("groups") or []
        raise ValueError(
            f"The .blend has no node group {group!r}. Its Geometry Nodes groups: "
            + (", ".join(groups) or "none")
            + "."
        )
    if error.startswith("kind:"):
        raise ValueError(
            f"{group} is a {error.removeprefix('kind:')}, not a Geometry Nodes group. "
            "Only plain Geometry Nodes groups become kits."
        )
    if found.get("problems"):
        raise ValueError(
            "This group can't become a kit yet: " + " ".join(found["problems"][:5])
        )
    outputs = [
        o
        for o in found.get("outputs", [])
        if o.get("socket_type") == "NodeSocketGeometry"
    ]
    if not outputs:
        raise ValueError(f"{group} has no Geometry output.")


def snake(text: str) -> str:
    words = re.sub(r"[^A-Za-z0-9]+", " ", text).strip().lower().split()
    made = "_".join(words) or "value"
    return made if made[0].isalpha() else "p_" + made


def kit_id(text: str) -> str:
    made = snake(text)[:40].rstrip("_")
    if not _ID.match(made):
        raise ValueError(
            "The kit id is lower-case letters, digits and '_', starting with a letter."
        )
    return made


def _bounds(row: dict[str, Any], default: float, whole: bool) -> tuple[float, float]:
    span = SPAN * max(1.0, abs(default))
    low = row.get("min_value")
    high = row.get("max_value")
    low = (
        low
        if isinstance(low, (int, float)) and abs(low) <= LIMIT
        else min(0.0, default) - span
    )
    high = (
        high
        if isinstance(high, (int, float)) and abs(high) <= LIMIT
        else max(0.0, default) + span
    )
    if whole:
        return float(math.floor(low)), float(math.ceil(high))
    return round(float(low), 6), round(float(high), 6)


def parameters(
    found: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, str], str, list[str]]:
    """(SPEC params, param -> socket identifier, the seed socket, fixed inputs)."""
    params: dict[str, dict[str, Any]] = {}
    sockets: dict[str, str] = {}
    seed = ""
    fixed: list[str] = []
    for row in found.get("inputs", []):
        kind = KINDS.get(str(row.get("socket_type")))
        name = str(row.get("name", ""))
        if row.get("socket_type") == "NodeSocketGeometry":
            continue
        if kind == "int" and name.strip().lower() == "seed" and not seed:
            seed = str(row["identifier"])
            continue
        if kind is None or row.get("hide_value"):
            fixed.append(name)
            continue
        key = snake(name)
        while key in params or key == "seed":
            key += "_2"
        default = row.get("default")
        spec: dict[str, Any] = {
            "kind": kind,
            "label": name[:40],
            "help": str(row.get("description") or f"The group's {name} input.")[:200],
        }
        if kind == "bool":
            spec["default"] = bool(default)
        else:
            number = float(default) if isinstance(default, (int, float)) else 0.0
            low, high = _bounds(row, number, kind == "int")
            number = min(high, max(low, number))
            if kind == "int":
                spec.update(default=int(number), min=int(low), max=int(high), vary="")
            else:
                spec.update(
                    default=round(number, 6),
                    min=low,
                    max=high,
                    step=0.01 if high - low <= 10 else 0.1,
                    vary="scale" if low >= 0 and number > 0 else "",
                )
                if row.get("subtype") == "DISTANCE":
                    spec["unit"] = "m"
        params[key] = spec
        sockets[key] = str(row["identifier"])
    return params, sockets, seed, fixed


def materials(
    found: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """(SPEC materials, role -> the group's material name)."""
    looks: dict[str, dict[str, Any]] = {}
    roles: dict[str, str] = {}
    for name, look in sorted((found.get("materials") or {}).items()):
        role = snake(name)
        while role in looks:
            role += "_2"
        looks[role] = {
            "colour": str(look["colour"]),
            "roughness": min(1.0, max(0.0, float(look["roughness"]))),
            "metallic": min(1.0, max(0.0, float(look["metallic"]))),
        }
        roles[role] = name
    if not looks:
        looks["surface"] = {"colour": "#8a8580", "roughness": 0.8, "metallic": 0.0}
    return looks, roles


BUILD = r"""

def build(p, rng, materials, name):
    import os

    path = os.path.abspath(SOURCE)
    with bpy.data.libraries.load(path, link=False) as (found, wanted):
        if GROUP not in found.node_groups:
            raise RuntimeError(f"{SOURCE} has no node group {GROUP!r} any more.")
        wanted.node_groups = [GROUP]
    group = wanted.node_groups[0]
    if group.bl_idname != "GeometryNodeTree":
        raise RuntimeError(f"{GROUP} is not a Geometry Nodes group.")
    shapes = Shapes()
    if BASE == "cube":
        bmesh.ops.create_cube(shapes.bm, size=1.0)
    elif BASE == "plane":
        bmesh.ops.create_grid(shapes.bm, x_segments=1, y_segments=1, size=0.5)
    mesh = bpy.data.meshes.new(name)
    shapes.bm.to_mesh(mesh)
    shapes.bm.free()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    # The user's group inside a wrapper that realizes its instances, so the
    # applied mesh holds everything the group shows.
    wrapper = bpy.data.node_groups.new("JoineryKit", "GeometryNodeTree")
    wrapper.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    wrapper.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    given = wrapper.nodes.new("NodeGroupInput")
    result = wrapper.nodes.new("NodeGroupOutput")
    user = wrapper.nodes.new("GeometryNodeGroup")
    user.node_tree = group
    realize = wrapper.nodes.new("GeometryNodeRealizeInstances")
    inputs = {socket.identifier: socket for socket in user.inputs}
    geometry_in = next((s for s in user.inputs if s.type == "GEOMETRY"), None)
    if geometry_in is not None:
        wrapper.links.new(given.outputs[0], geometry_in)
    for key, identifier in SOCKETS.items():
        inputs[identifier].default_value = p[key]
    if SEED:
        inputs[SEED].default_value = rng.randrange(0, 2**31 - 1)
    geometry_out = next(s for s in user.outputs if s.type == "GEOMETRY")
    wrapper.links.new(geometry_out, realize.inputs[0])
    wrapper.links.new(realize.outputs[0], result.inputs[0])
    modifier = obj.modifiers.new("Group", "NODES")
    modifier.node_group = wrapper
    graph = bpy.context.evaluated_depsgraph_get()
    made = bpy.data.meshes.new_from_object(obj.evaluated_get(graph))
    obj.modifiers.clear()
    obj.data = made
    bpy.data.meshes.remove(mesh)
    made.name = name
    bpy.data.node_groups.remove(wrapper)
    if not made.polygons:
        raise RuntimeError(f"{GROUP} made no mesh faces with these settings.")
    # Each of the group's materials becomes its role's material, the art
    # bible's version of it; faces with no material take the first role.
    by_name = {group_name: role for role, group_name in ROLES.items()}
    first = next(iter(materials.values()))
    if not made.materials:
        made.materials.append(first)
    for index, slot in enumerate(made.materials):
        role = by_name.get(slot.name) if slot is not None else None
        made.materials[index] = materials[role] if role in materials else first
    for tree in list(bpy.data.node_groups):
        if tree.users == 0:
            bpy.data.node_groups.remove(tree)
    for item in list(bpy.data.materials):
        if item.users == 0:
            bpy.data.materials.remove(item)
    return obj
"""


def generator(
    found: dict[str, Any],
    *,
    kit: str,
    label: str,
    source: str,
    blend_name: str,
    folder: str,
    base: str,
) -> tuple[str, dict[str, Any]]:
    """The kit's generator file, and a summary of what it takes and keeps fixed."""
    params, sockets, seed, fixed = parameters(found)
    looks, roles = materials(found)
    group = str(found["name"])
    summary = str(found.get("description") or "").strip() or (
        f"Your Geometry Nodes group {group}, from {blend_name}."
    )
    spec = {
        "id": kit,
        "label": label,
        "summary": summary[:200],
        "folder": folder,
        "params": params,
        "materials": looks,
        "sources": [source],
        "presets": [
            {
                "name": "As made",
                "params": {k: v["default"] for k, v in params.items()},
                "colours": {},
            }
        ],
    }
    lines = [
        f"# This project's own kit: the Geometry Nodes group {group!r} from {blend_name},",
        "# made by `joinery kits from-blend`. The group is the user's own work and stays",
        f"# in {source}; plain Geometry Nodes only, no add-on nodes.",
        "# A recipe joins this file after kits/blender/common.py (Shapes, bmesh, bpy).",
        "# ruff: noqa: F821",
        "",
        "SPEC = " + pprint.pformat(spec, sort_dicts=False, width=100),
        "",
        f"SOURCE = {source!r}",
        f"GROUP = {group!r}",
        f"BASE = {base!r}",
        f"SOCKETS = {sockets!r}",
        f"SEED = {seed!r}",
        f"ROLES = {roles!r}",
        f"FIXED = {fixed!r}",
        BUILD,
    ]
    return "\n".join(lines), {
        "kit": kit,
        "group": group,
        "params": list(params),
        "seed_input": bool(seed),
        "fixed": fixed,
        "materials": roles or {"surface": ""},
    }


async def import_group(
    stage: Path,
    blend: Path,
    group: str,
    *,
    kit: str = "",
    label: str = "",
    folder: str = "art/props",
    base: str = "none",
    licence: str = "the user's own work",
) -> dict[str, Any]:
    """Copy the user's .blend into `stage` and write the group's kit beside it.

    `stage` is a transaction's staged project: the user reviews the new kit,
    its .blend and the lock entry before they reach the project.
    """
    from joinery import kits
    from joinery.kit_library import project_kits
    from joinery.lockfile import HumanImport, record_imports
    from joinery.transactions import copy_file

    if blend.suffix.lower() != ".blend" or not blend.is_file():
        raise ValueError(f"{blend} is not a .blend file.")
    if not group.strip():
        raise ValueError("Name the node group with --group.")
    if base not in BASES:
        raise ValueError(f"--base is one of: {', '.join(BASES)}.")
    where = kits.check_folder(folder)
    made_id = kit_id(kit or group)
    taken = kits.every(stage)
    source = f"{GROUP_DIR}/{made_id}.blend"
    generator_path = stage / KIT_DIR / f"{made_id}.py"
    if made_id in taken or generator_path.exists() or (stage / source).exists():
        raise ValueError(
            f"The project already has a kit {made_id!r}; choose another --id."
        )
    target = stage / source
    target.parent.mkdir(parents=True, exist_ok=True)
    (target.parent / ".gdignore").write_text("")
    copy_file(blend, target)
    found = await read_group(stage, source, group)
    check_group(found, group)
    text, summary = generator(
        found,
        kit=made_id,
        label=(label or str(found["name"]))[:40],
        source=source,
        blend_name=blend.name,
        folder=where,
        base=base,
    )
    generator_path.parent.mkdir(parents=True, exist_ok=True)
    generator_path.write_text(text, encoding="utf-8")
    record_imports(
        stage,
        [
            HumanImport(
                path=source,
                sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
                origin="human-import",
                imported_by=IMPORTED_BY,
                licence=licence,
            )
        ],
    )
    if made_id not in project_kits(stage):
        raise RuntimeError("The kit Joinery wrote does not load; nothing was kept.")
    return {
        **summary,
        "source": source,
        "generator": generator_path.relative_to(stage).as_posix(),
    }
