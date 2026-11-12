# Project memory, project commands and headless runs

Accepted: 2026-11-12.

## Context

Three things other agent harnesses have that Joinery lacked:

- **Standing instructions.** Codex reads `AGENTS.md`
  (`codex-rs/core/src/context/user_instructions.rs`), Gemini CLI reads a
  `GEMINI.md` hierarchy with `@imports` (`utils/memoryDiscovery.ts`), Claude
  Code reads `CLAUDE.md`. Joinery had the art bible and the game brief, which
  cover art and design only. "Signals are snake_case", "the player is 1.8 m",
  "never touch res://addons" had nowhere to live, and each provider would have
  needed its own file.
- **Repeatable requests with blanks.** Goose recipes
  (`crates/goose/src/recipe/mod.rs`: typed `parameters`, a `response` schema,
  `sub_recipes`) and Gemini's TOML custom commands. Joinery's slash list was
  fixed in code.
- **A headless turn.** `codex exec --json`, `gemini -p --output-format
  stream-json`, `claude -p --output-format stream-json`. `joinery chat` only
  speaks the editor's stdio protocol, and the benchmark's live driver accepted
  every change it made.

## Decision

### 1. Project memory: `JOURNEYMAN.md`

One plain Markdown file at the project root (`joinery.memory`), written from
`templates/journeyman.md` by `joinery new` and `joinery setup` when the project
has none. Sections: Rules, Facts, Remembered.

- **Every agent reads it.** `bridge.provider_process.turn_text` puts it in the
  stable prompt prefix (ADR 0056) after the role's rules and before the project
  map, for every provider and every role. HTML comments and empty sections are
  dropped and a file that is still the template adds nothing, so a new project
  pays no tokens. The bytes change only when the file does, so the block stays
  cached.
- **Size cap.** Journeyman reads at most 6,000 characters (about 1,500 tokens)
  of the cleaned file. A longer file is cut at a line, with a note in the prompt
  telling the agent the rest was left out. `/remember` refuses to grow it past
  the cap and says to shorten it.
- **Native files point at it.** `Agent.memory_file` names each agent's own file
  (Claude Code `CLAUDE.md`, Codex and opencode `AGENTS.md`, Gemini CLI
  `GEMINI.md`). `joinery.wiring.write_memory_stubs` writes a short stub there
  only where the project has none; a file that is there (the user's own or an
  earlier stub) is never touched. The stub names the file instead of
  `@`-importing it, so a Joinery session, which already has it in the prompt,
  doesn't read it twice; a coding agent run by hand in the project reads it on
  the stub's say-so.
- **Adding to it is a reviewed change.** `/remember <text>` sends `remember` to
  the chat host; `bridge/remember.py` checks the line (one line, at most 300
  characters, not already there, file not full) and starts a scripted crew task
  that appends it under "## Remembered" on the staged copy. It goes through the
  checks and the Review tab like any other change. "Remember this" in a reply's
  right-click menu puts `/remember` and the selection (or the reply's first
  line) in the message box, so the user can trim it before sending.
  `joinery memory --project P add TEXT` stages the same change from the command
  line and keeps it for review (below). The user can always edit the file by
  hand.

### 2. Project commands: `.joinery/commands/<name>.toml`

