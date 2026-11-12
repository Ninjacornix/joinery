# Your first project

This walks through one small game from start to a build you can send to a
friend: make it from a starting point, check its art direction, have Journeyman
make a model, review it, play the game and share it. It takes about half an
hour, most of it waiting for the tools the first time.

You need Joinery installed (see the [README](../README.md#quick-start)),
Docker running, and an agent signed in: Claude Code, Codex, Gemini CLI or
opencode.

## 1. Make a game from a starting point

Open Joinery. In the Project Manager press **New project** and answer the
questions:

- **Name and folder**: an empty folder for the game.
- **Starting point**: a small game that already plays. Pick **Third-person 3D**
  for this walkthrough. **Blank** gives you an empty project instead.
- **Features**: leave the suggested ones, or add controller support, save and
  load and the rest. You can add or remove them later with `/feature`.
- **Apps**: Godot is always on. Turn on **Blender** for this walkthrough; Krita
  for textures and Surge XT for sounds are useful later.
- **Art direction**: a few words about the look: the mood, the colours, how
  detailed. Joinery drafts the game's art bible from your answers.

From the command line the same thing is:

```sh
uv run joinery new ~/games/harbour --name "Harbour" --tools godot,blender \
  --blueprint third-person-3d
uv run joinery bootstrap --project ~/games/harbour
uv run joinery launch --project ~/games/harbour
```

The first time, `bootstrap` downloads the offline tool images (several GB).

When the game opens, a short tour points out the sidebar, the review, the
Timeline and Share. Skip it if you like; `/tour` brings it back.

## 2. Check the art bible

Every model, texture and sound has to follow the game's art bible: its palette,
its sizes and triangle budgets, its loudness. Type `/art` in the Journeyman
sidebar to read it.

The bible drafted from your own answers counts as approved, so you can go on.
If you change it later, Journeyman can't start art or sound work until you
approve it again with **Approve** in `/art`. Journeyman never approves it for
you.

## 3. Make a model

1. In the 3D view, click the spot where something should go, or click the
   floor.
2. In the message box, type what you want, for example: *make a wooden crate
   with iron corners and put it by the door*.
3. Press Enter.

Journeyman opens Blender in its offline worker, models the crate there, saves
the `.blend` source in `art/` with a recipe that can rebuild it, exports it for
Godot and places it in the scene. The sidebar shows each step as it goes.

A bigger request comes with a short to-do first: its steps and how Joinery will
check it is done. Edit or remove lines if you like, then press **Go**.

## 4. Review the change

When the change passes Joinery's checks (art bible, Godot import, missing
files, provenance) it opens in the **Review** tab at the bottom: the model
before and after, which files changed, and what the checks found.

- **Accept** keeps it. Every accepted change is a step on the **Timeline**
  tab; open one to see it, or go back to it.
- **Discard** puts every file back as it was.

Nothing in your project changes until you press Accept.

## 5. Play it

Press **Play** at the top of the editor. Walk to the crate. To have a bot play
it and report errors and places it couldn't reach, type `/playtest`.

## 6. Share it

Open the **Share** tab at the bottom. Pick the web and the desktops you want,
press **Build**, and send the files or the web build to a friend. Share also
makes screenshots, a short trailer and a cover, and helps you publish on
itch.io or Steam yourself.

## Where next

- The [editor guide](editor-guide.md) covers every command and setting.
- Type `/` in the sidebar for every command, and `/help` for what each does.
- Your game is a plain Godot project: it opens in stock Godot too.
