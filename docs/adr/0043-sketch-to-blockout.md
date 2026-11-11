# 0043. Sketch to blockout

Status: accepted (2026-11-11)

## Context

Level design starts on paper: a few boxes for rooms, gaps for doors, an arrow up the
stairs, an X where the player starts and a star for the goal. Users asked to show
Journeyman that drawing (or a top-down screenshot) and get a level they can walk the
same afternoon. Rule Zero rules out turning the picture into geometry with an image or
3D generator, and a level the agent writes freehand into a `.tscn` is hard to check,
hard to change ("move the door to the north wall") and different every time.

## Decision

1. **A typed layout is the source.** `joinery/blockout/layout.py` defines the format
   (pydantic, version 1): rooms (rects or polygons, a floor height, open edges), openings
   (door, window or gap on one named wall of a room, at a distance from its west or north
   end), corridors, stairs (rising toward a compass direction between two rooms' floors),
   platforms, free walls, one spawn, and markers (`goal`, `pickup`, `coin`,
   `checkpoint`, `hazard`). The plan is seen from above (3D, top-down 2D) or the side
   (`2d-side`): x right, y down. 3D is in metres (plan x, y are Godot X, Z); 2D is in
   tiles or pixels. `scale` turns sketch units into plan units; with no measure given
   the agent reads a door gap as about 1 m (2 tiles in 2D). The canonical TOML lives in
   the project at `design/blockouts/<name>.toml`, and the same layout always writes the
   same bytes.
2. **One tool for every agent.** `blockout(layout, scene?, blueprint?, sketch?)` on the
   `joinery-registry` proxy, backed by the gateway RPC method `blockout`
   (`joinery/blockout/stage.py`), the same for Codex, Claude Code, Gemini CLI and
   opencode. It changes the project, so the lead and workers may call it and the
   read-only roles may not. `/blockout [words]` in the sidebar is sent as a message with
   the attached sketch; the host turns it into a request in words
   (`bridge.instructions.expand_command`), and `BLOCKOUT_GUIDE` tells every agent how to
   read a sketch. The sketch stays a reference: nothing copies or traces it.
3. **Checked before anything is written** (`check.py`, `reach.py`). Joinery resolves the
   layout against the player of the project's starting point (radius, height, step,
   jump; `world.PROFILES`) and checks ids, shapes (polygons that cross themselves, rooms
   that overlap), sizes (narrow corridors and doors, low doors, steep stairs, a level
   too large for its units), the mode against the starting point, the spawn, and that
   the spawn reaches every goal: plans are rasterised at half the player's radius and
   walked with steps, jumps up ledges, drops and jumps across gaps; side views are
   walked on half-tile cells with falls and jump arcs. Routes keep off hazards where
   there is another way. Each problem says what to change ("Room vault has no door,
   open side or corridor into it: add an opening to one of its walls") and comes back
   with a picture of the plan, so the agent fixes the layout and calls again.
4. **Built deterministically** (`build.py`). Walls are worked out once (`world.py`):
   every room edge gets a wall unless open, a wall two rooms share is built once,
   doors and windows cut it (with a lintel and a sill in 3D), and corridors and stairs
   cut the room walls they cross. 3D scenes are CSG boxes with collision (floors, wall
   pieces, ramps, platforms), a sun and an environment; 2D scenes are StaticBody2D
   rectangles with a Polygon2D each over flat floor polygons. Every room, corridor,
   piece and marker is a named node (`Rooms/hall/north_1`, `Markers/exit`); markers are
   areas in the groups the starting points and the playtest bot use; the spawn is in
   `player_spawn` and players appear under `Players`, the game core's level contract
   (ADR 0024). `game/blockout/blockout_level.gd` (written once, never replaced) calls
   `Game.level_ready`, ends the level at a goal, collects coins and pickups, moves the
   respawn at checkpoints and sends the player back from hazards and falls, through
   `GameEvents`. The default scene is `res://game/<name>_blockout.tscn`; `scene` may
   name the starting point's level scene to replace it. The root records the layout's
   path and hash.
5. **Staged, walked, reviewed.** The layout, the scene and the script go into the turn's
   staged change like any edit. Then the playtest bot walks the staged copy in stock
   Godot (`joinery playtest … --route`, `bot_route.gd`): for each target it puts the
   player on the spawn and steers it through the check's waypoints with the game's own
   controls, jumping where the route jumps. The review leads with that fact
   ("Walkable: spawn reaches 3 of 3 goals"; from the plan check when Godot can't run)
   and shows the plan beside the sketch (`review_media.gd`, an item with a
   `reference`); an earlier plan flips in place. The usual checks, reviewer and Accept
   or Discard follow (ADR 0035, 0037, 0040).
6. **Iterating** edits the layout file, not the scene: the agent reads
   `design/blockouts/<name>.toml`, changes it and calls `blockout` again with the whole
   layout. `joinery blockout check|build|walk <layout>` replays it from the command line.

## Consequences

- A blockout is a recipe: rebuilding gives the same scene bytes, and the layout diff is
  what the user reviews when the level changes.
- One floor height per plan position: rooms stacked above rooms are not supported; use
  separate layouts. Stairs are built as ramps.
- How well a sketch is read depends on the model; the checks catch what can't be
  walked, not a misread proportion. The live smoke compares the agent's layout with
  the one the sample sketch was drawn from (`joinery.blockout.accuracy`).
- The walk is a bot following a planned route: it proves the way is open in real
  physics; it does not judge whether the level is fun.
- Sample sketches in `tests/fixtures/blockout` are drawn by code in this repository
  (`make_sketches.py`), and tests check they still are.
