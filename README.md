# Joinery

**Made in the Joinery, not generated.**

![The Joinery editor in its dark theme. The level of the third-person starting point is open in the 3D view, with the crate selected and its move and rotate handles showing. The scene tree is below it, above tabs for Files, Inspector, Output, Review, Timeline, Share and Mixer, and an icon rail on the left switches between Godot, the asset list and Blender. On the right, the Journeyman sidebar shows a plan card for adding translations, listing the files and settings it would add, with Cancel and Apply. Below the card, the message box holds the selected crate as a chip and a typed request to make the crate look older.](docs/images/joinery-editor.jpg)

*A new third-person game in Joinery: the plan for adding translations waits for
Apply, and a request about the selected crate is typed but not sent yet.*

Joinery is a game editor built on Godot 4.7. You select something in your game,
say in the sidebar (Journeyman) what should change, and the work is done in the
program that owns it: Blender for models, Krita or GIMP for images, Surge XT or
SuperCollider for sound. You see the result before and after, then accept or
discard it.

Joinery does not use image, 3D, music or voice generators. Every asset is made
in a real tool, kept as an editable source file, and has a recipe that rebuilds
it. Games made with Joinery are plain Godot projects and open in stock Godot.

What changed in this version, and what is still missing:
[0.6.0 release notes](docs/releases/0.6.0.md).

## How a change works

1. You select an object, material or sound and write what you want, for
   example "make the hammer handle longer".
2. Journeyman opens the owning program in an offline container and edits the
   source file there.
3. The change is checked against your game's art bible (palette, triangle
   budgets, loudness) and shown in the Review panel with a preview.
4. Accept keeps it and can be reverted later. Discard restores every file, even
   when more than one tool was involved.

No art or audio work starts until the game's art bible is approved. The bible
the New project wizard drafts from your own answers counts as approved, so art
and audio work can start right away. If the bible changes later, approve it
again with one click in `/art`. Journeyman cannot approve the bible or its own
changes.

## Tools

| Area | Tools |
|---|---|
| 3D models | Blender 5.2 (MPFB2 humans, Rigify) |
| Materials | Material Maker 1.8, Blender shader nodes |
| 2D, UI and pixel art | Krita 5.2, GIMP 3.0, Inkscape 1.4 |
| Sound | Surge XT 1.3, SuperCollider 3.13, Audacity 3.7 (your own recordings: noise reduction, spectral edits, labels, mixdowns); sound edits (trim, fade, loudness, loops) |
| Music | MIDI rendered through FluidSynth and Surge XT |
| The game | Godot scenes, scripts and settings |

Aseprite, REAPER and MuseScore were removed as built-in tools in 0.2
([why, and what to use instead](docs/adr/0025-tool-removals.md)); Audacity came
back in its own offline worker ([ADR 0082](docs/adr/0082-audacity-worker.md)). You can add
them, or any other program, to a project as your own custom tool. The
[support matrix](docs/support-matrix.md) lists what each tool can do.

## Agents

Joinery works with Claude Code, Codex, Gemini CLI or opencode, through your own
subscription or API key. Each agent signs in with its own login; API keys are
kept in a private file outside your projects. The same rules and checks apply
whichever agent you use.

## Quick start

You need uv, Docker, Git and an agent CLI that is signed in (or an API key).
Node.js is needed only if you select SuperCollider; the Godot tools are built
into an offline worker image. The first setup downloads several GB of tool
images; after that the tools run with networking off.

```sh
uv sync --frozen
uv run joinery new ~/games/my-game --name "My Game" --tools godot,blender,krita \
  --blueprint third-person-3d --features controller,save-load
uv run joinery bootstrap --project ~/games/my-game
uv run joinery launch --project ~/games/my-game
```

`--blueprint` starts from a small playable game. `joinery blueprints` lists the
seven starting points (blank, first-person 3D, third-person 3D, 2D platformer,
top-down arena, memory cards, VR room) and `joinery features` what a game can
add: controller support, save and load, online play, Steam, translations,
accessibility, VR (OpenXR, for the 3D starting points; the game still plays on
the desktop without a headset), dialogue, enemy behaviour trees, camera rigs and
heightmap terrain (3D). Leave
`--blueprint` out for an empty project. The **New project** wizard in the
Project Manager asks the same questions.

New to Joinery? [Your first project](docs/getting-started.md) walks through one
small game from start to a shared build. A short tour also opens on a new
project's first open (`/tour` shows it again).

In the editor:

- Type `/art` to read the art bible drafted for your game, and to finish or
  approve it again after it changes, and
  `/brief` to read the game brief.
- Ask Journeyman for a change, then press **Accept** or **Discard** in the
  Review panel.
- Type `/` for every command. `/feature add translations` shows what would
  change and does nothing until you press **Apply**.
- Open **Share** in the bottom bar to build the game for the web and desktops,
  make screenshots, a trailer and a cover, and publish to itch.io yourself.

From a source checkout, build the patched editor first:
`uv run --frozen python release/build_editor.py --jobs 4`.

## Commands

| Command | What it does |
|---|---|
| `joinery new PATH [--blueprint ID --features a,b]` | Make a project, as the wizard does |
| `joinery blueprints`, `joinery features` | List starting points and features |
| `joinery feature add\|remove\|list ID --project P [--plan]` | Add or remove a feature as a reviewed change |
| `joinery setup --project P` | Change a project's tools |
| `joinery bootstrap --project P` | Install the pinned tool servers and offline workers |
| `joinery status --project P`, `joinery doctor` | What each tool can do right now |
| `joinery connect status\|verify --agent ID` | Is the agent signed in, and does it answer |
| `joinery brief --project P`, `joinery bible --project P [--upgrade]` | Write the game brief and art bible; update a 0.1 bible |
| `joinery parts MODEL --project P` | List a model's objects, materials and texture maps |
| `joinery transactions list\|accept\|reject\|revert --project P` | Decide, undo or recover changes |
| `joinery history usage\|compact --project P` | Space kept for reverting changes, and freeing it |
| `joinery rebuild --project P` | Rebuild every asset from its recipe and compare |
| `joinery launch [--project P]` | Open the editor |

The [editor guide](docs/editor-guide.md) has every option.

## Documentation

- [Your first project](docs/getting-started.md): a walkthrough from a starting
  point to a shared build
- [Editor guide](docs/editor-guide.md): the editor, sidebar commands and CLI
- [Support matrix](docs/support-matrix.md): platforms, tools and agents
- [Testing guide](docs/testing.md): how each part is checked
- [Implementation map](docs/implementation-map.md): what is done and its limits
- [Architecture decisions](docs/adr/)
- [Art bible template](templates/art_bible.md)
- [Rules every AI agent follows in this repo](CLAUDE.md)

## Licence

Joinery is released under the [GNU AGPL-3.0-or-later](LICENSE). The parts that
are copied into your game (the editor add-on, blueprints, features, templates
and kits) and the Godot engine changes are MIT, so your games stay yours to sell
or keep closed. See [LICENSING.md](LICENSING.md) for the full split. Godot and
other bundled components keep their own licences; see [licenses/](licenses/).
The creative tools are separate programs under their own licences and are not
part of Joinery's code.
