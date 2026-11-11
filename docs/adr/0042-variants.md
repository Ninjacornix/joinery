# 0042. Variants side by side

Status: accepted (2026-11-11)

## Context

"Make a door" has many right answers. Until now the user saw one, discarded it, described what
was wrong and waited again. Comparing is faster than describing: several versions next to each
other answer "which one?" at a glance, and the words for "that one, but with the other's colour"
come easily once both are on screen. Parallel tasks (ADR 0036) already run several agents on one
project, each in its own staged change with its own review, but their claims make two tasks on
the same files impossible, which is exactly what versions of one request are.

## Decision

1. **Ways to start.** `/variants N <request>` (N from 2 to 4, 3 by default; `| direction | …`
   after the request gives the directions), "Try 3 ways" in the composer's options menu (the
   typed message, three ways), and "Try other ways" on a change waiting for review (two more
   ways beside that change, which stays as the first, "As made"). The lead agent offers it in
   one line when the user is unsure how something should look, sound or play, and calls
   `start_variants(title, request, directions, targets)` only when the user asked for options
   or accepted the offer. Workers, the reviewer, the debugger and the playtester can't
   (`joinery.roles.LEAD_ONLY`). The same tool and gate serve every agent.
2. **A variant group** (`bridge/variants.py`). Joinery starts one parallel task per way on the
   same request and targets, each told its own short direction and the others' ("Yours is way
   2 of 3: slim and clean"). The directions are the lead's or the user's; otherwise Joinery
   picks them by what the request is about (a look, a sound, music, code, a scene). Each way
   stages its own change in its own lane, is checked by Joinery and read by the reviewer like
   any change, and never edits the scene on screen live: the scene on screen is staged as a
   file instead (a scene with unsaved edits stays out of reach).
3. **Shared claims.** Tasks of one group are alternatives, not rivals: they may claim the same
   files (`PathClaims.join`, `sharers`), since at most one is ever accepted. Against every other
   task their claims are exclusive as before. With "Try other ways" the chat's own change joins
   the group for as long as it lasts.
4. **The limit.** Ways count against `[agents] parallel` like any task; the chat's own request
   counts only while its turn runs. Ways over the limit wait ("Waiting for a free agent") and
   start, oldest first, as turns free up (`Crew.add`, `Crew.pump`).
5. **One look, one click.** The ways show as one strip (`variant_strip.gd`, `variant_way.gd`) in
   the sidebar, instead of a task card each, and above a way's full review in the Review tab:
   each way's render or texture, a play button for a sound, the first changed lines of text,
   its number and direction, one line of facts, and Use this. A picture opens that way's full
   before/after. Use this accepts that way (one Timeline commit) and discards or stops every
   other way at once; if the accept fails nothing else is touched and the strip comes back.
   Accepting a way from its own row in the Review tab is the same pick. Mix takes a note
   ("door shape from 2, colour from 3") and starts one more way told the note and what each way
   made (at most six ways). Discard all is one click. The transcript then says, in one line,
   what was kept.
6. **Saved chats** record the `/variants` request, each way's task with its group and direction,
   and the group's start and outcome (`type: variants`, with the pick). A reopened chat shows the
   outcome line; a fresh session's summary says which way the user kept.

## Consequences

- A request tried three ways costs three agent sessions and three reviews; the strip makes the
  extra choice cheap to take, and Discard all makes it cheap to drop.
- A mixed way can't read the other ways' stages; it works from their directions and summaries,
  so "colour from 3" is the agent's reading of way 3, not a copy of its file.
- Ways of the scene on screen are staged scene files: the editor reloads the scene once one is
  kept, as with any accepted scene change, rather than previewing each live.
