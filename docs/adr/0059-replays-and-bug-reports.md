# 0059. Replays and bug reports

Status: accepted (2026-11-12)

## Context

A bug found by playing (by the user, the playtest bot or a player) was a sentence and,
at best, a seed. Nothing kept the play itself, so a fix could not be checked against
what actually happened, and a later change could bring the bug back unseen. Players of a
shipped game had no way to say what went wrong beyond an email.

## Decision

1. **The game core records play** (`core/replays/replays.gd`, autoload `Replays`, the
   first autoload so it seeds before anything runs). It keeps every input the root
   window receives (keys, mouse buttons and motion, joypad buttons and axes, touch, input
   actions), each on the physics frame it reaches the game: the step it was sent in (a
   test or the bot sends on the step's signal), else the next one. Frames count from the
   first physics frame with a scene. It also keeps the global random seed (it calls
   `seed()`; `Replays.rng(name)` gives a game further generators seeded from it), the
   start scene and how the session started (the engine's main scene, or a test script),
   the game's own arguments, the settings file, the window size, the game and Joinery
   versions (`game/joinery_version`, written at project creation) and the engine build,
   and every second a checkpoint: a hash of the running scene and a short readable state.
   - **When.** Always in debug builds (editor runs, tests, playtests), in memory. In an
     exported release build only when the game opts in
     (`game/replays/record_in_release`, off). `-- --joinery-record[=<name|path>]` also
     writes the file as it goes, to `user://joinery_replays/<name>.replay`. Sessions
     longer than `game/replays/max_minutes` (30) keep their last part, marked partial.
   - **The file** is JSON lines: a header, `{"f", "e": [items]}` and
     `{"f", "check": {hash, state}}` lines, and `{"end"}`. Events are decoded from a fixed
     list of fields, never from a class name, so a file a player sends can only make
     input events. `"t": "call"` items are the few core calls a tool makes without input
     (`Replays.perform`: `Game.start_game/restart_level/quit_to_menu`,
     `Menus.close_all/open_pause/back`, `SaveGame.save_slot/load_slot`); a replay makes
     no other call.
   - **The hash** covers, under the current scene in tree order, each node's class and
     place, 2D and 3D transforms, body velocities, animation positions, timers,
     visibility and label text, plus the core's saveable state and level and play time,
     at full precision. Generated names (`@Node3D@12`) count as their class. The state
     beside it (scene, paused, node count, level time, player places, a short hash per
     child of the scene) says what differs.
2. **Recording from Joinery.** `/record` in the sidebar ("Record this play",
   `replay_record.gd`) runs the main scene once with `--fixed-fps <physics rate>` and
   `-- --joinery-record --joinery-pace` (held to real time), through Godot's own run
   arguments, put back on the next frame. `joinery playtest --record` has the game record
   the bot's run to `.joinery/playtests/<run>/play.replay`; the bot's own core calls go
   through `Replays.perform`, so they replay too. Every playtest now passes its seed to
   the game (`--joinery-seed`), so the game's random numbers follow it as well.
3. **Replays are tests.** `joinery replay add <file|report folder> [--name]` keeps a
   replay in `tests/replays/` (a partial one is refused). `joinery replay run [NAME…]
   [--all] [--file F] [--json]` replays headless in stock Godot (`JOINERY_REPLAY_GODOT`
   overrides it): `--fixed-fps` at the recorded rate, the recorded seed, arguments,
   settings and window size, an empty save folder and a HOME of its own, through
   `res://core/replays/replay_boot.gd` when a script started the recording. The game
   compares each checkpoint and prints one `JOINERY_REPLAY {json}` line; Python judges
   it: `passed`, `diverged` (frame, seconds, what differs), `errors` (errors the
   recording didn't have, with where and when) or `failed` (didn't finish).
4. **Replays run on every staged change** that touches the game (`.gd`, `.tscn`,
   `.tres`, shaders, `project.godot`), after Godot's import, in the offline worker, when
   `[checks] replays` is on: the default once `tests/replays/` has any, `false` turns it
   off. The worker's Godot is another build than the editor that recorded, so a replay
   is judged against what it did on the worker without the change (run once on a clean
   copy of the project and kept in `.joinery/replays/` until the game changes): one that
   diverged there the same way is not this change's doing. A replay that now diverges or
   errors is a fixable problem (`replays`) that sends the change back with the frame and
   what differed, and says the user can record it again if the change is meant to alter
   it.
5. **Bug reports from players.** The core's `BugReports` autoload adds "Report a bug" to
   the pause menu and the `report_bug` action (F8, part of the core's action
   vocabulary, never pressed by the playtest bot). It pauses, asks "What went wrong?" and
   on Save writes `user://joinery_reports/<id>/`: `report.json` (note, time, game and
   Joinery versions, build, OS, seed, scene, frame, the seconds looked back on),
   `replay.replay` (the session up to the page opening, from its start so it replays
   exactly, with its last `game/bug_reports/seconds` (60) marked as `focus`) and
   `log.txt` (the last 400 log lines, errors and warnings with their frame, through a
   Godot `Logger`). The id is time plus four hex digits from a generator of its own, so
   the game's seeded one is untouched. The last 20 reports are kept;
   `game/bug_reports/enabled` turns it off. Nothing is uploaded.
6. **Reading them.** `joinery reports list|open <id>|import <folder>` finds reports in
   the game's user folder on this machine (as Godot names it for the project) and in
   `.joinery/reports/`, where a player's report is imported. `/debug report <id>` in the
   sidebar sends `debug_start {report}`; the chat host turns the report into the
   debugger's request (its last error line, or the note, with the facts, the replay's
   path and the log), and the debugger proposes a fix that a worker stages as a normal,
   reviewed change (ADR 0037).

## Determinism: what replays exactly

On the same build, a recording made at a fixed frame rate replays to the same hash. All
seven blueprints were recorded and replayed twice with equal final hashes; two of them
(platformer-2d, top-down) are in the tests, with a speed change and a removed input
both caught at the first checkpoint after them. What it took, and what remains:

- **Unseeded generators.** `RandomNumberGenerator.new()` seeds itself randomly. The
  puzzle-cards deal and the top-down camera shake did, and did not replay; they now use
  the session's seed (`randi()`, `Replays.rng`). A game script that makes its own
  generator does not replay until it does the same.
- **Frame rate.** `--fixed-fps` gives every frame one physics step and the same delta.
  A recording without it (a normal editor run, a player's release build) has uneven
  frames: `_process` logic, idle-time Timers, tweens and animations, and the number of
  physics steps per frame differ on replay. The file says so (`end.steady`) and the
  result notes it. Scripts cannot see `--fixed-fps` (Godot strips engine options from
  `OS.get_cmdline_args`), so steadiness is measured from the frames' deltas.
- **Where frames start.** A test script loads the main scene itself a frame later than
  the engine does; replays start the same way the recording did and count from the
  first frame with a scene, and the start scene is the first scene added, not the one
  showing when counting began.
- **Window.** Headless Godot's window is 64×64, and `canvas_items`/`expand` stretching
  shows more or less of the world by aspect. A replay sets the recorded window size;
  mouse positions are scaled by it once, so a window resized during the session does
  not replay its clicks exactly.
- **Builds.** Hashes are exact floats. Another engine build, platform or CPU may differ
  by itself (the result names both builds); the staged check compares against the same
  build before the change for this reason.
- **Not replayed:** the wall clock (`Time.get_ticks_*`, dates), threads and threaded
  loading, audio playback positions, online sessions and anything from the network,
  saves made before the session (replays start with an empty save folder), gestures,
  MIDI and IME text input, and what a tool changes outside `Replays.perform` (the
  bot's direct unpause after a softlock).
- **Order inside a frame.** An event sent from a `_process` callback reaches the replay
  at the start of the next physics step, before that frame's remaining `_process`
  calls rather than after them.

## Consequences

- Every Joinery game records in debug builds; it costs a few kilobytes a minute and a
  tree walk every second (five seconds in memory only).
- A replay is a regression test of behaviour, not of intent: a change meant to alter
  what the recording did fails it, and the user records it again.
- Replays in the staged checks cost one headless run per replay per change, plus one on
  a clean copy whenever the game changed since the last baseline.
- An uploading crash reporter (Sentry or the like) is a later, per-game opt-in; reports
  stay on the player's machine until the player sends them.
