# 0035. Joinery owns the turn

Status: accepted (2026-11-11)

## Context

Before this decision the provider (Codex, Claude Code, Gemini CLI, opencode) ran the turn, and
Joinery reacted to whatever the provider left behind:

- The gateway opened a `FileTransaction` on the first mutating call and staged every later
  call into it.
- Joinery checked the change only once the provider reported `Done`, in `gateway.finish()`.
  It checked protected paths, missing `res://` references, the art gate, the asset lock, the
  dependency denylist, the art bible rules and the Godot import. If any check failed, the whole
  stage was thrown away and the user saw a warning. By then the agent had already said "it will
  open for review", and it never found out what failed.
- The before/after renders also ran in `finish()`, after the agent's last message. That took
  12–50 s in real projects (`latency.jsonl`), and the user waited for it.
- The rule against mixing live and staged edits was checked in three places, covered only the
  open scene and only the `godot` server. Codex's dynamic `joinery_edit_scene` took a
  separate path.
- Context use was reported (`Usage` events), but nothing acted on it.

## Decision

Joinery runs every turn the same way, whichever provider runs it. The providers' CLIs remain
the model runtime and keep the user's subscription.

1. **One change per request.** A turn owns at most one `FileTransaction` plus that turn's live
   edits. `joinery/turn.py` holds a small turn controller in the shared core:
   `idle → working → submitting → in_review → decided → idle`, plus `compacting`. Every
   mutation path calls `admit_mutation()`: the gateway's tool calls, `edit_scene` over the RPC,
   and Codex's dynamic scene tool. Once the change is in review, nothing more can change.
2. **Scene routing in the core.** `scene_route()` decides how a scene is changed. A scene that
   is open with unsaved edits, or that has no saved file yet, is edited live. Every other
   scene, including an open saved scene, is a staged file. The staged Godot write path, the
   live-edit admission and the submit check (`live_conflict`) all use it.
3. **`submit_for_review` is the agent's hand-off.** It is one MCP tool on the
   `joinery-registry` proxy, backed by the gateway RPC method `submit`, the same for all four
   providers. It runs the checks (`joinery/submission.py`) and returns a structured result:
   `status` (`in_review`, `failed`, `needs_user` or `nothing_to_review`), `attempts_left`,
   `final` and `problems`. A turn gets 3 attempts. Protected paths and live conflicts are
   final at once; the art gate needs the user. A final failure drops the stage.
4. **The review appears on a successful submit.** `file_preview` is emitted then, carrying the
   agent's one-line summary and `media_pending`. Renders run in the background and arrive as
   `review_media`; a failed render marks its item with `error`, and the review stands. Accept
   stops a render still running before it moves the stage. A live edit's Accept and Discard
   wait for `in_review`. If the turn ends with an unsubmitted change, Joinery submits it on the
   agent's behalf. If that fails with problems the agent can fix, Joinery asks the agent once
   more in the same turn. If the change still fails, Joinery posts the outcome as its own
   `notice`, so the agent's last words never stand unbacked.
5. **Context summaries.** The controller tracks `Usage`. At 80 % of a known context window,
   after the turn, Joinery runs a read-only summary turn: mutations and submit are refused
   while the controller is `compacting`. Joinery adds the facts it recorded itself (accepted,
   discarded and open changes, the last 10 requests), saves the summary (`SummaryStore`: the open saved chat, or
   `.joinery/summaries/` when no chat can be saved) and starts a fresh provider session (`new_session()` on each
   adapter). The next prompt carries the summary as `Prompt.primer`. The user sees one quiet
   line. If the summary turn fails, the facts Joinery recorded stand alone.

## What stays with the providers

Model choice, effort, authentication and billing; the agent loop and reasoning; tool selection
inside the turn; streaming text; image input; each provider's transport. Provider-native
compaction stays available as a backstop. Where a provider reports no context window, Joinery
never compacts.

## Consequences

- Agents get check failures while they can still act on them.
- The review shows up as soon as the checks pass; renders no longer block it.
- The user can accept while the agent is still writing its last sentence. This is safe
  because nothing can change after `in_review`.
- Saved scenes are no longer edited live. Small tweaks to an open saved scene are slower, but
  every saved-scene change is one reviewable transaction. The routing is one function, so the
  rule can be changed in one place.
- `review_preparing` is gone. New host→addon events: `turn_state`, `review_media`, `notice`.
- Staged apps are closed for each submit, so a failed submit restarts them if the agent keeps
  working. This costs seconds, but no app writes while the change is checked.
- The controller holds no globals: one controller drives one agent's turn with one gateway, so
  a later scheduler can run several. Review, debug and playtest agents are not part of this
  decision.
- gdlint is not in the offline worker image, so "lint" at submit means the Godot import and
  parse check. GUT tests run only where a project already has GUT, after the import, and are
  bounded to 180 s.
