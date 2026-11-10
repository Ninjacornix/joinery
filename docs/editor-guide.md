# Joinery editor guide

The full reference for the editor, the command line and project settings. The
[README](../README.md) has the short version.

In the Joinery editor, use the left app icons to switch between Godot and the
project's creation tools. Tool views show a large preview, readable asset names,
search, and an Open action. They update automatically after source changes.
Scene, FileSystem, Import, History and Inspector live in the bottom tab row;
Journeyman stays on the right without a docking tab bar. Bottom panels open to
roughly half the work area and can be resized by dragging their divider.

Scene tabs sit above the viewport: click a tab to switch between open scenes,
or **+** at the end of the tabs to make a new scene (also **Scene > New Scene**,
Ctrl+N or Cmd+N). The 3D viewport controls are hidden by default. Type
`/toolbar` in Journeyman, use the bottom rail icon, or choose **Joinery: Toggle
viewport toolbar** from the editor's Tools menu to show them. The preference is
saved.

### Classic Godot view

If you prefer Godot's own layout, switch to it from **Editor > Switch to
classic Godot view**, from the command palette, or in Journeyman settings under
**Appearance > Layout** (Joinery or Classic Godot). In Classic Godot the
docks go back to their stock places, the 2D, 3D and Script buttons return to
the title bar, the run bar is Godot's own and Journeyman is an ordinary
dock under the Inspector. **Switch to Joinery view** goes back. The switch
applies after a restart; the dialog offers Restart or On next start. Each
layout remembers its own docks, and the theme stays as you set it. The setting
is `interface/joinery/editor_layout` in Editor Settings
([ADR 0031](adr/0031-classic-godot-view.md)).

Journeyman shows the selected object, audio, or resource in a compact context line above the input and
attaches that target to submitted prompts. Type `/` for a command menu, which
opens upward and supports filtering and arrow keys. Tab or a click completes a
command; Enter sends it in chat. `/help` replies with the command list. System and CLI
diagnostics go to Output. The sidebar starts wide enough for chat and connects the agent selected at startup.

Changes open in a separate **Review** panel below the viewport. Model/image/audio
previews appear before the scrollable file details; Accept and Discard stay visible.
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
and persistent signals. Accept records native undo/redo; Discard restores the
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
A bible drafted from your own answers when the project is made (the New project
wizard, or `joinery bible` reading its answers file) counts as approved at
creation, recorded as `user (at project creation)`; a draft with required
sections left unset still needs finishing first. Changing its content
invalidates approval; use `/art` to review and approve the current version
again. An agent cannot approve its own direction or file changes (ADR 0033).
Use `/tools` for actual MCP connection/probe status; diagnostics stay in Output.

### Sidebar commands

- `/feature` or `/feature list` says what the game has and what can be added.
  `/feature add|remove <id>` shows a plan first (files, settings in plain words,
  notes and conflicts). Nothing changes until you press **Apply**; Journeyman
  never applies a feature itself. After it is applied the card offers
  **Revert**, which shows **Reverting…** while it runs, or **Too old to revert**
  once its restore data is gone. When `project.godot` changed, the editor
  offers to reload the project so its settings in memory never overwrite the
  new ones.
- `/brief` opens `design/brief.md` read-only, with **Ask Journeyman to change
  it**. With no brief, **Write the brief** drafts one from the starting point.
- `/tools` gives each app's state and its one action (for example Start Docker
  or Rebuild), the same report as the app rail and project setup. When an app
  needs something, the sidebar header says so in one line.
- When a request needs art and the art direction is not approved yet, the
  request's card stops at the art gate: **Review art direction** opens `/art`,
  and after approval the held request goes out again.
- Changes are accepted or **Discarded** in the Review panel. **Compact** in
  history asks for confirmation inline before it frees space.

### Picking a part of a model