`joinery.project_commands`: a `title`, a `description`, a `prompt` with
`{{name}}` blanks, typed `[[parameters]]` (`string`, `integer`, `number`,
`boolean`, `choice` with `choices`; no `default` means required), optional
`[[checks]]` (`file` in the change, `changed` glob, `reply` regex) and optional
`[[subcommands]]` (other commands whose requests come first, with parameters
that can use the parent's blanks). Sub-commands make **one** request ("Do these
in order, as one change: 1. … 2. …"), because a chat has one change in review
at a time; loops and nesting deeper than four are refused. The name is the file
stem and can't be one of the sidebar's own commands (a test keeps
`RESERVED` in step with `slash_commands.gd`). A file that is wrong is named in
the listing, not fatal.

- **Sidebar.** `sidebar_commands.gd` reads `joinery command list --json` when
  the sidebar opens and again when a file in the folder changes (it looks every
  three seconds). Commands show under "This project" in the command menu.
  `/new-enemy Bat` or `/new-enemy name=Bat` with every required value goes
  straight to Journeyman; otherwise a small form (`command_card.gd`) opens with
  defaults filled in. `joinery command render` fills in the request either way,
  so the form never duplicates the typing rules; a wrong value comes back on
  the form's error line. The request is sent like a typed one and queues the
  same way.
- **Command line.** `joinery command list|render|run NAME --param k=v`. `run`
  goes through the headless run below and then runs the command's checks
  against the staged change and the agent's reply (exit 3 when one fails).
- **Shared.** `.joinery/*` is git-ignored in a project; `!.joinery/commands/`
  is added to `timeline.IGNORE_RULES` for new projects so commands travel with
  it. Projects whose `.gitignore` already has Joinery's block need the line by
  hand.

Response checks run only for `joinery command run`, not for the sidebar path:
there the user reviews the change themselves.

### 3. Headless run: `joinery run`

`joinery.headless` drives the real chat host (`joinery chat`) without the
editor: `joinery run --project P --request "…" [--agent A] [--model M]
[--effort E] [--json]`, or `--idle` to start the runs waiting for idle (ADR
0072) now. It answers what only an editor can do (a live scene edit, a look at
the viewport) with "no editor is open", so the agent stages saved files. With
`--json` every host event that matters is one line (connection, turn states,
steps, tool calls, checks, token use, notices, the change for review, timing),
then a `run_result` line with the transaction id, the reply, any parallel
tasks' changes and the change's cost from `joinery.metrics`.

It **never accepts.** When the turn ends it sends `when_quiet`; the host answers
`quiet` once nothing is at work (seen twice 250 ms apart). The runner then
closes the host's input. The host was started with `--keep-reviews`
(`ProjectGateway.keep_reviews`), so a change still in review on close is
marked with `FileTransaction.hold` (`held.json`) instead of discarded, and the
next chat host keeps it too (as it already did for a teammate's version, ADR
0071). It waits in the Review tab, or `joinery transactions accept|reject`.
Without the flag nothing changes: an editor's host still discards what's
pending when it closes.

The benchmark's `LiveDriver` is now the same driver with `accept=True` and no
`--keep-reviews`; `benchmark.Pin` is `headless.Pin`. Agent choice: `--agent`,
else the provider of the project's most recent chat.

## Consequences

- One memory file serves all four agents and the raw CLIs, with the same
  review as any other change. The prefix grows by the cleaned file only.
- Project commands need no code changes, and the CLI and the sidebar fill them
  in with the same Python.
- Evals, CI and the idle queue have one agent-neutral entry point.
- A headless run can't share a project with an open editor: one host holds
  the project lease (`ProjectLease`). The run fails with the lease's message.
- Existing projects get `JOURNEYMAN.md` and the stubs on their next
  `joinery setup`; the ignore rule for commands only on new projects.

## Not verified

- No live agent was run: the headless driver is tested against a stand-in host
  process and the host's `when_quiet`/`--keep-reviews` path with the fake
  bridge; `remember` and the memory prompt block with the fake crew. Whether
  each provider follows a JOURNEYMAN.md rule in practice was not measured.
- Whether Claude Code, Codex, Gemini CLI and opencode pick up the stubs when run
  by hand was not exercised; the file names come from their docs and sources.
- The sidebar pieces are checked by `tests/godot/project_commands_test.gd`,
  including a real `joinery command list|render`, and
  `tests/godot/run_project_commands_shots.py` captures the menu and the form
  (dark and light, 420 px) and runs `ui_checks.gd` on each. No one has used
  them in a real editor session yet.
