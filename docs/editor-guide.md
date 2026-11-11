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

### How a change reaches you

Each request makes at most one change, and Joinery, not the agent, decides when
you see it. When Journeyman has finished, it hands the change over with
`submit_for_review` (the same tool for Codex, Claude Code, Gemini CLI and
opencode). Joinery then checks it: art bible rules, the Godot import, scenes
that point at missing files, asset provenance and the project's own GUT tests
when it has them. A problem goes back to Journeyman so it can fix it in the
same turn, up to three tries. The review opens as soon as the checks pass, with
Accept ready; model renders fill in a moment later. While the change waits for
you, Journeyman can still answer questions but changes nothing.

If Journeyman stops without handing the change over, Joinery submits it for it.
If that fails, Journeyman gets one more try. If the change still fails, Joinery
drops it and says so in its own line ("Journeyman's change didn't pass Joinery's
checks: … Nothing was changed."), so you are never left waiting for a review
that will not come.

The scene on screen is changed live in the viewport, so you see the change
before you decide; its Accept and Discard appear once the change is submitted.
So is any scene with unsaved edits, and a new scene not saved yet. Every other
scene is changed as a staged file and reloads when you accept. One request
never changes the same scene both ways.

When a conversation nears the model's context limit, Joinery asks Journeyman for
a short summary, adds what it recorded (changes accepted, discarded and still
open, and your recent requests), and continues in a fresh session from it. The
chat shows one quiet line when this happens. The summary is kept with the saved
chat (or in `.joinery/summaries/`, the newest 20, when the chat can't be saved).
Decision record: ADR 0035.

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
creation, recorded as `user (at project creation)`, even with questions
skipped: those sections stay open to fill in later in `/art`. So does a bible
drafted from the starting point's preset alone. Changing its content
invalidates approval; use `/art` to review and approve the current version
again. An agent cannot approve its own direction or file changes (ADR 0033).
Use `/tools` for actual MCP connection/probe status; diagnostics stay in Output.

### The Review tab

**Review** is always the first tab of the bottom bar (in Godot's classic view
too, beside Output). Its title counts what waits for you: **Review 2**.

- **Waiting for you**: every change that needs a decision, one line each:
  what it does, which app made it and when (`Blender · 3 min ago`), with
  **Discard** and **Accept** on the row. Clicking the change Journeyman just
  made opens its full before/after review; **All approvals** goes back.
  Clicking any other waiting change (for example one made with
  `joinery feature add` in a terminal) shows its files under the row.
  With nothing waiting it says "Nothing waits for your review."
- **Art bible**: one row, **Approved at creation** or **Approved by you**
  with **Open**; when it changed since, it moves under Waiting for you as
  "Needs your approval before new art or audio" with **Review** (`/art`).
- **Decided**: accepted, discarded and reverted changes, newest first, with
  **Revert** while the change can still be put back and **Too old to revert**
  once its restore data is gone (the same Revert as in the chat).

The list comes from `joinery transactions list --project <path>`, which
prints each change's state, summary, apps, times, files and the art bible's
approval as JSON. The tab reads it when it is shown, when you come back to
the editor and whenever a change is staged or decided; it never polls.

### Several agents at once

While Journeyman works on a request, more agents can work beside it, each on
its own part (ADR 0036). Each makes its own change, and you review each one on
its own.

- **Run alongside**: type a message while Journeyman works and press **Run
  alongside** beside Stop. The message starts at once as its own task instead
  of waiting in the queue (Enter still queues it).
- **Journeyman splits a request** itself when its parts are independent, such
  as a chair model and a lamp model: it hands a part to a task with
  `start_task`. It keeps dependent steps (a model and the scene that places
  it), small requests and anything in the same files for itself.
- Each task shows as a card above the message box: its title, the apps at
  work, what it is doing, a timer and its own **Stop**. While two or more
  agents work, the top of the sidebar says **3 agents working**.
- When a task's change passes Joinery's checks its card says **Ready for
  review**, and **Review** opens the Review tab, where the change is its own
  row with **Accept** and **Discard**. Accepting one change never touches
  another's. When the task is done its card leaves one line in the chat, such
  as "Make a desk lamp: change accepted."
- Two agents never change the same file. A task that tries gets a plain
  answer ("`art/props/lamp.glb` is being changed by another task: Make a
  desk lamp. Work on something else or wait.") and leaves it alone. If a
  change you accepted meanwhile touched a file a task also changed, the task
  redoes its part on top of yours before its change reaches you. Only
  Journeyman's own request changes the scene on screen; tasks change other
  scenes as files.
- **Stop** on Journeyman's request asks once whether to stop the tasks too:
  **Stop all** or **Keep them**.
- Blender and Krita work on one document at a time, so tasks using them take
  turns; the waiting task's card says so.
- `[agents] parallel = 3` in `joinery.toml` is how many agents may work at
  once, Journeyman included (at most 6). `parallel = 1` turns tasks off.
  Each task is a separate session with your provider and uses its own
  context and tokens. Saved chats keep each task and what became of it.

### The Timeline tab

**Timeline**, beside Review in the bottom bar, shows how the project changed
over time, newest first. (Godot's own **History** dock is the undo history of
this session; the Timeline is the project's long memory.) Each change you
accept becomes one entry, and so does each revert: one line with what it did,
who made it, how many files and when (`Ada Lovelace · 3 files · 2 h ago`).
Type in **Filter** to keep entries whose message, author or a changed file
matches. Entries load 50 at a time; **Show older entries** reads the next 50.

Click an entry to see its files. Each opens in place: coloured added and
removed lines for scenes and scripts, before and after pictures for images,
and sizes for models, sounds and other binaries (`Model changed · 1.2 MB →
1.4 MB`). Under it:

- **Restore this file as it was** puts that version back as an ordinary
  change waiting in the Review tab, so you see it before anything on disk
  changes. A file the entry deleted comes back as it was just before. The
  timeline is never rewritten, and Journeyman can't do this for you.
- **Open file** opens the file as it is now.

The timeline is the project's own git repository. A new project starts one
when it is made, with a first entry **Start <name>**. An existing project
never gets git by itself: its Timeline tab offers **Start keeping a
timeline**, which sets up git in the project folder (with Joinery's restore
data and Godot's cache ignored) and makes the first entry. Without Git
installed the tab says so; a project inside a bigger repository is left to
you.

Only the files of each accepted change go into its entry; your own unsaved
work and anything you staged stay as they were. Entries are made with your
git name and email, or "Joinery user" when git has none, never an agent's.
A scene edit accepted live in the 3D view is on disk only once you save the
scene, so it joins the timeline when you save it. Switch it all off with
**Timeline** in Journeyman settings (`[timeline] auto_commit = false` in
`joinery.toml`). The command line is `joinery timeline list|show|restore
--project <path> --json` (ADR 0034).

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
- **Older versions**, under History in Settings, sets how many accepted
  changes keep the older versions of the files they replaced (models,
  textures, sounds), so they can be reverted: **Last 50 changes** (up to 2 GB,
  the default), **Last 10 changes** (up to 1 GB), **Only the newest**, or
  **Custom…** (changes to keep and space in GB). A choice that would remove
  restore data says so first ("3 older changes can no longer be reverted.
  Frees 120 MB.") and applies only on **Keep fewer**. Project apps (`/setup`)
  has the same choice under **Older versions** and saves it with **Save**.
- While Journeyman works or a change waits for review, Enter queues the
  message instead of dropping it (the box says "Queue a message…"). Queued
  messages wait under **Queued** at the end of the conversation, each with its
  target chips, **Edit** (puts it back in the message box) and **×** (removes
  it). They go out one at a time, oldest first, when the reply finishes and
  nothing waits for review; a change under review is accepted or discarded
  first. After a reply that failed or was stopped, the queue waits and offers
  **Send queued message**. A lost connection keeps the queue. Up to five
  messages can wait. Editor commands such as `/art` still run at once; others
  such as `/feature` queue like messages.

### Showing Journeyman what you see

The eye button under the message box, **Share my view**, takes a screenshot
of the viewport you are looking at (the 3D viewport, or the 2D one when that
is showing) and puts it above the message as a thumbnail. It goes with your
next message; **×** on the thumbnail takes it off. The request in the
conversation keeps a small copy.

Journeyman can also look by itself: when you say "look at this", it calls the
read-only `view_editor` tool, which returns the same screenshot together with
the editor camera's position and direction (zoom and visible area in 2D). The
request shows the step "Looking at your view" when it does.

To give Journeyman other pictures (a sketch, a reference, a screenshot of a
bug), paste an image with Cmd+V or Ctrl+V, paste image files copied in your
file manager, drop image files on the message box (from your file manager or
the FileSystem dock), or click the paperclip to choose them. PNG, JPEG and
WebP work. Each one is scaled to at most 2048 pixels on its long side, and up
to five images go with one message. A file dropped anywhere else in the
editor is imported into the project as before.

How each agent receives them: Codex as images (`localImage` input), Claude
Code as image blocks in its stream-json input, and Gemini CLI or opencode as
images when the agent says it accepts them; otherwise the agent gets the
images' file paths in the message and opens them itself.

Screenshots and attached images are references for Journeyman only, never
assets. They are saved in `.joinery/attachments/` in your project, which is
kept out of git and out of Godot's import, and never under `art/`; Joinery
does not copy them anywhere else.

### Context and token use

Once the agent reports its token use, a small ring appears in the sidebar
header beside the menu. It fills with how much of the model's context window
the conversation holds, and turns red from 90%, when the conversation is close
to the model's limit. Hover over it for the numbers: context used of the
window, this session's input tokens (with the part read from the provider's
cache) and output tokens, and the model. Reconnecting starts a new session and
the count over.

The numbers come from the agent's own reports, after each turn and never by
polling: Codex's token usage updates, the usage in Claude Code's stream-json
results, and Gemini CLI or opencode when they send usage over ACP. An agent
that reports nothing shows no ring; one that reports tokens but not its
context window shows an empty ring with the counts.

### Saved chats

Every conversation with Journeyman is saved in your project as you go, one
file per chat in `.joinery/chats/` (kept out of git and out of Godot's
import). A chat keeps your requests with their targets, the paths of any
images you attached (the images themselves stay in `.joinery/attachments/`),
Journeyman's replies and steps, the changes it proposed and how each turn
ended, plus which agent answered. A chat file stays under 1 MB by dropping
its oldest turns, and a project keeps its 100 most recent chats.

When you open the editor again, your most recent chat is back in the sidebar,
and the next message continues it. The two buttons in the sidebar header,
left of the menu, are **Chats** and **New chat**:

- **New chat** clears the conversation; your next message starts a new chat
  with a fresh agent session.
- **Chats** lists this project's chats, newest first: the title (your first
  request, until you rename it with the pencil), which agent answered and when
  it was last used. The open chat says **Open**. Choosing another chat shows
  it read-only, with **Continue** where the message box was; Continue makes it
  the open chat.

Change cards in a restored chat show what became of each change now, as
`joinery transactions list` reports it: accepted (with **Revert** while its
restore data is kept), **Too old to revert**, reverted, discarded, or not
reviewed.

How a chat continues depends on the agent:

| Agent | Continuing a saved chat |
| --- | --- |
| Codex | Resumes the same Codex thread (app-server `thread/resume`). |
| Claude Code | Resumes the same session (`--resume`), while Claude Code still keeps it for this project. |
| Gemini CLI, opencode | Reopens the session with ACP `session/load` when the agent offers it. |

When the agent can't resume (an agent without session loading, a session the
agent no longer has, another agent or API connection than the one that saved
the chat), a fresh session starts with a short summary of the chat's last
turns and what became of each change, and the conversation says "Continued
with a summary; the agent starts fresh."

`joinery chats list|show|rename|delete --project <game>` reads and manages the
same files from the command line.

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
blueprint's preset, marked as suggestions. That bible is approved at creation,
with any skipped sections left open to fill in later. A bad feature mix is refused before any folder is made.

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
tools with a command and arguments. The step ends with **Keep older
versions**: the last 50 changes up to 2 GB (the default), the last 10 up to
1 GB, or only the newest. **Brief and art direction** asks the brief and art
questions, pre-filled from the starting point.

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
`joinery history policy --project <path> [--keep N] [--max-mb M] [--dry-run]`
reads or sets `[history]` in `joinery.toml` and removes what a smaller choice
no longer keeps, saying how much that frees; `joinery new`, `joinery game
apply` and `joinery setup` take `--history keep-50|keep-10|latest|custom` (or
`--history-keep N --history-max-mb M`). The choice bounds restore data for
accepted changes, which is where older versions of models and textures live.
Starting documents that current recipes replay from (`recipes/inputs`) and
replaced recipes (`recipes/history`) are kept until **Compact**, which keeps
only what replaying the current state needs.