In the Tools workspace, a model's parts list shows its objects, materials and
maps; pick one, or drag it onto the sidebar, to make it the target. In the 3D
viewport, Alt+click picks the material under the pointer (on stock Godot only
while the model's root is selected). Typed edits then stay inside that part,
and review says what else changed.

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
use `joinery setup --project /path/to/game` instead of `new`.

### Starting from a blueprint

`joinery new` makes the same project as the Project Manager's new-project
wizard. Add `--blueprint` to start from a small playable game:

```sh
uv run joinery blueprints                 # the starting points
uv run joinery features                   # what a game can add
uv run joinery new /path/to/game --tools godot,blender \
  --blueprint first-person-3d --features controller,save-load
# With Steam: the app ID is digits only; leave it out to add it later.
uv run joinery new /path/to/game --tools godot --blueprint top-down \
  --features online-play,steam --steam-app-id 480 --connect steam
```

Without `--features` the blueprint's suggested features are used;
`--features ""` adds none. Every new project gets the Joinery addon with its MIT
licence (the addon ships inside your game) and the plugin turned on. With a
blueprint it also gets the game, `design/brief.md` with the controls the game
really binds, and an `art/bible.md` draft whose technical sections come from the
blueprint's preset, marked as suggestions. Finish and approve the bible before
any asset work; a bible drafted from the wizard's answers with every required
section answered is approved at creation. A bad feature mix is refused before any folder is made.

With Steam on, Joinery writes `[game.steam]` (app ID and how players connect)
and downloads the pinned GodotSteam extension into the project, checked against
the SHA-256 recorded in the repo and listed in `joinery.lock`. If the download
fails, the project is still made: the game runs, and Steam calls do nothing
until the extension is there. Set `JOINERY_NO_DOWNLOADS=1` to skip the download
(tests, offline machines). Joinery never publishes anything to Steam.
`--connect` only matters with Online play: players meet through Steam (the
default) or by direct IP.

The wizard runs the same step as `joinery game apply --project <path>
--blueprint <id> --features a,b [--steam-app-id N] [--connect steam|direct]`,
which prints one JSON line per finished step. `joinery blueprints --json` is the
catalogue the wizard shows: each blueprint's screenshot, and for every feature
whether it can be chosen there, why not, and what it adds.

### Changing features

Adding or removing a feature is a reviewed change, like any other:

```sh
uv run joinery feature list --project /path/to/game
uv run joinery feature add translations --project /path/to/game --plan   # what would change
uv run joinery feature add translations --project /path/to/game          # review, then y/N
uv run joinery feature remove steam --project /path/to/game --yes
```

`--plan` writes nothing and prints the files, the `project.godot` edits and any
notes as JSON; the sidebar shows the same before and after. Without `--yes` the
change is staged and you are asked; with `--json` it waits for your review
(`joinery transactions accept|reject --project <path> --id <id>`). Files you
edited after a feature added them are kept on removal. Adding Steam downloads
GodotSteam once the change is accepted; removing it removes the extension. With
Translations, Joinery offers the command that rebuilds the list of text to
translate.

### Brief, bible and checks

```sh
uv run joinery brief --project /path/to/game        # write design/brief.md (setup answers, else the starting point)
uv run joinery brief --project /path/to/game --json # the brief's summary and suggestions
uv run joinery bible --project /path/to/game --upgrade   # bring an older bible up to date
uv run joinery check bible --project /path/to/game  # gaps, reference board and suggestions
uv run joinery check catalogue                      # every blueprint and feature is valid
```

The bible upgrade only adds sections and markers. It is a reviewed change, and
because the bible's text changes you approve it again with `/art`.

New bibles and briefs start with a short "How to write in this file" note. When
a section uses filler words (stunning, immersive, seamless, vibrant, elevate,
delve, tapestry, unleash, cutting-edge, game-changer), the bible check and the
brief check suggest plainer words. It is only a suggestion and never blocks.

### Tools and agents

`joinery status --project <path>` shows what each of the project's tools can do
right now (`--text` for plain text, `--catalog` for every tool, `--refresh` to
run every check again, `--agents` to add each agent's connection). `joinery doctor` reads the same report and runs the
checks the cache cannot answer in the offline workers; `--cached` answers from
the cache only and `--refresh` runs everything. Nothing needs installing on the
host besides the Joinery editor.

`joinery connect status` only says an agent is installed and signed in.
`joinery connect verify --agent codex [--project <path>]` sends one tiny message
through the real chat bridge (one small model call), records the answer, and
exits 0 only when it worked. The agent picker and settings then show a
**verified** badge; quota or sign-in problems show as their own message
instead. A source checkout
also needs the patched editor built first; the release notes cover that path.
Bootstrap installs the configured servers and offline worker images, and may
take a while on the first run. Review its `unavailable` results and the MCP probe;
having a desktop application installed does not establish MCP readiness.

`update-addon --project ...` keeps an addon backup and updates only managed gate
commands; it refuses to hot-replace the addon in a running project editor.
`transactions list|accept|reject|recover|revert --project ... [--id ...]`
decides a waiting change and provides durable file recovery/revert. Native scene edits remain in Godot's undo history and must be
saved normally. A new authoring connection recovers interrupted file commits and
discards abandoned previews before accepting requests. Concurrent authoring
hosts for one project are refused.

`diagnostics --project ...` reports the engine build/UUID, addon revision, provider
state and transaction summaries without credentials. `package --output ...tar.gz`
bundles the matching engine and source with measured file hashes;
`package --desktop --output ...zip` adds the platform launcher/application layout.
These bundles contain source and a platform-specific editor, and use uv
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

The new-project wizard has three steps in one fixed window. **What are you
making?** shows the starting points as cards with a screenshot, then the name,
folder and the game in one sentence (the renderer is under Advanced).
**Features and apps** has a switch per feature: Steam asks for the Steam app ID
inline (digits only, empty to add it later) and, with Online play on, how players
connect (Steam or Direct IP). The apps sit behind one line with **Change…**;
preferred MCPs are on by default, and other candidates can be set up as custom
tools with a command and arguments. **Brief and art direction** asks the brief
and art questions, pre-filled from the starting point.

The editor's **Tools** workspace previews project assets and opens their native
sources in Blender, Krita, GIMP, Inkscape, SuperCollider, Material Maker,
or a configured custom application. Choose a tool
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

[desktop.gimp]
command = "/path/to/gimp"
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
`joinery parts <model> --project <path>` lists the objects, materials and maps
of a model (a GLB or its source document). `joinery history usage|compact
--project <path>` shows or frees the space kept for undoing accepted changes.
