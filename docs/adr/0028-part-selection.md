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

## Not in the first slice

Making a map an editable Krita or Material Maker source, `paint_map`,
`replace_map`, "make a copy for Head only", and typed operations for bones and
shape keys. The second slice below adds them.

## Addendum: slice 2 (2026-11-11)

**A map can have its own source.** `make_map_editable(source, material, image,
editor)` takes the map's exported pixels (`art/textures/<model>/<image>.png`)
as an immutable preimage under `recipes/inputs/`, named by its content, and
starts a document from it: a Krita `.kra` whose bottom layer is `Map`
(`editor="krita"`, the default), or a Material Maker graph whose image node
feeds the material input for the map's role (`editor="material-maker"`, for
procedural changes). Both live under `art/sources/maps/<model>/`, which Godot
ignores. A Blender edit then loads the document's export into the map
(`_joinery_link_map`: colour space first, then pixels, then pack) and marks
the image with `joinery_source` and `joinery_map`. That recipe lists the
export in `sources`, so `joinery rebuild` replays the Krita (or Material
Maker) recipe and the Blender one each from their own recorded inputs. The
parts list reports the source as the map's `editable`. If the second step
fails, the first is undone: its outputs, recipes and lock entries.

**Painting is typed Krita layers.** `paint_map(source, material, image, ops)`
adds one layer per operation on top of the document, so earlier work is never
painted over: `fill` (a paint layer, whole map or a rectangle), `stroke` (a
vector layer with one stroked path, round caps and joins, drawn by Krita's
renderer) and `adjust` (a Krita filter layer: invert, levels,
hue/saturation/value, Gaussian blur, desaturate). Colours are sRGB 0-1 and
positions are map pixels. Krita 5.2 has no brush API a script can drive
headless with a reproducible result, so there are no brush-engine dabs; a
stroke is a vector path. On replay the PNG is byte-identical and the `.kra`
matches its content signature (`kra:`, every entry but the timestamps).
A map whose source is a Material Maker graph is changed with `operate_graph`,
then `replace_map` loads the new export.

**`replace_map` takes only tool-made images.** The replacement must be a PNG or
JPEG under `art/` that a project recipe makes (Krita, GIMP, Material Maker or
another model's map), never one of this model's own exported maps (that would
make the recipe read its own output). The size may differ; the map takes the
new size.

**A copy for some objects.** `split_material(source, material, objects, name,
copy_maps)` copies the material for the named objects only; the others keep
the original. With `copy_maps` (default) each map is copied too, named
`<image>_<copy>`, so map edits stay separate. An object whose mesh is shared
with objects outside the list gets an object-linked slot. Splitting for every
user is refused: change the material itself.

**Bones and shape keys.** `edit_bone(source, armature, bone, op, params)`:
`rename` (Blender renames the vertex groups, constraints and animation that
follow the bone), `set_rest` (head, tail in armature space, roll), `set_parent`
(or none, optionally connected) and `scale_chain` (the bone and every child,
about the bone's head). `edit_shape_key(source, object, shape_key, op,
params)`: `set_value`, `rename`, `delete`, `new_from_mix` and `new_from_pose`
(the Armature modifier applied as a shape key, keeping the modifier). The
basis cannot be deleted or set. The scope guard now hashes an armature's bones
(name, parent, connect, deform, rest matrix, length), and an operation may
widen its allowed set at run time for what Blender changes with it (a renamed
bone's vertex groups on the meshes it deforms).

**Routing.** Each part target carries `ops`, the operations that change it, and
`editable`. The sidebar chip keeps what `joinery parts` gave and fills in the
kind's operations for a viewport pick. Agents are told to use those
operations, not code.

**MPFB2 macro shape keys** are baked on export (not part selection, but the
same models): the GLB export bakes the current mix of `$md-…` keys into the
mesh and keeps every other key as a morph target, unless the recipe says
`keep_macro_shape_keys`. The option is recorded in the recipe script and a
later edit keeps it unless told otherwise.

Not in this slice: a brush engine, masks and selections in `paint_map`, Krita
layers for part types other than maps, and choosing which non-macro keys reach
the game.
