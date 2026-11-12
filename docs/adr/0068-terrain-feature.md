# 0068. Terrain feature

Status: accepted (2026-11-12)

## Context

Outdoor 3D games need ground that is more than a box: hills, a level plot for a
camp, a path that climbs gently, rock on the steep parts, grass and stones
scattered where it makes sense. Godot has no terrain node of its own. The usual
answers are add-ons: Terrain3D (MIT, a C++ GDExtension with clipmaps and
in-editor sculpting) for the ground, ProtonScatter or Spatial Gardener (MIT,
GDScript editor add-ons) for what grows on it.

The same constraints as the dialogue, behaviour and camera features (ADR 0065)
apply, plus Rule Zero for the ground itself:

- The output opens in stock Godot 4.7; a feature ships code only, no binaries,
  and third-party code is a pinned, hash-checked download the user approves
  (ADR 0027), never required.
- The heightmap and the paint mask are assets: they need a `joinery.lock` entry
  and a recipe that makes them again. They are made by operating a real tool or
  procedurally in code, never by a generative model, and never fetched.
- The agent changes the ground through typed operations it can name ("flatten
  here, raise there, a path from the camp to the ridge"), each a reviewed
  change, not by writing pixels.

## Decision

`joinery feature add terrain`: our own small MIT GDScript in `features/terrain/`,
for the 3D blueprints (third-person, first-person, VR room). About 1,400 lines.

### Runtime

- **`Terrain` (Node3D)** reads a heightmap (16-bit grey PNG, 8-bit PNG or EXR) and
  maps 0-1 to `min_height`-`max_height` metres over `size`, centred on the node.
  It builds square chunks (`chunk_cells`) as internal children, so the scene keeps
  only its settings.
- **LODs by chunk distance.** Each chunk has a mesh per level, each level half the
  grid of the one before, switched by Godot's visibility ranges (`lod_distance`,
  doubling per level; `view_distance` hides far chunks). No script runs per frame
  for it. Each mesh has a skirt hanging from its outline, so the cracks where a
  detailed chunk meets a coarse one never show the sky.
- **Collision** is a HeightMapShape3D per chunk with a uniform scale (heights are
  stored in cells), so physics engines that dislike non-uniform scale are happy.
- **Splat material** (`terrain.gdshader`): up to four `TerrainLayer`s, each the
  project's own texture (Krita, Material Maker, a Blender bake) or a plain colour.
  Layer 0 covers everything; layers 1-3 go on top where the mask's r, g, b paint
  them and, with `use_rule`, inside a height and slope band. The mask's a channel
  is free for scatter.
