# 0073. Character parts: body regions, MPFB2 body shape and the game's shape keys

Status: accepted (2026-11-12)

## Context

Part selection (ADR 0028) lets the user pick an object, a material, a map, a bone or a
shape key of a prop and say what to change. A character is different in two ways.

- Its body is one mesh with one skin material, so "bulkier shoulders" names no object
  and no material. The shoulders are the vertices the shoulder bones move.
- An MPFB2 human carries dozens of shape keys that are not expressions: the `$md-…`
  macro targets (age, gender, muscle, weight, proportions) and any detail target MPFB2
  loads by name (`l-upperarm-shoulder-muscle-incr`). The export baked the macros into
  the GLB (ADR 0028, slice 2) but kept every other key as a morph target, detail targets
  included, and there was no way to say which keys the game animates.

## Decision

1. **Body regions come from the bones.** `joinery/tools/blender_regions.py` names the
   region of a bone or vertex group (`region_of`): head, neck, shoulders, torso, hips,
   arms, hands, legs, feet, from Rigify's names (its spine is numbered hips to head:
   `spine` hips, `.001`-`.003` torso, `.004`-`.005` neck, `.006` head), MPFB2's own rig
   (`upperarm01.L`, `clavicle.R`), Mixamo (`mixamorig:LeftHand`) and the usual hand-made
   names. MPFB2's helper and joint groups are no region. Each vertex belongs to the region
   of the group that weighs it most; a mesh with an Armature modifier counts only that
   armature's deform bones. The function is plain Python: Joinery calls it, and the
   Blender scripts embed its source, so both sides name a bone's region the same way.

2. **The inspection and the GLB carry them.** The part inspection reports per mesh its
   regions (bones, vertex count), the regions each shape key moves (10 % or more of its
   moved vertices), the shape key values, and for an MPFB2 body its macro settings and
   object type. MPFB2 stores those as registered properties, which Blender 5 does not
   expose without the extension, so the inspection and the export turn MPFB2 on (with
   `default_set=True`, or it fails to register) only for a file holding an MPFB2 human
   (a `$md-` key or the `HelperGeometry` group). The export writes `joinery_regions`
   (region → bones, JSON) and `joinery_role` (`basemesh`, `clothes`, `hair`, ...) on each
   object, so the editor picks regions on the game's copy without reading the source.

3. **New part kinds.** `region` (id `…#object=Hero&region=shoulders`) and `body_shape`
   (an MPFB2 macro, `…#object=Hero&parameter=muscle`). A region of an MPFB2 body lists the
   detail targets and macros that shape it (`REGION_TARGETS`, `REGION_MACROS`; the
   integration test checks each target exists in MPFB2) and routes to `set_body_shape`,
   then `edit_bone`; a region of any other rigged model routes to `edit_bone`. A bone
   names its region. Shape keys say whether they are body shape or expression and whether
   the game gets them (`in_game`, read from the model's recorded choice). Objects MPFB2
   made as clothes, hair, eyes or teeth list no regions and are picked whole.

4. **Picking.** On the asset page a click on the body picks the region under the pointer
   (the hit triangle's vertices vote with their heaviest skin bone); on clothes, the
   object; elsewhere, the material as before. Picking a region, a bone or a shape key in
   the list tints the triangles that follow those bones (two of three corners): an
   overlay mesh with the body's own vertices, bones and weights, drawn as a child of the
   body with its skin and skeleton, so it moves with a pose, mixed over the surface in the
   accent and grown 2 mm along the normals. Review does the same on the live models
   (ADR 0040): a click (not a drag) on either side picks the part there, tints a region on
   both sides and makes it the next message's target; the review item now names the
   model's resource (`model_resource`) for that.

5. **`set_body_shape(source, object, macros, targets)`** changes an MPFB2 human through
   MPFB2's own scripting API: `HumanObjectProperties.set_value` and
   `TargetService.reapply_macro_details` for macros, `TargetService.set_target_value` /
   `load_target` for detail targets (0 removes one). It then calls `HumanService.refit`,
   which refits clothes and the rig where MPFB2 can. A generated Rigify rig whose meta-rig
   was deleted cannot be refitted; the result says so (`refit`) with how far bones moved,
   so the agent can tell the user. The scope guard allows the body, its rig and their
   children. It can be batched.

6. **`bake_shape_keys(source, keep, values, object, in_source)`** chooses the GLB's morph
   targets. `keep` lists them (expressions, visemes); every other key is baked into the
   exported mesh at its value, from Blender's own mix of those keys (values, mutes,
   vertex groups and relative keys count), and each kept key gets the same offset as the
   base. `keep: null` is the default: MPFB2's body-shape keys (macros, and detail targets
   known by name from MPFB2's own target files, found without enabling it) are baked and
   every other key is kept. `values` sets key values first. The .blend keeps every key,
   still editable; `in_source` bakes there too, only when the user asks, since MPFB2
   cannot change a baked body again. A key no mesh has is refused before anything is
   written.

7. **Recorded and replayed.** The choice is the recipe line
   `_joinery_bake_shape_keys(keep_macros=False, keep=('smile',))`. Later edits read it
   from the model's last recipe and keep it unless told otherwise (`keep_macro_shape_keys`
   on `operate_document` and `blender_batch` still works and keeps the recorded `keep`);
   recipes made before read as their old `_joinery_bake_macro_keys(keep=…)` choice.
   `joinery rebuild` replays the same export.

8. **Budgets see the baked mesh.** The art bible's GLB checks already measure the export,
   so triangles, bounds and pivot are those of the baked body. A new rule,
   `[models.max_morph_targets]` per folder, budgets the morph targets per mesh and points
   at `bake_shape_keys`. Typed operations' own reports (`JOINERY_PART_OP`, such as the
   refit) now reach the tool result as `operation`.

## Verified

`tests/integration/character_parts.py` in the creative worker: an MPFB2 human with a
generated Rigify rig and two mouth keys. The default export keeps exactly `smile` and
`viseme_aa`; `set_body_shape` (muscle 0.8, both shoulder-muscle targets and the shoulder
distance target) widens the GLB from 1.051 m to 1.101 m as `measure_glb` reads it while the
.blend keeps every key; `bake_shape_keys(keep=["smile"])` leaves one morph target, a later
edit keeps it, an unknown key is refused; the parts list has nine regions (19 142
vertices), the body settings and the in-game flags; the recipes replay.
`tests/godot/character_parts_test.gd` (run by `run_parts_tests.py`) picks every region on
the committed character GLB, the list and the model click on the asset page, and a click
on the live model in review.

## Limits

- A region is whole: both shoulders, both arms. "Only the left arm" is the region plus the
  `l-` targets; there is no left/right region yet.
- Regions follow skin weights, so a hand-made mesh without an armature or named vertex
  groups has none.
- A body changed after its Rigify rig was generated with the meta-rig deleted keeps its
  old bones (MPFB2 cannot refit it); regenerating the rig is a separate step.
- Clothes are known by MPFB2's object type; clothes made otherwise are picked by region
  like the body when they are skinned.
- The editor's Alt+click in the 3D view picks regions the same way, but only on the model
  root selected (ADR 0028).
