# 0045. Make it match

Status: accepted (2026-11-11)

## Context

The art bible's checked rules (ADR 0022) judge a change when it is submitted: only the files
that change, and only to pass or fail it. A project drifts anyway. Assets made before a rule was
added, a palette that changed after the textures were painted, a loud sound imported early, an
Environment tuned by hand. Nothing said how far the whole project was from its bible, and fixing
it meant asking Journeyman for each file in turn.

## Decision

1. **An audit, no model.** `joinery match --project P [--json] [--area A] [--plan]`
   (`joinery/match.py`, `match_checks.py`, `match_look.py`) measures everything the bible's rules
   cover, reusing the measurements of ADR 0022: palette distance (CIEDE2000) of images in
   `palette.folders`; texture sizes and power of two; texel density, applied scale, pivots and
   triangle and bone budgets of `.glb` models; roughness and metallic of their materials;
   loudness and true peak of sounds; and two new optional tables, `[environment]` (tonemap,
   exposure range, fog, glow, brightness/contrast/saturation ranges for every Environment, in a
   `.tres` or inside a scene) and `[ui]` (`fonts` UI resources may use; `palette_colours`: theme
   and StyleBox colours stay within `palette.max_delta_e`), with `accessibility.min_text_px` for
   text sizes. Each finding has a severity, the measured value, the bible's target and the typed
   fix for the tool that owns the file, or why Joinery can't fix it ("you imported it", "made in
   GIMP"). Each area gets a score: the share of what it checked that already matches
   ("Palette 72% · Models 100% · Sound 90%"). Pixels and loudness are measured in the offline
   worker, read-only; a file that can't be measured is listed, never passed.
2. **Typed fixes in the owning app.** Krita gains `palette_map` (a layer that covers only the
   pixels further than the bible's ΔE from the palette with their nearest palette colour, using
   the same CIEDE2000 code as the audit) and `resize`, through `edit_image` on `joinery-paint`
   for a painting of its own. Blender gains `apply_scale`, `set_pivot`, `reduce_triangles`
   (Decimate, applied) and `set_texel_density` (a texture too dense is resized, one too sparse
   has its UVs scaled so it repeats) on `joinery-blender-ops`, each scope-guarded. Materials use
   `set_material_value`, sounds `process_audio` (normalise or gain, re-applied on every
   re-render), Environments and themes the Godot MCP in the worker: `manage_resource` for a
   resource file's own properties, a text patch through `write_file` for a sub-resource, a font
   swap, or a file that has both. Every fix is an ordinary edit with a replayable recipe.
3. **Small reviewed changes, in parallel.** `joinery/match_plan.py` groups fixes by the file they
   change (so no two tasks want one file), then by area and app, at most six files per change:
   "Remap 6 textures to the palette", "Normalise 9 sounds to −16 LUFS", "Match the lighting".
   Several fixes on one file become one call where the tool takes a list. The chat host's
   `match_fix` (`bridge/match_runner.py`) audits again, plans and starts each change as a
   *scripted task* of the crew (ADR 0036): its own lane, claims and staged change, no provider of
   its own, since Joinery already knows the calls; worker slots still serialise each app. As many
   run at once as `[agents] parallel` leaves free beside the chat's own request; the rest queue.
   Each change goes through the submit checks, the reviewer agent and the glanceable review
   (ADR 0040) with Joinery's facts ("Exposure 1.6 → 0.9–1.1"); nothing is accepted for the user.
4. **What the user protects.** The bible's `exclude`, `[match] exclude` (folders or files) and
   `[match] final` in `joinery.toml` are never measured or changed, nor is anything exported from
   a final source. `joinery match --final PATH` (and `--not-final`) marks one.
5. **The card.** `/match`, or **Make it match** in Settings, on the Review tab's art bible row and
   in the art direction, opens the Match card with the agents at work: one headline ("9 things
   drift from the art bible"), one row per area with its bar and score, the three most serious
   findings (what was measured → what the bible asks), **Fix N things** and a **Fix** per area.
   While the changes run the card counts what is ready to review and its details step back; each
   change has its own task card and its row in the Review tab. After an accept the project is
   measured again and the scores roll up in place.

Measuring the whole stage after each fix also exposed two shared bugs, fixed here: recipe
preimages under `recipes/` were judged as assets by the submit checks (so any Blender edit of a
model with unapplied scale failed on its own preimage), and two parallel changes that both
created a source folder clashed on its empty `.gdignore`.

## Consequences

- An approved bible can be applied to a whole project in one go, and every change is still a
  normal reviewed change with a recipe.
- An older Joinery reading a bible with `[environment]` or `[ui]` reports the unknown table.
- Not fixed by Joinery: bone budgets, albedo luminance and sprite colour counts, sounds or
  textures without a recipe (imported), textures made in GIMP or Inkscape, and images baked
  into a `.glb` without a Krita source (their texel density is fixed in Blender instead).
- Texel density by UV scale makes a texture repeat; it assumes the texture tiles. A model whose
  UVs are an atlas should get a smaller texture instead, which the reviewer and the user see in
  the before/after.
- A text patch is computed from the file as it was when the change was planned; a file changed
  meanwhile is a conflict at submit, as for any parallel change.
