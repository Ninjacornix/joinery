# Joinery editor guide

The full reference for the editor, the command line and project settings. The
[README](../README.md) has the short version.

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
Blender native documents/parts, Material Maker graphs, Krita layers (pixel art
too), Inkscape SVG and SuperCollider NRT patches. Their offline workers save native sources and
exports, record replay/provenance, validate Godot import, and show before/after
images or audio for review. Existing source edits preserve the owning document;
replay preimages are kept separately from editable sources. Surge XT renders
offline in the worker from named parameters and notes; SuperCollider remains
available too. Aseprite, REAPER, MuseScore and Audacity are no longer built-in
tools (ADR 0025); add any of them as a custom tool if you want it.

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

The **Use an API key instead** page accepts a base endpoint, model ID and
masked API key. It can also activate an environment key. Custom profiles use
Codex (Responses API) or opencode (OpenAI-compatible API) and are saved outside
projects in a private local file. Keys never enter subprocess arguments or logs.

Project setup uses a fixed window with horizontal renderer choices and Back/Next
navigation. Preferred MCPs are selected by default; other candidates can be
configured as project custom tools by supplying an executable and JSON arguments.

The editor's **Tools** workspace previews project assets and opens their native
sources in Blender, Krita, GIMP, Inkscape, SuperCollider, Material Maker,
Audacity (when installed), or a configured custom application. Choose a tool
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
