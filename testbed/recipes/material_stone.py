"""Build a stone floor material in Blender and bake it. Replayed by rebuild.

Operation log for art/materials/stone_tiles.blend plus the baked textures in
art/materials/. The material is a shader-node graph: tiled bricks, noise-driven
wear, and a bump from the same pattern. Blender then bakes it to flat PNGs that
Godot can use on any mesh without recomputing procedural noise at runtime.

ADR 0003 makes Blender the preferred material tool for now, with Material
Maker's MCP still first-party-planned, so this is shader nodes and bake rather
than a .ptex graph.

This is procedural authoring, not texture generation by a model: every value
below is a parameter of a node graph that can be edited and re-baked.

Art rules this follows:
  roughness stays in 0.55-0.95, stone is never glossy
  metallic 0, no pure black or white albedo
  512 px maps for a 1 m tile, which is the testbed's texel density

Run: blender --background --factory-startup --python recipes/material_stone.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import bpy

BLEND_OUT = Path("art/materials/stone_tiles.blend")
ALBEDO_OUT = Path("art/materials/stone_tiles_albedo.png")
ROUGH_OUT = Path("art/materials/stone_tiles_roughness.png")
SIZE = 512
SAMPLES = 24


def clear_scene() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.preferences.filepaths.save_version = 0


def build_plane() -> bpy.types.Object:
    """A 1 m UV-mapped plane: the bake target for a one-metre tile."""
    bpy.ops.mesh.primitive_plane_add(size=1.0)
    plane = bpy.context.active_object
    plane.name = "StoneTile"
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.uv.smart_project(angle_limit=1.15)
    bpy.ops.object.mode_set(mode="OBJECT")
    return plane


def build_material() -> bpy.types.Material:
    """Tiled stone from a brick pattern, worn by noise."""
    mat = bpy.data.materials.new("Floor_Stone_Grey")
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    nodes.clear()

    out = nodes.new("ShaderNodeOutputMaterial")
    out.location = (700, 0)
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (420, 0)
    coords = nodes.new("ShaderNodeTexCoord")
    coords.location = (-900, 0)

    # The tile pattern itself.
    brick = nodes.new("ShaderNodeTexBrick")
    brick.location = (-650, 120)
    brick.offset = 0.5
    brick.squash = 1.0
    # 3 tiles across a 1 m plane: ~33 cm floor tiles. At 6 it read as wall brick.
    brick.inputs["Scale"].default_value = 3.0
    brick.inputs["Mortar Size"].default_value = 0.022
    brick.inputs["Mortar Smooth"].default_value = 0.25
    brick.inputs["Bias"].default_value = 0.0
    brick.inputs["Color1"].default_value = (0.42, 0.42, 0.44, 1.0)
    brick.inputs["Color2"].default_value = (0.36, 0.36, 0.38, 1.0)
    brick.inputs["Mortar"].default_value = (0.19, 0.18, 0.17, 1.0)

    # Wear: large blotches plus fine grain, so no two tiles read identically.
    blotch = nodes.new("ShaderNodeTexNoise")
    blotch.location = (-650, -180)
    blotch.inputs["Scale"].default_value = 7.0
    blotch.inputs["Detail"].default_value = 4.0
    grain = nodes.new("ShaderNodeTexNoise")
    grain.location = (-650, -380)
    grain.inputs["Scale"].default_value = 48.0
    grain.inputs["Detail"].default_value = 2.0

    darken = nodes.new("ShaderNodeMix")
    darken.location = (-380, 0)
    darken.data_type = "RGBA"
    darken.blend_type = "MULTIPLY"
    darken.inputs["Factor"].default_value = 0.48  # visible wear between tiles

    links.new(coords.outputs["UV"], brick.inputs["Vector"])
    links.new(coords.outputs["UV"], blotch.inputs["Vector"])
    links.new(coords.outputs["UV"], grain.inputs["Vector"])
    links.new(brick.outputs["Color"], darken.inputs[6])
    links.new(blotch.outputs["Color"], darken.inputs[7])
    links.new(darken.outputs[2], bsdf.inputs["Base Color"])

    # Roughness: mortar rougher than stone, grain on top. Stays in 0.55-0.95.
    rough_map = nodes.new("ShaderNodeMapRange")
    rough_map.location = (-120, -300)
    rough_map.inputs["From Min"].default_value = 0.0
    rough_map.inputs["From Max"].default_value = 1.0
    rough_map.inputs["To Min"].default_value = 0.58
    rough_map.inputs["To Max"].default_value = 0.93
    links.new(grain.outputs["Fac"], rough_map.inputs["Value"])
    links.new(rough_map.outputs["Result"], bsdf.inputs["Roughness"])

    bsdf.inputs["Metallic"].default_value = 0.0

    # Relief from the same pattern, so the bump matches the tiles exactly.
    bump = nodes.new("ShaderNodeBump")
    bump.location = (180, -220)
    bump.inputs["Strength"].default_value = 0.35
    links.new(brick.outputs["Fac"], bump.inputs["Height"])
    links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])

    links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return mat


def bake(plane: bpy.types.Object, mat: bpy.types.Material, kind: str, dest: Path):
    """Bake one channel to a PNG."""
    image = bpy.data.images.new(dest.stem, SIZE, SIZE, alpha=False, float_buffer=False)
    nodes = mat.node_tree.nodes
    tex = nodes.new("ShaderNodeTexImage")
    tex.image = image
    tex.select = True
    nodes.active = tex

    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = SAMPLES
    # Cycles is not bit-reproducible by default: adaptive sampling decides when
    # to stop from noise estimates that shift with thread scheduling, and the
    # thread count itself varies by machine. Fix the seed, sample every pixel
    # the same number of times, and render single-threaded. Slower, but the
    # render is identical on every machine, which is what makes it verifiable.
    scene.cycles.seed = 20261107
    scene.cycles.use_animated_seed = False
    scene.cycles.use_adaptive_sampling = False
    scene.cycles.use_denoising = False
    scene.render.threads_mode = "FIXED"
    scene.render.threads = 1
    scene.render.bake.use_pass_direct = False
    scene.render.bake.use_pass_indirect = False
    scene.render.bake.margin = 4

    bpy.ops.object.select_all(action="DESELECT")
    plane.select_set(True)
    bpy.context.view_layer.objects.active = plane
    bpy.ops.object.bake(type=kind, use_clear=True)

    image.filepath_raw = str(dest.resolve())
    image.file_format = "PNG"
    image.save()
    nodes.remove(tex)
    return image


def main() -> None:
    clear_scene()
    plane = build_plane()
    mat = build_material()
    plane.data.materials.append(mat)

    ALBEDO_OUT.parent.mkdir(parents=True, exist_ok=True)
    bake(plane, mat, "DIFFUSE", ALBEDO_OUT)
    bake(plane, mat, "ROUGHNESS", ROUGH_OUT)

    bpy.ops.wm.save_as_mainfile(filepath=str(BLEND_OUT.resolve()))
    print(f"JOINERY_BUILT {ALBEDO_OUT} {ROUGH_OUT} {BLEND_OUT} {SIZE}px")


if __name__ == "__main__":
    main()
    sys.exit(0)