- **Scatter** (`TerrainScatter`): a scene or mesh from the project (a kit's GLB),
  density per 100 m², seeded per chunk, allowed by a mask channel, a height band
  and a slope limit, with scale, turn, tilt and sink. Drawn as a MultiMesh per
  chunk and rule, without collision; props the player bumps into are placed as
  scenes by the existing `scatter` tool.
- **16 bits.** Godot's image import keeps 8 bits per channel, which terraces a
  heightmap. `terrain_png.gd` reads 8- and 16-bit PNGs (every row filter) and
  writes 16-bit grey and 8-bit RGBA in GDScript; the PNGs are imported as
  **Keep File** (`importer="keep"`), so the game reads all 16 bits and an export
  still contains them. EXR goes through Godot's importer, which keeps floats.
- **Playtester.** The node is in `playtest_state`: chunks, the LODs the camera
  draws, triangles per LOD, scatter counts, and how many checks found a player on
  the surface or below it (falling through the ground), so a playtest catches a
  hole in the collision.

### Making the ground: operations in Godot, recipes, review

- `terrain_ops.gd` runs a **document**: grid (`size`, `resolution` = 2ⁿ+1 points a
  side, height range, seed, an optional `base` heightmap and `base_mask`) and a
  list of operations: `noise` (fractal value noise from integer hashing, so a seed
  gives the same ground on every run), `raise`, `lower`, `flatten`, `smooth`,
  `erode` (thermal: steep slopes slide into screes), `path` (eases the ground
  along the points to a smoothed profile, optionally sunk and painted), `paint`
  (a mask channel). Positions are metres in the terrain's space.
- `terrain_bake.gd` writes `art/terrain/<name>/height.png` and `mask.png` from a
  document. Our PNG writer uses no row filter and Godot's zlib, so the same
  document gives the same bytes and the recipe is checked exactly (`sha256:`).
- **Typed tools** in joinery-paint (`servers/paint/terrain.py`, `joinery/terrain.py`):
  `terrain_new`, `terrain_edit` (appends operations; the whole log runs again) and
  `terrain_info` (read-only). Each validates the operations (pydantic, bounds
  inside the terrain, flatten heights inside its range), writes
  `recipes/<id>.json` (the whole document: the operation log), `recipes/<id>.gd`
  (three lines that call the bake) and `recipes/<id>.toml` (tool `godot`), runs it
  with `godot --headless --script` in the offline worker, records fingerprints and
  lock entries, and stages the change for review. The previous recipe moves to
  `recipes/history/`. They sit with the other painting tools, so the art gate
  (approved bible) and the review apply as for any image. `joinery rebuild`
  replays the recipe in Godot.
- **Krita** stays the way to paint by hand: a heightmap or mask painted there is a
  `base`, and the operations work on top of it. The painting remains the source
  and is listed in the recipe's sources.
- The agent learns the API from `features/terrain/GUIDE.md` (ADR 0065), and the
  shared instructions point it at the terrain tools whenever the game has the
  feature: never write terrain pixels by hand.

### Alternatives, pinned at nothing

Terrain3D, ProtonScatter and Spatial Gardener are listed in the feature's
`[[external]]` (never required) and in `policy/terrain_alternatives.toml` with an
empty version, URL and hash. `joinery.terrain.install_alternative` refuses an
unpinned one and has no download path; pinning one is a reviewed change, and
installing it a separate step the user approves, which is not built.

Terrain3D was not adopted: it is a native binary per platform (the GDExtension
model of GodotSteam), its data lives in its own resource format the agent cannot
diff, and its sculpting is an editor tool rather than operations that replay.
The scatter add-ons are editor workflows whose results are scenes; our scatter is
a few rules on the terrain node that are seeded and need no editor.

## Tests

- `tests/blueprints/test_feature_terrain.py`: code-only overlay offered to the 3D
  blueprints only; apply and remove leave the project as it was; in stock Godot on
  third-person-3d and first-person-3d (`terrain_test.gd`): the same seed builds
  the same maps and another seed other ones, every operation does what it says,
  16 bits survive a PNG round trip and Godot's own filtered PNGs read back, rays
  hit the collision where the mesh is drawn, the player lands on the terrain and
  walks across it, chunks are coarser further away, scatter keeps to its mask and
  slope and is seeded, a terrain from the files equals one built in code. The
  playtest bot then plays a third-person level whose starting island is replaced
  by a terrain: nothing serious, coverage, the terrain's state shows the player on
  it and never below it.
- `tests/test_terrain.py`: settings and operations are validated with the reason,
  `terrain_new` and `terrain_edit` write the document, script, recipe, keep-imports
  and lock entries, a failed bake leaves nothing, the tools need a transaction and
  the feature, alternatives are refused, joinery-paint lists the tools; with Godot,
  the bake writes a 16-bit PNG, `rebuild_one` replays it to the same bytes, and an
  edit changes it.
- The blueprint matrix runs `terrain_test.gd` in every cell with the feature.

## Consequences

- Hills, plots and paths are typed, reviewable, replayable changes; the art gate
  and provenance rules hold for terrain like any other image.
- We maintain a small terrain instead of tracking a native add-on. Missing next to
  Terrain3D: clipmap geometry for very large worlds, holes, in-editor sculpting
  brushes, normal and roughness maps per layer, more than four layers.
- Not verified yet: byte-identical bakes across CPU architectures (the recipe is
  recorded and replayed in the same linux/amd64 worker, which is what `joinery
  rebuild` compares); the replay running with the game's autoloads in a stage
  without Godot's class cache (they log errors, the bake does not use them and
  succeeded in a test without the cache); an exported game reading the kept PNGs
  (the editor and headless runs do); terrains larger than 1025 points a side.
