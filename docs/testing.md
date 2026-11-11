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
| Programs and workers | Open the right native document from an exported resource; handle a missing app; open SuperCollider in its IDE without running the patch; mount only the selected project; keep MCP workers offline; detect modified installations; report unavailable automation honestly | [Program workflows](../tests/test_program_workflows.py) — 44 cases |
| MCP installation | Install the exact source revision; use locked dependencies without install scripts; reuse a verified install; reject wrong revisions, missing builds/locks, denied packages and unavailable Node; clean incomplete installs | [Install workflows](../tests/test_mcp_install_workflows.py) — 12 cases |
| Godot MCP in its worker | Check the pin and dependency gate of the server built into the MCP worker; say Needs Docker or Rebuild when it cannot run; move a host install from an older Joinery; launch it offline with the project at /work (read-only for inspection); map host paths in and worker paths back; keep the image, versions and catalog pins in agreement | [Godot worker](../tests/test_godot_worker.py) — 17 cases |
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

Blender's texture and extension handling has its own fixture:

```sh
# Painted and re-painted images survive the .blend save and export as PNGs.
uv run python tests/integration/native_documents.py blender
# Builds an MPFB2 human through a recipe. A creative worker built before the
# MPFB2 extension fix stops with the rebuild hint instead.
uv run python tests/integration/native_documents.py blender-mpfb
# An MPFB2 human with a generated Rigify rig: the GLB skin holds exactly the
# deform bones, MPFB2's helper geometry stays out, shape keys and an FK-driven
# action reach the GLB; a Subdivision modifier and shape key on a crate too.
uv run python tests/integration/blender_rig.py
```

ffmpeg audio edits through the `joinery-sound` MCP server (trim silence, fade,
loudness, a looping stereo Ogg at another rate, and five refusals that change
nothing) on a SuperCollider render: `uv run python tests/integration/audio_edits.py`.
MIDI songs (FluidSynth and Surge XT) and audio edits on Surge renders are in
`uv run python tests/integration/midi_render.py`. The latest measured run of
every tool probe is in [tools-working](../evals/results/tools-working.md).

Real upstream Godot MCP and cross-application checks are separate commands:

```sh
uv run python tests/integration/mcp_round_trip.py
uv run python tests/integration/review_view.py
uv run python tests/integration/cross_tool.py
```

They require the prepared worker images (the pinned Godot MCP is built into
`joinery-mcp-worker`) and `joinery mcp install --tool godot`, which records it.
They exercise actual scene create/add/read, preview, reject/accept, and a Blender
source → GLB → immediate Godot import/scene instance → review/accept/revert path.
They use temporary projects and no model calls. The cross-tool fixture mocks art
approval only in that temporary technical project; it creates no approval record.

The native Godot GUI/undo/audio/startup harnesses under `tests/godot/` remain
separate from Python input validation. Request-schema tests do not establish
visual correctness or execute arbitrary live editor operations.

## Editor harnesses

Build the fork first (`godot_patches/fork.sh build`); every harness runs an
isolated copy of that binary, with a throwaway home and no agent sign-in.

```sh
# Headless widgets: command jobs, chat transport, sidebar, audio, scene edits,
# responsive sidebar, design tokens (ui_theme_test) and the Review panel.
uv run python tests/godot/run_widget_tests.py
# Project Manager: agent connect, API form, the three-step wizard (starting point
# cards, features and apps, brief and art) and creating a fixture project.
uv run --frozen python tests/godot/run_startup_test.py
# The Project apps dialog and the app rail, against a fixture CLI built on the
# real readiness core (--capture DIR keeps screenshots).
uv run python tests/godot/run_setup_tests.py [--capture DIR]
# Part picking: the parts list, Alt+click and part targets.
uv run python tests/godot/run_parts_tests.py
# The sidebar in a real editor window, and scene edits in the editor.
uv run python tests/godot/run_assistant_ui_test.py
uv run python tests/godot/run_scene_edit_test.py
# Joinery's layout, Godot's classic one and back, in both themes (out/classic-view).
uv run python tests/godot/run_classic_view_test.py
# Design tokens: no literal sizes or palette colours outside ui_theme.gd.
uv run pytest -q tests/test_ui_tokens.py
```

Graphical harnesses turn Joinery's motion off (`ui_motion.set_enabled(false)`,
or `Engine.set_meta("joinery_motion_enabled", false)` from the Project Manager)
so every check and capture sees end states rather than a frame mid-animation.

## Visual suite

One command captures every Joinery screen in the patched editor, in the Joinery
(dark) and Joinery Light editor presets, and checks each screen while it is shown:

```sh
uv run python tests/godot/run_visual_suite.py --report-only
# Only some harnesses, e.g. the sidebar and the light editor, without the older ones:
uv run python tests/godot/run_visual_suite.py --only sidebar --only editor-light --skip-legacy
```

Screens:

