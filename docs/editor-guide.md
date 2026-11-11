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
SuperCollider and Inkscape servers at exact revisions. The Godot server is built
into the offline MCP worker image and runs there with no network, next to that
image's headless Godot; it reads your project read-only, and every change it
makes is staged in its own worker for review. First-party servers operate
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

### Reviewing a change

A review is made to be taken in at one look (ADR 0040). The same compact layout
shows in the Review tab, the bottom panel and for every parallel task:

- **The change itself**, as large as the panel allows:
  - a texture, a selected material or map, or a live scene edit: one picture
    split by a line you can drag, the original on the left and the change on
    the right;
  - a model: its front and its far side next to each other;
  - a sound: its waveform with play, so Before and After play A/B at the same
    position;
  - code and other text: only the lines that changed, removed above added.

  A change with several items gets a filmstrip of small tiles under it, the
  most important first; click a tile (or use Left and Right) to show it.
  **Before** and **After** in the corner show one side in full; with neither
  pressed a texture shows the split.
- **One line of facts**: what changed in plain words, for example
  "Door 20% wider · wood darker · 2 files". The words come from Journeyman
  (`submit_for_review` takes up to three short `facts`), then from what the
  review can measure itself (an image darker, lighter, warmer or resized, a
  sound longer or shorter, lines added and removed), then the counts.
