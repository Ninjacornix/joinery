# Desktop alpha verification

Release target: `v0.1.0-alpha.1`. Publication requires native editor, bundle and
exported-game checks on macOS ARM64, Linux x86_64 and Windows x86_64. Pending
checks below are not reported as successes.

## Latest hosted evidence

Candidate `437566e`, [run 42505422247](https://github.com/Ninjacornix/joinery/actions/runs/42505422247),
2026-11-08:

- All three operating systems pass Ruff, strict mypy (75 source files), Python
  tests and policy checks: macOS/Windows **408 passed, 4 skipped**;
  Linux **407 passed, 5 skipped**.
- Pinned real Codex CLI **0.167.0** passes initialization, normal pipe EOF and
  cancellation without authentication or inference on all three hosts.
- All three patched native editors build and pass the five native widget/pipe
  cases, including buffered Unicode output, cancellation and audio transport.
- All three pass isolated provider/API connection and project creation with five
  retained screenshots: provider gating, API submenu, horizontal renderers,
  stable wizard geometry and custom tool configuration.
- All three pass graphical scene/review/undo, Godot MCP installation and probe,
  measured desktop packaging and fresh extraction checks. POSIX launchers also
  execute from the extracted bundle. The preview regression verifies both forced
  and stamped unchanged refreshes preserve the camera, then a genuine model
  reimport updates geometry with identical raw GLB bytes and catalog data.
- macOS produces all three desktop game exports. Each exported game then runs
  on its actual native macOS, Linux or Windows runner and proves GLB loading.
- The first asset attempt stopped on an upstream HTTP 403 during the pinned
  Blender archive download, before checksum verification or any asset test. The
  exact URL subsequently responded HTTP 200. Retrying only that job on a fresh
  runner, with the same pins and gates, passed all **six unchanged recipe
  fingerprints** and all **four native creative create/edit/replay/reject/accept/
  revert lifecycles**. The complete candidate workflow is green after that retry.

The successful Linux accepted-model framebuffer shows the imported technical
GLB, selected resource context, wider sidebar and separate Review workspace.
CI fixtures use isolated settings and accounts; they never sign in to a real
provider or change the user's open project.

## Real native failures corrected during validation

| Gate | Observed failure | Correction and verification |
| --- | --- | --- |
| Windows provider lifecycle | Node wrapper exited before its native Codex descendant released the temporary home | Wait on stable handles for owned descendants and require pipe EOF and immediate child exit. Actual Windows Codex startup/cancellation and descendant cleanup pass. Temporary fixture cleanup retries only Windows sharing violation after completed process/pipe waits, with a hard deadline and lock-owner diagnostics. |
| Windows private files/hooks | POSIX assumptions in locks, paths, permissions and shell wrappers | Native byte locks, user+SYSTEM ACLs before secret writes, literal PowerShell argument/stdin forwarding and trusted wrapper resolution. Native ACL, hostile/Unicode path, lease contention and recovery tests pass. |
| Windows editor build | Missing AccessKit, NIR and D3D12 build dependencies | Verify exact upstream archives before extracting; preserve accessibility/D3D12 support and package original runtime redistributables with notices. Actual MSVC build passes. |
| Windows graphical startup | GDI selected pixel formats before WGL initialization; a second SetPixelFormat failed | Native WGL probe isolated both causes. Patch 0010 initializes WGL first and removes duplicate pixel-format configuration. Actual Windows editor renders through private test-only Mesa. |
| Windows process output | PeekNamedPipe reported ERROR_BROKEN_PIPE after a completed child | Patch 0011 treats that precise condition as EOF while retaining buffered bytes and other errors. Real buffered Unicode and empty-stderr EOF widget checks pass. |
| Windows first catalog load | Passing an empty --since argument lost its value through native Windows argument quoting | Omit --since until a stamp exists. Actual Windows catalog loads and renders the accepted GLB. |
| Linux native tool saves | Worker UID 1000 could not write host UID 1001 files although Blender returned zero | Map the regular invoking Linux UID:GID and use an ephemeral private home. All six recipes and four richer creative lifecycles pass on hosted Ubuntu; output ownership remains with the invoking user. |
| macOS hosted display | Native OpenGL was rejected by the hosted virtual Mac | Build upstream ANGLE with verified dependencies and retain its license. Actual macOS graphical widget/startup tests pass. |
| Linux hosted display | Missing/ambiguous software Vulkan presentation setup | Select the installed native Lavapipe ICD and require a successful presentation preflight. Full Linux graphical/bundle gate passes. |
| Preview refresh/reimport | Filesystem notifications cleared the confirmed catalog stamp and rebuilt an identical preview; cached scenes also hid genuine import-setting changes | Retain the confirmed stamp, skip identical payload application and repeated ready-model selections, explicitly reload a reimported selected resource. Observe initial native import completion before camera assertions. Existing and empty scene tests pass on all three systems, including actual 2x geometry reload with unchanged raw GLB/catalog. |
| Audio fixture clock | A fixed UI-timer delay assumed the audio mixer had already finished | Wait for the actual finished signal with a finite deadline; verify seek, pause, speed and loop restart. Three intentional broken-player mutations fail, and real native fixtures pass on all three hosts. |

The two Windows engine exceptions are exact-file, documented ADR exceptions to
the editor-only patch policy, not a directory-wide exemption. Original policy,
provenance, replay and transaction gates remain enabled.

## Other measured evidence and limits

The real signed-in three-turn Codex/Blender source/export/scene workflow passed
with **23 RPC requests and zero failed calls or retries**. It verified native
geometry, exact rejection restore, accepted incremental changes and clean replay.
See [alpha-tools.md](alpha-tools.md) for commands, timings and supporting-tool
boundaries. Hosted provider smoke uses no account credentials and does not imply
all providers or endpoints have a verified live authoring workflow.

All three official-runtime game exports were produced and executed on their
actual native macOS, Linux and Windows CI hosts, proving the technical GLB
geometry loads. The Linux export additionally ran in offline AMD64 Docker. Editor packages use the patched engine; game export templates
are stock upstream Godot 4.7.2.

The release workflow repeats the native, asset and unit gates on the release tag
before publishing. The tag adds documentation updates to the verified source
candidate. Archive checksums accompany the
[prerelease downloads](https://github.com/Ninjacornix/joinery/releases/tag/v0.1.0-alpha.1);
its release notes link the actual publication workflow. Native CI checks measured
contents of every fresh extraction before an archive can be published. Builds are unsigned and not
notarized. Windows alpha chat supports Codex; other provider sandbox adapters
remain explicitly unsupported there.
