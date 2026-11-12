# Hammer fixture: provenance

origin: tool-created (Blender 5.2.1 in the offline `joinery-creative-worker`, no network)

Made by operating Blender, from the committed recipe in this folder. No file was
downloaded and no generative model was used.

- `hammer_model.py`: the operation code. It builds two primitives (a cylinder
  handle and a cube head named `Head.001`), two node materials (Steel and Wood)
  and five 32 × 32 images whose pixels are written by formula (ramps, a sine
  pattern for the height map, a packed ORM map).
- `build.py`: wraps that code in Joinery's own Blender recipe script (save,
  texture export, GLB export with `joinery_name` / `joinery_maps` extras) and
  runs it in the worker. It then records Blender's part inspection as
  `parts.json`.

Rebuild with `uv run python tests/fixtures/parts/build.py`. Outputs:

| File | Made by |
|---|---|
| `hammer.blend` | Blender, saved by the recipe script |
| `hammer.glb` | Blender glTF exporter (export_extras on) |
| `textures/*.png` | Blender, the texture export Joinery writes beside a model |
| `parts.json` | Blender part inspection (`joinery.tools.blender.INSPECT_BLEND`) |

Blender saves can differ byte for byte between runs, so the hashes change on a
rebuild. The tests check names, roles and picks, never these bytes.

# Character fixture: provenance

origin: tool-created (Blender 5.2.1 with MPFB2 and Rigify in the offline
`joinery-creative-worker`, no network)

- `character_model.py`: the operation code. MPFB2 builds its default human from its own
  base mesh (no download, no generative model), adds its Rigify meta-rig with MPFB2's
  weights, Rigify generates the rig, and two shape keys (`smile`, `viseme_aa`) are made by
  moving the mouth's vertices a few millimetres.
- `build_character.py`: wraps that code in Joinery's own Blender recipe script (the GLB
  export bakes MPFB2's body-shape keys and writes `joinery_name`, `joinery_role` and
  `joinery_regions` extras) and records Blender's part inspection as
  `character_parts.json` (Rigify's `WGT-` control shapes left out). The .blend is not
  kept; the widget tests read only the GLB and the inspection.

Rebuild with `uv run python tests/fixtures/parts/build_character.py`.

| File | Made by |
|---|---|
| `character.glb` | Blender glTF exporter (export_extras on) |
| `character_parts.json` | Blender part inspection (`joinery.tools.blender.INSPECT_BLEND`) |
