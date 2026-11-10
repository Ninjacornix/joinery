# 0022: Art bible, second format

Status: accepted, 2026-11-09.

## Context
The first bible covered look and sound in nine short sections and checked three
things: loudness, triangle budgets on exported `.glb` files and palette distance.
Journeyman had nothing to cite for camera, shapes, light, materials, animation,
VFX, UI, writing or accessibility, so it either asked the same questions again or
guessed. References were one free-text paragraph, and much of what the bible
already said in prose (applied scale, pivots, texel density, no pure black
albedo, power-of-two textures) was never measured.

## Decision
- **Sections from one schema.** `joinery/bible_schema.py` lists the 20 sections,
  their `{{token}}`, whether each is *required* (blocks approval while unset) or
  *open* (Journeyman suggests it when the work reaches it), what it is relevant
  to (`3d`, `2d`, `ui`, `audio`, `any`) and when to raise it. Required is the
  first format's set exactly, so no project gets stricter. Two unset markers keep
  the two apart; approval still refuses only the required one.
- **References are a board.** Rows `R1, R2, …` with Kind, *Take this*, *Not
  that* and an optional file. Files are the user's own (Krita boards made in
  Krita, their photos and scans as `human-import`) and live in `art/reference/`,
  which has a `.gdignore`, is skipped by every rule check, and may be changed
  before the bible is approved. The lock gate still applies to it. A creative
  call whose asset paths all point into `art/reference/` may start before
  approval; `finish` still refuses every other asset change without it.
- **Presets fill technical sections only.** `templates/bible_presets/<id>.toml`
  per blueprint (`lowpoly-3d`, `stylised-pbr-3d`, `pixel-2d`, `flat-2d`,
  `cards-ui`, `none`) suggest camera, budgets, animation, UI and accessibility
  numbers, each marked "Suggested by …; change freely", and merge rule values
  over the default block. A preset that names a taste section is refused.
- **Checked rules, schema 2.** Ten families: audio loudness and format, model
  budgets (triangles, deform bones), applied scale, pivots, texel density,
  material factors and albedo textures, file and node naming, texture size and
  power of two, sprite colour counts, palette (now with named roles), and
  accessibility (WCAG contrast, minimum text size, colour-blind separation).
  Every table is optional; unknown keys are errors. `exclude` defaults to
  `art/reference`. A schema newer than this Joinery is refused by name.
- **Measured where the format can be read.** `.glb` (geometry, skins, factors,
  embedded texture sizes, texel density), WAV headers, PNG/JPEG/WebP headers and
  Godot `.tres`/`.tscn` text are read in Python. `.blend` files are measured by
  Blender itself in the offline creative worker (`joinery/bible_blend.py`, one
  run for all changed files, read-only); Ogg streams by `ffprobe`; pixels by
  `ffmpeg`. A file that cannot be measured is a problem, never a pass.
- **The bible is linted, not enforced.** `joinery/bible_lint.py` reports
  invalid rules, palette pairs below the contrast target, roles that merge for
  protan, deutan or tritan vision (Machado 2009 matrices, CIEDE2000), reference
  rows without *Take this*, and reference files that are missing or unlocked;
  it lists gaps and turns them into ordered suggestions for Journeyman (required
  first, then the project's kind). Lint never blocks asset work.
- **UI meets the bible.** With an approved bible, changed `.tres`/`.tscn` files
  under `accessibility.ui_folders` are checked for text contrast against their
  StyleBoxFlat backgrounds and for font sizes below the minimum (scaled from
  1080p to the project's viewport height).
- **Upgrading only adds.** `joinery/bible_upgrade.py` keeps every first-format
  line in order and adds the version marker, the missing sections as open, an
  empty reference board, and `schema = 2` with the new tables commented out.
  It changes the bible's bytes, so it is a reviewed edit approved again.

## Compatibility
A first-format rules block parses unchanged and turns on nothing new. Existing
approvals stay valid because no bible bytes change. Two behaviours change for
first-format projects: `art/reference/` is excluded by default, and a `.blend`
under a `max_triangles` folder is now measured in Blender, not only `.glb`.

## Limits
Pivots in `.glb` are judged on the whole scene's bounds, in `.blend` per root
mesh (or mesh parented to an armature). Texel density needs a base colour
texture embedded in the `.glb` or an image node feeding Base Color in Blender;
untextured models are not judged. Material factors driven by a texture are
checked through the texture only when it is an albedo image in the project.
UI contrast covers StyleBoxFlat backgrounds and opaque colours; textured or
translucent backgrounds are not judged.
