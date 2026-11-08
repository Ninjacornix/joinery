# Joinery

**Made in the Joinery, not generated.**

![The Joinery editor: the 3D viewport, a staged scene change with Accept and Reject in the Review panel, and the chat sidebar connected to Codex](docs/images/joinery-editor.jpg)

*The editor during review of a large test change: the viewport, the staged diff
with Accept and Reject, and the chat sidebar.*

Joinery is a Godot 4.7.2-based game editor you direct through its AI sidebar,
**Journeyman**. It operates real creative tools and keeps editable native sources,
exports and replay recipes. Blender, Krita, Material Maker, Inkscape,
SuperCollider and Surge XT have verified native authoring workflows. Changes are staged for
your review; asset workers run offline and the shared policy gate excludes
generative asset services. Optional desktop apps and custom tools have different
levels of integration; see the [support matrix](docs/support-matrix.md).

Joinery is agent-agnostic: run it with Claude Code, Codex, Gemini CLI, or opencode, on your own subscription or API key (`joinery auth`). The creation-only gate is wired into whichever agent you use (`joinery agents`).

On native Windows, this alpha supports **Codex only**: its built-in read-only
sandbox supplies the host project boundary. Claude, Gemini and opencode are
explicitly refused there until an equivalent boundary exists. On macOS those
providers require `sandbox-exec`; on Linux they require bubblewrap (`bwrap`).

- Agent rules (read by every agent): [CLAUDE.md](CLAUDE.md)
- Per-project art bible template: [templates/art_bible.md](templates/art_bible.md)

Status: **0.1 alpha** (`0.1.0a1`), unsigned. Native macOS ARM64, Linux x86_64
and Windows x86_64 editor, startup, review/undo, extracted bundle and exported-game
runtime checks pass. See the [desktop verification report](evals/results/desktop-release.md)
for the measured candidate, CI evidence and limits.

Program and MCP regression scenarios and native test commands are documented in
[the testing guide](docs/testing.md).

[Assistant modes and performance](docs/assistant-performance.md) covers model
routing, read-only specialists, native worker reuse and the updated editor UI.

Read the [alpha release notes and installation steps](docs/releases/0.1.0-alpha.1.md)
before using a release bundle. This alpha requires **uv, a running Docker engine,
Git, Node.js/npm, and a signed-in supported agent CLI or a verified API profile**.
Windows worker setup also requires Git Bash. Initial dependency and worker setup
needs internet access and several GB of storage; creative workers subsequently
execute with networking disabled.

See the [implementation map](docs/implementation-map.md) for audited capability
status, the remaining integration work, and readiness checks for each delivery.

In the Joinery editor, use the left app icons to switch between Godot and the
project's creation tools. Tool views show a large preview, readable asset names,
search, and an Open action. They update automatically after source changes.
Scene, FileSystem, Import, History and Inspector live in the bottom tab row;
Journeyman stays on the right without a docking tab bar. Bottom panels open to
roughly half the work area and can be resized by dragging their divider.

Viewport controls and scene tabs are hidden by default. Type `/toolbar` in
Journeyman, use the bottom rail icon, or choose **Joinery: Toggle viewport
toolbar** from the editor's Tools menu to show them. The preference is saved.

Journeyman shows the selected object, audio, or resource in a compact context line above the input and
attaches that target to submitted prompts. Type `/` for a command menu, which
opens upward and supports filtering and arrow keys. Tab or a click completes a
command; Enter sends it in chat. `/help` replies with the command list. System and CLI
diagnostics go to Output. The sidebar starts wide enough for chat and connects the agent selected at startup.

Changes open in a separate **Review** panel below the viewport. Model/image/audio
previews appear before the scrollable file details; Accept and Reject stay visible.
Accepting a model import opens its 3D asset preview automatically. Exporting a
model does not place an instance in the game scene unless that was requested.
The chat status shows the current work phase while the native application runs.
Codex uses its persistent app-server transport; Claude uses stream-json, and
Gemini/OpenCode use ACP. Authentication, transport and tool readiness are separate.
Codex is tested against a real signed-in account. Claude returned an account quota
error during the live check; Gemini/OpenCode protocol tests pass with fixtures,
but their CLIs are not installed on the development machine for live verification.

Live scene tools support node creation, deletion, duplication, renaming,
reparenting, transforms, resources, imported scene instances, existing scripts
and persistent signals. Accept records native undo/redo; Reject restores the
preview. Saved scenes/scripts and creative documents use isolated project
transactions, conflict checks and durable recovery journals.

