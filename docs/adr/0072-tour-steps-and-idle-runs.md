# 0072. A first-project tour, steps that run together, and runs that wait for idle

Status: accepted (2026-11-12)

## Context

Three gaps, each small:

- A new user opening their first project sees a full editor and a sidebar, and
  nothing says where to start: where to type, that a change waits for Accept,
  that the Timeline undoes, that Share builds the game. The README and the
  editor guide are reference material, not a walkthrough.
- A to-do (ADR 0060) often has steps that don't depend on each other ("a chair,
  a lamp, then a shelf for both"). The lead agent makes them one after another,
  although the parallel task machinery (ADR 0036) could make them at once, and
  every step it makes itself costs its own context.
- Some runs need no one watching: "Make it match", the accessibility and
  performance fixes, the design check's agent pass, the eval benchmark.
  Providers sell slower, cheaper capacity for such work (Anthropic Message
  Batches, the OpenAI Batch API and Flex processing, Gemini Batch Mode).

## Decision

### 1. The first-project tour

`godot_addon/joinery/tour.gd`, installed by `plugin.gd` with the bottom tabs.
Six steps, each lighting one place with an accent ring while the rest of the
window dims, the card beside it (or over it near its bottom when the place is
most of the window): the Journeyman sidebar, picking something (the main view),
asking for a change (the message box), the review with Accept and Discard (the
Review tab), the Timeline, Share. Back, Next (Done on the last step), **Skip
tour**, and the keys Left, Right and Esc.

- **When**: once, on a new project's first open. `joinery new`, the wizard's
  `joinery game apply` and a first `joinery setup` of a new project write
  `.joinery/tour-pending` (git-ignored, never part of the game); the addon
  removes it and opens the tour after project setup closes, if it shows.
  Again from **Help › Joinery Tour** (when the editor has a Help menu where
  Godot puts it), **Project › Tools › Joinery: Tour** and `/tour`.
