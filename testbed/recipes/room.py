"""Build the testbed room in Blender. Replayed by `joinery rebuild`.

Operation log for art/kit/room.blend. A modular room assembled from a small
kit: floor, ceiling, four walls, and a doorway cut with a boolean so the door
from art/props/door.blend fits it. Everything is Blender mesh operations and
shader nodes; nothing is generated and nothing is fetched.

Art rules this follows:
  metres; 4 x 5 m floor, 2.6 m ceiling
  doorway 0.9 x 2.1 m, matching the door leaf exactly
  pivots at bottom-centre, so a room drops onto the origin plane
  named Part_Material_Variant

Run: blender --background --factory-startup --python recipes/room.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import bpy

OUT = Path("art/kit/room.blend")
ROOM_X, ROOM_Y, ROOM_H = 4.0, 5.0, 2.6
WALL_T = 0.12
DOOR_W, DOOR_H = 0.9, 2.1


def clear_scene() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    # No .blend1 backups: they would land under art/ unlocked by the gate.
    bpy.context.preferences.filepaths.save_version = 0


def flat_material(name: str, rgb: tuple[float, float, float], rough: float):
    """A plain surface material. No pure black or white albedo, per the rules."""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
    bsdf.inputs["Roughness"].default_value = rough
    bsdf.inputs["Metallic"].default_value = 0.0
    return mat


def box(name: str, size: tuple[float, float, float], at: tuple[float, float, float]):
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=at)
    obj = bpy.context.active_object
    obj.name = name
    obj.scale = size
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    return obj


def main() -> None:
    clear_scene()
    plaster = flat_material("Wall_Plaster_Warm", (0.56, 0.52, 0.46), 0.85)
    boards = flat_material("Floor_Wood_Mid", (0.29, 0.19, 0.12), 0.70)

    floor = box("Floor_Wood_Mid", (ROOM_X, ROOM_Y, WALL_T), (0.0, 0.0, -WALL_T / 2))
    floor.data.materials.append(boards)
    ceiling = box(
        "Ceiling_Plaster_Warm",
        (ROOM_X, ROOM_Y, WALL_T),
        (0.0, 0.0, ROOM_H + WALL_T / 2),
    )
    ceiling.data.materials.append(plaster)

    half_x, half_y = ROOM_X / 2, ROOM_Y / 2
    walls = [
        ("Wall_Plaster_Warm_N", (ROOM_X, WALL_T, ROOM_H), (0.0, half_y, ROOM_H / 2)),
        ("Wall_Plaster_Warm_S", (ROOM_X, WALL_T, ROOM_H), (0.0, -half_y, ROOM_H / 2)),
        ("Wall_Plaster_Warm_E", (WALL_T, ROOM_Y, ROOM_H), (half_x, 0.0, ROOM_H / 2)),
        ("Wall_Plaster_Warm_W", (WALL_T, ROOM_Y, ROOM_H), (-half_x, 0.0, ROOM_H / 2)),
    ]
    made = []
    for name, size, at in walls:
        wall = box(name, size, at)
        wall.data.materials.append(plaster)
        made.append(wall)

    # Cut the doorway out of the south wall with a boolean, so the opening is
    # real geometry rather than a texture, and the door actually fits it.
    cutter = box(
        "DoorwayCutter",
        (DOOR_W, WALL_T * 3, DOOR_H),
        (0.0, -half_y, DOOR_H / 2),
    )
    south = made[1]
    boolean = south.modifiers.new("Doorway", "BOOLEAN")
    boolean.operation = "DIFFERENCE"
    boolean.object = cutter
    bpy.context.view_layer.objects.active = south
    bpy.ops.object.modifier_apply(modifier="Doorway")
    bpy.data.objects.remove(cutter, do_unlink=True)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(OUT.resolve()))
    tris = sum(
        len(p.vertices) - 2
        for o in bpy.data.objects
        if o.type == "MESH"
        for p in o.data.polygons
    )
    print(f"JOINERY_BUILT {OUT} objects={len(bpy.data.objects)} tris={tris}")


if __name__ == "__main__":
    main()
    sys.exit(0)
