"""Build the testbed door in Blender. Replayed by `joinery rebuild`.

Operation log for art/props/door.blend. Everything here is Blender doing the
work: mesh primitives, transforms, a bevel, and a material built from shader
nodes. Nothing is generated and nothing is fetched.

Art rules this follows (metres, +Y up in Godot terms):
  door leaf 2.1 m tall, 0.9 m wide, 0.05 m thick
  pivot at the hinge, so a rotation about Z swings the door
  named Part_Material_Variant

Run: blender --background --factory-startup --python recipes/door.py
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import bpy

OUT = Path("art/props/door.blend")
LEAF_W, LEAF_H, LEAF_T = 0.9, 2.1, 0.05
HANDLE_R = 0.035


def clear_scene() -> None:
    """Start from an empty file so a replay cannot inherit stray state."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    # No .blend1 backups: they would land under art/ as unlocked assets and
    # trip the provenance gate, and a rebuild has no use for them.
    bpy.context.preferences.filepaths.save_version = 0


def wood_material() -> bpy.types.Material:
    """A stylised wood material from shader nodes, authored not sampled."""
    mat = bpy.data.materials.new("Door_Wood_Dark")
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    nodes.clear()

    out = nodes.new("ShaderNodeOutputMaterial")
    out.location = (600, 0)
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (300, 0)
    # Grain from a stretched noise, so the panel reads as timber not plastic.
    noise = nodes.new("ShaderNodeTexNoise")
    noise.location = (-300, 0)
    noise.inputs["Scale"].default_value = 18.0
    noise.inputs["Detail"].default_value = 6.0
    mapping = nodes.new("ShaderNodeMapping")
    mapping.location = (-500, 0)
    mapping.inputs["Scale"].default_value = (1.0, 14.0, 1.0)
    coords = nodes.new("ShaderNodeTexCoord")
    coords.location = (-700, 0)
    ramp = nodes.new("ShaderNodeValToRGB")
    ramp.location = (-80, 0)
    # Two browns, well inside a stylised palette: no pure black or white albedo.
    ramp.color_ramp.elements[0].color = (0.13, 0.08, 0.05, 1.0)
    ramp.color_ramp.elements[1].color = (0.33, 0.20, 0.11, 1.0)

    links.new(coords.outputs["Object"], mapping.inputs["Vector"])
    links.new(mapping.outputs["Vector"], noise.inputs["Vector"])
    links.new(noise.outputs["Fac"], ramp.inputs["Fac"])
    links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
    links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    bsdf.inputs["Roughness"].default_value = 0.72  # timber, not varnish
    bsdf.inputs["Metallic"].default_value = 0.0
    return mat


def metal_material() -> bpy.types.Material:
    mat = bpy.data.materials.new("Handle_Metal_Brass")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (0.52, 0.38, 0.14, 1.0)
    bsdf.inputs["Metallic"].default_value = 1.0  # only on real metal
    bsdf.inputs["Roughness"].default_value = 0.35
    return mat


def build_leaf(wood: bpy.types.Material) -> bpy.types.Object:
    """The door leaf, with its origin at the hinge edge."""
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0.0, 0.0, 0.0))
    leaf = bpy.context.active_object
    leaf.name = "Door_Wood_Dark"
    leaf.scale = (LEAF_T, LEAF_W, LEAF_H)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

    # Move the mesh so the hinge edge sits at the object origin: rotating the
    # object about Z then swings the door instead of spinning it in place.
    mesh = leaf.data
    for vert in mesh.vertices:
        vert.co.y += LEAF_W / 2.0
        vert.co.z += LEAF_H / 2.0

    bevel = leaf.modifiers.new("Bevel", "BEVEL")
    bevel.width = 0.006
    bevel.segments = 2
    leaf.data.materials.append(wood)
    return leaf


def build_handle(leaf: bpy.types.Object, brass: bpy.types.Material) -> bpy.types.Object:
    """A lever handle on the swinging edge, at the usual 1.05 m height."""
    bpy.ops.mesh.primitive_cylinder_add(
        radius=HANDLE_R,
        depth=0.11,
        location=(0.0, LEAF_W - 0.07, 1.05),
        rotation=(0.0, math.pi / 2.0, 0.0),
    )
    handle = bpy.context.active_object
    handle.name = "Handle_Metal_Brass"
    handle.data.materials.append(brass)
    handle.parent = leaf
    return handle


def main() -> None:
    clear_scene()
    wood, brass = wood_material(), metal_material()
    leaf = build_leaf(wood)
    build_handle(leaf, brass)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(OUT.resolve()))
    tris = sum(len(p.vertices) - 2 for p in leaf.data.polygons)
    print(f"JOINERY_BUILT {OUT} objects={len(bpy.data.objects)} leaf_tris={tris}")


if __name__ == "__main__":
    main()
    sys.exit(0)
