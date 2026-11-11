# 0053. Design check

Status: accepted (2026-11-11)

## Context

Users asked for a regular check of game design: when something is bad design, flag it.
Joinery already checks art against the bible (ADR 0022, 0045), plays the game for bugs
(ADR 0038) and measures accessibility and performance (ADR 0049, 0050). Nothing compared
the game with what its own brief and design document say it is, and nothing caught the
design faults a player hits first: a verb the brief promises with no key, a win state
nothing reaches, a room you can drop into and never leave. "Good design" is also where a
model is most tempted to report taste, and a report full of opinions is one nobody reads.

## Decision

1. **Only two kinds of finding.** The game contradicts its own brief or design document,
   or it is broken for a player (unreachable, softlock, unbound input, no feedback).
   Taste is never a finding: a finding that can't cite the brief, the document or the
   breakage is not one.
2. **Deterministic checks first, no model.** `joinery design --project P [--json]`
   (`joinery/design.py`, `joinery/design_checks.py`) reads what the project has: the
   brief (`design/brief.md`), design documents imported at setup (`design/docs/`, ADR
   0023), `project.godot`'s input map, scripts and scenes (never
   `addons/`, `tests/` or Joinery's state), blockout layouts (`design/blockouts/`) and the
   newest playtest report. Each finding has a severity, one line, what the player would
   experience, why, and evidence (file, line, node, words):
   - a verb in the brief's verbs table with no input action, an action missing from the
     input map, an action bound to nothing, or a bound action no script or scene reads
     (Godot's own `ui_*` actions count as bound);
   - a mechanic a design document's verbs or mechanics section lists that no input
     action, script or scene mentions;
   - the brief's "Win: …" or "Lose: …" with no code that ends the level that way, or code
     that does but that nothing the game runs reaches (engine callbacks, scene
     connections and every function their bodies name, followed to a fixed point);
     "Lose: none" and undecided sections promise nothing;
   - bound gameplay actions (not restart, pause or menus) that no menu, label or hint
     shows, and no code reads from the input map to show;
   - a pickup script (an Area that removes or hides itself on contact) with no sound,
     particles, animation, tween or signal, in the script or its scenes;
   - one player scene instanced with different movement values (jump, speed, gravity,
     acceleration…) in different scenes, or player scripts tuning the same name
     differently;
   - in each blockout, a goal, coin, pickup or checkpoint the spawn can't reach, and a room
     the spawn reaches from which no walk, jump or drop reaches a goal (the blockout's own
     reachability walk, ADR 0043, from a probe in each room, within a time budget);
   - from the newest playtest: softlocks and falls out of the world with their seed and
     place, "it may be too hard" and "the goals may be hard to find" notes, and failures
     clustered within 20 seconds as a difficulty spike. "Too easy" is taste and is left
     out.
3. **An optional agent pass, read-only, evidence or dropped.** A new role, `designer`
   (`joinery/roles.py`), is read-only like the reviewer and playtester (ADR 0037, 0038):
   the same `joinery hook` policy and gateway endpoint for every provider let it read and
   call `report_design`, and nothing that changes the project, starts work or reaches the
   web. The chat host (`bridge/designer.py`) runs Joinery's checks, tells the agent what
   they found, and the agent reports judgement calls (a goal the brief sets that is never
   shown, feedback the verbs table promises that the code never gives). `report_design`
   takes up to eight findings, each with a basis (`brief`, `gdd` or `broken`) and evidence;
   `design.agent_findings` keeps only those whose file exists, line is in range, quoted
   words are on that line and node is in the scene, and a brief or document finding must
   cite its line under `design/`. The rest are dropped and listed back to the agent, never
   shown. A finding that repeats one of Joinery's is left out. The pass has five minutes;
   past that, Joinery's findings are shown alone.
4. **Glanceable, never a fix by itself.** `/design`, or **Design check** in Settings,
   opens a card with the agents at work (`design_card.gd`, `sidebar_design.gd`): a
   headline ("3 design problems"), the severities in one line, one line per finding with
   its severity dot, and **Ask agent to fix**, which puts the finding's request in the
   composer for the user to edit or send as a normal reviewed request. Nothing is changed
   by the check, by the card or by the agent. The evidence, the player's view and why are
   in the line's tooltip. **Look deeper** starts the agent pass; **Check again** reruns the
   checks.
5. **Optional cadence, off by default.** `[agents] design_check_every = N` in
   `joinery.toml`, beside `access_check_every` and `perf_check_every`, runs the checks
   (no model) after every N accepted changes; the card shows only when they find
   something. Settings has **Check the design** (Off, after every change, every 3, 5 or 10
   changes), saved through `joinery design --every N`. The count is the chat session's.

## Consequences

- A game made from a starting point checks clean; what it flags is what the user's own
  changes or brief introduced. Starting points without the Controller feature are flagged
  for controls never shown, which is true of them.
- The checks read text, not a running game: a win reached only through reflection or a
  string built at run time can be reported as unreachable, and an action read only through
  a variable as unread. The evidence names the place, so the user can see why.
- Tuning differences that the brief wants (a slippery ice level) are still flagged; the
  request says to keep them if the brief says so.
- Blockouts are walked as their layouts, not the built scenes; a scene edited by hand
  after it was built is not walked.
