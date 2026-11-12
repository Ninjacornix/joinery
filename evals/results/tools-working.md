# Every catalog tool, end to end (2026-11-10)

> Update (2026-11-12): Audacity is back in the catalog in its own offline
> worker ([ADR 0082](../../docs/adr/0082-audacity-worker.md)). The stall below
> was a plugin check in a child `audacity --host` process that replaced the
> script pipes under the running Audacity, plus a stale X lock left in the
> image. Measured on macOS arm64 under amd64 emulation: the worker's own layers
> build in 42 s (no cache, warm base) and add about 37 MB (compressed content)
> to `joinery-mcp-worker`; 26 of 26 fresh starts answered within 5.8 s;
> `tests/integration/audacity_native.py` passes in 30 s, with the `.aup3`, WAV
> mixdown and label file identical across recipe rebuilds, separate runs and
> image rebuilds. Native amd64 CI has not run it yet.

One real run per remaining catalog tool against disposable projects: create a
native source, edit it, export, replay every recipe from scratch, and read the
tool's readiness from the shared core (`joinery/readiness.py`). No model calls,
no accounts; every worker command ran with `--network none`. Fixtures are
technical documents in temporary directories; nothing approved art direction or
touched a user's game.

**Result: 21 of 21 probes pass; all 8 catalog tools report `ready`.** Five
failures were found on the way and fixed (below). Aseprite, REAPER, MuseScore
and Audacity are out of the catalog (ADR 0025) and were not probed; music is
MIDI rendered through FluidSynth and Surge XT, and audio edits are ffmpeg ops
in `joinery-sound`.

Host: macOS arm64, Docker 28.3.2 (8 CPUs, 8 GB), workers are `linux/amd64`
under emulation, so times are slow-machine times. Images were rebuilt for this
run (see failure 1): `sandbox/build.sh --all --tag joinery-sandbox-runtime`, then
the mcp, creative, blender and inkscape worker Dockerfiles, 508 s with layer cache.

## Readiness (shared core)

`uv run python -m joinery.readiness --project <disposable> --catalog --refresh --deep`,
78 s, every tier including the deep MPFB2 human check and the MCP probes.

| Tool | State | Version | Summary (as every agent and the addon see it) | Features | Desktop "Open in" |
|---|---|---|---|---|---|
| Godot | ready | 4.7.2 | Godot 4.7.2 tools ready (offline worker) | – | this editor |
| Blender | ready | 5.2.1 | Blender 5.2.1 ready (offline worker). Rigify, MPFB2 humans, official Blender MCP connected. | rigify, mpfb, mcp: ready | not found (informational) |
| Krita | ready | 5.2.9 | Krita 5.2.9 ready (runs offline in the creative worker) | – | not found |
| GIMP | ready | 3.0.4 | GIMP 3.0.4 ready: edits .xcf layers and channels offline; exports PNG | – | /opt/homebrew/bin/gimp |
| Inkscape | ready | 1.4 | Inkscape 1.4 ready (offline worker). Analysis tools. | analysis: ready | not found |
| Material Maker | ready | 1.8 | Material Maker 1.8 ready (offline worker) | – | not found |
| SuperCollider | ready | 3.13.0 | SuperCollider 3.13.0 ready (offline renders). Live audition. | live-audition: ready | not found |
| Surge XT | ready | 1.3.4 | Surge XT 1.3.4 ready: renders offline in the creative worker. MIDI music (General MIDI and Surge XT), audio edits (trim, fade, loudness, loops). | music, audio-edit: ready | – |

