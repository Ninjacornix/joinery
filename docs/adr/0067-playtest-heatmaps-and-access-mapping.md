# 0067. Playtest heatmaps, longer text, Xbox guidelines and the running game

Status: accepted (2026-11-12)

## Context

The playtest bot (ADR 0038) maps the level and reports what share of it it reached, but a
single run says little about where players die, get stuck or linger, and the coverage
map showed only reached and unreached floor. The bot already turns Godot's pseudo-locale
on during its pause check, with the default settings (no expansion), and reports text that
doesn't fit as a low finding; the accessibility check (ADR 0049) didn't look at text length
at all. Its findings were grouped by Joinery's own nine areas, which nobody outside
Joinery uses; studios that ship on Xbox think in the Xbox Accessibility Guidelines. And an
agent could start the game with Godot's `run_project` and reach the runtime inspector's
`game_*` tools through `call_tool`, but only as raw, unjudged calls: no role could look
without being able to change the game too, and there was no way to play a timed sequence.

## Decision

1. **Heatmaps over the last N playtests.** The bot records where the player was when a
   level was lost (`deaths` in bot.json), where each respawn happened (`respawn_at`, the
   position just before the jump), and the frames spent in every coverage cell (`dwell`
   in the coverage summary). `joinery/playtest_heat.py` turns one run into points on the
   ground plane (`heat` in report.json: deaths, stuck points from softlock and fall
   findings, seconds per cell; a respawn within 3 s of a lost level is the same death),
   and `aggregate` lays the last N runs of the same scene and level kind over the newest
   run's grid: three layers of `[col, row, value]` and the top hotspots in world units.
   N is `[agents] heatmap_runs` (5 by default, at most the 10 runs kept).
   `joinery playtest --heatmap [--last N] [--json]` prints it without playing; the
   `playtest` tool returns the hotspots; the playtest card gets the whole aggregate.
2. **The same map, three toggles.** Under the coverage map the card shows one toggle per
   layer that has anything: Deaths (count), Stuck (count), Time spent, and "last 5
   playtests". Deaths are filled dots in the danger colour, stuck points open rings in the
   text colour (they differ in shape, not only colour), time spent an accent wash (square
   root of the share, so a few long waits don't drown the rest). While a layer shows, the
   unreached floor steps back to a faint tone and the legend's square follows it. Deaths
   and stuck start on when present; time spent waits for its toggle.
3. **A translations area in the accessibility check.** After reading the menus, the
   access probe shows every screen again (the HUD, pause, settings and each settings page,
   main) with Godot's pseudo-locale at `expansion_ratio` 0.3 and fake BiDi on (set on the
   main TranslationDomain and restored after), lets containers lay out, and lists every
   visible Label or Button whose text runs off the screen, is cut off on its line, loses
   wrapped lines, or runs past a clipping ancestor, with its scene, node path, page and
   rectangle. The windowed run (the flash run, never focused) saves each screen with those
   rectangles outlined (`long_text_<page>.png` in the run's folder); its path is the
   finding's `evidence.shot`. Off the screen is high, the rest medium. A one-line label in a
   scene that is cut off gets a typed, reviewed fix (`autowrap_mode` word-smart and no
   trimming, through the Godot MCP like every text fix); everything else is an agent's
   change naming the node and screen. The area's score is the share of texts that fit.
4. **Findings by Xbox Accessibility Guideline.** `joinery/access_xag.py` holds XAG 3.2's 23
   guidelines (101 Text display to 123 Mental health best practices) and files each
   finding under one by its check (options by the setting they name). Joinery measures 9
   of them: 101 (text size, longer text), 102 (contrast, HUD backdrops), 103 (colour-only
   signals, palette pairs, colour filter), 104 (subtitles), 106 (names screen readers say),
   107 (remapping, gamepad, hold or toggle), 108 (pause anywhere, difficulty or game speed),
   117 (shake and motion settings) and 118 (flashes). The report's `facts.xag` lists every
   guideline with its status: fails, passes (its areas were measured clean), not measured
   this time, or left to a person. The card puts a disclosure, "Xbox guidelines · 9 of 23
   checked", on Show all's line; open, it takes the place of the scores and findings: what
   failed first (each opening to its findings in their own words), then what passed or
   wasn't measured, then one line naming the guidelines a person judges. The list scrolls
   past 30 % of the window. Agents get only the failing guidelines.
5. **The running game, judged.** Two joinery-registry tools reach the runtime inspector of
   the game this agent's gateway started with `run_project` (`ProjectGateway.game_call`;
   anything else is refused, and so is a game the gateway didn't start):
   - `inspect_game` reads the scene tree, one node's details and up to 12 properties, the
     visible UI and a screenshot (kept under `.joinery/live/`, the last 20; context, never
     an asset). Every role may call it, read-only ones and inspection specialists too.
   - `drive_game` plays up to 64 steps in 30 s: tap (press, hold its seconds, release),
     hold, release or wait, on an input action, a key, a gamepad button or a stick axis.
     Whatever is still held at the end, or when the game refuses a step, is released.
     Timing is wall clock through the inspector; the result lists each event's planned and
     actual time and the worst lateness. The lead and workers may drive. Of the read-only
     roles only the playtester and the debugger may, and only where the project lists them
     in `[agents] game_input_roles`; the tool is listed to those two roles, and the gateway
     and the policy hook judge every call by the same `joinery.roles.may_drive`, so every
     provider (Claude Code, Codex, Gemini CLI, opencode) gets the same answer.
   Neither tool writes a file; the game's state is gone when it stops.

## Verification

- The XAG list was read on 2026-11-12 from Microsoft Learn: the guidelines page (version
  3.2, published 8 June 2023), the version history (every guideline's number and title)
  and the pages of XAG 116 and 123. The titles here follow Learn's; XAG 119 is named
  "STT / TTS" in the version history and is written out as speech-to-text and
  text-to-speech here. Which Joinery check speaks to which guideline is Joinery's own
  reading, not Microsoft's; in particular "pause anywhere" is filed under 108 (Game
  difficulty options), since XAG 116 (Time limits) is about UI time limits, not pausing.
- The runtime inspector's tool names and arguments (`game_get_scene_tree`,
  `game_get_node_info {nodePath}`, `game_get_property {nodePath, property}`,
  `game_get_ui`, `game_screenshot`, `game_key_hold`/`game_key_release {key | action}`,
  `game_gamepad {type, index, value, device}`) were read from the pinned Godot MCP's
  README and source at its catalog ref through a web summary, not from a running game;
  the unit tests use a fake inspector, and no test here drives a real running game.
  A screenshot is accepted as an image part or as base64 PNG in the text.

## Consequences

- Heat only lines up across runs of the same scene and level kind; a level whose bounds
  change shifts the grid, and points past the newest grid's edge are dropped.
- Deaths are what the game core reports (`level_failed`) plus respawns the bot sees as a
  jump; a game that kills the player without either shows no deaths.
- The longer-text pass reads Labels and Buttons; RichTextLabel, LineEdit and text drawn in
  code are not judged. Screenshots need the windowed run; headless runs list the nodes only.
- `drive_game` timing is as good as the inspector's round trip (a frame or two); for
  frame-exact input use a replay (ADR 0059).
