# 0060. Done-when checks

Status: accepted (2026-11-12)

## Context

A request like "lock the vault behind a lever gate" takes several steps, and both the user and
the agent need to agree what finished looks like before work starts. Other tools show a plan
the user approves and then trust the agent's word that it is done. Joinery already has what
it needs to check instead: a seeded playtest bot (ADR 0038), the project's GUT tests and its
own submit checks. What it lacked was a place to write down "done when" in terms those can
verify, and a moment to check it before the user sees the change.

## Decision

1. **A to-do, proposed by the agent.** `propose_todo(steps, done_when)` is a lead-only tool on
   the `joinery-registry` proxy (`propose_todo` in `LEAD_ONLY`, so the shared policy hook and
   the gateway endpoint refuse it for tasks and read-only roles alike), backed by the gateway
   RPC method of the same name and `TurnController.propose_todo` (`joinery/turn.py`). Steps
   are 1 to 8 short lines in the user's words; up to 6 done-when items each say what checks
   them:
   - `reach` (target: a place named like its node): the playtest bot heads there in a seeded
     run of the changed game (`--reach`, ADR 0038) and must get inside its meshes or box
     shapes, or near its origin.
   - `no_errors` (10 to 300 s, default 60): the same run has no crash, freeze, script, shader
     or engine error.
   - `test` (target: a `res://` GUT test the agent writes in the change, for behaviour such as
     "the door opens when the lever is pulled"): it is in the change, in the project's test
     folder, and ran with the project's tests in the submit checks.
   - `check` (tests, Godot import, art bible, references, provenance or dependencies): that
     submit check ran on this change.
   - `look`: only the user can judge; always Inconclusive.
   The agent is told (`TODO_GUIDE`) to propose a to-do for requests with several steps and to
   skip it for small ones.
2. **Go, when the user's setting asks.** `[agents] todo_go` in `joinery.toml`: `"multi"` (the
   default) waits for Go when the to-do has two steps or more, `"always"` waits for every
   to-do, `"never"` never waits. A to-do proposed after the agent already changed something
   never waits. Waiting does not hold a tool call open (provider tool timeouts differ): the
   tool returns `waiting_for_go`, every change is refused until then (`WAITING_FOR_GO`), and
   the agent ends its turn. The sidebar's to-do card (`todo_card.gd`, through
   `sidebar_todo.gd`) shows the list at once; while it waits every line edits in place and
   has its own remove. Go sends the list as left (`todo_go`, a request of its own whose bubble
   reads "Go", so it has its change card and review like any other); the agent hears it as
   `done_when.go_prompt` and the controller holds the to-do for the turn. Cancel drops it, and
   so does any other message (`dropped`). The user can't change an item's kind or target,
   only its words or whether it stays.
3. **Checked after Joinery's checks, before the reviewer.** Once a submitted change passes
   the submit checks, `TurnController._check_done` runs `done_when.verify`: every playing item
   shares one run of the stage (the change as files) with seed 4242, as long as the longest
   asks, its report in the project's `.joinery/playtests/`; the rest read the submit checks'
   `ran` and `skipped`. A change that is only a live preview in the editor can't be played
   yet: its playing items are Inconclusive and say so. A stage unchanged since the last check
   keeps its results. Each result is `{id, text, status: pass|fail|inconclusive, evidence}`,
   the evidence in plain words with the seed where there is one ("The bot reached Vault at
   0:42, seed 4242."). Events `{kind: "todo", todo_id, state: waiting|going|checking|fixing|
   checked|cancelled|dropped, steps?, done_when?, results?, summary?}` drive the card.
4. **A Fail goes back once.** Any Fail sends the change back to the agent as a fixable
   problem (`check: "done_when"`, `NEXT_DONE_WHEN`) in the same turn, costing no submit
   attempt, the card reading "Fixing what didn't pass…". It is a send-back like the
   reviewer's: timed as a `send_back done_when` span and counted in the change's
   `joinery.review.send_backs` (ADR 0054). After one round, or on Joinery's own last submit,
   the change opens with its results, Fails included: a done-when item never drops a change.
   The agent may leave an item it can't pass and say why (a bot can't make a jump a person
   can).
5. **The review shows each item.** The `file_preview` carries `done_when` and
   `done_when_summary`; the review's glance (`review_summary.gd`) adds a chip "2 of 3 done",
   its glyph the worst result, and one row per item with its Pass, Fail or Inconclusive glyph
   and the evidence under its words. A Fail makes the change one to open in the panel rather
   than decide from the chat card. The to-do card shows the same results.

## Consequences

- The same tool, gate and events serve Codex, Claude Code, Gemini CLI and opencode; nothing
  here knows the provider.
- Done means something checked, not something claimed, but only as far as the checks go: a
  bot that doesn't reach a room may be a bad platformer, so a reach Fail says "check the way
  is open" and the agent may argue it in one line. Behaviour the bot can't see needs a test
  the agent writes, which then runs with every later change too.
- Checking adds a seeded headless run (about a second of real time per minute of a
  blueprint) to submits of requests with playing items; requests without a to-do pay nothing.
- `todo_go` has no control in Journeyman's settings yet; it is set in `joinery.toml`.
