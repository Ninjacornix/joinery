# All catalogued programs and MCPs

Measured locally on 2026-11-08, macOS ARM64. Native worker checks use offline
Linux AMD64 Docker containers under emulation. These changes have not been
pushed or executed by hosted CI.

The inventory covers **12 catalog programs and 15 distinct MCP servers**. It
includes unavailable and planned entries rather than silently skipping them.
The fast suite now reports **753 passed, 4 skipped in 10.49s**. The four skips
predate this expansion. The earlier eight regression files contribute 319 cases;
the new catalog/source-integrity/runner file adds 26. Completeness checks compare the
explicit plan against every preferred and first-party server in the catalog.
Ruff, strict mypy for 81 production/integration/helper files (including Windows
platform stubs), shared policy checks and CI workflow validation pass locally.

## Additional upstream MCP suites

Each test process imports the actual exact-pinned upstream implementation into
an isolated, frozen dependency profile. Native CLI/plugin/reapy/WebSocket
boundaries are simulated. These are interface/behavior tests, not native app
passes and not evidence that a missing Joinery integration has been implemented.

| Program / MCP | Catalog revision | Portable result | Test source |
| --- | --- | --- | --- |
| Aseprite / `aseprite` | `4bcdb6f5932116bbdbd1b7d3fcc1e8f965f7657a` | 28 passed | New SDK/tool checks: drawing, frames, layers, tags, exports, invalid inputs and native failures |
| GIMP / `gimp` | `7325865385541a01aaf336ebc93b00332297f77f` | 40 passed | New SDK/tool checks: selected image/layer, painting commands, XCF/raster exports and plugin failures |
| Audacity / `audacity` | `2835f96c18934fe93b0b0673ba18fc88d884a12b` | 153 passed, 1 skipped | Pinned upstream client/track/selection/label/effect/pipe tests |
| REAPER / `reaper` | `e3dff9fd8e215d86462e6dd1599055a0dcd7921a` | 364 passed, 13 live cases deselected | Pinned upstream mixer/MIDI/FX/render/project tests |
| MuseScore / `musescore` | `80e2af772a51f152298a2186d1d18ba2b734c280` | 85 passed, 3 skipped | Pinned upstream server/WebSocket/score-file tests using generated technical scores |

Portable total: **670 passed, 4 skipped**. This run used fresh Git source fetches,
matching the new five-program CI matrix. A separate run using already-local
MuseScore example-score fixtures reports 112 passed, 6 skipped and 2 excluded
viewer cases; the five-suite total in that mode is **697 passed, 7 skipped**.
No example artwork/music is fetched. Audacity's optional speech/model suite is
excluded and REAPER's live cases require a licensed executable. Catalog-blocked
MuseScore viewer methods remain excluded by policy.

SDK 1.x and SDK 2.x/FastMCP environments are locked separately; production
dependencies remain unchanged. Source preparation verifies committed Git blobs
against the catalog pin, excludes private files/binary assets, and never trusts
uncommitted audit checkout edits. JSON/JUnit/log reports preserve suite counts,
scope and omissions; zero-pass and timeout results fail the runner.

## Integrated and native evidence

| Program | MCP servers covered | Native / integrated result |
| --- | --- | --- |
| Godot | `godot`, `joinery-registry`, `joinery-verify` | Actual discovery/read-only calls pass; scene create/add/read/review and Blender import/instance lifecycle pass |
| Blender | `blender`, `joinery-blender-ops` | Actual discovery/read-only calls pass; two-part native model, named-part transform isolation, GLB, replay/review/revert pass |
| Krita | `joinery-paint` | Capability call passes; actual layered KRA and exact PNG pixels, replay/review/revert pass |
| Material Maker | `joinery-materialmaker` | Capability call passes; actual connected graph, texture pixels and roughness, replay/review/revert pass |
| Inkscape | `inkscape`, `joinery-paint` | Discovery/diagnostics pass; native SVG identity, exact PNG pixels, replay/review/revert pass |
| SuperCollider | `claude-collider`, `joinery-sound` | Both discover; first-party capability call and audible NRT 440/660 Hz replay/review/revert pass. Upstream `cc_status` passes (3.6 s first boot, 2026-11-08, Apple Silicon under x86 emulation): results, runtime and syntax errors read back correctly, and a live 440 Hz recording measures RMS 0.141 |
| Aseprite | `aseprite`, `joinery-paint` | Upstream contract tests pass; native checks unavailable without the licensed executable |
| GIMP | `gimp` | Contracts pass. **9 native plugin-handler commands pass**, including XCF writes and exact red/blue PNG pixels; plugin socket startup and Joinery authoring integration are not proven |
| Audacity | `audacity` | Contracts pass. Native probe: two harness causes fixed (module never loaded; fresh-install "What's new" dialog blocked commands). Under x86 emulation, first commands still stall during startup; the native x86 CI run is the deciding result |
| REAPER | `reaper` | Contracts pass; native checks unavailable without the licensed executable |
| MuseScore | `musescore` | Contracts and score-file edits pass; native GUI/plugin executable unavailable |
| Surge XT | No MCP; planned entry | Unsupported capability behavior tested; no native integration claimed |

All ten currently configured integrated servers expose schemas. All ten read-only
probes are expected to pass; `claude-collider` failed until its worker fix. The probe command exits nonzero for that
failure. Application readiness failures are kept separate from passing contract
tests; the test harness does not downgrade them to skips.

GIMP's handler check executes the pinned method bodies against real GIMP
documents, with plugin registration bypassed to avoid nesting its event loop
inside GIMP's batch runner. It does not mock native document APIs. Audacity has
a reusable failure-reporting native harness and an isolated test-only image;
its attempted startup changes no host application installation.

## Evidence and reproduction

- `out/upstream-mcp-portable/`: five portable JSON/JUnit/log reports.
- `out/upstream-mcp-tests/`: five extended local-audit reports and `integrated.json`.
- `/tmp/joinery-all-mcp-root-suite.log`: 753 passed, 4 skipped.
- `/tmp/joinery-native-gimp-repeatable.log`: native GIMP handler/file/pixel proof.
- `/tmp/joinery-native-audacity-repeatable.log`: failed script-pipe readiness probe.
- `/tmp/joinery-sc-dummy-probe.log`: failed upstream SuperCollider retry.
- Earlier native and cross-tool evidence is linked in [program-regressions.md](program-regressions.md).

See [testing.md](../../docs/testing.md) for commands and coverage boundaries.
The existing five-app native creative CI job now includes Blender; a new CI
matrix runs the five additional upstream MCP suites. No paid binaries, model
inference, generative services, user's game edits or art approvals were involved.