Before the fixes the same command reported GIMP `broken` ("GIMP failed to
start: env: 'gimp-console': No such file or directory") and Godot
`needs_image` ("Godot tools: not installed. Run Joinery setup.").

MCP probe (`tests/integration/mcp_catalog.py`, 26 s): all 10 configured servers
discovered and answered a real call: godot 157 tools (`get_project_info`),
blender 26 (`get_blendfile_summary_datablocks`), inkscape 8
(`inkscape_system`), claude-collider 9 (`cc_status`), joinery-registry 5,
joinery-blender-ops 4, joinery-paint 4, joinery-sound 9, joinery-materialmaker 3,
joinery-verify 1.

## Probes

All commands are `uv run python tests/integration/<file>`. "Replay" means
`rebuild_one` in a disposable copy with the outputs deleted, each output
fingerprinted against the recipe.

| Tool | Probe | Create · edit · export | Replay | Result | s |
|---|---|---|---|---|---|
| Blender | `creative_updates.py blender` | Two-part assembly through the real Blender MCP SDK; move/rotate/scale only `Head`; `Grip` and topology kept; GLB | clean | PASS | 30 |
| Blender | `native_documents.py blender` | Generated albedo painted, repainted, untouched: 0.25 → 0.75 → 0.75 in the .blend and the PNG | clean | PASS | 32 |
| Blender + MPFB2 | `native_documents.py blender-mpfb` | MPFB2 human (> 10 000 vertices) through a recipe, GLB | clean | PASS | 17 |
| Blender + MPFB2 + Rigify | `blender_rig.py` (new) | Human + `rigify.human_toes` meta-rig with weights, generated rig: 1 090 bones, 209 deform; GLB skin = exactly the 209 `DEF-` bones; 19 158 source vertices → 14 517 exported (helpers out), 8 shape keys kept as morph targets; `Wave` action on `upper_arm_fk.L` exports as 209 deform-bone channels; crate with Subdivision + `Dent` → 384 vertices, 1 morph target | 2 recipes clean | PASS | 40 |
| Blender + Krita + Material Maker | `part_edits.py` (new, through the MCP server) | Hammer: `steel_rough` made a Krita document (map pixels identical, nothing in the model changed), painted with a fill, a vector stroke and a levels filter layer (only `image:steel_rough` changed; GLB roughness texture follows); `wood_base` replaced with the Krita export (pixels equal); `steel_base` made a Material Maker graph (albedo export linked); Steel split for `Head.001` (changed: `object:Head.001`, the new material and its 3 copied maps), then inverting the copy's map changed only that map; `Dent` set/renamed, a key made from the mix and deleted (GLB targets `["Dented"]`). Arm rig: `Mid` → `Elbow` (only `Rig` and the skinned `Arm`'s vertex groups changed), rest/parent/scale-chain change only `Rig`, GLB joints follow; a key from the bent pose. Godot imports both GLBs without an error | 4 current recipes clean (Blender, Krita, Material Maker) | PASS | ~600 |
| Blender kits | `kits.py` (new, through the MCP server; 2026-11-11) | Seven kits × defaults, every parameter at its lowest and at its highest (21 models, 3.8–5.0 s each): rock 80/320/5 120, crate 276/396/780, barrel 152/356/776 (reduced from 812 to the 800 `art/props` budget), fence 78/144/1 572, hut 50/122/696, tree 196/548/296, bush 80/368/940 triangles; every GLB inside its folder's budget, bottom-centre within 2 cm, scale 1, every material colour a palette colour; LOD1 GLBs under the main count. `kit_batch` 10 rocks in one Blender run (5.0 s): all different, sizes 0.78–1.23 m; `kit_edit` mossier and smaller (4.7 s): same 10 names, every GLB changed, old recipe to history; an unchanged edit says so. Bible submit checks clean | 23 recipes, all fingerprints equal (GLB bytes included), 197 s | PASS | 363 |
| Scatter | `kits.py` | 20 of the rocks on a 30 m Blender hill beside a Path3D: 1.5–5.5 m from the path, on the ground (± 0.15 m), tilted to it; the same call writes the same file; Godot imports every model and loads the scene with 20 instances (13.8 s) | – | PASS | 0.06 |
| Krita | `creative_updates.py krita` | Exact red/blue pixels, layers kept, `.kra` + PNG, review reject/accept/revert | clean | PASS | 76 |
| Krita | `native_documents.py krita` | New document, added paint layer read back by inspect | clean | PASS | 35 |
| GIMP | `gimp_native.py` | `.xcf` with `height`/`albedo` layers and `left_half` channel; edit only `height` inside the channel (left 128 → 255, right 128, albedo byte-identical); per-layer PNG exports; failing script and unknown layer refused, nothing recorded | 2 recipes byte-identical (`.xcf` and PNGs) | PASS | 130 |
| Inkscape | `creative_updates.py inkscape` | SVG element id kept, colour changed, exported PNG pixels checked, review | clean | PASS | 10 |
| Material Maker | `creative_updates.py material-maker` | Connected graph kept, red/blue texture pixels, roughness changed, exports overwritten, review | clean | PASS | 41 |
| Material Maker | `native_documents.py material-maker` · `materialmaker_parts.py` | Graph save/export; image-node input recorded in recipe sources, relative `.ptex` path, unrecorded input refused | clean | PASS | 15 · 13 |
| SuperCollider | `creative_updates.py supercollider` · `native_documents.py supercollider` · `supercollider_native.py all` | Audible 440/660 Hz NRT render, UTF-8 source, fail-fast on a broken patch, live audition records outside the project with no bundled samples | repeatable | PASS | 13 · 6 · 21 |
| Surge XT | `surge_native.py` | Patch → WAV (RMS 0.057), darker cutoff (RMS 0.045), one-parameter edit keeps the rest; Surge-only project starts `joinery-sound`; unknown parameter refused | clean | PASS | 68 |
| MIDI | `midi_render.py` | Song: FluidR3_GM piano + drums, Surge XT bass; exactly 192 000 samples, loop tags, −16.0 LUFS; transpose/program edit; imported `.mid` rendered unchanged; art bible audio rules clean | 4 recipes byte-identical | PASS | 135 |
| ffmpeg ops | `midi_render.py` (Surge renders) · `audio_edits.py` (new, through the MCP server) | Trim silence (1.000 → 0.608 s), fade from silence, −18.0 LUFS under −1 dBTP; second edit → looping stereo 44.1 kHz Ogg (LOOPSTART 0, LOOPLENGTH 17 640, `loop=true` import); 5 refusals (imported file, empty fade, outside `audio/`, escaping path, step after loop) change nothing | byte-identical | PASS | 135 · 17 |
| Godot MCP | `mcp_round_trip.py` | Real upstream Godot MCP: scene create/add/read, preview, reject/accept | – | PASS | 29 |
| Cross-tool | `cross_tool.py` | Blender source → GLB → Godot import and instance → review/accept/revert | – | PASS | 35 |

