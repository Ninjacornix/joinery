# 0050. Performance check

Status: accepted (2026-11-11)

## Context

The playtester (ADR 0038) flags frame hitches and growing memory, and the art bible caps
texture sizes and triangles per model (ADR 0022). Nothing said whether the game as a
whole runs on the machines it is meant for, what makes a frame slow, or how to fix it.
Users had to read Godot's profiler themselves.

## Decision

1. **Measured by playing.** `joinery perf --project P [--target high|mid|low|steam-deck|web]
   [--json] [--area A] [--plan] [--no-window]` (`joinery/perf.py`) plays one seed of the
   game twice with the playtest bot and its probe (`--probe=perf`,
   `joinery/playtest_bot/bot_probe.gd`; `joinery/check_runs.py`):
   - headless in stock Godot: CPU frame times (p50, p95, p99), the first scene's load
     time, Godot's monitors every half second (nodes, physics, memory), a census of the
     running scene (meshes and their repeats, triangles, lights and shadows, physics
     bodies and masks, particles, visibility ranges, textures in use) and each script's
     cost: after play, the nodes of each processing script stop for a few frames and the
     median frame time they free is that script's cost (Godot's own process-time
     monitors change once a real second, too seldom for this);
   - in a background window (the Joinery editor binary with `JOINERY_BACKGROUND=1`):
     frame times with rendering, draw calls, primitives in view, video and texture
     memory, and pipeline compilations after the start (shader hitches).
   The files are read too (`joinery/perf_static.py`): texture sizes and import settings,
   `.glb` triangles and whether Godot makes LODs on import, props repeated many times in
   a scene, shadow-casting lights, and per-frame code that looks up nodes, loads
   resources or loops thousands of times.
2. **Scaled to a target.** Each target has a budget (frame ms, draw calls, primitives,
   texture MB, nodes, physics bodies, shadowed lights, load ms, texture size, triangles a
   model, memory growth) and a speed next to the "high" class:

   | Target | Speed | Frame | Draw calls | Primitives | Textures | Shadowed lights | Max texture |
   | --- | --- | --- | --- | --- | --- | --- | --- |
   | high | 1.0 | 16.7 ms | 4000 | 6 M | 3072 MB | 8 | 4096 |
   | mid (default) | 0.5 | 16.7 ms | 2000 | 2 M | 1536 MB | 4 | 2048 |
   | low | 0.25 | 33.3 ms | 800 | 500 k | 512 MB | 1 | 1024 |
   | steam-deck | 0.35 | 25 ms | 1500 | 1.5 M | 1024 MB | 2 | 2048 |
   | web | 0.3 | 33.3 ms | 500 | 500 k | 256 MB | 1 | 1024 |

   A frame time measured here becomes 1 ms + (measured − an empty window's frame time
   here) × host_speed / speed on the target: an empty scene in the same window is
   measured once per binary (`.joinery/checks/baseline.json`), so what any window costs
   (more in an editor binary) isn't scaled up as if it were the game's. Load times scale
   too; counts and memory don't. `[performance]` in joinery.toml sets `target`,
   `host_speed` (1.0) and any budget for every target or one
   (`[performance.web] draw_calls = 300`); the bible's `[models] max_triangles` caps
   models where it says. It is an estimate and the card and CLI say so.
3. **Findings with evidence and fixes.** Each finding has the measured value, the
   target's budget, where it was seen (scene, node, script, moment, seed) and its fix:
   - *typed*: Krita resizes a texture's painting (its recipe replays); a texture with no
     recipe is limited in its `.import` (`process/size_limit`); a big lossless 3D texture
     is VRAM-compressed; a model's LODs are turned back on in its `.import`; a model far
     over budget made in Blender is decimated there (`reduce_triangles`); repeated props
     get a visibility range; shadows go off on the lights over the budget;
   - *agent*: scripts that cost too much a frame, repeated props into one MultiMesh,
     physics layers and masks, too many nodes or bodies, slow loading, memory that grows.
   - The frame time itself has no fix of its own: the others make it.
4. **One runner for both checks** (`bridge/check_runner.py`). The cards run the check
   (`--json`); **Fix** sends `check_fix`. The host takes the card's report when it is
   under five minutes old, else measures again, and plans (`checks_core.plan`): typed
   fixes grouped by the file they change (no two tasks want one file) run as scripted
   tasks of the crew, as match's do; for code, the check's read-only agent —
   `accessibility` or `performance` in `joinery/roles.py`, judged by the same role table
   for every provider — reads the findings and the files (and may call `access_check` or
   `perf_check`, and `playtest` for performance) and words the change; a worker task
   makes it from those words (or the check's own when the agent can't say). As many run
   at once as `[agents] parallel` leaves free. Every change goes through the submit
   checks, the reviewer and the user. `access_check` and `perf_check` are read-only
   registry tools for the lead and workers too. `[agents] access_check_every` and
   `perf_check_every = N` check again after every N accepted changes (off by default).
5. **The card.** `/perf [target]`, **Performance check** in Settings, or the Review
   tab's **Game checks** row opens the Performance card: a headline naming the machine
   ("4 problems on a Steam Deck"), a target picker that measures again, a score per area
   (frame time, rendering, textures, lights, physics, scripts, loading, memory), the top
   findings and Fix. After an accept the game is measured again and the scores rise.

## Consequences

- A check takes about half a minute (headless runs fast; the window plays in real time).
- An empty window's frame time on this machine sets a floor: a small game measures as
  the window's own cost, so differences under about a millisecond aren't seen.
- Draw calls, primitives and video memory need a display; headless they come from the
  scene census (triangles, an upper bound of texture memory) and draw calls aren't judged.
- Script costs are measured per script, not per function; the agent reads the code for
  the line. Stopping a script's nodes for a few frames can change what the game does
  after; it happens once play is over.
