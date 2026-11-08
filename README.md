# Joinery

**Made in the Joinery, not generated.**

![The Joinery editor with a low-poly hammer made in Blender placed in the scene, the Godot and Blender icons on the left, and the chat with Codex on the right](docs/images/joinery-editor.jpg)

*A hammer modelled in Blender through chat, reviewed and placed in the scene.*

Joinery is a game editor built on Godot 4.7 that you direct by chatting with it.
You select something, say what should change, and the AI sidebar, **Journeyman**,
does the work inside a real creative tool: it models in Blender, paints in Krita,
designs a sound in Surge XT. You see a before and after, then accept or reject it.

Nothing is made by image, 3D or audio generators. Every asset is built in a real
tool, kept as an editable source file, and comes with a recipe that rebuilds it
exactly.

## How it works

1. **You ask.** "Make the hammer handle longer", "a darker door creak", "add a
   health bar". You can point at the object, material or sound you mean.
2. **The right tool does the work.** Journeyman opens the owning program
   (Blender, Krita, Material Maker, Inkscape, SuperCollider or Surge XT) in an
   offline sandbox and edits the source file there.
3. **You review.** The change is staged, never applied straight away. You get a
   preview of the model, image or sound plus the file changes.
4. **You decide.** Accept keeps it, with undo. Reject restores everything, even
   when several tools were involved.

Before any art or audio is made, each game gets an **art bible**: palette, style,
budgets and sound. You approve it, and changes are checked against it before you
see them: colours against the palette, triangle counts against budgets, and
loudness against targets.

## Creative tools

| Area | Tools Joinery works in |
|---|---|
| 3D models | Blender |
| Materials | Material Maker, Blender shader nodes |
| 2D and UI art | Krita, Inkscape, Aseprite (your own licence) |
| Sound and music | Surge XT, SuperCollider |
| The game itself | Godot scenes, scripts and settings |

GIMP, Audacity, REAPER and MuseScore can open your files and connect through
community MCP servers, with lighter integration. You can also add your own
programs. The [support matrix](docs/support-matrix.md) shows exactly what works
for each tool.

## Bring your own AI

Joinery works with **Claude Code, Codex, Gemini CLI or opencode**, using your own
subscription or API key. Subscriptions sign in through each agent's own login;
API keys stay in a private file on your machine, outside your projects. The same
creation-only rules apply whichever agent you pick.

So far Codex is the one tested live end to end. On Windows this alpha supports
Codex only; on macOS and Linux the others are supported but not yet verified live.

## Status

**0.1 alpha.** Builds for macOS (Apple Silicon), Linux and Windows pass their
editor, review, undo and exported-game checks. It is early software: expect rough
edges, and read the [release notes](docs/releases/0.1.0-alpha.1.md) first.

You need **uv, Docker, Git, Node.js and a signed-in agent CLI** (or an API key).
The first setup downloads several GB of tools; after that, the creative tools run
with networking switched off.

## Quick start

```sh
uv sync --frozen
uv run joinery new ~/games/my-game --name "My Game" --tools godot,blender
uv run joinery bootstrap --project ~/games/my-game
uv run joinery launch --project ~/games/my-game
```

Then, in the editor:

- Project setup asks a few questions and drafts your art bible; type `/art` to
  review and approve it.
- Ask Journeyman for something, for example "make a low-poly wooden crate".
- Review the preview and press **Accept** or **Reject**.
- Type `/` to see every command, or `/help`.

Projects stay plain Godot projects, so they open in stock Godot too.

## Learn more

- [Editor guide](docs/editor-guide.md): the editor, commands, previews and CLI
- [Support matrix](docs/support-matrix.md): what each tool can do today
- [Testing guide](docs/testing.md): how everything is checked
- [Implementation map](docs/implementation-map.md): what is done and what is next
- [Art bible template](templates/art_bible.md)
- [Rules every AI agent follows in this repo](CLAUDE.md)

## Licence

Joinery is released under the [MIT License](LICENSE). Godot and other bundled
components keep their own licences; see [licenses/](licenses/). The creative tools
(Blender, Krita, Surge XT and others) are separate programs under their own
licences and are not part of Joinery's code.
