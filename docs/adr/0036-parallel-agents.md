# 0036. Parallel agents

Status: accepted (2026-11-11)

## Context

One chat ran one agent turn at a time (ADR 0035). A request with independent parts, such as
"a chair, a lamp and a rug", was made one part after another, and while Journeyman worked the
user could only queue the next message. The gateway held one `FileTransaction`, so two changes
could not be staged at once, and nothing said which agent was changing which file.

ADR 0035 already kept the turn controller free of globals so that a scheduler could run several.

## Decision

1. **Tasks.** Beside the chat's own request (the *lead*), Joinery runs parallel *tasks*. A task
   is one more turn run exactly like the lead's (`run_turn`): its own `TurnController`, its
   own provider session (the chat's provider, model and effort), its own staged change and its
   own review. `bridge/crew.py` schedules them. `[agents] parallel` in `joinery.toml` is the
   number of turns at once, the lead included (default 3, at most 6); `1` turns tasks off.
2. **Two ways to start one.** The user presses *Run alongside* in the composer while Journeyman
   works (the host message `task_start`). The lead agent calls `start_task(title, request,
   targets?)`, one tool on the `joinery-registry` proxy backed by the gateway RPC method
   `start_task`, the same for Codex, Claude Code, Gemini CLI and opencode; `task_status(id)`
   reports on it. A task's endpoint refuses `start_task`: tasks are one level deep. The agent
   instructions (`TASK_GUIDE`) say when to split (independent assets or scenes) and when not
   (dependent steps, the same files, small requests).
3. **Gateway lanes.** The chat's `ProjectGateway` owns the project: the lease, recovery and the
   app connections. Each task works through a *lane* (`ProjectGateway.lane`): the same project
   and connections, its own transaction, staged apps, turn and call lock. A lane closes when its
   task ends or its change is decided. The lead's gateway is unchanged, so one agent alone
   behaves exactly as before.
4. **Claims, never silent merges.** While more than one turn works, each claims the files its
   stage changes (`joinery/claims.py`). A call whose arguments name a file another task holds
   is refused before it runs; a call that wrote one anyway has that file put back. Both read
   "`<path>` is being changed by another task: `<title>`. Work on something else or wait."
   Claims last until the change is decided or dropped. `joinery.lock` is never claimed: it is
   shared bookkeeping, merged entry by entry. Folders are never claimed.
5. **Following the project.** Accepting a change bumps a counter. A stage that is behind it
   follows the project before its next write and at submit (`joinery/rebase.py`): files it did
   not touch are brought in, the lock is merged, a folder both made is the same folder. A file
   changed on both sides is a fixable `conflict` problem at submit: the stage gets the accepted
   version and the agent redoes its part on top. A change in review that is accepted after
   another one only has its lock and folders refreshed, so the files the user saw are exactly
   what is applied. Each accepted change is still one timeline commit.
6. **One live scene owner.** Only the lead changes the scene on screen live (`scene_route`).
   A task's `edit_scene` is refused, and so is a staged write to a scene that routes live.
7. **Shared workers.** Blender and Krita document operators take turns per worker image
   (`WorkerSlots`); the waiting task says so in its step. Other apps run side by side, each
   task in its own staged worker.
8. **Policy.** A task's provider is launched the same way as the lead's, so the same policy
   hook judges every call and reports a Rule Zero stop to the task's own endpoint. Any Rule
   Zero stop stops every agent of the chat, so no task routes around it.
9. **What the user sees.** Each task has a card above the footer: its title, the apps at
   work, its step, a timer and its own Stop. The header says "3 agents working" while two or
   more work. Each task's change is its own row in the Review tab, decided through the chat (the
   host runs the task, so the CLI never decides it behind its back). Stop on the lead's request
   offers, in one line, to stop the tasks too. A finished task leaves one line in the
   transcript, and saved chats record each task's start and outcome (`type: task`).

## Consequences

- Independent parts of a request are made at the same time and reviewed one by one; accepting
  one never touches another's stage.
- Every task costs a provider session and its own context; a task's context use is in its
  status. Tasks are single turns, so they never need a session summary.
- A task's change is reviewed from its Review tab row (summary, apps, files); the full
  before/after panel remains the lead's.
- Claims are checked by path. A tool that writes a file its arguments don't name is caught
  after the call, so it may have done other work first.
- Two tasks adding or removing game features at once still race on the carried game manifest;
  feature changes stay with the lead.