## Failures found and fixed

1. **GIMP was missing from every worker.** The runtime image had been rebuilt
   with an explicit tool list that left out GIMP (`WITH_GIMP=0` in the build
   history), so `gimp_native.py` failed at once with `env: 'gimp-console': No
   such file or directory`. Fixed by rebuilding the images the way `joinery
   setup` does (`build.sh --all`); GIMP 3.0.4 is back in the creative worker.
2. **That missing program was reported as `broken`, not `needs_image`.** The
   GIMP module already maps a failed `gimp-console --version` to "rebuild", but
   the Python-Fu probe after it failed too and ranked worse. The readiness core
   now lets the first failing check decide its scope (the tool, or one feature)
   and skips the later checks in it, as the plan's "first failing check's
   message" rule says. The user now sees "GIMP is missing from the creative
   worker; rebuild it". Test: `test_gimp_missing_from_the_worker_needs_a_rebuild_not_broken`.
3. **The Godot MCP install had no recorded source hash.** Readiness correctly
   said "Godot tools: not installed. Run Joinery setup."; the pinned install
   (88f0fbc) was redone with Joinery's own installer and the tool turned `ready`.
4. **A second audio edit on a SuperCollider render never ran.** Both chain
   levels wrote their ffmpeg program to the same `/tmp` file, and the outer
   level's ShutDown action ran before the inner one, so `process_audio` with an
   `.ogg` export after an in-place edit failed with "did not write
   audio/sfx/tone.ogg". Each level now writes its own file (named by content)
   and puts its action first, so the inner edit runs first
   (`joinery/tools/audio_chain.py`). Tests: `audio_edits.py`,
   `test_nested_supercollider_edits_run_inner_first_from_their_own_files`.
   Surge XT chains were not affected.
