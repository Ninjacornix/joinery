# 0071. Collaboration over git

Status: accepted (2026-11-12)

## Context

Every accepted change is already a git commit with the user's own identity (the Timeline,
`joinery/timeline.py`), and every project keeps art and audio in Git LFS. Teams want to work
on one game together. A sync server would mean accounts, hosting and Joinery holding
credentials; the user chose collaboration over git only. What plain git does not give a
Joinery team: a Pull that respects changes waiting for review, a safe answer when two people
changed the same model or painting, a warning before an agent edits a file a teammate just
changed, a way to say "I'm on door.blend this afternoon", and a guarantee that a teammate's
change to the art bible or brief doesn't silently direct this user's agents.

## Decision

1. **One remote, the user's own git login.** `joinery team connect <remote>` sets `origin`
   (starting the Timeline if the project has none), installs LFS hooks locally, adds the LFS
   rules and moves already-committed binaries that match them into LFS (their current
   version; history is not rewritten), and asks the remote once. `joinery team status`
   reports the remote, commits to pull and push, and the teammates seen in the history.
   `joinery team clone` makes a teammate's copy. Joinery never stores, prompts for or passes
   on credentials: git's ssh keys and credential helpers do that, with prompts disabled.
2. **Pull and Push in the Timeline** (and `joinery team pull|push`). Pushing waits when a
   teammate pushed first ("Pull, then push") and while a teammate's version waits in Review.
   A pull fast-forwards when it can, otherwise merges with a commit by the user ("Bring in
   Ana's changes"); the teammates' own commits keep their author, so the Timeline names them
   and marks what the last pull brought. A pull waits while a chat host is still making a
   change, and refuses when the user's own unrecorded edits overlap the incoming files.
3. **Changes waiting for review are never touched.** They live in `.joinery/transactions`,
   outside git. After a pull each one follows the new state the way it follows a parallel
   task's accepted change (`rebase.refresh_review`); one whose files a teammate also changed
   is named in the pull's result, and accepting it is refused as before. The chat host
   reloads its own pending change from its journal before accepting, since the pull runs in
   another process.
4. **Binary files are never merged.** For a model, painting, image or sound both sides
   changed (by suffix, LFS attribute or NUL bytes), the merge keeps the user's file, and the
   teammate's version is staged as an ordinary reviewed change ("Ana's door.png or yours"),
   with `team.json` beside its journal. Review shows "Use Ana's" / "Keep mine" and, opened,
   pictures of both: the image itself, a Krita file's merged image, a .blend's saved
   preview; sizes for sounds. Text files merge as git merges them; where both changed the
   same lines the user's lines stay and the teammate's lines wait in Review the same way.
   `joinery.lock` is merged entry by entry. These reviews survive a new chat session.
5. **Teammates' recent changes are warnings; claims are respected.** On fetch and pull the
   files teammates changed in the last three days (on the pulled history or on any fetched
   branch) are cached. Before a write tool call the gateway says "Ana changed
   art/door.blend 10 min ago" once per turn, to the agent (prepended to the result) and to
   the user (progress), adding "Pull before changing it" when that change isn't pulled.
   `joinery team claim --path … --hours N` writes a small `claims.json` to the
   `joinery-claims` branch of the shared remote (no checkout, plumbing commits, retried on
   a race); claims expire on their own. Agents refuse to edit a file a teammate claimed,
   with a plain tool error naming who and until when. The user's own claims never stop them.
6. **A teammate's change to the bible or brief needs this user's approval.** A pull that
   changes `art/bible.md` or `design/brief.md` with a teammate as author records it in
   `.joinery/team/documents.json` (local, ignored). Until the user approves, the art gate
   stays closed (`art_approval.approved`), with a message naming who changed what. The bible
   is approved where its rules are shown (/art), which clears it; the brief has its own row
   in Review with Approve (`joinery team approve`). A teammate's committed approval never
   counts for this user: the local approval file is set aside during the pull and restored
   only while it still matches the bible.

## Consequences

- Nothing beyond git is needed to work together, and any git host works, including a
  folder on a shared drive. Joinery stays agent-agnostic: the warnings and refusals live in
  the shared gateway, not in one agent's hooks.
- A pull can create a review the user must decide before pushing; that is the price of never
  merging art silently.
- Claims are advisory between Joinery agents; plain git users can still push to a claimed
  file, and an agent still sees the recent-change warning afterwards.
- Merge commits appear in the Timeline under the user's name. History is never rewritten.
- Not done: rebasing local commits instead of merging, partial picks inside one binary file,
  and pictures for .blend files saved without a preview or compressed with zstd.
