# 0.1 alpha support matrix

This matrix distinguishes measured local/hosted behavior from intended release targets.
Native validation is recorded below; publishing requires the complete release workflow.

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
| Blender 5.2.1 | Native `.blend` source editing, GLB export, registered source resolution and replay | Real Codex create/edit/scene-instance/review workflow; reject restores originals; native geometry and Godot import verified |
| Krita 5.2.9 | Native `.kra` painted layers and PNG export | Actual red/blue pixels, layer retention, create/edit/replay/reject/accept/revert verified |
| Material Maker 1.8 | Native `.ptex` graph and Godot material/texture export | Connected shader exports real red/blue texture pixels; roughness changes, replay and transaction lifecycle verified; shutdown warnings remain |
| Inkscape 1.4 | Editable SVG and PNG export | Stable element ID, actual exported pixels, replay and transaction lifecycle verified |
| SuperCollider | Editable `.scd` patches and offline NRT audio export | Audible WAV, native UTF-8 source recreation, update/replay and transaction lifecycle verified; no live audio-device workflow claim |
| Aseprite | Native Lua adapter exists; bring your own licensed executable | Not packaged or exercised with an actual licensed app in this audit |
| GIMP 3.0.4 | Desktop open/view; app installed in the development worker | No evaluated first-party native authoring adapter or complete pinned MCP installer |
| Audacity, REAPER, MuseScore | Optional desktop open/view when installed | Not installed or verified for automated authoring in this audit; REAPER requires your own licence |
| Surge XT | Planned integration | No packaged/evaluated native authoring runtime; use the verified SuperCollider lane |
| Custom tools | Explicit project MCP/desktop configuration | Readiness and provenance remain tool-specific; never inferred from a catalog entry |

Application versions here identify the evaluated worker, not guaranteed future
apt package resolution. Direct-download versions/hashes are recorded; Debian's
mutable base and unversioned apt dependencies are not a frozen OS snapshot.
FluidSynth and FFmpeg support command-line media work; a full DAW/MIDI composition
integration was not evaluated. No unlicensed applications, generative asset
services or downloaded art were used to inflate test coverage.

Desktop **Open in**, native authoring, MCP connection and read-only probe success
are separate capabilities. Run `joinery mcp status --project ... --probe` (or
`/tools`) to inspect actual readiness. `joinery doctor` checks host executables;
it does not certify the contents or connectivity of offline Docker workers.

## Providers and review

| Provider | Verification |
| --- | --- |
| Codex | Signed-in persistent app-server session; real three-turn native source/export/Godot workflow passes with 23 RPC calls and zero tool failures/retries |
| Claude Code | Stream-JSON transport covered; live account check reached a provider quota error, so a complete native authoring workflow is not claimed |
| Gemini CLI, opencode | ACP protocol fixture coverage; CLIs unavailable for a real signed-in workflow in the local audit |
| Additional API models | Explicit endpoint/model/key profiles supported through Codex Responses or opencode OpenAI-compatible transport; individual endpoints/models are not all certified |

Provider login does not approve art direction or transactions. The model cannot
accept its own changes or approve the project's art bible. Asset export alone
does not place an object in a scene; request placement explicitly. Saved-file
operations and live unsaved editor operations use different review paths.

Detailed commands, raw evidence locations, measured runtime limits and failure
fixes: [alpha tool audit](../evals/results/alpha-tools.md).
