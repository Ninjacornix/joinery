# Terrain

Heightmap ground for 3D games: a `Terrain` node builds chunked meshes with
distance LODs, HeightMapShape3D collision, a splat material of up to four
layers, and seeded scatter of the project's own models. Our own MIT GDScript.

## The node

```
Level
  Terrain (Terrain)   heightmap = "res://art/terrain/valley/height.png"
                      mask = "res://art/terrain/valley/mask.png"
                      size = (128, 128)  min_height = -4  max_height = 30
```

The terrain is centred on the node: x runs from `-size.x/2` to `size.x/2`, z
likewise, and a heightmap value of 0-1 becomes `min_height` to `max_height`
metres above the node. Move the node to place the terrain; do not rotate or
scale it. Everything it builds (chunks, collision, scatter) is an internal
child, so the scene file keeps only its settings.

| Export | Default | Meaning |
|---|---|---|
| `heightmap` | | 16-bit grey PNG (best), 8-bit PNG or EXR; empty is flat |
| `mask` | | RGBA PNG: r, g, b paint layers 1-3; a is free for scatter |
| `size`, `min_height`, `max_height` | (64, 64), 0, 20 | metres; use what `terrain_new` returned |
| `chunk_cells` | 32 | grid cells along a chunk's side |
| `lod_levels`, `lod_distance` | 3, 48 | full detail to 48 m, half to 96 m, quarter beyond |
| `view_distance` | 0 | chunks further away are not drawn; 0 never hides them |
| `layers` | [] | up to four `TerrainLayer`s (below) |
| `scatter` | [] | `TerrainScatter` rules (below) |
| `collision`, `collision_layer` | true, 1 | one HeightMapShape3D per chunk |

From code:

```gdscript
var ground: float = terrain.height_at(body.global_position)   # world y of the ground
var up: Vector3 = terrain.normal_at(body.global_position)
if terrain.contains(position): ...
terrain.build()                       # after changing heightmap or mask at run time
```

A PNG heightmap must keep its import as **Keep File** (`importer="keep"` in its
`.import`), which Joinery's terrain tools write: Godot's texture import keeps
only 8 bits, which turns slopes into terraces, and an exported game would not
contain the PNG. EXR heightmaps are imported as textures and keep their floats.

## Making and changing the ground

Never write the images by hand or with code of your own. The terrain tools of
joinery-paint run the terrain's operations in Godot, record a recipe and stage
the change for the user's review:

- `terrain_new(name, size, resolution, min_height, max_height, seed, ops)` makes
  `art/terrain/<name>/height.png` and `mask.png`, and returns the node settings.
- `terrain_edit(name, ops)` adds operations to the terrain's log and makes both
  images again.
- `terrain_info(name)` lists the terrains, their operations and node settings.

Operations, in order; positions are `[x, z]` metres in the terrain's space
(centred), heights metres above the node; brushes take `at`, `radius` and
`falloff` (the share of the radius that fades out):

| Op | Fields | Does |
|---|---|---|
| `noise` | `amplitude`, `scale`, `octaves`, `seed`, `ridged`, `offset` | adds fractal noise (hills; `ridged` for ridges) |
| `raise`, `lower` | brush, `amount` m | lifts or sinks an area |
| `flatten` | brush, `height` m (default: the ground at `at`) | levels an area: a plaza, a building plot |
| `smooth` | brush or none (whole terrain), `strength`, `iterations` | softens bumps |
| `erode` | `iterations`, `talus`, `amount` | slides steep slopes down into screes |
| `path` | `points`, `width`, `falloff`, `depth`, `paint` | eases the ground along a road or trail, and paints it |
| `paint` | brush, `channel` (r, g, b, a), `value` | paints the mask (0 erases) |

Example: a valley with a level camp and a dirt track painted as layer 1.

```json
[{"op": "noise", "amplitude": 6, "scale": 40},
 {"op": "flatten", "at": [0, 0], "radius": 12, "height": 2},
 {"op": "path", "points": [[0, 0], [20, 30], [45, 40]], "width": 4, "paint": "r"},
 {"op": "paint", "channel": "a", "at": [-30, 20], "radius": 18}]
```

The same document always makes the same bytes, so `joinery rebuild` checks the
images exactly. A heightmap the user paints in Krita can be the start
(`terrain_new(base="art/…/height.png")`); the operations then work on top of it.

## Layers

`TerrainLayer`: `albedo` (a texture the project made: Krita, Material Maker, a
Blender bake; none means plain `color`), `color`, `uv_scale` (repeats per metre),
`roughness`, and for layers 1-3 `use_rule` with `height_range` (metres) and
`slope_range` (degrees). Layer 0 is the ground under everything; each later
layer goes on top where the mask paints it (r, g, b for layers 1-3) or, with
`use_rule`, where the ground is inside its bands: rock on slopes over 35°,
snow above 25 m.

## Scatter

`TerrainScatter`: `scene` (a GLB or scene from the project's kits; every mesh
in it is drawn) or `mesh`, `density` (per 100 m²), `placement_seed`,
`mask_channel` (-1 everywhere, 0-3 = r, g, b, a) with `mask_threshold`,
`height_range`, `max_slope`, `scale_range`, `random_turn`, `align_to_ground`,
`sink`, `visible_distance`. Instances are MultiMeshes per chunk with no
collision; trees or rocks the player must bump into belong in the scene as
scenes (the `scatter` tool of joinery-blender-ops places those).

## Playtester

The node is in the `playtest_state` group. Its state: the grid, chunks, which
LODs the current camera draws, triangles per LOD, scatter counts, and how many
checks found a player standing on it (`checks_on_surface`) or below its surface
(`checks_below_surface`, which should stay 0: falling through the ground).

## Alternatives

Terrain3D (a C++ GDExtension with clipmaps and in-editor sculpting),
ProtonScatter and Spatial Gardener (editor scatter add-ons) are larger options,
all MIT. They are listed in `policy/terrain_alternatives.toml` with no hash, so
Joinery will not install them; the user can add one as a custom tool.
