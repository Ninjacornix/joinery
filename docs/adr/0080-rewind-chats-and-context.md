# 0080. Rewinding chats, and keeping the context an agent works from

Status: accepted (2026-11-12)

## Context

Four gaps in how a chat carries its context, all seen in other agent hosts:

- A request that went the wrong way could only be followed by more requests.
  Gemini's `/restore`, Roo's checkpoints and opencode's snapshots let the user
  go back to an earlier message, or fork the conversation there.
- A session summary could cut the facts that matter most: what changed, the
  open to-do, the console errors still open, and the user's own answers.
- The project map is the same for every turn, so it does not point the agent
  at the files around what the user selected.
- A provider can stop mid-request on its usage or quota limit ("You've hit
  your usage limit", HTTP 429, `RESOURCE_EXHAUSTED`). The turn failed, the
  staged change went with it, and the user had to switch agents and ask again.

## Decision

### Rewind and branch

1. Each request card in the sidebar has a quiet menu (`chat_rewind.gd`).
   **Edit and resend** asks first and says which turns leave the chat. Files
   are not changed. **Branch** opens a new chat with the history up to that
   turn.
2. `joinery chats rewind` (`joinery.chats`) drops the later turns from the
   saved chat and records what they changed. The host starts a fresh provider
   session from a summary. Branch copies the history into a new chat that also
   continues from a summary. Neither touches the project.
3. Undoing the later changes is optional. It is one ordinary reviewed change
   (`joinery.undo_changes`), built from each change's restore data, that puts
   their files back as they were. The Timeline keeps the later changes,
   followed by this one. A change too old to have restore data, or a file
   changed since, is refused with words pointing at the Timeline tab.

### Pinned facts through compaction

4. A summary has a pinned block that is never cut
   (`joinery.session_summary.pinned`): the change outcomes, the open to-do,
   the open console errors, and the user's answers. Answers carry over from
   one summary to the next.

### Files near the selection

5. Each turn lists the files near the selection and the open scene
   (`joinery.project_map`). This is PageRank over references in both
   directions, restarting only at the selected files and the open scene. The
   line goes after the selection (`bridge.provider_process.nearby`), so the
   stable map above it keeps its cached bytes.

### Another agent on a quota stop

6. **Recognising the stop.** `joinery.fallback.QUOTA` holds the quota words,
   and `joinery.connection` checks a test message against the same words
   (plus billing). `bridge.turn_runner` keeps the provider's words in
   `TurnController.quota` when a warning or refusal says so, or when the
   provider fails outright with them.
7. **An offer, not a switch.** The chat host
   (`bridge.provider_switch.ProviderSwitch`) lists the other agents whose own
   sign-in is in place: supported, installed and connected
   (`joinery.connection.status`). Joinery brokers no credentials. The sidebar
   shows one action, "Continue with <agent>". The staged change waits,
   untouched, until the user takes it, presses Stop, or ten minutes pass. With
   no other agent signed in, the transcript says so and the request ends as
   before.
8. **Opt-in automatic switch.** `[agents] fallback_provider = "<agent id>"`
   in `joinery.toml` switches without asking when that agent is signed in. The
   transcript says it is the project's fallback agent.
9. **What the other agent starts from.** It gets its own provider session,
   bound to the same project gateway: the same stage, review and policy hook.
   Its first prompt carries Joinery's own record of the chat (the latest
   summary, the recorded outcomes and the pinned facts), the user's request,
   and the staged files, each marked new, changed or removed. The stopped
   provider is not asked for a summary. Token totals restart, because they
   are per provider session.
10. **Scope.** The other agent serves the rest of that request only,
    including the one continuation Joinery allows after a failed auto-submit.
    If it also runs out of quota, the next signed-in agent is offered. When
    the request ends, its bridges close, and the next request goes to the
    agent chosen in the sidebar. Parallel tasks, reviewers and other role
    agents are not switched.

## Consequences

- Going back is cheap and never rewrites history. The project changes only
  through a change the user reviews.
- Summaries can lose the agent's prose, but never the outcomes or the user's
  answers.
- The map stays cacheable, and each turn still points the agent at the
  relevant files.
- A quota stop no longer throws away work in progress, and the user still
  chooses the provider. The default is to ask.
- Enforcement is unchanged. Every agent runs through the same `joinery hook`
  gate and gateway (ADR 0006).
- Quota detection is word-based and may need new phrases as providers change
  their messages. A missed phrase only means the old behaviour: the request
  fails.