The shared MCP gateway installs the catalogued Godot, official Blender,
SuperCollider and Inkscape servers at exact revisions. First-party servers operate
Blender native documents/parts, Material Maker graphs, Krita layers, Aseprite Lua
and SuperCollider NRT patches. Their offline workers save native sources and
exports, record replay/provenance, validate Godot import, and show before/after
images or audio for review. Existing source edits preserve the owning document;
replay preimages are kept separately from editable sources. Aseprite still needs
a user-licensed executable in the worker image. Surge XT renders offline in
the worker from named parameters and notes; SuperCollider remains available too.

Art creation requires approval of the project's completed `art/bible.md`.
Use `/art` to review and approve its current version. Changing its content
invalidates approval. An agent cannot approve its own direction or file changes.
Use `/tools` for actual MCP connection/probe status; diagnostics stay in Output.

For an installation with a matching Joinery editor already built or bundled:

```sh
uv sync --frozen
uv run joinery new /path/to/game --name "My Game" --tools godot,blender
uv run joinery bootstrap --project /path/to/game
uv run joinery mcp status --project /path/to/game --probe
uv run joinery launch                     # start at agent connection/project manager
uv run joinery launch --project /path/to/game  # update and open a closed project
```

Run commands from the extracted runtime/source folder. For an existing project,
use `joinery setup --project /path/to/game` instead of `new`. A source checkout
also needs the patched editor built first; the release notes cover that path.
Bootstrap installs the configured servers and offline worker images, and may
take a while on the first run. Review its `unavailable` results and the MCP probe;
having a desktop application installed does not establish MCP readiness.

`update-addon --project ...` keeps an addon backup and updates only managed gate
commands; it refuses to hot-replace the addon in a running project editor.
`transactions list|recover|revert --project ... [--id ...]` provides durable file
recovery/revert. Native scene edits remain in Godot's undo history and must be
saved normally. A new authoring connection recovers interrupted file commits and
discards abandoned previews before accepting requests. Concurrent authoring
hosts for one project are refused.

`diagnostics --project ...` reports the engine build/UUID, addon revision, provider
state and transaction summaries without credentials. `package --output ...tar.gz`
bundles the matching engine and source with measured file hashes;
`package --desktop --output ...zip` adds the platform launcher/application layout.
These alpha bundles contain source and a platform-specific editor, and use uv
for their Python runtime. They do not bundle agent subscriptions, Docker, or all
optional desktop applications.

Model previews support drag to orbit, wheel scrolling and trackpad zoom without
extra buttons. Audio previews include a clickable waveform, seeking, play/pause,
stop, restart, five-second skips, looping, volume, mute and playback speed.
Speed changes pitch. These controls affect the preview only.

The Project Manager first connects a local Claude Code, Codex, Gemini or opencode
account. Choose an agent and use its provider-owned sign-in; successful sign-in
advances automatically. Selection and configuration are required to advance.
Provider marks are imported from locally installed apps/extensions.
After connection, create or open a project; Change agent returns to connection.
The selected agent sidebar connects after the editor opens.

The **API / additional model** submenu accepts a base endpoint, model ID and
masked API key. It can also activate an environment key. Custom profiles use
Codex (Responses API) or opencode (OpenAI-compatible API) and are saved outside
projects in a private local file. Keys never enter subprocess arguments or logs.

Project setup uses a fixed window with horizontal renderer choices and Back/Next
navigation. Preferred MCPs are selected by default; other candidates can be
configured as project custom tools by supplying an executable and JSON arguments.

The editor's **Tools** workspace previews project assets and opens their native
sources in Blender, Krita, Audacity, GIMP, Inkscape, Aseprite, REAPER, MuseScore,
SuperCollider, Material Maker, or a configured custom application. Choose a tool
to filter its assets, select an asset, and use **Open in**. Changes saved in the
desktop app are detected automatically. Provenance and recipe information stay
available through the CLI.
Desktop viewing works independently of whether a tool's MCP is ready.

Apps are detected on PATH and in the usual macOS Applications folders. For a
different install location, add an executable or macOS app bundle to the game
project's `joinery.toml`:

```toml
[desktop.krita]
command = "/Applications/krita.app"

[desktop.audacity]
command = "/path/to/audacity"
```

Custom tools declared in `[[tools.custom]]` can add a separate desktop command:

```toml
[desktop.my-painter]
command = "/path/to/painter"
args = []
extensions = [".paint", ".png"]
```

Recipes may declare `sources = ["art/painting.kra"]` to link an exported image
to its editable document. When omitted, the workspace uses native outputs of
that recipe. Viewing and opening leave provenance records unchanged; file hash
agreement does not claim that recipe replay has passed.

CLI equivalents: `joinery workspace --project <path>` and
`joinery open art/painting.kra --tool krita --project <path> --dry-run`.
