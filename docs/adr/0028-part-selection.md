# 0028: Picking one part of a model and editing only that part

Status: accepted, 2026-11-10

## Context

Users want to say "make the steel rougher" about one material or one texture
map of a model, not about the whole file. Three facts shape how:

- Godot does not keep Blender's names. A node called `Head.001` in Blender is
  `Head_001` in Godot. Material names survive, but Godot extracts embedded
  textures to unnamed files by default.
- glTF has no height map. A height map wired through Bump is exported as the
  normal texture (wrong), and the real normal map is dropped.
- Shader nodes between a map and the shader (Map Range, Invert, Gamma) are lost
  on export. Blender shows one thing and the game shows another.

## Decision

**Parts come from the owning tool.** `joinery parts <asset> --json`
(`joinery/parts.py`, also `python -m joinery.parts`) lists a model's objects,
materials, texture maps, shape keys and bones. Blender reports them: an
inspection walks each image node's links to the shader socket it feeds, so a map
is known by its role (base colour, roughness, metallic, normal, height,
emission, alpha, ambient occlusion), through Normal Map, Bump or Displacement,
and through Separate Color with its channel (an ORM map's Green is roughness).
Providers are registered per tool id (`ToolModule.parts_provider`): Blender now,
others later without changing callers. Reports are cached in `.joinery/parts/`
by the source's sha256.

A part has a stable id with no instance ids:
`res://art/models/hammer.glb#material=Steel&image=steel_rough`. The sidebar
sends it as a flat string target with the Blender names, the role and the owner.

**Names are mapped back through export metadata.** Every Blender export writes
`joinery_name` on each object and material and `joinery_maps` (role → image) on
each material, with `export_extras=True`. They reach Godot as the `extras` meta
of the node and the material. Picking reads them first and falls back to the
node or resource name for older exports. Surfaces map to materials by name,
never by slot index, because a slot with no faces has no surface.

**Textures stay embedded.** The first time Joinery exports a model it writes the
`.glb.import` with `gltf/embedded_image_handling=3`. Godot then writes no
`<model>_<image>.png` files that no recipe makes (which would break the
provenance lock), and each texture keeps its Blender image name. Settings the
user changes later are left alone.

**Edits change pixels, not nodes.** The typed operations in
`joinery-blender-ops` are `set_material_value` (one unlinked shader value),
`adjust_map` (invert, gamma, levels, contrast, tint, hue/saturation/value,
optionally one channel) and `bake_height_to_normal`. `adjust_map` sets the
image's colour space first (setting it later reloads the image and drops the
new pixels), rewrites `image.pixels`, then packs the image. Each is an ordinary
Blender edit, so preimage, recipe, lock, texture export, GLB export and review
all apply.

**Height becomes a normal map by baking.** `bake_height_to_normal` runs a
Cycles tangent-space NORMAL bake through a temporary Bump fed by the height map
(keeping any detail normal already wired in) into `<material>_normal_baked`,
then wires that image through Normal Map into the shader's Normal. The height
map keeps driving Displacement in Blender only. The bake needs UVs; overlapping
UVs on the objects sharing the material overlap in the bake too.

**Edits stay inside the selection.** Each typed operation refuses a material,
image or object that is not one of the prompt's part targets. The gateway writes
those targets into the stage (`.joinery/part_targets.json`) before calling a
Joinery tool server. With no part picked in that model, the whole model is in
scope. A scope guard in the same Blender run hashes every object (transform,
slots, modifiers, geometry, UVs, shape keys), every material (settings, nodes,
links, socket values) and every image (size, colour space, pixels) before and
after the operation. Anything outside the named part changing fails the edit,
and nothing is saved.

**Review shows the map.** `review.evidence()` takes the prompt's part targets.
For a material or map target it renders the model in Workbench with
`color_type='TEXTURE'` and that map's node active, framed on the target object,
before the full model. Changed PNGs under `art/textures/` come first, so the
order is map before/after, the map on the model, then the full model.

**Picking in the editor.** The Tools workspace lists the parts under the model
preview. Clicking a row, or clicking the model (a press and release without
dragging), makes the part the message target; dragging a row carries it. A
picked map shows beside the model with Joinery's exported pixels (one channel
as grey for a packed map), and the material's surfaces are tinted. In the 3D
view, Alt+click on a selected model picks the material under the pointer.
Picking needs no physics bodies: a cached `TriangleMesh.intersect_ray` gives a
face, and the face gives the surface. The chip label is leaf first:
"Roughness · Steel", or "Roughness · Head.001" when picked on Head. The tooltip
has the breadcrumb, "Used by Handle, Head.001" for a shared material, and where
the part is edited.

## What works on stock Godot

Everything above is plugin code. Two limits remain without a fork patch:

- Alt+click picks only while the model's instance root is the selected node.
- The plugin cannot use `_handles` for this: it has a main screen, and a
  main-screen plugin that handles an object switches the editor to its screen.
  So it uses `set_input_event_forwarding_always_enabled()` with
  `_forward_3d_gui_input`, which passes every other click through.

## Not in this slice

Making a map an editable Krita or Material Maker source, `paint_map`,
`replace_map`, "make a copy for Head only", and typed operations for bones and
shape keys (they are listed and can be targeted, but have no operations yet).
