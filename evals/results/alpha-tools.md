# 0.1 alpha creative tool verification

Date: 2026-11-08. Host: macOS ARM64; native creative applications execute in
the offline `linux/amd64` worker under emulation. The native application fixtures
below used no model calls; the separate Codex workflow section records its three
real model turns. Neither used downloaded art, generative assets, licensed app
installations, or changes to the user's game project/art approval. All new
documents were disposable technical fixtures made through native APIs.

## Verified supporting tools

| Tool | Actual operation and evidence | Result |
| --- | --- | --- |
| Krita 5.2.9 | Paint a red layer in a 16×16 native `.kra`, retain it while adding a blue layer; check exact exported PNG pixels and both layer names | Create, edit, clean replay, reject, accept, revert pass |
| Inkscape 1.4 | Create/edit a self-contained 32×24 SVG rectangle with stable element ID; export PNG and check exact red/blue pixels | Create, edit, clean replay, reject, accept, revert pass |
| Material Maker 1.8 | Native `.ptex` graph connects Uniform color to Material albedo; check actual red/blue exported PNG pixels and `.tres` roughness 1.0→0.25 | Create, edit, clean replay, reject, accept, revert pass |
| SuperCollider | Native UTF-8 `.scd` score renders audible 48 kHz mono 16-bit WAV through offline NRT synthesis; change score from 440 to 660 Hz and require changed audio bytes | Create, edit, clean replay, reject, accept, revert pass |

These lanes support layered UI/sprite work, vectors/icons, PBR material textures,
and synthesized SFX/music. They are meaningful native integration checks, not a
claim that arbitrary art direction, every graph node, every audio instrument, or
all third-party MCP operations have been evaluated.

Permanent reproduction:

```sh
docker build --platform linux/amd64 -t joinery-creative-worker -f sandbox/creative.Dockerfile sandbox
uv run python tests/integration/creative_updates.py all
uv run python tests/integration/native_documents.py krita
uv run python tests/integration/native_documents.py material-maker
uv run python tests/integration/native_documents.py supercollider
uv run joinery rebuild --project testbed --runner sandbox
uv run pytest -q tests/test_recipe.py tests/test_authoring_outputs.py tests/test_locking.py tests/test_lockfile.py tests/test_workspace.py tests/test_gateway.py
uv run ruff check sandbox/krita_worker.py tests/integration/creative_updates.py joinery/authoring.py joinery/recipe.py tests/test_recipe.py tests/test_authoring_outputs.py
uv run mypy joinery/authoring.py joinery/recipe.py
```

The richer four-tool run passed, then Material Maker was strengthened with a
connected real texture graph and SuperCollider with exact UTF-8 source content;
both strengthened individual lifecycles also passed. Focused regression suite:
**65 passed in 1.40s**. Ruff and mypy passed. Existing testbed clean replay:
**6/6 pass** (door 3.6s, stone material 28.3s, music 2.3s, room 3.2s,
door SFX 2.3s, sky 8.6s). Times measure this emulated local worker, not an
interactive response-time guarantee.

Executed worker image:
`sha256:44f819a13804baa0486cac449a94d0840f0160b82a5dfbc5d2359dbd13b50a0a`.
Local raw evidence: `/tmp/joinery-alpha-clean-creative-updates.log`,
`/tmp/joinery-alpha-material-textured-lifecycle.log`,
`/tmp/joinery-alpha-sc-clean-utf8.log`,
`/tmp/joinery-alpha-clean-testbed.log`, `/tmp/joinery-alpha-tool-regressions.log`.
The final 65-test invocation is also recorded in the agent execution transcript.

## Real blockers found and fixed

