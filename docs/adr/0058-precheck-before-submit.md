# Precheck before submit

Accepted: 2026-11-12.

## Context

Every change reaches the user through `submit_for_review`. The submit runs
Joinery's deterministic checks (protected files, live/saved conflicts, missing
`res://` files, objects built from placeholder shapes (ADR 0052), the art-bible
gate and provenance lock, the dependency denylist, the art bible's measured
rules, Godot's import and the project's GUT tests), then the reviewer agent
(ADR 0037). A failure sends the change back to the agent, costing a submit
attempt (three per turn), a round trip and often a reviewer pass. The metrics
(ADR 0054) count these rounds but could not say which rules cause them.

The agent had no way to see the checks' verdict before submitting, and each
check answered in its own words: some as `"path: what"` lines, some as one long
sentence, the reviewer as free text. Work on self-correcting agents (Reflexion,
Shinn et al. 2023; Self-Refine, Madaan et al. 2023) finds that an agent
corrects itself best from specific, structured feedback it can act on before
it commits, not from a verdict after.

## Decision

- **`precheck` tool.** The registry offers `precheck` (read-only hint) to the
  lead and to workers. It runs `submission.validate`, the same function the
  submit runs, on the stage as it is, under the gateway's call lock, and
  returns what the submit would: `status` (`clean`, `problems`, `needs_user`,
  `nothing_to_check`, `error`), the changes, the problems, the checks that ran
  and were skipped, notes, and the attempts left. It never starts the
  reviewer, never counts as a submit attempt or a send-back, never changes the
  turn's state and never drops the stage, whatever it finds.
- **Read-only.** Unlike the submit, a precheck leaves the staged apps open,
  leaves a running game's inspector in place, puts no file back for another
  task and does not bring the stage up to date with changes accepted
  meanwhile. Those would change what the agent made, so a precheck says them
  as `notes` instead ("submit first brings your stage up to date…"). An art
  gate is reported (`needs_user`) but not raised; the submit raises it.
  Godot may fill the stage's import cache, as any staged Godot call does; a
  clean import there is remembered, so a submit of the unchanged stage skips
  its own import and a precheck costs the submit nothing.
- **One shape for every reason.** Each problem keeps its plain `message` (what
  the user may read) and adds `items`, each `{rule, file, node, measured,
  expected, fix}`: a stable rule id `<check>.<what>` (`art_bible.texture_size`,
  `provenance.no_lock_entry`, `references.missing_file`,
  `complex_models.placeholder_shapes`, `godot_import.parse_error`,
  `tests.failed`, `reviewer.art_bible`, …), the file, the node, object,
  material or part inside it, what was measured, what is expected, and a
  one-line fix hint. `joinery/reasons.py` turns each check's existing output
  into items; a check with nothing finer gets one generic item per file it
  names. The reviewer's `report_review` notes take an optional `rule` (request,
  art_bible, brief, broken, work_lost, other), so its objections count too.
  Rule ids never change once released: the metrics keep them.
- **Instructions.** The shared review guide every agent gets (Claude Code,
  Codex, Gemini CLI, opencode) says: when the change touched assets, scenes or
  code, call `precheck` once before submitting and fix every item first.
- **Metrics.** Each failed submit, reviewer send-back and precheck with
  findings adds a zero-length `findings` span with `joinery.findings.by`
  (`checks`, `reviewer`, `precheck`), `joinery.findings.final` and
  `joinery.findings.rules` (rule ids, one per item, comma-separated; never the
  messages). Prechecks are `precheck` spans, and the change counts them
  (`joinery.prechecks`). `joinery metrics --project P --rules [--last N]
  [--json]` lists each rule with how many items it sent back, how many were in
  failures that dropped the change, how many a precheck caught first, and in
  how many changes it showed up, most frequent first.

## Consequences

- A change that passes its precheck and is not edited after reaches the
  reviewer on its first submit; the fake-agent tests show one submit where
  there were two.
- The most common send-back rules can be read from any project's metrics and
  targeted in the agents' instructions or the tools.
- A precheck is not free: the art bible's measured rules, Godot's import and
  the tests run in the offline worker. The guide asks for one precheck, not
  one per call.
- A precheck can differ from the submit when another change was accepted
  meanwhile, when another task holds a file, or when the game is running from
  the stage; it says so in its notes.
- `Problem.as_dict()` carries `items`; consumers that compared whole problem
  dicts compare more keys now.
