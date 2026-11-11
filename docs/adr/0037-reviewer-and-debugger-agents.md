# 0037. Reviewer and debugger agents

Status: accepted (2026-11-11)

## Context

Joinery's checks at submit (ADR 0035) are mechanical: protected files, missing files, asset
provenance, dependencies, art bible rules, the Godot import and the project's tests. They can't
tell that a lamp is three times the size the art bible asks for, that a script change breaks the
request's intent, or that a texture is off-palette in a way no rule measures. The user was the
only reader of each change.

When the game threw a script error, the user had to copy it into the chat and hope the agent
looked in the right place. Nothing read the Output dock or the game's log, and nothing tried
the game again to see the error happen.

ADR 0036 runs several agents as tasks, all with the same powers. Agents that only read had no
place in that model other than the inspection specialists, which were not a role the policy
core knew.

## Decision

1. **Roles in the shared core.** Every agent has one role (`joinery/roles.py`): `lead` (the
   chat's own request), `worker` (a parallel task or a debugger's fix), `reviewer` and
   `debugger`. Each role has its own turn (chat, task, review, diagnosis), its own instructions
   (`bridge/instructions.py`) and its own tool allowlist. The role reaches the provider's
   policy hook as `JOINERY_AGENT_ROLE` and the gateway endpoint as `GatewayRPC(role=…)`; both
   judge calls against the same tables, the same for Codex, Claude Code, Gemini CLI and
   opencode (`Bridge(agent, role=…)` sets it on each adapter). Rule Zero is judged first for
   every role.
   - Reviewers and debuggers only read: the registry's read tools, `call_tool` on audited
     reads (`read_access.py`), shell commands that only read (`cat`, `sed -n …p`, `rg`, `git
     log` and the like; no redirection or substitution), no web, no submit, no live edits, no
     tasks, no delegation. The OS sandbox that keeps every provider from writing the project
     stays underneath.
   - Each reports through its own tool: `report_review` (reviewer), `report_diagnosis` and
     `reproduce_error` (debugger). No other role may call them.
   - Workers can't start tasks. No role can approve, accept or discard a change, or approve or
     edit the art bible: approval is not in the model-facing API, and the read-only roles can't
     write at all.
2. **The reviewer reads every change before the user.** When a submit passes the checks, the
   turn controller (`joinery/turn.py`) runs its reviewer (`bridge/reviewer.py`) on the staged
   change, while the turn is still `submitting`. It is a one-shot read-only turn of the same
   provider and model as the author, unless `[agents] reviewer_model` names another. It reads
   the request, the author's summary, the changed files and their text diff, the art bible and
   game brief from the stage, the checks' results and the staged images (attached as pictures).
   It reports notes `{severity: info|warn|block, text, file?}` (`joinery/peer_review.py`).
   - A `block` goes back to the author as a fixable `reviewer` problem, with the notes it asked
     for last time so the next round checks them. A send-back costs no submit attempt. After
     `[agents] review_rounds` send-backs (default 2) the review opens with the notes left.
   - The reviewer never drops a change: when the author stops without fixing a block, Joinery's
     own submit opens it with the notes.
   - It is time-boxed (90 s). On a timeout, an error, or a reply with no findings the review
     opens without it and says so ("The reviewer ran out of time, so this opened without its
     notes.").
   - A stage unchanged since the last review keeps that review.
   - The sidebar's request card shows "Reviewing the change…" as its step; a task's card does
     the same for its change.
   - `file_preview` carries `review_notes`, `reviewer_status` and `reviewer` (`{status: reviewed|timeout|error|off|
     skipped, rounds, elapsed_s, message?, model?}`); the transaction journal keeps both, so
     `joinery transactions list` shows them.
   - `[agents] reviewer = false` turns it off (Settings → Project → Reviewer agent, or
     `joinery reviewer --set off`).
3. **The debugger finds the cause; a worker fixes it.** `/debug` in the sidebar, or **Debug**
   on the one-line offer that follows a new error in Godot's Output (`sidebar_debug.gd`), sends
   `debug_start` with the error line, the Output text around it and the tail of
   `user://logs/godot.log`. The crew (`bridge/crew.py`) opens one task card, "Debugging:
   <error>". Its debugger reads the errors, inspects scripts and scenes, and may run
   `reproduce_error`: the project, copied to a throwaway folder, runs headless in the offline
   worker (`--network none`) for a few seconds (`joinery/debugging.py`). It reports the cause
   and the fix. The same card then runs a worker with that fix as its request; the change goes
   through `submit_for_review`, the checks and the reviewer like any other, and the user
   reviews it in the Review tab. A cause with no project fix ends the card with the cause. One
   error is debugged at a time, even with `[agents] parallel = 1`.
4. **What the user sees.** The glanceable review (ADR 0040) owns the layout. The reviewer's
   data fills it: `file_preview` carries `review_notes` and `reviewer_status` (with the fuller
   `reviewer`), the worst note is the review's notes line and every note is in Details. A
   timed-out or failed reviewer joins the status ("Reviewer ran out of time"). A block note
   never disables Accept: the user decides.

## Consequences

- Every change costs one more provider turn, usually a short one, before the user sees it. A
  slow reviewer delays the review by at most its time box.
- The reviewer sees the staged images, but not the 3D before/after renders: those still run
  after the review opens (ADR 0035), so a model is judged by its diff, its source summary and
  the art bible. A live scene edit alone opens without a review.
- The reviewer is the same kind of model as the author unless the project names another, so it
  shares some of its blind spots; its notes inform the user, who still decides.
- The Output panel is read once a second while the editor runs; errors already there when the
  sidebar opens, and Joinery's own, are never offered.
- Read-only roles can't run anything that writes, so a debugger can only reproduce through
  `reproduce_error`, never by running the game in the editor.
