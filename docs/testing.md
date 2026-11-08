# Testing program and MCP workflows

Run the default suite with `uv run pytest -q`. The program regression tests run
without Docker, application installations, accounts, API keys or inference. MCP
contract tests use the actual SDK's in-memory client/server transport, with
native application execution replaced at its boundary. Connection/installation
unit tests use controlled peers and command fixtures.

The eight new files add **319 parameterized test cases**. They protect the
following user requests and failure paths:

| Area | User intent and checks | Test file |
| --- | --- | --- |
| Native documents | Create a source; edit its existing version; keep an immutable preimage; preserve every export; report save failures; inspect layers/objects/Unicode; reject external SVG content; retain material graph node IDs and connections | [Native authoring](../tests/test_native_authoring.py) — 71 cases |
| Authoring MCPs | Discover actual capabilities; send a request to its owning app; transform only a named model part; interpret degrees correctly; retain quoted/Unicode part names; reject empty names, booleans and nonfinite transforms before execution; preserve actionable errors | [Authoring MCP](../tests/test_authoring_mcp.py) — 68 cases |
| Programs and workers | Open the right native document from an exported resource; handle a missing app; open SuperCollider in its IDE without running the patch; mount only the selected project; keep MCP workers offline; detect modified installations; report unavailable automation honestly | [Program workflows](../tests/test_program_workflows.py) — 57 cases |
| MCP installation | Install the exact source revision; use locked dependencies without install scripts; reuse a verified install; reject wrong revisions, missing builds/locks, denied packages and unavailable Node; clean incomplete installs | [Install workflows](../tests/test_mcp_install_workflows.py) — 17 cases |
| MCP connections | Read every discovery page; reject duplicate tools; validate local schemas without fetching remote references; reject malformed input before forwarding; preserve application errors; distinguish discovery from a successful probe; timeout, close and reconnect; omit provider secrets | [Connection contracts](../tests/test_mcp_connection_contracts.py) — 24 cases |
| Review | Keep editable sources and game exports together; reject without changing originals; accept and revert both files; preserve newer manual edits; refuse content changed after its preview | [Asset review](../tests/test_asset_review_workflows.py) — 24 cases |
| Source registry | Resolve a selected Godot file/subresource to its actual native source and owner; refuse outside/unregistered resources; expose art direction without giving the model approval authority | [Registry MCP](../tests/test_registry_mcp_workflows.py) — 18 cases |
| Godot requests | Create meshes, place imported scenes, delete/duplicate/rename/reparent, move/rotate, assign audio/resources, attach existing scripts, connect/disconnect signals; reject stale identities, invalid resource paths, incomplete requests and oversized batches | [Godot workflows](../tests/test_godot_user_workflows.py) — 40 cases |

## Native application checks

Unit tests establish Joinery's orchestration contracts. Native tests separately
measure the applications' output. With the prepared offline worker images and
Docker running:

```sh
uv run python tests/integration/creative_updates.py all
# Run a single application, for example:
uv run python tests/integration/creative_updates.py blender
```

The existing CI asset job invokes `creative_updates.py` without arguments, which
runs `all`. It now includes the new Blender MCP workflow as well as the four
existing creative lifecycle checks. Fixtures are disposable technical documents;
they never approve art direction or create content in a user's game.

| Application | Native checks |
| --- | --- |
| Blender | Use the actual MCP SDK to create a two-part assembly, translate/rotate/scale only `Head`, retain `Grip` and topology, export GLB, clean replay, reject/accept/revert source and export together |
| Krita | Paint exact red/blue pixels, retain original and added layers, save `.kra`, export PNG, replay and review/revert |
| Inkscape | Keep SVG element identity, change vector colour, verify exported PNG pixels, replay and review/revert |
| Material Maker | Preserve a connected native graph, verify actual red/blue texture pixels and changed material roughness, overwrite exports, replay and review/revert |
| SuperCollider | Save the native patch, render audible WAV at 440/660 Hz, retain UTF-8 source, replay and review/revert |

Real upstream Godot MCP and cross-application checks are separate commands:

```sh
uv run python tests/integration/mcp_round_trip.py
uv run python tests/integration/cross_tool.py
```

They require the pinned Godot MCP installation and prepared worker images.
They exercise actual scene create/add/read, preview, reject/accept, and a Blender
source → GLB → immediate Godot import/scene instance → review/accept/revert path.
They use temporary projects and no model calls. The cross-tool fixture mocks art
approval only in that temporary technical project; it creates no approval record.

The native Godot GUI/undo/audio/startup harnesses under `tests/godot/` remain
separate from Python input validation. Request-schema tests do not establish
visual correctness or execute arbitrary live editor operations.

## Every catalogued program and MCP

The [explicit test plan](../tests/upstream/plan.py) accounts for all **12 catalog
programs and 15 MCP servers**, including Surge XT through `joinery-sound`. The default
suite includes 26 additional completeness/source-integrity/runner cases: a new program
or MCP cannot silently disappear from the plan, and empty, failed or timed-out
suites cannot report success. This inventory is separate from
the support matrix: a catalog entry does not mean Joinery has an installer or
authoring adapter for it.