- `pm-*`: agent connection, sign-in, API form, project list, the app-list failure
  banner, each wizard step (What are you making?, Features and apps, Brief and
  art direction), the Steam app ID errors (letters, and too big), a failed
  creation step with Try again,
  and creation in progress.
- `sidebar-*`, at 480 and 1000 px: connecting, no assistant, empty, chips,
  command menu, a request sending, working (steps and timer), waiting for review
  (thumbnail and footer), accepted (with Revert), conversation, not sent (Retry),
  lost connection banner and settings. At 480 px also each Appearance accent
  (Brass, Blue, Green, Violet) and settings with Reduce motion on.
- `editor-*`: layout, disconnected/busy/commands/settings sidebar, the Review
  panel with a model, its details, applying and a failed decision, Focus mode
  with a review open, image and audio reviews on After and on Before, the Tools
  workspace (all, Blender, SuperCollider), every tool readiness state on the rail,
  the bottom panel, Focus mode, Project apps and the art direction dialog.
- `classic-view-*`: the editor, the Layout setting, its restart note and the
  switch dialog, in Joinery's layout and in Godot's classic one
  (`run_classic_view_test.py`).

The older startup, assistant and scene-edit harnesses run too and write into the
same directory unless `--skip-legacy`.

Every run uses an isolated, self-contained copy of the editor binary and a
disposable project (the editor screens use a copy of `testbed/` with Blender
import disabled). No agent signs in and no model is called. Each sidebar theme
and width gets a fresh dock, and its request states start from a real submit.

`tests/godot/ui_checks.gd` walks the visible controls of each screen and prints
`UI-CHECK <screen> <kind> <node path> <detail>` for:

| Kind | Rule |
| --- | --- |
| clipped | Label, Button, OptionButton or ItemList text wider than its room (cut, ellipsis or drawn outside), or wrapped text showing fewer lines than it has |
| contrast | WCAG ratio under 4.5:1, or 3:1 for large text, between the font colour and the background read from the rendered frame (stylebox fallback) |
| overflow | A control reaching outside its parent, or a visible part outside the window |
| target | A button, field, slider, list or tab bar under 24×24 logical px (the editor scale is removed) |

Disabled controls and content scrolled out of view are not judged. Two things
are allowed on purpose: text trimmed with an ellipsis when the control or a near
ancestor shows the full text as a tooltip (a long file name on a chip), and the
`ALLOWED` list in `ui_checks.gd`, which names stock Godot controls Joinery does
not restyle and why (the 3D viewport's "Perspective" menu, and the Scene,
FileSystem and Inspector docks, which draw into their dock margin by design).
`ui_checks_selftest.gd` proves every rule, and both allowances, first.

Output goes to `out/visual/` (`--out` to change): the PNGs, `report.json` (all
findings, per screen, and grouped per control), `report.md` (counts and the 30
most widespread findings) and `logs/`. Any finding fails the run unless
`--report-only`; a missing capture or a broken harness always fails it.

## Every catalogued program and MCP

The [explicit test plan](../tests/upstream/plan.py) accounts for all **8 catalog
programs and 10 MCP servers**, including Surge XT through `joinery-sound`. The default
suite includes completeness/source-integrity/runner cases: a new program
or MCP cannot silently disappear from the plan, and empty, failed or timed-out
suites cannot report success. This inventory is separate from
the support matrix: a catalog entry does not mean Joinery has an installer or
authoring adapter for it.

Aseprite, REAPER, MuseScore and Audacity were removed from the catalog and
GIMP's community MCP pin was dropped ([ADR 0025](adr/0025-tool-removals.md)),
so no community MCP currently needs a separate upstream suite. The lane stays
for the next one: list its id in `PROFILES` in `tests/upstream/plan.py`, then

```sh
# Fetch software source at the exact pin, then run an isolated locked profile.
uv run python -m tests.upstream.run --tool <catalog id>
```

The runner writes JSON, JUnit XML and logs to `out/upstream-mcp-tests/`. A failed,
empty or timed-out suite fails the command. The SDK 1.x profile is locked under
`tests/upstream/sdk1`; it uses Python 3.13 without changing Joinery's Python
3.12 environment. No provider keys are passed to the test processes. Earlier
measured results for the removed programs remain in
`evals/results/all-mcp-programs.md` as history.

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

GIMP 3 is checked in the offline worker by the shared readiness core
(`gimp-console --version`, then a Python-Fu batch probe); see "Tool readiness"
below. Its Python-Fu adapter runs natively in the creative worker:
`uv run python tests/integration/gimp_native.py` creates an `.xcf` with named
layers and a saved channel, edits one layer inside that channel, checks pixels
per layer export, replays every recipe byte-identically and checks that a
failing script and an unknown layer name are refused.
Surge XT runs natively in the creative worker: `uv run python tests/integration/surge_native.py`.
A failing SuperCollider offline render (parse error, runtime error or a patch
that never exits) ends well inside a minute instead of hanging until the worker
limit (`uv run python tests/integration/supercollider_native.py nrt`), and a recorded
image feeds a Material Maker graph as a recipe input
(`uv run python tests/integration/materialmaker_parts.py`).