5. **Blender GLB exports ignored modifiers.** The exporter ran without
   `export_apply`, so Godot got the unmodified mesh: an MPFB2 human shipped its
   helper geometry (hidden in Blender by a Mask modifier) as unweighted
   vertices, which also added a `neutral_bone` joint (210 joints for 209 deform
   bones), and a Subdivision or geometry-nodes model would export as its cage.
   Exports now apply modifiers. The exporter drops shape keys of a mesh whose
   modifiers it applies, so after the `.blend` is saved Joinery bakes such
   meshes once per shape key and keeps every key (it refuses, with a reason,
   when the vertex count differs per key). The saved source keeps its
   modifiers. Measured: 14 517 exported vertices instead of 21 833, 209 joints,
   8 morph targets kept; the crate keeps `Dent`.
6. **Rigify control shapes reached the GLB and failed the art bible.** A
   generated rig keeps ~280 face-less `WGT-` meshes as bone custom shapes,
   unparented and scaled to their bones. The GLB carried each as an empty node,
   and the art bible's name and applied-scale rules judged them as the model, so
   every MPFB2 + Rigify character failed its submit checks (the live "bouncier
   run" smoke: two sent-back rounds, about 10 minutes). The export now removes
   face-less custom shapes after the save, and the `.blend` measure flags them so
   the checks skip them (`joinery/tools/blender_export.py`,
   `joinery/bible_blend.py`, `joinery/bible_rules.py`). Measured: 282 widgets
   left out, 493 → 211 GLB nodes (209 deform bones, body, rig). The body is
   still judged: MPFB2's mesh data name `base` fails a bible naming pattern,
   so the test hero names its mesh. Smoke after: one round, review 175 s after
   sending, checks 3.6 s. Tests: `blender_rig.py`, `tests/test_rig_checks.py`.

Also corrected: `tests/test_authoring_mcp.py` still expected `joinery-sound` to
expose only the three generic tools; it now lists the six typed sound
operations (`inspect_music` read-only).

## Still open

- `joinery rebuild` cannot replay GIMP recipes yet: `joinery/recipe.py`
  `_tool_argv` has no GIMP branch (and still has a dead Aseprite one).
  `gimp_native.py` replays through `tools.get("gimp").replay_argv`, which is
  byte-identical; dispatching `_tool_argv` through the tool modules closes it.
- (Done since: MPFB2's macro shape keys are baked into the exported mesh by
  default; `blender_rig.py` now checks 8 → 0 morph targets, 3.25 MB → 1.07 MB
  GLB, and 8 kept with `keep_macro_shape_keys`, remembered by the next edit.)
- `paint_map` has no brush engine: Krita 5.2.9's scripting has no headless,
  reproducible brush API, so strokes are vector paths. No masks or
  selections yet beyond a fill rectangle.
- A map made a Material Maker graph starts as an image node into the
  material; the graph is changed with `operate_graph` and relinked with
  `replace_map`, not with a typed graph operation.
- `make_map_editable` and `paint_map` run Krita while the gateway's slot holds
  "blender-ops", so a parallel Krita task is not told to wait for them.
- The default suite has 5 failures outside the tool packages
  (`tests/test_fork_patches.py` for patches 0012/0013 and
  `tests/test_ui_tokens.py`), from editor UI work in progress in the same tree.
- Desktop apps: only GIMP and the Joinery editor are installed on this host;
  every other "Open in" entry is informational and editing works offline.