Five upstream MCP suites run separately because their SDK dependencies differ.
They execute actual upstream code at the catalog's exact Git revision; only the
native application boundary is simulated. Aseprite and GIMP have new SDK/tool
tests; Audacity, REAPER and MuseScore reuse their pinned upstream test suites.

| Program | Application-specific checks | Portable result |
| --- | --- | --- |
| Aseprite | Canvas, layers, frames, tags, drawing, export checks, CLI failures, timeout configuration and MCP input schemas | 28 passed |
| GIMP | Canvas and selected layers, resizing/cropping/filling/text, XCF and raster exports, undo/redo, plugin errors and MCP input schemas | 40 passed |
| Audacity | Tracks and selections, editing, labels, effects, script-pipe framing, paths and errors | 153 passed, 1 skipped |
| REAPER | Track/mixer changes, MIDI, FX, rendering, project operations and reapy failures | 364 passed; 13 live cases deselected |
| MuseScore | Notes/chords/rests, tempo, metadata, repetition, rolls, score-file round trips and simulated WebSocket plugin requests | 85 passed, 3 skipped |

```sh
# Fetch software source at the exact pins, then run isolated locked profiles.
uv run python -m tests.upstream.run --all
uv run python -m tests.upstream.run --tool reaper
```

The runner writes JSON, JUnit XML and logs to `out/upstream-mcp-tests/`. A failed,
empty or timed-out suite fails the command. SDK 1.x and SDK 2.x/FastMCP profiles
are locked separately under `tests/upstream/`; they use Python 3.13 without
changing Joinery's Python 3.12 environment. No provider keys are passed to the
test processes. The new CI job runs all five suites as separate matrix entries
and retains their evidence. Hosted execution is pending a push.

An existing local audit directory containing `<program>/.git/objects` can be
used with `--local-audits PATH`. Tests still read committed blobs at the exact
pin, never the editable checkout. Only this local mode can include the existing
MuseScore example-score test fixtures: its measured result is 112 passed,
6 skipped and 2 policy-excluded viewer cases. The portable path uses generated
technical scores and does not download music or artwork. Optional Audacity
speech-model tests are excluded; no model is installed or invoked.

## Additional native probes

The integrated-server probe discovers every schema and makes a read-only call
against all ten configured MCP servers, using temporary projects and the
installed pinned servers/workers:

```sh
uv run python tests/integration/mcp_catalog.py --output out/mcp-catalog.json
```

It reports a failure instead of treating discovery as proof of a working app.
An earlier run had `claude-collider.cc_status` return `SuperCollider boot timed
out`. Three worker causes were fixed: the extension mount left root-owned folders in
the home, JACK had no server and too little shared memory, and Linux `sclang` echoed
input into replies. The separate
first-party SuperCollider native NRT audio workflow passes. These are different
interfaces and neither result cancels the other.

GIMP's pinned plugin handler bodies also run against actual native GIMP APIs:
create/edit named layers, save XCF and verify exact red/blue exported PNG pixels.
They pass in the offline creative image. This verifies native handlers, not the
plugin's socket registration or a complete Joinery GIMP authoring adapter.

Prepare persistent disposable source directories for these optional probes:

```sh
uv run python - <<'PY'
from pathlib import Path
from joinery.catalog import load_catalog
from tests.upstream.sources import prepare
for name in ("gimp", "audacity"):
    tool = load_catalog().get(name)
    prepare(tool.mcp.repo, tool.mcp.ref, Path("out/native-mcp-source") / name)
PY
uv run python tests/integration/gimp_mcp.py --source out/native-mcp-source/gimp

# Audacity is installed only in this test image, not in production workers.
docker build --platform linux/amd64 -t joinery-mcp-conformance -f tests/upstream/Dockerfile tests/upstream
uv run python tests/integration/audacity_mcp.py --source out/native-mcp-source/audacity
```

Use fresh source destinations when preparing again. Both native harnesses run
offline in their own named containers and remove only those containers on exit.
The Audacity probe seeds Audacity's version and module settings, since a fresh
install resets them and opens a blocking dialog, then waits until Audacity accepts
commands. CI runs it on native x86 Linux. Under x86 emulation on Apple Silicon its
first commands can still stall; that is not counted as a pass.

Native Aseprite and REAPER checks require user-provided licensed executables;
none were supplied. Native MuseScore is unavailable here. The five additional
upstream suites do not install these MCPs into Joinery's production server cache
or implement the missing application-specific authoring adapters/installers.
Surge XT runs natively in the creative worker: `uv run python tests/integration/surge_native.py`.

Tests cannot establish every operation exposed by a third-party application or
MCP. These scenarios cover Joinery's implemented contracts and representative
native workflows. See the [support matrix](support-matrix.md) for capability
boundaries and [execution report](../evals/results/program-regressions.md) for
measured results. The [complete program/MCP evidence matrix](../evals/results/all-mcp-programs.md)
records the broader upstream and native probe outcomes, including failures.