## Art bible rules in the workers

`tests/test_bible_rules.py` checks the bible's measured rules. The Godot-backed
test runs whenever `JOINERY_TEST_GODOT` or the stock binary under `.tools/`
exists. The Blender and ffmpeg tests measure real files in the creative worker,
so they run only on request, with Docker running and the worker image built:

```sh
JOINERY_WORKER_TESTS=1 uv run pytest tests/test_bible_rules.py
```

## Blueprints and features in Godot

`tests/blueprints/` applies each blueprint and feature into a fresh project and
runs it headless in Godot 4.7 (`JOINERY_TEST_GODOT`, else the stock binary under
`.tools/`; without either the Godot tests skip with a reason). Every Godot run
gets a private HOME beside its project (`tests/blueprints/godot_run.py`), so
`user://`, saves and editor settings never touch your own folders and projects
can run in parallel. `JOINERY_COMBO_MATRIX=full` boots every combination instead
of the default sample.

Steam tests never download GodotSteam. The real-extension check runs only when
the pinned archive is already in `~/.cache/joinery/godotsteam/<version>/`, and
`JOINERY_NO_DOWNLOADS=1` makes `joinery new` and `game apply` skip the download.
Godot user arguments in `godot_run` may contain `{settings}`, replaced with a
settings file of the project's own. Starting the native workflow with
`combo_matrix: full` boots every combination; on Linux it keeps the blueprint
matrix report (`out/matrix/matrix.md`) as an artifact.

The new-project cards show `blueprints/<id>/capture.png`, made by running each
blueprint in the patched editor (recorded in `capture.toml`). Re-run it by hand
when a blueprint's look changes:

```sh
uv run python release/capture_blueprints.py [--blueprint ID]
```

The full matrix (every blueprint alone, with each feature and with all of them,
played in Godot) is slow, so it carries the `blueprint_matrix` marker and runs
only when asked:

```sh
JOINERY_BLUEPRINT_MATRIX=1 uv run pytest tests/test_blueprint_matrix.py
uv run pytest -m blueprint_matrix
# Write <cell>.json per cell and a matrix.md table with timings:
JOINERY_MATRIX_REPORT=out/matrix JOINERY_BLUEPRINT_MATRIX=1 uv run pytest tests/test_blueprint_matrix.py
uv run python -m tests.blueprints.matrix out/matrix   # print the table again
```

The command-line path to a new game is tested without Godot:
`tests/test_project.py` runs `joinery new --blueprint` on the real catalogue
(addon with its MIT licence, brief controls from the applied input map, bible
preset, no folder left after a bad feature mix), `tests/test_game_setup.py`
covers `joinery game apply` and Steam (GodotSteam calls replaced, so nothing is
downloaded), and `tests/test_cli.py` drives every other subcommand through
`joinery.cli.main`. Feature changes as reviewed transactions, including the game
manifest they carry, are in `tests/test_transactions.py`.

## Tool readiness

Each catalog tool has one module under `joinery/tools/` that declares its
readiness checks as data; `joinery/readiness.py` is the only code that runs
them, so every front end and every agent sees the same state (`ready`,
`downloading`, `needs_docker`, `needs_image`, `needs_licence` or `broken`),
with per-feature states and a separate desktop "Open in" block.
`tests/test_readiness.py` drives it with Docker replaced at its boundary;
`tests/test_tool_modules.py` keeps the modules in step with the catalog. Against
the real workers:

```sh
# Tier 0 live, worker and MCP checks from the cache (missing ones show "checking").
uv run joinery status --project testbed
# Run every check now (worker commands run with --network none).
uv run joinery status --project testbed --refresh
# The same report as text, and doctor from the cache alone.
uv run joinery status --project testbed --text
uv run joinery doctor --project testbed --cached
```

## Secret scan

[gitleaks](https://github.com/gitleaks/gitleaks) checks for credentials and
private keys with its default rules, extended by `.gitleaks.toml`. The only
allowlisted path is `godot_patches/godot-src/`, the local upstream Godot
checkout: its test keys and third-party sources look like secrets but are not
ours. With gitleaks 8.30.1 installed:

```sh
# Every commit on every branch.
gitleaks git . --redact
# The working tree, untracked and ignored files included.
gitleaks dir . --redact
```

The CI `secrets` job runs the history scan on every push and pull request, and
before a release. It downloads the gitleaks 8.30.1 Linux binary and refuses it
unless its SHA-256 matches the one published with that release.
`tests/test_ci_workflows.py` checks that pin, the allowlist, and that a release
tag equals the package version.

Tests cannot establish every operation exposed by a third-party application or
MCP. These scenarios cover Joinery's implemented contracts and representative
native workflows. See the [support matrix](support-matrix.md) for capability
boundaries and [execution report](../evals/results/program-regressions.md) for
measured results. The [complete program/MCP evidence matrix](../evals/results/all-mcp-programs.md)
records the broader upstream and native probe outcomes, including failures.
