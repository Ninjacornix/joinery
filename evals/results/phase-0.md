# Phase 0 report

Date: 2026-11-07. Sandbox runner, amd64 emulated on an arm64 host, so the
timings below are an upper bound on native CI.

## Gate

> Every Testbed asset rebuilds from its recipe in CI; editing each source
> updates the running Godot editor.

**First half: met.** Six recipes, ten assets, all replayed and verified.

```
rebuilding 6 recipe(s) via the sandbox runner, in a scratch copy
  [ok ] door.toml                       3.1s
  [ok ] material_stone.toml            29.5s
  [ok ] music_explore.toml              2.8s
  [ok ] room.toml                       3.1s
  [ok ] sfx_door.toml                   2.7s
  [ok ] sky.toml                        9.3s
rebuild: ok
```

CI runs this, plus a check that `joinery.lock` still matches what the recipes
produce, so an asset cannot change without its lock entry.

**Second half: partly met.** Godot imports every source headlessly and
reimports on change, which is the mechanism the editor uses. That it updates a
*running editor window* is unproven: there is no sidebar or plugin yet, and
nothing GUI has been exercised. Suggested split in the plan below.

## What works

| Round-trip | Proven by | Build | Verify |
|---|---|---|---|
| Blender -> `.blend` -> Godot native import | door, room kit | 6.1 s / 4.0 s | ~3 s |
| Blender shader nodes -> bake -> PNG | stone tiles 512 px | 7.6 s | 29.5 s |
| Blender sky model -> equirect render | dusk sky 1024x512 | 3.6 s | 9.3 s |
| SuperCollider -> NRT -> WAV | 2 door SFX | 2.7 s | 2.7 s |
| SuperCollider -> NRT -> WAV | 4-bar loop, 84 BPM | 3.9 s | 2.8 s |
| Godot headless import | 8 resources, scene assembled | 7.4 s | — |

Everything is made by the tools' own features: Blender mesh ops, modifiers,
`TexBrick`/`TexNoise`/`TexSky` nodes and `object.bake`; SuperCollider UGens.
No model, no generative service, no fetched asset. The sandbox runs
`--network none`, so none is reachable even by accident.

Details and the reproducibility findings are in ADR 0009; the sandbox itself is
ADR 0008.

## What is missing

**MPFB2 character with rigify.** Not attempted. It is the heaviest asset in the
list and wants the typed-op layer that Phase 3 is for.

**The 2D asset (Krita UI panel).** Not delivered, and the reason is structural
rather than a missing hour:

- **Krita** has no headless authoring path. Its Python API needs the running
  GUI; there is no `--run-script`. Joinery's own Krita MCP is still
  first-party-planned, so there is nothing to drive it with either.
- **GIMP 3.0.4** was the obvious substitute and does not work: `gimp-console`
  in batch mode hangs, reporting it is "running as a background process" while
  the script-fu plug-in never returns. GIMP 3 changed the script-fu API from
  2.10 as well, so existing recipes do not port directly.
  *Correction (2026-11-10):* the hang was a missing flag, not a GIMP defect.
  Without `--batch-interpreter` and `--quit`, GIMP 3 prints "No batch
  interpreter specified" and idles. With them it runs headless and fails fast
  on errors. Joinery now edits GIMP `.xcf` documents through a Python-Fu
  adapter (`tests/integration/gimp_native.py`); the 2.10 Script-Fu
  `testbed/recipes/ui_panel.scm` was deleted.
- **Inkscape** is in the image and is reliably headless, but it rasterises a
  document rather than authoring one, so it does not fit the current
  one-tool-one-script recipe shape without a change.

So the 3D, audio and engine pipelines are proven and the 2D one is not. Called
out rather than papered over, per the honest-refusal rule.

**Material Maker.** Not used: ADR 0003 already makes Blender the material tool
for now, so the material is shader nodes plus a bake. The plan's asset table
still says Material Maker.

**MCP servers.** Still stubs. Round-trips were proven by driving the tools
directly, which was the point of a spike; the typed-op layer on top is later
phases.

## What reproducibility cost

Three things were not reproducible until forced, each found by running the
rebuild twice rather than by reasoning:

1. `.blend` bytes differ on every save for an identical model
   (`c15f53f8…` vs `df54fa2d…`), hence a semantic signature.
2. Synth patches reseed their noise per run. `RandSeed.ir` made the WAVs
   byte-identical.
3. Cycles **camera renders** cannot be made bit-exact: seed, thread count,
   adaptive sampling, sun disc and pixel filter all pinned, every sky model
   tried, still varies. A *bake* has no camera and is exact. Renders therefore
   get a documented coarse check, scoped to renders only.

Determinism is not free: the material bake went from 7.6 s to 29.5 s once it
rendered single-threaded.

## Recommended plan changes

1. **Asset table: materials.** Replace the Material Maker row with Blender
   shader nodes plus bake, matching ADR 0003, and move Material Maker to the
   phase where its server actually exists.
2. **Asset table: music.** The plan wants `.mid` plus SoundFont. SuperCollider
   covers composition and rendering today with one enabled tool, and FluidSynth
   with the GM SoundFont is proven in the image. Allow either, and defer the
   MIDI-first path to the sound phase with `servers/sound`.
3. **2D path.** Decide between: wrap Krita's GUI process under xvfb (keeps the
   intended tool, costs a live-process server), or make Inkscape the headless
   2D path and keep Krita for live manual work. This blocks the 2D pipeline
   either way and is worth settling before the 2D phase.
4. **Character.** Move MPFB2 plus rigify out of Phase 0 and into the Blender
   phase, where the op library and the character work already live.
5. **Add a reproducibility rule to the conventions.** Recipes must be
   deterministic: seed every RNG, pin thread counts where output depends on
   them, and declare a camera render explicitly so its looser check is visible.
   This is cheap to state and was the single biggest source of rework here.
6. **Split the gate wording.** "Editing a source updates the running editor"
   should be two items: headless reimport, verifiable in CI now, and live
   editor reimport, which belongs with the sidebar work.
7. **Note the Godot/Blender coupling.** Native `.blend` import needs the
   Blender executable path in an editor setting that cannot be set headlessly.
   The sandbox bakes it in; a user on stock Godot has to set it themselves, or
   the project ships glTF beside the `.blend`. Worth an explicit decision.

## Numbers

| Metric | Value |
|---|---|
| Recipes | 6 |
| Locked assets | 10 |
| Recipe reproducibility | 6/6 |
| Whole-testbed rebuild | ~55 s emulated |
| Sandbox image | 4.71 GB with Blender, SuperCollider, Material Maker, Krita, GIMP, Inkscape |
| Tests | 175 |
| Testbed asset categories delivered | 6 of 8 |
