# 0009: Recipes, reproducibility, and the tool round-trips

Status: accepted, 2026-11-07

## Context
Rule Zero says an asset that cannot be rebuilt from its recipe does not ship.
That needs a recipe format, a way to replay it, and a way to decide whether the
replay produced the same asset. Phase 0 also has to prove each tool round-trip
in the asset table with a real asset and a measured latency.

## Decision

### Recipes
A recipe is `recipes/<name>.toml`: a tool, the script it runs (the operation
log), the outputs, and a fingerprint per output. `joinery rebuild` replays it
and compares; `joinery lock` derives `joinery.lock` from what the recipes claim.
Hashes are measured, never hand-written, for the same reason the sandbox
checksums are: a guessed hash verifies nothing while looking verified.

**Replay happens in a scratch copy, never in place.** A rebuild rewrites its
outputs, and a `.blend` is not byte-reproducible, so rebuilding the working tree
would leave every lock hash stale even when the recipe is perfectly correct.
Verification must not dirty the thing it verifies.

**The host orchestrates, the container executes.** `joinery` runs outside the
sandbox and drives tools inside it with `--network none` (`joinery/runner.py`).
That keeps the Rule Zero boundary intact and means the image needs no Python
environment of its own.

### Fingerprints: as exact as the format allows
Not every format can be compared byte for byte, so each output is checked the
strictest way that is actually stable, and the prefix records which:

| Prefix | Used for | Why |
|---|---|---|
| `sha256:` | WAV, baked PNG, MIDI, text | Deterministic; compared exactly |
| `blend:` | `.blend` | Blender writes pointers and timestamps, so bytes differ while the model is identical. Compares objects, topology counts, materials, modifiers, rounded transforms |
| `img:` | camera renders | Cycles renders are not bit-exact (below). Downsampled to 32x16 and quantised to 4 bits, which survives sampling noise but catches real change |

A recipe must name its camera renders in `renders`, so the loose check is always
a visible choice. Asking for it on a non-image is an error, which stops `img:`
being used to dodge an exact comparison.

## Measured
Sandbox runner, amd64 emulated on an arm64 host, so native CI will be faster.

| Round-trip | Asset | Build | Verify |
|---|---|---|---|
| Blender -> `.blend` -> Godot native import | door (leaf + handle, bevel, shader-node wood) | 6.1 s | 3.1 s |
| Blender -> `.blend` -> Godot | room kit (floor, ceiling, 4 walls, boolean doorway) | 4.0 s | 3.1 s |
| Blender shader nodes -> bake -> PNG | stone tiles, 512 px albedo + roughness | 7.6 s | 29.5 s |
| Blender sky model -> equirect render -> PNG | dusk sky, 1024x512 | 3.6 s | 9.3 s |
| SuperCollider -> NRT render -> WAV | door creak + close, 48 kHz mono | 2.7 s | 2.7 s |
| SuperCollider -> NRT render -> WAV | 4-bar loop, 84 BPM, 11.4 s stereo | 3.9 s | 2.8 s |
| Godot headless import of all of it | 8 resources | 7.4 s | — |

Whole-testbed rebuild: 6 recipes, all verified, about 55 s.

Godot imports the `.blend` files natively and a script instantiates them:
`root=door children=Door_Wood_Dark:MeshInstance3D`. The assembled scene loads
with the sky, material and audio wired.

## What reproducibility actually cost
Three things were not reproducible until they were forced to be, and each was
found by running the rebuild twice rather than by reasoning about it:

1. **`.blend` bytes differ every save.** Two runs of one script gave
   `c15f53f8…` and `df54fa2d…` for an identical model. Hence `blend:`.
2. **Synth renders need a seeded server RNG.** The SFX patches use noise UGens,
   which seed themselves per run, so renders differed musically-identically.
   `RandID.ir(0)` plus `RandSeed.ir(1, …)` fixed it, and the WAVs are now
   byte-identical.
3. **Cycles renders are not bit-exact, and cannot be made so.** Adaptive
   sampling and thread count were the first cause; fixing the seed, disabling
   adaptive sampling and rendering single-threaded made the material *bake*
   exact. A *camera render* still varies with all of that pinned, plus
   `sun_disc` off and `filter_size` 0, under every one of the four sky models
   (`sampling_pattern` no longer exists in 5.2). A bake has no camera and is
   exact; a render is not. Hence `img:`, and only for renders.

## Consequences
Godot's native `.blend` import needs the Blender *executable* path in an editor
setting it refuses to configure in headless mode, so the sandbox bakes it in.
The error says "Blender path is invalid or not set" and not that it wants the
binary rather than its directory, which is worth knowing.

A `.blend` that holds no mesh, like the sky source, has nothing for Godot to
import, so render-only sources live behind a `.gdignore`. `.gdignore` and
`.import` are Godot's own files and are not assets.

Single-threaded deterministic rendering is slower: the material bake went from
7.6 s to about 30 s. Worth it, since an unverifiable asset is not shippable.

The `img:` check is deliberately the weakest link. It is scoped to camera
renders, and the `.blend` beside such a render is still verified exactly, so a
changed sky *source* is caught precisely even where its PNG is not.