- **Remembered**: Skip, Done or Esc marks it seen in the editor's project
  metadata (per project, per machine, never in the project's files).
- **Theme, motion, width**: it uses the Joinery theme and follows its light or
  dark palette; under Reduce motion every move jumps; the card is 22 em wide or
  the window less its margins, whichever is narrower.
- **Stock Godot**: a place the editor doesn't have leaves that step's card in
  the window's middle with everything dimmed.

`docs/getting-started.md` is the written counterpart: a game from a starting
point, the bible, a model, the review, playing, sharing. The README links it.

### 2. Steps, then run

`propose_todo` takes an optional `after`: for each step, the earlier step
numbers it needs (`[]` for none); without it each step needs the one before.
Only earlier steps may be named, so the order can't loop. Removing or adding a
step on the to-do card drops `after` (the numbers no longer line up) and the
steps run in order.

`[agents] run_steps = "together" | "one_by_one"`, default `"one_by_one"`
(today's behaviour). With `"together"` and two or more steps that need nothing
first, the harness (`bridge/steps.py`, from `bridge/turn_runner.py`):

1. runs those steps as parallel tasks of the crew (origin `lead`, title
   "Step 2 · Make a lamp", the fixed `STEP_PROMPT` rules), as many at once as
   `[agents] parallel` allows; the lead's own turn takes no slot meanwhile;
2. gives the lead no turn until they settle: every step in review (or decided,
   when other steps need them, so the lead builds on accepted files), or
   **as soon as one fails** (the steps not started yet stop; those at work
   finish);
3. then hands the lead what is left, once (`done_when.steps_prompt`: what each
   step became, the steps left, the done-when items). When nothing is left,
   the lead takes no turn at all; its done-when items read Inconclusive
   ("Made as 2 separate changes, each checked on its own"), since there is no
   one change to check, and Joinery says so in its own line.

It works both when the user pressed Go and when the to-do ran on without Go:
then `propose_todo` returns `running_steps`, every change is refused
(`STEPS_RUNNING`) and the agent ends its turn; the steps start when it does. A
project with `parallel = 1` gets a notice and the lead makes every step itself.
The to-do card shows "Making steps 1 and 2 at once…".

Metrics (ADR 0054): the request's root span carries `joinery.steps.mode`
("one_by_one" or "together" for every request with a to-do),
`joinery.steps.together`, `joinery.steps.failed` and
`joinery.steps.back_to_lead`; each step run as a task is a `step` span with its
number, state and task id (each task also writes its own change file).
`joinery metrics` prints a `steps:` line.

### 3. Batch pricing: not reachable, so a "when idle" queue

What each agent CLI offers (`joinery idle --project P batch`, kept in
`joinery.idle_runs.BATCH_SUPPORT`):

| CLI | Batch API | How we know |
|---|---|---|
| Claude Code 2.1.321 | No flag or setting. Its bundled SDK has the Message Batches endpoints, but the agent loop streams each step. | Verified: `claude --help`, strings in the installed binary |
| Codex CLI 0.167.0 | No batch mode. Its config has a `service_tier` key (`fast`, `flex` seen in the binary): Flex processing is cheaper and slower, but not the Batch API. | Verified from the installed binary; `service_tier = "flex"` itself not exercised (it would bill a live run, and applies to API-key accounts only) |
| Gemini CLI | No batch mode; streams `generateContent`. | Assumed: not installed here |
| opencode | No batch mode; streams through each provider's chat API. | Assumed: not installed here |

A batch API also doesn't fit an agent turn: a turn is a loop of tool calls, and
a batch request answers once, up to a day later. So Joinery doesn't use one,
and doesn't set Codex's `service_tier` (unverified, and API-key billing only).

Instead, `[agents] unhurried = "now" | "idle"` (default `"now"`):

- With `"idle"`, the sidebar's `match_fix`, `check_fix` (access, perf) and
  `design_start` go into `.joinery/idle-queue.json` instead of starting (a run
  can also ask `when: "idle"` or `"now"` itself). A notice says when it runs;
  its card settles.
- The chat host (`bridge/idle_queue.py`) looks every 30 s and starts the oldest
  waiting run when nothing else works, the editor has been quiet for
  `idle_minutes` (default 15; messages the editor sends by itself don't count)
  and the local time is inside `idle_hours` (default `"22:00-07:00"`, may wrap
  midnight; `""` for any hour). It runs on the project's light model (ADR 0061)
  when one is set, otherwise the chat's. Its changes are reviewed like any
  others. One run of each kind waits at a time; a newer ask replaces it.
- `/idle`, `/idle now`, `/idle clear` in the sidebar; `joinery idle --project P
  [list|add KIND|remove ID|clear|batch] [--json]` on the command line.
- Each run that starts writes one line to the change metrics: the kind, how
  long it waited, the model and its tier.
- The benchmark: `evals/run.py --suite benchmark --live --off-peak` waits for
  the testbed's `idle_hours` and runs on its light model unless `--model` pins
  one.

Everything here is in the shared core and the chat host, so it is the same for
Claude Code, Codex, Gemini CLI and opencode.

## Consequences

- A new user gets six steps once and can always get them back; nothing in the
  game changes.
- `run_steps = "together"` trades tokens in several sessions (each task has its
  own context) for wall time and a lead that only plans and finishes. It is
  opt-in; the default is unchanged. Steps that depend on others still wait for
  the user's decisions, so the lead never builds on unaccepted work.
- Unhurried runs save money only through the light model and the user's own
  off-peak hours, not provider batch prices, and they need the editor open.
  Should a CLI expose a batch or flex mode later, `BATCH_SUPPORT` and this ADR
  are where it goes.
- Not verified live: the steps and the idle queue are tested with fake agents
  and a fake clock; the tour by widget test and editor screenshots (light and
  dark, wide and narrow). No paid agent ran.
