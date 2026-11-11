# 0044. Parametric kits made in Blender

Status: accepted (2026-11-11)

## Context

Rocks, crates, barrels, fences, small buildings, trees and bushes fill most game worlds, and
an agent asked for "fifty rocks" wrote fifty pieces of Blender code, each a little different,
each a new chance to break the art bible. A level also needs those models placed: by hand in
the editor, or one `edit_scene` instance at a time. Rule Zero rules out a generative model for
either, so the work has to be done by real tools with replayable recipes.

## Decision

1. **Kits are hand-written procedural generators.** `kits/blender/<id>.py` (MIT, like every
   file copied into a game) holds a `SPEC` (typed parameters with ranges or choices, a label
   and a help line each, and the material roles with their own colour, roughness and
   metallic) and a `build(params, rng, materials, name)` that makes one mesh from bmesh
   shapes. `kits/blender/common.py` holds the shared shapes, the pivot, the triangle budget,
   LODs, the export and the per-model loop. The first kits: rock (size, roughness, facets,
   flatness, moss as a second material on the faces that look up), crate (size, height,
   planks, bands, wear), barrel (height, radius, staves, bands, bulge, wear), fence (length,
   post spacing, height, rails, rails or pickets, post size, broken posts), hut (footprint,
   floors, floor height, gable, hip, shed or flat roof, pitch, overhang, doors, windows,
   chimney) and low-poly stylised tree (height, trunk, branch levels, branches, round, cone,
   tall, flat or no canopy, canopy size, leaf cards, lean) and bush (size, clumps, flatness,
   roughness, leaf cards). bmesh, not geometry nodes: the same Python works in every Blender
   5.x, every vertex comes from the seed and the parameters, and a reader can follow it.
2. **Joinery checks, the kit builds.** `joinery/kits.py` reads each kit's `SPEC` (and an
   optional `limits(params)`, such as "more windows than walls"), refuses unknown, mistyped
   or out-of-range values with the range in the message, and turns the art bible into the
   build's context: every material colour is the nearest palette colour (inside the bible's
   albedo luminance where it can be), or one the user picks that is in the palette; roughness
   and metallic sit inside the bible's ranges, and metallic stays only on a material whose
   name says metal when the bible asks for that; the folder's triangle budget and pivot come
   from the model's own path. Blender reduces a model over budget (and refuses one that
   would lose three quarters of its triangles), sets the pivot (bottom-centre unless the
   bible says otherwise) and keeps scale 1; Joinery then measures every GLB again.
3. **One recipe per set, a source per model.** `kit_make` makes one model, `kit_batch` a set
   of 2 to 100 in one Blender run; each model gets its own `.blend` under `art/sources/kits/`
   and GLB in the kit's folder (`art/environment` or `art/props` by default), plus
   `<name>_lod1.glb` and `_lod2.glb` when asked. A set varies each parameter by a share of its
   range (or relative to its value for sizes), from seeds drawn from the set's seed, so the
   same call makes the same set. The recipe carries the kit's code, so `joinery rebuild`
   needs nothing from this repository, and its second line records the kit, the parameters,
   the seed and every model (`# joinery-kit: {...}`). `joinery rebuild` signs a set's
   `.blend` files in one Blender run, where it started Blender once per file before.
4. **kit_edit makes the set again.** Changing a kit-made model re-plans its whole set with
   the new values, keeping names, count, seed and variation, and replaces the old recipe, so
   scenes that place the models pick up the change. A model of a set changes with the set.
5. **Scatter is a staged scene edit.** `scatter` places instances of models in a saved scene
   as one Node3D group of instanced GLBs: Poisson-disc spacing, a random turn and scale,
   heights (and, with `align: normal`, tilts) from a surface (a node instancing a GLB, with
   its transform, or a GLB at the origin), clear of Path3D paths or point lists, optionally
   within a band beside them. Placement is seeded and written in the group's own space;
   scattering again with the same group name replaces it. A scene on screen or with unsaved
   edits is refused, like every staged Godot tool.
6. **Everywhere an agent works.** The operations live in `joinery-blender-ops` (all
   providers, through the gateway); `kit_list` is also in `joinery-registry`, read-only.
   `kit_make`, `kit_batch` and `kit_edit` hold the Blender worker slot (ADR 0036); every
   agent's instructions point at the kits before Blender code.
7. **The Kits section of the asset workspace.** An Assets | Kits switch puts the kits in the
   browser; a kit shows its parameters as sliders, pickers and switches, and Blender draws a
   preview in the worker half a second after the last change (Workbench, cached by values in
   `.joinery/kits/previews`, not an asset). **Make** and **Make 10 variations** run
   `joinery kits make` and leave the models waiting for review; making needs an approved art
   bible. A kit-made model says its kit in its caption, and **Kit settings** opens the panel
   on its set, with **Apply changes** once a value differs (`joinery kits edit`).

## Consequences

- Kits are flat-coloured: no UVs and no textures, so texel density rules don't apply to
  them. A texture can still be added later with the Blender operations.
- Scatter writes instanced scenes, not a MultiMeshInstance3D: a MultiMesh needs a mesh
  resource, and Godot can't reference a mesh inside an imported GLB from a scene file, so it
  would need a mesh file no recipe makes. Each rock stays movable by hand.
- Budget reduction collapses edges; a kit pushed to its limits looks rougher rather than
  breaking the bible. The result says `reduced_from` when it happened.
- Kit code is copied into each recipe, so a set made before a kit changes keeps replaying
  as it was made; `kit_edit` makes it with the current kit.
