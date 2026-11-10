# Support matrix

What Joinery 0.2 can do, and the evidence for it. Measured behaviour is kept
apart from intended targets. The platform rows below were measured on the
0.1 alpha 1 candidate; 0.2 is checked by the same release workflow before it
is published.

## Platforms and distribution

| Target | Editor/package evidence | Game export evidence | Status |
| --- | --- | --- | --- |
| macOS ARM64 | Patched editor, native widgets/startup, graphical review/undo, MCP and fresh app bundle checks pass on macOS 15 runner | All desktop exports produced; macOS game executed natively | Verified unsigned alpha target |
| Linux x86_64 | Ubuntu 24.04 patched editor, widgets/startup, graphical review/undo, MCP and fresh bundle checks pass | Linux game executed on native Ubuntu and in offline AMD64 Docker | Verified alpha target; Ubuntu 24.04 baseline |
| Windows x86_64 | Native MSVC build, private IPC/ACL/process tests, real Codex startup, widgets/startup, graphical review/undo, MCP and fresh bundle checks pass | Windows game executed natively on Windows 2025 runner | Verified unsigned alpha target; Codex chat only |

Hosted Ubuntu 24.04 offline worker builds, measured asset checks, all six clean
testbed recipe rebuilds and all four native creative-tool update/review lifecycles
**passed** in [run 42488962130](https://github.com/Ninjacornix/joinery/actions/runs/42488962130/job/128756906863)
for candidate `37e2679` on 2026-11-08. Existing recipe fingerprints remained
unchanged. The latest [run 42503383271](https://github.com/Ninjacornix/joinery/actions/runs/42503383271)
passes all three native graphical editors, fresh bundle checks and exported-game
runtimes, including the corrected catalog refresh/reimport regression; see the
[desktop verification report](../evals/results/desktop-release.md).

`release/build_editor.py` sets the macOS deployment target to 13.0 for new native
CI builds. This is a build setting, not evidence that every existing local binary
has that minimum or that macOS 13 has been tested.

The macOS game export preset is universal; that does not make the ARM64 editor
bundle universal or establish Intel Mac testing. Linux ARM64, Windows ARM64,
mobile and web are not evaluated alpha editor targets. A build/package unit test
or successful cross-export is not a native graphical editor check. Windows CI
may provision pinned software OpenGL privately for its graphical harness; those
Mesa DLLs are test infrastructure, not a shipped user dependency.

All bundles require uv and a usable provider CLI/API profile. Offline native
authoring additionally requires a running Linux-container Docker engine. Server
setup uses Git and Node.js/npm; Windows setup also uses Git Bash. Creative tool
workers are Linux AMD64 and run emulated on Apple Silicon. Host desktop apps are
optional unless you want to open their native files interactively.

Run Joinery as your regular Linux account. Offline workers map its UID:GID so
staged project files remain writable by their owner, and keep tool homes/caches
inside disposable containers. Root Linux invocations are refused.

Native Windows permits Codex sessions through Codex's built-in read-only sandbox.
Claude, Gemini and opencode sessions are explicitly refused on Windows: their
read-only host project boundary is not implemented. This also applies to API
profiles using the opencode backend. On macOS those providers require
`sandbox-exec`; Linux requires bubblewrap (`bwrap`). Missing isolation is a startup
error. Codex's native startup/EOF/cancellation and Windows process/ACL checks
pass without authentication or inference; the real signed-in end-to-end model
audit ran on macOS.

## Creative applications

| Application | Alpha automation | Evidence and boundary |
| --- | --- | --- |
| Blender 5.2.1 | Native `.blend` source editing, GLB export, registered source resolution and replay | Real Codex create/edit/scene-instance/review workflow; reject restores originals; native geometry and Godot import verified. GLB exports apply modifiers (a Mask, Subdivision or geometry-nodes model exports as Blender shows it) and keep every shape key as a morph target; the saved `.blend` keeps its modifiers. An MPFB2 human with a Rigify rig exports exactly its deform bones |
| Krita 5.2.9 | Native `.kra` painted layers and PNG export | Actual red/blue pixels, layer retention, create/edit/replay/reject/accept/revert verified |
| Material Maker 1.8 | Native `.ptex` graph and Godot material/texture export | Connected shader exports real red/blue texture pixels; roughness changes, replay and transaction lifecycle verified. An image input is recorded in the recipe's sources with a relative path; an unrecorded input is refused. Shutdown warnings remain |
| Inkscape 1.4 | Editable SVG and PNG export | Stable element ID, actual exported pixels, replay and transaction lifecycle verified; the pinned MCP adds read-only analysis tools |
| SuperCollider 3.13 | Editable `.scd` patches and offline NRT audio export | Audible WAV, native UTF-8 source recreation, update/replay and transaction lifecycle verified. A broken patch fails within a minute instead of hanging. Live audition records outside the project with no bundled samples; no live audio-device routing claim |
| GIMP 3.0.4 | First-party adapter: native `.xcf` create, inspect (layer tree, channels, paths) and edit through Joinery's Python-Fu adapter in `joinery-paint`, run headless in the offline creative worker; per-layer or composite PNG exports, byte-identical on replay; desktop open/view | `joinery rebuild` replays GIMP recipes through the GIMP tool module. Wrapping an existing PNG into an `.xcf` does not yet record that PNG as a recipe input. The community gimp-mcp pin was dropped because it needs a live desktop GIMP |
| Surge XT 1.3.4 | Supported through `joinery-sound` | Offline render of named parameters and notes to WAV, exact replay; a one-parameter edit keeps the rest; an unknown parameter is refused. No factory `.fxp` loading or random sources yet ([ADR 0021](adr/0021-surge-xt.md)) |
| Music (MIDI) | Songs written as MIDI sources (`compose_music`, `edit_music`, `render_music` in `joinery-sound`), rendered offline through FluidSynth (Debian FluidR3_GM SoundFont) and Surge XT patches | Piano and drums through FluidSynth with a Surge XT bass: exact sample count, loop tags, −16 LUFS; transpose and program edits; an imported `.mid` renders unchanged; four recipes replay byte-identically |
| Audio edits | Typed ffmpeg operations (`process_audio`): trim silence, fade, loudness, loop points, format | On SuperCollider and Surge XT renders: trim, fade from silence, −18 LUFS under −1 dBTP, then a looping stereo 44.1 kHz Ogg with loop tags; edits chain, replay byte-identically, and refusals (imported file, empty fade, outside `audio/`, escaping path) change nothing |
| Custom tools | Explicit project MCP/desktop configuration | Readiness and provenance remain tool-specific; never inferred from a catalog entry |

### Removed tools

Aseprite, REAPER, MuseScore and Audacity are no longer catalog tools
([ADR 0025](adr/0025-tool-removals.md)). A project that still lists one keeps
opening; the id is skipped and a notice says what replaced it:

- **Aseprite**: its licence forbids packaging it in an offline worker. Use Krita
  for pixel art, or add Aseprite yourself as a custom tool.
- **REAPER**: proprietary, needs a live desktop session. Use SuperCollider or
  Surge XT, or add it yourself as a `[[tools.custom]]` tool with a
  `[desktop.reaper]` command for "Open in".
- **MuseScore**: no headless mode. Music is written as MIDI sources rendered
  through FluidSynth and Surge XT.
- **Audacity**: no reliable headless worker. Sound edits (trim, fade,
  normalise, loop points, format) move to Joinery's sound server; "Open in
  Audacity" still works when it is installed.

Application versions here identify the evaluated worker, not guaranteed future
apt package resolution. Direct-download versions/hashes are recorded; Debian's
mutable base and unversioned apt dependencies are not a frozen OS snapshot.
Music is written as MIDI sources and rendered offline through FluidSynth (the
Debian FluidR3_GM SoundFont) and Surge XT patches by `joinery-sound`
(`compose_music`, `edit_music`, `render_music`); FFmpeg runs its typed audio
edits (`process_audio`). A full DAW integration was not evaluated. No
unlicensed applications, generative asset
services or downloaded art were used to inflate test coverage.

Desktop **Open in**, native authoring, MCP connection and read-only probe success
are separate capabilities. Run `joinery mcp status --project ... --probe` (or
`/tools`) to inspect actual readiness; a server whose runtime is missing counts
as a failure. `joinery status` and `joinery doctor` report the shared readiness
checks: Docker, each worker image, a version or feature command run inside the
worker with no network, and the installed MCP pins. Doctor no longer needs
Godot, Xvfb or ffmpeg on the host; the checks run in the workers
(`--cached` answers from the cache alone, `--refresh` runs everything again).
A pinned MCP without a recorded source hash is reinstalled. Neither command
runs an authoring round trip.

## Editor features on stock Godot

The addon still loads on stock Godot 4.7; some parts need the patched editor.

| Feature | Patched editor | Stock Godot 4.7 |
| --- | --- | --- |
| New-project wizard (starting points, features, Steam) | Project Manager wizard | `joinery new --blueprint` makes the same project |
| Part selection (ADR 0028) | Workspace parts list and viewport Alt+click | Parts list works fully; Alt+click only while the model's root is selected |
| Steam projects | GodotSteam 4.23 loads in the editor and game | The same; Joinery lists the extension for startup loading, which avoids a crash on the first import |

Models exported by Joinery embed their textures in the GLB
(`embedded_image_handling=3`, written on the first export), so Godot writes no
texture files that no recipe made.

## Providers and review

| Provider | Verification |
| --- | --- |
| Codex | Signed-in persistent app-server session; real three-turn native source/export/Godot workflow passes with 23 RPC calls and zero tool failures/retries |
| Claude Code | Stream-JSON transport covered; live account check reached a provider quota error, so a complete native authoring workflow is not claimed |
| Gemini CLI, opencode | ACP protocol fixture coverage; CLIs unavailable for a real signed-in workflow in the local audit |
| Additional API models | Explicit endpoint/model/key profiles supported through Codex Responses or opencode OpenAI-compatible transport; individual endpoints/models are not all certified |

`joinery connect verify --agent <id>` sends one tiny, tool-free message through
the real chat bridge and records the answer; only then does the agent show as
verified. Signed in is not the same as verified: an account can be signed in
and still out of quota.

Provider login does not approve art direction or transactions. The model cannot
accept its own changes or approve the project's art bible. Asset export alone
does not place an object in a scene; request placement explicitly. Saved-file
operations and live unsaved editor operations use different review paths.

Detailed commands, raw evidence locations, measured runtime limits and failure
fixes: [alpha tool audit](../evals/results/alpha-tools.md).