1. **Existing Krita edits created untracked backups.** Before the fix, run
   `uv run python tests/integration/creative_updates.py krita`: the second native
   save produces `art/fixture.kra~`, and the provenance gate rejects it. The older
   blank-document fixture did not cover this. `sandbox/krita_worker.py:43` now
   sets `CreateBackupFile=false` in the disposable application's unnamed config
   group. This is the native setting read by [Krita's KisConfig](https://github.com/KDE/krita/blob/v5.2.9/libs/ui/kis_config.cc#L1242).
   No backup extension was exempted from provenance. Worker completion reporting
   now uses atomic rename, preventing the supervisor from reading a partial
   acknowledgement. An intentional native Python exception was also verified to
   report its original cause, fail the operation, and remove the owned worker.

2. **Material Maker skipped overwriting exported resources.** Before the fix,
   create a material at roughness 1.0, then update to 0.25 using the permanent
   fixture: native `.ptex` changes but existing `.tres` remains at 1.0.
   [Material Maker 1.8's command-line export path](https://github.com/RodZill4/material-maker/blob/1.8/addons/material_maker/engine/nodes/gen_material.gd#L650)
   puts files requiring overwrite confirmation aside without adding them back
   when `command_line=true`. `joinery/authoring.py:409` turns off that native
   profile's overwrite prompts in the staged export process before export.
   Existing files belonging to unrelated sources are preserved.

3. **A no-op recipe could falsely pass replay.** Previously old declared outputs
   remained in the copied project. `joinery/recipe.py:282` now makes a disposable
   copy and removes only confined declared regular-file outputs before execution.
   Preimage/source inputs remain available. It refuses output/input overlap,
   external links, and absolute links that would route scratch writes back into
   the original project. `tests/test_recipe.py:143` reproduces a no-op against an
   existing matching file and now requires failure, with the original untouched.
   The regression at `tests/test_recipe.py:198` checks the scratch-link escape.

4. **SuperCollider source could not recreate itself in clean replay.** The host
   previously wrote `.scd` while the logged operation only rendered its WAV.
   `joinery/authoring.py:242` now logs a self-contained native wrapper that writes
   the exact UTF-8 source then interprets it offline. Declared native source and
   audio outputs are both genuinely regenerated after removal.

5. **Export ownership was too broad.** Previously a Material Maker operation
   claimed every file in the export directory. `joinery/authoring.py:452` now
   compares before/after file hashes and includes changed/new files plus unchanged
   deterministic outputs already owned by a recipe for the same native source
   and tool. `tests/test_authoring_outputs.py:13` verifies an unrelated unchanged
   material remains unclaimed and byte-identical.

## Readiness and honest limits

- Exact pinned MCP discovery/connection probes succeeded for Godot and Inkscape.
  SuperCollider's pinned server connects; its interactive device inspection was
  not available in the offline worker. First-party registry/paint/sound/material/
  verification bridges connect; connection alone is not a native authoring test.
  Probe evidence: `/tmp/joinery-alpha-mcp-probes.json`.
- GIMP **3.0.4 is installed**. It has a catalog candidate but no first-party
  native authoring adapter in `joinery/authoring.py:23`, and its app-specific
  pinned server installer is absent (`joinery/server_install.py:58`). Do not
  present GIMP editing as an evaluated alpha capability.
- (Later removed from the catalog with REAPER, MuseScore and Audacity; see
  [ADR 0025](../../docs/adr/0025-tool-removals.md).)
  Aseprite has a native adapter but its licensed binary is absent. REAPER,
  MuseScore, and Audacity are absent and their catalog entries do not imply
  complete installers or authoring integrations. Surge XT is planned. No
  unlicensed binary or unvetted server was installed to inflate coverage.
- FluidSynth/FFmpeg are installed supporting CLI tools; this audit did not claim
  a complete MIDI composition or DAW workflow. Blender and Godot existing testbed
  recipes were replayed; deeper scene/editor checks belong to the main audit.
- Material Maker's actual export driver exits successfully with correct outputs
  and no `SCRIPT ERROR`, but emits shutdown/resource/thread and X11 select
  warnings in the disposable process. Evidence:
  `/tmp/joinery-alpha-mm-native-output.log`. Do not describe execution as
  warning-free. Graph changes that remove export channels can leave former
  outputs; clean replay will expose a stale declaration instead of blessing old
  files. Arbitrary channel-removal cleanup is not verified here.
- Debian package signatures authenticate packages, but the current mutable
  `debian:trixie-slim` base and unversioned apt resolution in
  `sandbox/Dockerfile:18` do not pin future rebuilds to byte-identical dependency
  sets. Report the executed image ID and semantic recipe checks; do not imply a
  fully frozen OS package snapshot.

All common transaction, confinement, provenance, dependency, and offline gates
remain in force. Fixture acceptance only applies to the temporary fixture
projects and does not approve any user's art direction or changes.

## Real Codex native prop workflow

`tests/integration/agent_prop_workflow.py` runs **three real Codex turns** in one
persistent session through the actual registry MCP proxy and project gateway.
It mocks only the art-direction response and art-approval check inside its
disposable technical greybox fixture. It writes no approval record and makes no
changes to any user's project. Native authoring, provenance, transaction review,
Godot import and recipe replay remain real.

Executed result: **PASS**, with no provider quota/authentication failures and no
gateway/tool errors. Reproduction (requires an already authenticated Codex CLI
and the local native worker images):

```sh
uv run python tests/integration/agent_prop_workflow.py --metrics /tmp/joinery-alpha-agent-prop-metrics.json
```

The model created `art/sources/technical_prop.blend` with exactly two eight-vertex
cube meshes, `Head` and `Grip`, and no default camera/light; exported
`art/models/technical_prop.glb`; then wrote `technical_prop.tscn` with a
`TechnicalPropLevel` root and one `TechnicalProp` GLB instance. The harness
measured real source geometry before accepting the preview. It then requested
Head's X width change from 1.0 to 1.5 metres on the same registered source.
Rejecting the first update restored the complete project inventory exactly.
Repeating the update and accepting it produced the requested geometry while
retaining Grip and Head's original position. Clean replay regenerated the native
source and GLB after removing declared outputs. Godot reopened the saved scene
and measured the imported Head's X bounds as 1.5 metres.

Final measured native values (floating-point tolerance 0.0001):

| Mesh | Dimensions in metres | Position | Vertices |
| --- | --- | --- | --- |
| Head | 1.5, 0.4, 0.3 | 0, 0, 1 | 8 |
| Grip | 0.2, 0.2, 1.0 | 0, 0, 0.35 | 8 |

The initial accepted assembly additionally passed a separate geometry/position
check and Godot root/instance hierarchy smoke. Replay and smoke helpers operate
in disposable copies, and the harness checks that verification leaves the
accepted project inventory unchanged. Ruff and mypy pass for the fixture.

Latency and actual RPC traffic from this run:

| Model turn | Turn time | RPC counts: discovery / schema / call | Input / output bytes | Native document operation |
| --- | --- | --- | --- | --- |
| Create and instance | 68.306s | 2 / 5 / 4 | 2,647 / 15,844 | 16.763s |
| Update, then reject | 48.772s | 1 / 4 / 4 | 1,631 / 16,459 | 15.303s |
| Repeat update, then accept | 35.725s | 0 / 0 / 3 | 1,066 / 3,360 | 16.319s |

There were **23 RPC requests and zero failed calls or retries**. The final turn
reused schemas and made only source resolution, source inspection and one native
document operation. Some first-turn discovery was broader than necessary: it
read the unused `create_scene` schema before choosing `write_file`. The first
update also read the unused `transform_parts` schema and fetched `list_assets`
after `resolve_source` already identified the source/exports; that asset listing
alone returned 7,279 bytes. This is avoidable context/round-trip overhead, not a
missing integration or failed asset operation.

Review preparation added 13.575s for creation and 27.727s/24.677s for updates;
test-side acceptance/rejection took 0.004–0.013s. Clean native recipe replay took
6.001s and Godot reopen/import smoke 10.864s. Model-turn time includes provider
generation, gateway requests, policy hooks and orchestration, so subtracting
native runtime does not isolate pure inference latency. These are single-run
figures on emulated workers, not a benchmark distribution.

Local evidence: `/tmp/joinery-alpha-agent-prop.log`,
`/tmp/joinery-alpha-agent-prop-metrics.json`, and
`/tmp/joinery-alpha-agent-prop-strong-original.log`. This proves the tested
technical source/export/instance/edit/review path; it does not prove arbitrary
finished prop quality, Blender rigging/animation, live unsaved-editor placement,
or that all future provider sessions will respond at the same speed.

## Ubuntu CI ownership failure and verified correction

Candidate `002390b`, [run 42487334081, assets job](https://github.com/Ninjacornix/joinery/actions/runs/42487334081/job/128751963262):
the actual Ubuntu worker build and measured asset checks passed, then all six
clean recipe rebuilds failed because all ten declared outputs were **not
produced**. The richer lifecycle step was consequently skipped. This was not a
geometry, pixel, audio or provenance fingerprint disagreement.

The image's fixed user had UID 1000 while the Linux host project belonged to UID
1001. A minimal reproduction inside the worker's Linux filesystem creates a
directory owned by 1001 with normal 0755 permissions, then asks the image's
`joiner` user to save a Blender file there. Blender prints `Cannot open file ...
for writing: Permission denied` yet exits 0, leaving no file. macOS Docker bind
mount ownership translation had hidden this difference in the earlier local
tests. Raw evidence: `/tmp/joinery-alpha-ci-assets.log` and
`/tmp/joinery-alpha-ci-uid-repro.log`.

`joinery/worker_identity.py` now supplies the host UID:GID on native Linux to
recipe runners, native document operators and all four app MCP worker specs.
It configures a private ephemeral container HOME and XDG paths; the shared
entrypoint copies only prepared image-owned tool settings/SC extensions there.
The official Blender MCP worker now uses that shared bootstrap too. Docker
Desktop macOS/Windows retain their existing image-user behavior. Root Linux
invocations are refused rather than granting model workers root. There is no
host home/credential mount, world-writable project permission change, relaxed
fingerprint, refreshed expectation, or weakened provenance gate.

The corrected launch path was exercised using actual Docker Linux volumes owned
by UID 1001, rather than Mac bind-mount ownership semantics:

- All four richer native create/update/replay/reject/accept/revert lifecycles
  passed, including actual painted pixels, audible synthesis, SVG/PNG pixels,
  and Material Maker texture pixels/roughness.
- All six committed recipes reproduced **every unchanged existing expected
  fingerprint**, including exact baked textures/WAV, semantic Blender geometry
  and the existing camera-render signature.
- Actual Godot, Blender, SuperCollider and Inkscape MCP processes connected as
  UID 1001 (157, 26, 9 and 8 exposed tools respectively). A saved-file Godot MCP
  write produced a file whose Linux stat owner was exactly 1001:1001.
- The base, MCP, creative, Blender and Inkscape worker images were rebuilt. Five
  permanent identity regressions cover all launchers, read-only mounts,
  Desktop behavior and refusing root; the focused suite passed 25 tests. Ruff,
  mypy and shell syntax checks passed for the changed launch code.

Evidence: `/tmp/joinery-alpha-ci-linux-owner.log`,
`/tmp/joinery-alpha-ci-linux-mcp-owner.log` and the
`/tmp/joinery-alpha-ci-owner-*-build.log` files.

The corrected candidate `37e2679` subsequently passed the actual hosted Ubuntu
24.04 [assets job in run 42488962130](https://github.com/Ninjacornix/joinery/actions/runs/42488962130/job/128756906863),
completed 2026-11-08 at 14:21:03 UTC. Its offline worker build passed in 151
seconds, measured lock/assets checks passed, all six recipes clean-replayed
without changing any expected fingerprint, and all four real native creative
lifecycles passed. Hosted recipe measurements: door 2.5s, stone material 20.7s,
music 0.9s, room 2.3s, door SFX 0.9s and sky 6.1s; the complete replay step took
43s including verification overhead. Krita painted pixels/layer retention,
audible SuperCollider, Inkscape SVG/PNG pixels and Material Maker red/blue texture
plus roughness updates all passed their replay/reject/accept/revert checks in
the subsequent 91-second lifecycle step.

Completed job log: `/tmp/joinery-alpha-hosted-assets-pass.log`. This is actual
hosted Linux worker and asset evidence, distinct from local emulation and from
native desktop editor/build/package verification. It resolves the asset-job
ownership failure; it does not turn the original failed run green or certify
other pending platform jobs.


## Final desktop candidate

Candidate `437566e`, [run 42505422247](https://github.com/Ninjacornix/joinery/actions/runs/42505422247),
passes all six unchanged recipe fingerprints and all four rich native creative
lifecycles on hosted Ubuntu. Its first asset attempt stopped before any asset
test because the pinned Blender download returned HTTP 403. The exact URL
responded normally afterward; retrying only that failed job on a fresh runner
passed with the same source, download pins and verification gates. No checksum
or asset fingerprint was changed to obtain a pass.

The same candidate passes native editor widgets, isolated startup/API setup,
graphical scene/review/undo, imported-preview refresh/reimport, Godot MCP, fresh
desktop extraction, and exported-game runtime checks on macOS ARM64, Linux
x86_64 and Windows x86_64. See [desktop-release.md](desktop-release.md) for the
platform evidence and corrections; these smoke checks do not expand the
creative application or live-provider capability claims above.
