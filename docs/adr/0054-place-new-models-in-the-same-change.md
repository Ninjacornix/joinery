# 0054. Place a new model in the same change

Status: accepted (2026-11-12)

## Context

An agent asked to replace a placeholder TV in the scene on screen made the model
in Blender and then could not place it. It told the user "placement is blocked
because the live editor cannot load the staged GLB yet" and submitted the model
alone. Two things stood in the way:

- **The scene on screen is edited live** (ADR 0035), through `edit_scene`, in the
  user's own editor. Its `res://` is the project. A model exported in this turn
  exists only in the change's stage (`.joinery/transactions/<id>/stage`) until the
  user accepts, so `instantiate_scene` refused it ("must exist in this project
  first"), and the staged Godot tools refuse the live scene.
- **The stage has no import cache.** A stage is cloned without `.godot`, so even
  the staged Godot tools could only instance a model after something had run
  Godot's import there (only Joinery's own native ops did).

## Decision

1. **The gateway hands the editor the stage's copy** (`joinery/staged_assets.py`,
   `ProjectGateway.staged_files`). Before a live edit is sent, every `res://` path
   it loads that the project lacks and the turn's stage has is resolved to the
   stage's imported file (the `.import` remap under the stage's
   `.godot/imported`). The stage is imported in the offline worker first when a
   file has no current import there; "current" is Godot's own `.md5` record
   matching the source. The `editor_action` carries the map as `staged`. Both
   live-edit paths use it: `edit_scene` over the RPC (Claude Code, Gemini CLI,
   opencode, tasks) and Codex's own scene tool (`turn_runner.stream`).
2. **The editor loads that copy and keeps the project path.** `node_operations.gd`
   loads a staged path from the given file (only inside this project's
   `.joinery/transactions/`, with the cache ignored) and sets the instance's
   `scene_file_path` to the project path. The preview shows the model; one
   Accept moves the model into the project and keeps the placement; the editor
   then imports the file, so the scene saves and reopens normally. Saving with a
   preview still pending discards the preview, as before.
3. **Replacing a placeholder is one operation.** `instantiate_scene` takes
   `replace` (the node's identity) and `fit_size`. The instance takes the node's
   parent, place among its siblings, name and transform; with `fit_size` it is
   scaled evenly to the node's mesh bounds and stands on their bottom centre.
   The replaced node is removed in the same preview, restored on Discard and on
   undo.
4. **Staged Godot calls import first.** A staged Godot write whose arguments name
   a model or texture with no current import in the stage imports the stage
   before the call.
5. **Agents say what they made and where.** The shared instructions tell every
   agent to make the model, place it and submit once, and to tell the user
   plainly what was made and where it was placed, never Joinery's own steps or
   "blocked". Tool errors on this path name the fix ("doesn't exist. Make or
   export it first", "couldn't open it… export it again").

## Consequences

- A model and its placement reach the user as one review: the model's files and
  renders beside the viewport's before and after.
- A live edit that places a new model can wait for one offline import (seconds).
- A staged `.tscn` made in the change is loaded as it is; if it in turn points at
  other files only the change has, the editor can't open it, and the agent is
  told to place the model itself.
- After Accept, Godot's first import of the model may reload its instances in
  open scenes; the placement stays.
