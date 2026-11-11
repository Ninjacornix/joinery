# 0038. Playtester

Status: accepted (2026-11-11)

## Context

Every blueprint has scripted play tests (`tests/game/play_test.gd` on the PlayHarness), and
they prove what their author thought to check. Nobody plays the game the way a curious first
player does: walking into walls, jumping off edges, pausing at odd moments, saving mid-fall.
Users asked Journeyman to "try it and tell me what's broken", and the agent could only read
code. Custom games made from the blank blueprint have no play tests at all.

## Decision

1. **A playtest engine that needs no model.** `joinery playtest --project P [--minutes N]
   [--seed S] [--scene res://…] [--json]` (`joinery/playtest.py`) runs the game in stock Godot
   4.7, headless and offline, with a bot (`joinery/playtest_bot/bot.gd`, loaded by `--script`
   from Joinery's own folder, never copied into the game):
   - **It plays through the game's input map** (`bot_drive.gd`): camera-relative
     `move_forward/back/left/right` for 3D, `move_up/down/left/right` for top-down,
     `move_left/right` and `jump` for platformers, clicking visible buttons for a game
     without a player, and random presses of every action and clicks for anything else.
     It heads for the nodes a blueprint puts in goal groups (`coin`, `pickup`,
     `checkpoint`, `goal`, `interactable`, `pressure_plate`, …), taps interact when it gets
     there, turns and jumps when stuck, and never clicks buttons that leave the game.
   - **It uses the game core where there is one**: it boots like the PlayHarness
     (`Game.autostart`), listens to `GameEvents` for levels, scores, checkpoints and world
     changes, carries on after a level ends, and checks pause and resume. With the
     save-load feature it saves, plays on, loads and compares every `saveable` node. With
     translations it checks the pause menu's text in every language and in the
     pseudo-locale.
   - **It watches** (`bot_watch.gd`, `bot_logger.gd`): every error and warning through a
     Godot `Logger`, with its script line and the frame; frame times; memory, node and
     orphan counts; a player whose position becomes NaN, who is flung, who falls out of
     the world and is not brought back, or who respawns; and softlocks: the game paused with
     nothing that unpauses it, the player unable to move while the bot holds movement keys,
     the player trapped in one spot, or nothing responding to input. After a softlock it
     restarts the level so the run goes on. A game with nothing to play yet (blank) is not
     stuck.
   - **Deterministic**: the bot's choices come from one seeded generator and the game runs at
     a fixed 60 physics frames a second, so `--seed` replays a run frame for frame. Headless
     runs much faster than real time (two minutes of a blueprint take about a second).
   - **Never writes in the game**: the report, console log and screenshots go to
     `.joinery/playtests/<run>/` (excluded from the Timeline and from Godot's import, last
     ten kept), and the run gets a HOME of its own, so the game's `user://` saves and
     settings never touch the player's. A project without an import cache is imported first.
2. **One report, judged in Python** (`joinery/playtest_report.py`). The bot reports facts;
   Python merges them with Godot's console (how the process ended, errors before the bot
   listened) and gives each item a severity: `critical` (crash, freeze, never started),
   `high` (script error, softlock, fell out of the world, physics, broken save/load),
   `medium` (engine error, frame hitches, growing memory, pause doing nothing, the game
   quitting itself), `low` (warnings, text that doesn't fit) and `note` (balance). Each item
   has the script line where there is one and repro steps: the exact command with the seed
   and when to watch. Balance notes come from timings and deaths: a level finished in under
   20 s by a random bot may be too easy; dying three or more times a minute without
   finishing may be too hard; a minute with no score, checkpoint or change suggests goals
   that are hard to find. They are hints, and say so.
3. **Screenshots from a replay.** Headless Godot draws nothing, so pictures come from a
   second run of the same seed in a window, which saves the frames of the notable moments
   the headless run found (findings, level ends, start and end) at 480 px wide. It runs in
   the Joinery editor binary with `JOINERY_BACKGROUND=1` (ADR 0032), so the window never
   takes focus, and is skipped where nothing can draw. Godot's movie writer would write
   every frame; the bot saves only the frames it needs.
4. **The `playtest` tool.** `playtest(minutes?, seed?, focus?)` is one read-only tool on the
   `joinery-registry` proxy, backed by the gateway RPC method `playtest`, the same for
   Codex, Claude Code, Gemini CLI and opencode, on the lead's, every task's and the
   playtester's endpoint. It returns the report trimmed for an agent and up to three
   screenshots as images (context, never assets). One playtest runs at a time.
5. **The playtester role** (`bridge/playtester.py`, `playtester` in `joinery/roles.py`,
   ADR 0037). `/playtest [focus]` in the sidebar sends `playtest_start`; the host starts the
   chat's provider in the read-only `playtester` role, judged by the same role tables in the
   policy hook (`JOINERY_AGENT_ROLE`) and at its gateway endpoint: it may read, look at the
   editor and call `playtest`, and nothing that changes the project, starts tasks or reports
   as another role. Its prompt is `PLAYTEST_PROMPT`: call
   `playtest` once, read the report and screenshots, write a short report of bugs, softlocks
   and balance notes with severities and repro steps, and end with up to three `Fix:` lines,
   each a request Journeyman can act on alone. The host plays anyway if the agent never
   called the tool, so the card always has facts. `[agents] playtest_after_change = true`
   starts the playtester after an accepted change to scripts, scenes, resources, shaders or
   `project.godot` (off by default); `playtest_minutes` sets the run length (default 2).
   Follow-ups are normal requests; an error the run finds can also go to the debugger
   with `/debug`. `playtest` is a shared read: the lead and workers may call it too, the
   reviewer (timed to 90 s) may not.
6. **The card.** Events `{kind: "playtest", playtest_id, state: starting|playing|writing|
   done|failed|stopped, elapsed, seconds, report, summary, followups}` drive one card above
   the footer with the task cards (`playtest_card.gd`, through `sidebar_tasks.gd`): "Playing…
   0:42 / 2:00" in game time with its own Stop, then one count per severity and each finding
   as a row that opens to its detail, script line, seed and screenshot, with **Fix this**.
   The list scrolls past 30 % of the window so the message box stays put. The playtester's
   words go to the transcript and each `Fix:` line becomes an "Ask Journeyman to fix" offer;
   both only fill the message box. The header counts the playtester among agents working.

## Consequences

- Every blueprint gets a real playtest in CI: a 20 s seeded run each in stock Godot must find
  nothing serious, and a fixture game with a planted script error, hitch and softlock must
  have all three found at the same moments on every run. Two-minute runs with every feature
  are marked slow.
- The bot is a bot. It finds crashes, errors, softlocks and broken saves well; it rarely
  finishes a level, so most balance notes are about deaths and goals it couldn't find, and
  they are worded as hints.
- A custom game gets error capture, crash and freeze detection, and random input; it needs
  `player` or `move_*` actions in its input map for the bot to explore with aim.
- Softlock detection is by time (15 s by default, `--softlock`): a slow puzzle that takes
  longer without any visible change reads as stuck.
- A playtest costs one provider session for the write-up; the run itself costs nothing.