- **The checks as one quiet line**: "Checks passed" (a review only opens once
  they pass); hover it for what ran. When a reviewer agent looked at the
  change, its notes follow as one line, the most serious first ("The hinge
  side lost its bevel · 1 more").
- **Discard** and **Accept**, always on screen. **Details** beside them opens
  everything else on demand: each file with its diff, the checks that ran and
  every note.

With the focus in the review (click anywhere in it), **Enter** accepts,
**Backspace** or **Delete** discards, and **A** or **B** flips between before
and after.

In the chat, the request's card shows the waiting change as a tiny picture,
the same line of facts and **Discard** / **Accept**, so a small change is
decided without opening the panel. A bigger one (several assets, many changed
lines, or a reviewer's warning) offers **Review** instead, which brings the
panel up.

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
  work, what it is doing (first "Starting Codex CLI…", or your provider, then
  its own steps), a timer and its own **Stop**. While two or more
  agents work, the top of the sidebar says **3 agents working**.
- When a task's change passes Joinery's checks its card says **Ready for
  review**, and **Review** opens it in the Review tab with the same full
  review as Journeyman's own change: before and after, Details, **Accept** and
  **Discard**. Its row in the list opens it too. One review shows at a time;
  opening another row swaps it, and a task's change never pops up over
  Journeyman's own review. Accepting one change never touches another's. When the task is done its card leaves one line in the chat, such
  as "Make a desk lamp: change accepted."
- Two agents never change the same file. A task that tries gets a plain
  answer ("`art/props/lamp.glb` is being changed by another task: Make a
  desk lamp. Work on something else or wait.") and leaves it alone. If a
  change you accepted meanwhile touched a file a task also changed, the task
  redoes its part on top of yours before its change reaches you.
- One agent at a time changes the scene on screen live. While Journeyman's
  own request works, it keeps the scene and tasks change other scenes as
  files; once it is idle, a task may change the open scene live, and its
  preview is reviewed when that task finishes. Deciding the preview hands
  the scene on to the next agent.
- Game features change one at a time: while a task's change adds or removes
  one, `/feature` says which task has them ("Game features are being changed
  by another task: …"), and the other way round.
- **Stop** on Journeyman's request asks once whether to stop the tasks too:
  **Stop all** or **Keep them**.
- Blender and Krita work on one document at a time, so tasks using them take
  turns; the waiting task's card says so.
- `[agents] parallel = 3` in `joinery.toml` is how many agents may work at
  once, Journeyman included (at most 6). `parallel = 1` turns tasks off.
  Each task is a separate session with your provider and uses its own
  context and tokens. Saved chats keep each task and what became of it.

### Trying a few ways

When you aren't sure how something should look, sound or play, let Journeyman
make it a few different ways at once and keep the one you like (ADR 0042).

- `/variants 3 a wooden door for the cabin` makes three versions side by side
  (2 to 4; 3 when you leave the number out). Add your own directions after the
  request: `/variants a wooden door | chunky and worn | slim and clean`.
  Otherwise Joinery gives each way its own short direction, chosen by what you
  asked for (a look, a sound, music, code or a level).
- **Try 3 ways** in the message box's options menu (the mode picker) does the
  same for the message you typed.
- **Try other ways** on a change waiting for your decision keeps that change as
  the first way, **As made**, and makes two more beside it.
- Journeyman may offer it in one line when you're unsure; it only starts
  variants when you ask for options or say yes.
- The ways show as one card in the chat: each way's picture (a render or a
  texture), a play button for a sound, or the first changed lines of a script,
  with its number and direction ("2 · slim and clean") and one line of facts.
  A way still working shows its step; one waiting for a free agent says so.
  Click a picture for that way's full before and after in the Review tab, where
  the same strip sits above it.
- **Use this** keeps that way: it is accepted as one Timeline entry, and every
  other way is discarded (or stopped, if it still works) at once. Accepting a
  way from its own row in the Review tab does the same. If keeping it fails,
  nothing else changes and you can choose again.
- **Mix…** asks for one more way from what you liked: "door shape from 2,
  colour from 3". **Discard all** throws every way away.
- Each way is its own agent, checked by Joinery and read by the reviewer like
  any change, and they all count towards `[agents] parallel`; ways over the
  limit wait their turn. The ways change files, never the scene on screen
  live: a scene that is open is changed as a file and reloads once you keep a
  way. Saved chats keep the ways and which one you kept.

### Reviewer and debugger agents

Two more kinds of agent help, and both only read (ADR 0037).

- **The reviewer** reads each change after it passes Joinery's checks and
  before you see it: your request, the diff, the art bible, the game brief,
  the check results and the staged images. The request's card says
  "Reviewing the change…". If it finds a real problem it sends the change
  back to Journeyman to fix, at most twice; then the review opens with
  whatever notes are left: the worst as the review's notes line ("… · 2
  more"), every note in **Details**. A reviewer that takes longer than 90
  seconds, or fails, is skipped and the review's status says so ("Reviewer
  ran out of time").
  It never accepts, discards or changes anything; you decide.
- **The debugger** works on an error. When Godot's Output shows a new error
  (in the editor or while the game runs), the sidebar offers it once:
  "Godot reported an error: … **Debug** / **Dismiss**". `/debug` does the
  same for the last error, and `/debug the player can't jump` adds your own
  words. A card "Debugging: <error>" follows it: the debugger reads the
  error, the Output text and the game's log, looks at the scripts and
  scenes, and can run the game headless in the offline worker to see the
  error happen (no network, a throwaway copy of the project). Then a worker
  agent makes its fix as a normal change, checked and reviewed, and it opens
  in the Review tab. If nothing in the project needs to change, the card
  ends with the cause.
- Settings → Project → **Reviewer agent** turns the reviewer on or off, and
  **Reviewer model** picks its model (empty: the one Journeyman uses). They
  are `[agents] reviewer` and `reviewer_model` in `joinery.toml`, also set
  with `joinery reviewer --set on|off --model <name>`. `joinery transactions
  list` shows each change's `review_notes` and `reviewer`.

### Playtests

`/playtest` asks the playtester to play your game and tell you what went wrong
(ADR 0038). Words after it say what to aim for: `/playtest the lever and the
coins`. The playtester is your provider in the read-only `playtester` role (ADR
0037): it never changes the game, and it can't.

- A **Playtest** card shows above the message box with the other agents at
  work: **Playing… 0:42 / 2:00** (game time) and its own **Stop**. A bot plays
  the game headless in stock Godot, offline, through the game's own controls:
  it walks, jumps and presses interact, heads for the coins, pickups,
  checkpoints, switches and goals a blueprint marks, pauses and resumes, saves
  and loads where the game can, and checks the menus' text in every language
  the game has. It records script errors and warnings, crashes, freezes,
  softlocks, falls out of the world, frame hitches, memory that keeps growing,
  text that doesn't fit, and how long each level took.
- When the run is over the card shows one count per severity (**1 high · 2
  low · 1 note**) and each finding as a row. A row opens to what happened,
  where in the code, the seed and a screenshot of the moment. **Fix this** puts
  a ready request in the message box, with the seed to check the fix with;
  nothing is sent until you send it. **×** dismisses the card.
- The playtester's own report appears in the chat: bugs with repro steps,
  softlocks and balance notes, most serious first. Each fix it proposes is a
  line with **Ask Journeyman to fix**, which also only fills the message box.
- Balance notes come from a bot pressing keys with some aim, not a person:
  "finished in 0:09, may be too easy", "died 9 times without finishing".
- The same seed plays the same run: `joinery playtest --project <path> --seed
  4821 --seconds 120` repeats it from the command line (`--json` for the full
  report, `--scene res://…` for one scene, `--screenshots` for pictures).
  Each run's report and pictures stay in `.joinery/playtests/`, the last ten
  runs; the game's own saves and settings during a run go there too, never
  into your real ones.
- Journeyman can play the game the same way with its `playtest` tool, for
  example after changing how the player moves; its runs show on the same card.
- `[agents] playtest_after_change = true` in `joinery.toml` starts the
  playtester after you accept a change to scripts or scenes (off by default).
  `playtest_minutes = 2` sets how long a run is.
- Screenshots come from a second run of the same seed in a window that never
  takes focus. Where nothing can draw (Linux without a display) the report
  has no pictures.

### Making everything match

`/match` checks the whole project against the approved art bible's checked
rules and fixes what drifts, as reviewed changes (ADR 0045). **Make it match**
in Settings, on the Review tab's art bible row and at the foot of the art
direction does the same. No model is needed to check.

- A **Match** card shows above the message box with the other agents at work:
  one headline (**9 things drift from the art bible**), one row per area with a
  bar and its score (**Palette 72%**: the share of textures on the palette), and
  the three most serious findings, each with what was measured and what the
  bible asks (`wall.png 100% off-palette → ≤ 5%`). **Show all** lists the rest;
  hovering one says how it would be fixed.
- Areas: **Palette** (CIEDE2000 distance of textures to the palette),
  **Textures** (size limits, power of two, texel density of models),
  **Models** (applied scale, pivots, triangle and bone budgets), **Materials**
  (roughness and metallic ranges), **Lighting** (each Environment's tonemap,
  exposure, fog, glow and colour grading against `[environment]` in the
  bible), **UI** (theme and style colours on the palette, the fonts `[ui]
  fonts` lists, text size) and **Sound** (loudness and true peak).
- **Fix N things** makes every fix Joinery can make; **Fix** on an area makes
  only that area's. Each fix is done in the app that owns the file, with a
  typed operation and a recipe: a palette-map layer or a resize in Krita, apply
  scale, pivot, decimate, texel density or a material value in Blender, a
  loudness edit on the rendered sound, the Environment's or theme's values
  through Godot. Fixes are grouped into small changes ("Remap 6 textures to
  the palette", "Normalise 9 sounds to −16 LUFS"), one file in one change
  only, and run side by side as tasks with their own cards, as many at once as
  `[agents] parallel` allows. Each change is checked, read by the reviewer and
  waits in the Review tab like any other; nothing is accepted for you.
- While they run, the card says **Making 5 changes… 2 ready to review** with
  **Review** and its own **Stop**. After you accept one the project is checked
  again and the scores rise in place.
- **N need you** counts what Joinery won't change itself (a file you imported,
  a texture made in GIMP, a bone budget); hover it for each reason. **N kept
  as they are** counts what you protected: `[match] exclude = ["art/wip"]`
  (folders or files) and `[match] final = ["art/props/crate.blend"]` in
  `joinery.toml`, or `joinery match --project <path> --final <file>`. A final
  source protects what it exports.
- `joinery match --project <path>` prints the same audit (`--json` for all of
  it, `--area palette` for one area, `--plan` for the changes Fix all would
  start).

### Building a level from a sketch

Draw the level on paper (or take a top-down screenshot), attach the picture with
the paperclip, paste or drop, and send `/blockout`, or just "build this level"
(ADR 0043). Words after the command go along: `/blockout the tower is 2 m up`.

- Journeyman reads the sketch and writes a **layout**: rooms, walls with doors
  and windows, corridors, stairs, platforms, the spawn (draw an X), the goal (a
  star), coins, pickups, checkpoints and hazards. With no measure on the sketch
  a door gap counts as about 1 m (2 tiles in a 2D game). Your sketch is only a
  reference; it is never copied into the game.
- Joinery checks the layout first: closed rooms, sizes the player fits through,
  stairs that aren't too steep, and that the spawn reaches every goal with your
  starting point's own player (how high it jumps, how wide it is). Journeyman
  fixes whatever the check finds before you see anything.
- The level is built as a greybox scene, `game/<name>_blockout.tscn`: CSG boxes
  with collision in 3D, solid rectangles in 2D, a named node for every room,
  wall piece and marker, and the spawn and markers in the groups the game and
  the playtest bot use. Then a bot walks it in Godot from the spawn to every
  goal.
- The review shows your sketch and the plan of the level side by side. The
  first fact is the walk: **Walkable: spawn reaches 3 of 3 goals**. Accept or
  Discard as with any change. To play it, open the scene and press Play Scene.
- To change it, just ask: "move the door to the north wall", "make room B
  bigger". The layout in `design/blockouts/<name>.toml` is the source;
  Journeyman edits it and rebuilds, and the review shows the earlier plan and
  the new one. Edit the TOML yourself and run `joinery blockout build
  design/blockouts/<name>.toml --project .` to rebuild the same way
  (`check` only checks it, `walk` walks it in Godot).

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

### Sharing your game

**Share**, beside Timeline in the bottom bar, takes the game to players
(ADR 0046). It has four parts:

- **Builds.** Tick Web, Windows, macOS and Linux, then **Build**. Joinery keeps
  an export preset per target in `export_presets.cfg` (it adds what is missing
  and leaves your own settings alone), exports with Godot 4.7 and zips each
  build with a README and the licence notes into `share/builds/`. Each row
  shows the size, whether the build started and what the playtester found:
  the build for your own computer is started once and played for 30 seconds;
  the others are checked for size and contents. The web build has threads off
  and uses the Compatibility renderer, as itch.io needs. The first build needs
  Godot's export templates; if they aren't on your computer the page says how
  big they are and offers **Download and build**. Nothing downloads otherwise.
- **Store media.** **Make media** plays a seeded run of the game and keeps 4
  to 8 screenshots of different moments, records a 30-second trailer with
  Godot's Movie Maker (a title card and an end card with the game's name;
  the game's own sound only if all of it is tool-made, never added music) and
  composes a 630 × 500 cover in Krita. Everything is in `share/media/`;
  **Remake** plays a new seed. It needs Docker for ffmpeg and Krita.
- **Page text.** **Draft page text** writes `share/page.md` from your brief,
  the controls and the credits in `joinery.lock`. What the project doesn't say
  is left as "_Write this part._". **Edit** opens it in your text editor; a new
  draft never replaces your edits unless you ask (`--redraft`).
- **Publish to itch.io…** asks for your itch.io page (`user/game`), the builds
  to push, and shows each file it will push. Publish stays off until you are
  signed in to butler, itch.io's upload tool, with your own `BUTLER_API_KEY`
  or `butler login` in a terminal; Joinery never sees your key. If butler
  isn't installed, a checkbox offers to install the pinned version from
  itch.io. Only you can publish: agents can't run it.

The same from a terminal:

```sh
uv run joinery share build --project <path> --targets web,macos
uv run joinery share media --project <path> [--only screenshots,trailer,cover] [--new-seed]
uv run joinery share page --project <path>
uv run joinery share itch --project <path> --target you/your-game --dry-run
```

Builds, `share/.work/` and rendered media are ignored by git; the page text,
`share/media/cover.kra` and `share/recipes/` are kept so the media can be made
again.

### Sidebar commands

- `/feature` or `/feature list` says what the game has and what can be added.
  `/feature add|remove <id>` shows a plan first (files, settings in plain words,
  notes and conflicts). Nothing changes until you press **Apply**; Journeyman
  never applies a feature itself. After it is applied the card offers
  **Revert**, which shows **Reverting…** while it runs, or **Too old to revert**
  once its restore data is gone. When `project.godot` changed, the editor
  offers to reload the project so its settings in memory never overwrite the
  new ones.
- `/debug` debugs the last error Godot reported; words after it say what
  goes wrong (see Reviewer and debugger agents).
- `/variants [2-4] <request>` makes the request a few ways side by side so you
  can keep one (see Trying a few ways above).
- `/playtest [what to aim for]` plays the game with a bot and reports bugs,
  softlocks and balance notes (see Playtests above).
- `/match` checks the project against the art bible and offers to fix what
  drifts (see Making everything match above).
- `/blockout [notes]` builds a greybox level from the sketch you attach (see
  Building a level from a sketch above).
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

What you can ask for depends on the part:

- **A map** ("make the steel rougher", "paint a worn edge on the left"):
  Journeyman adjusts its pixels, or first makes it an editable source, a Krita
  painting (or a Material Maker graph for procedural changes) under
  `art/sources/maps/<model>/` that starts from the map as it is. Painting then
  adds layers (fills, strokes, filter layers) you can open in Krita and hide or
  change; the model's map follows. A map can also take an image made in Krita,
  GIMP or Material Maker.
- **A material shared by several objects** ("make a copy for Head only"): the
  named objects get their own copy, with copies of its maps, so later edits
  change only them.
- **A bone** (rename, move its rest position, change its parent, scale a chain)
  and **a shape key** (set its value, rename, delete, or make a new one from the
  current mix or the rig's pose).

MPFB2 humans: their body-shape sliders (shape keys named `$md-…`) are baked into
the game's mesh on export, so the model in the game has the body you made and
no extra morph targets. Expressions and other shape keys stay. Ask to keep the
body-shape keys if the game changes the body at run time; the model remembers.

### Animations

A model with animations shows them under its preview in the Blender tab: each
one with its length, whether it loops and its frame rate. The model plays the
selected one; ← and → (or the arrow buttons) go to the previous or next, Space
plays and pauses, the slider scrubs, and the menus choose Loop, Ping-pong or
Once and a speed from 0.25× to 2×. The name and frame number show beside the
controls. An action that is in the `.blend` but not yet in the game copy says
so. Click a row, or **Use in message**, to make that animation the target:
"make the run bouncier" then changes only the run.

Every motion is made by operating Blender: Joinery writes keyframes on the
rig's controls (the FK controls of a Rigify or MPFB2 rig, or the bones of a
hand-made humanoid named like hips, spine, thigh, upper_leg, forearm, hand),
with a small leg solver that keeps planted feet still. Nothing is downloaded
and no motion model is used. You can ask for:

- **A new motion**: walk, run, idle, jump (start, in the air, landing), attack
  or wave, with how fast, how long, stride, bounce, arm swing and lean.
- **A change to one**: faster or slower, more or less motion in the arms,
  legs, spine or head, more bounce (the feet stay planted), shifted timing,
  feet pinned while they touch the ground, a loop that meets itself, left and
  right swapped, a copy, a new name, or deleting it.
- **An animation pass** ("make the animations the game needs"): Journeyman
  compares the character's actions with the clips your game plays (a
  blueprint's player plays idle, walk, run, jump_start, jump_air and
  jump_land) and makes the missing ones.

The review plays the animation before and after as two looping clips side by
side, with one line of facts such as "Run: 0.8 s loop, bounce +30%". In the
third-person and first-person blueprints, the player plays the character's
idle, walk, run and jump clips when the model you put in it has them.

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

### Kits

Rocks, crates, barrels, fences, simple buildings, trees and bushes come from
kits: procedural generators run in Blender, written by hand (`kits/blender/`),
that make a model from a few values and a seed. Nothing is generated by a
model; the same values always make the same rock.

In the Tools workspace, switch the browser from **Assets** to **Kits** and pick
a kit. Each value is a slider (or a picker or a switch); a moment after you
change one, Blender draws the model beside them, with its triangles against the
art bible's budget and its size in metres. **Try another seed** keeps the
values and changes the details. **Make** makes one model, **Make 10
variations** ten that differ a little; both wait for your review like any
change. Making needs an approved art bible: colours come from its palette,
roughness and metallic stay in its ranges, and each model fits the triangle
budget and pivot of its folder (`art/environment` or `art/props`). Each model
is a `.blend` in `art/sources/kits/` and a GLB, with a recipe that rebuilds it.

A model made with a kit says so under its name ("Model · made in Blender · Rock
kit"). **Kit settings** opens its values; **Apply changes** makes it again with
the new ones. A model of a set changes with the whole set, each keeping its own
variation, and scenes that place it pick up the change.

Ask Journeyman in words, too: "make 50 rocks, mossier and smaller" makes a set
in one Blender run; "make those rocks mossier" changes it; "scatter them by the
path" places them in a saved scene, spaced apart, turned and sized at random,
on the ground model and clear of a Path3D. Scattering again replaces the group.
The scene on screen can't be scattered into while you edit it: save it and open
another, or scatter into its own scene and place that.

From a terminal: `joinery kits list`, `joinery kits preview --kit rock
--params '{"moss": 0.6}'`, `joinery kits make --kit rock --count 10` and
`joinery kits edit --asset art/environment/rock.glb --params '{"size": 2}'`
(`--yes` accepts the change at once).

### Complex models in Blender

A chair, a lamp or a car is made in Blender (or with a kit) and placed as one
model. A change that stacks Godot's box, cylinder or CSG shapes into one object
goes back to the agent before you see it. Floors, walls, platforms, ramps,
collision shapes, sketch-built levels and placeholders you ask for by name are
fine, and only what a change adds or modifies is judged. Prototyping? Turn off
Settings → Project → **Complex models in Blender** (`[agents] complex_models =
"placeholders"`, or `joinery complex-models --set placeholders`).

### Shared kits

Things you made in one game can go into the next as a shared kit: a
`.joinerykit` file with the models, sounds, materials and prefab scenes you
pick, the recipes that make them, kit generators with their presets, and who
made them under which licence. You move the file yourself (to another project
or to a friend); Joinery never downloads one.

At the top of the Kits tab:

- **Export kit…** asks for a name, a version, a licence (your game's, unless
  you pick another) and what goes in: prefab scenes, kit generators and assets.
  What they need comes along (a recipe's script and inputs, the models a scene
  places), and the dialog says what the kit holds and how big it is, or why
  something can't go: a reference image, a file with no `joinery.lock` entry,
  or a scene with a script. Assets made with a custom tool go only when you
  turn on **Include assets made with a custom tool**, and stay marked as such.
  **Save kit…** writes the file.
- **Import kit…** opens a `.joinerykit` and shows its name, author, licence,
  what it holds and where it goes (`kits/<name>/`), before anything changes.
  **Add to project** leaves it as one change in the Review tab. Every file is
  checked against the kit's SHA-256 list first; a changed or unsafe kit is
  refused.
- Each imported kit has a row with its version, author and licence.
  **Update…** opens a newer file of the same kit and shows what changes;
  **Remove** takes it out again (both reviewed). A kit a scene still uses
  can't be removed until the scene stops using it.

An imported kit's assets keep their licence: `joinery.lock` names the kit, its
author and licence, and `joinery rebuild` makes them again from their recipes.
Its generators join the Kits list ("Harbour rock") for you and for Journeyman,
with their presets. The Share build's licence notes list every kit you
imported. Journeyman can use imported kits but never imports, exports or
removes one.

From a terminal: `joinery kit export --project . --name Harbour --include
scenes/harbour.tscn --generator rock --out ~/harbour.joinerykit` (`--plan`
shows what would go in), `joinery kit import --project . ~/harbour.joinerykit`,
`joinery kit remove --project . --name Harbour` and `joinery kit list --project .`.

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

The starting points: `blank`, `first-person-3d`, `third-person-3d`,
`platformer-2d`, `top-down`, `puzzle-cards` and `vr-room` (a small greybox room
to walk and teleport in, with things to pick up, a button and a goal: put the
cube on the table). The features: `controller`, `save-load`, `online-play`,
`steam`, `translations` and `vr`. VR works with the three 3D starting points
and is refused for the others with a plain reason.

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
The app ID is optional, so you can plan for Steam before you have a Steamworks
page. Without one Steam stays off in the game (`SteamService.status` is
`no app id`) and everything else works. Add or change it any time:
`uv run joinery steam --project /path/to/game --app-id 480` (`--app-id 0`
clears it, `--connect` changes how players meet, no flags shows what is set).
`--connect` only matters with Online play: players meet through Steam (the
default) or by direct IP.

The wizard runs the same step as `joinery game apply --project <path>
--blueprint <id> --features a,b [--steam-app-id N] [--connect steam|direct]`,
which prints one JSON line per finished step. `joinery blueprints --json` is the
catalogue the wizard shows: each blueprint's screenshot, and for every feature
whether it can be chosen there, why not, and what it adds.

### VR

`--features vr` (on by default for the VR room) turns on Godot's built-in
OpenXR: nothing is downloaded. With an OpenXR runtime and a headset the game
starts in the headset and an XR rig is carried on the player: greybox hands,
a pointer ray (trigger presses buttons and switches and clicks the menus,
which show on a panel in front of you), grip to pick things up and throw
them, the left stick to walk, the right stick to snap-turn and, pushed
forward, to aim a teleport arc that only lands on level floor. **Settings ›
VR comfort** has smooth movement and its speed, snap turn 30° or 45°,
teleport, the comfort vignette, seated play and left-handed. Without a
runtime or headset the game starts on the desktop with mouse, keyboard or pad
and says so once; Godot's own log notes that OpenXR could not start. Tests
pass `-- --vr-simulate` to run the rig without a headset; see ADR 0039 for
what was and was not tested on real hardware.

### References and design documents

Your own pictures and design documents can come with a new game, or be added
later. In the wizard's last step (**Brief and art direction**) drop files on the
window or use **Add images…** under **Reference images** and **Add documents…**
under **Design documents**. Each picture shows a thumbnail and a field for what
to take from it; each document its name and kind; × takes a file off the list.
For an existing game, **Project apps** (`/setup`) has **References and design
documents** with **Add references…** and **Add design documents…**: Joinery
first says what the change would do, and **Add to project** adds them as one
change the Timeline can revert.

```sh
uv run joinery new /path/to/game --tools godot --blueprint blank \
  --reference "~/refs/dusk.png=the warm low light" --reference ~/refs/quarry.jpg \
  --design-doc ~/docs/gdd.md --design-doc ~/docs/pitch.pdf
uv run joinery references add --project /path/to/game \
  --reference ~/refs/pier.webp --plan   # what would change; --yes adds it
```

- Pictures (PNG, JPEG or WebP, up to 20 at a time) are copied into
  `art/reference/` under their own names (`dusk-2.png` when the name is taken,
  nothing when the same picture is already there), recorded in `joinery.lock`
  as your import (`human-import`, "reference only, not shipped"), and added to
  the art bible's reference board as R1, R2, … with your note as *Take this*.
  Kind and *Not that* are left for you. At creation this happens before the
  bible is approved; later it changes the bible, so you approve it again in
  `/art`.
- Design documents (Markdown, text or PDF, up to 10) are copied into
  `design/docs/` and listed at the top of `design/brief.md` under **Design
  documents**. For Markdown and text, a heading that names a brief section
  (Pitch, Core loop, Controls, Target audience, Scope and so on) has its text
  copied, word for word, into that section if it is still open, ending with
  the file and heading it came from. Nothing is summarised or invented, and a
  section you already wrote is never touched. PDFs are kept as files: Joinery
  doesn't read them, and Journeyman opens them itself.
- Only the files you pick are copied: nothing else from their folder, nothing
  they link to, and never anything from the internet. A file of the wrong
  kind is refused with the reason, before anything is written. Only you add
  references and design documents; an agent asking to run `joinery references
  add` is refused.

Journeyman reads `design/docs/` with the brief (`game_brief` returns each
document, with the text of Markdown and text ones) and sees the pictures on the
board (`art_direction` sends them along to agents that take images).

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
uv run joinery match --project /path/to/game        # how far the project is from the bible, per area
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
inline (optional: digits only, or empty to add it later with `joinery steam`) and, with Online play on, how players
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
of a model (a GLB or its source document); `--json` adds each part's typed
operations (`ops`) and a map's editable source (`editable`). `joinery history usage|compact
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
