# 0040. A review you take in at a glance

Status: accepted (2026-11-11)

## Context

Every change waits for the user's Accept or Discard. The Review panel showed one preview with a
file picker, a counts caption ("1 model · 3 files") and Details that often opened by default, and
the chat card only pointed at the panel. Users decide many changes a session; nobody reads a
large revision screen each time. The question a review has to answer is "is this right?", and
most of the time one look at the change should be enough.

## Decision

1. **One view per kind, sized to the panel** (`review_media.gd`). Textures, part views and live
   scene edits are one picture split by a draggable line (`review_swipe.gd`): both sides share
   one frame, so the eye compares without moving. Models are rendered from two angles (the
   front three-quarter and the far side, `BLENDER_THUMBNAIL` writes `<name>-back.png`, the item
   carries `before_back` / `after_back`) and crossfade between sides, since each side is
   framed on its own. Sounds keep the compact A/B player with its waveform. Text shows only the
   changed lines (`review_diff.gd`). A live scene edit uses the editor viewport's frame before
   the edit and at review time (`review_snapshot.gd`); without both it points at the viewport.
2. **A filmstrip for several items**, in the order the renders are planned (the targeted part
   first), text last. It replaces the file picker.
3. **One line of facts, one quiet status, one line of notes** (`review_summary.gd`,
   `review_glance.gd`). Facts come from the agent first: `submit_for_review` takes up to three
   optional `facts` ("door 20% wider"), passed on as `file_preview.facts`. Then what the review
   measures itself (mean brightness, hue towards or away from orange, size, sound length,
   lines), then the counts. The checks are "Checks passed", with what ran in the tooltip and in
   Details (`file_preview.checks = {ran, skipped}`). A reviewer agent's `review_notes`
   ([{severity: info|warn|block, text, file?}]) and status, on the preview or a later event,
   show as one line, worst first.
4. **Accept and Discard always on screen**, with Details beside them as the one disclosure for
   files, diffs, checks and notes; closed unless there is nothing to preview or the user's
   preference opens it. With focus in the review, Enter accepts, Backspace or Delete discards,
   A or B flips. Accept and Discard shrink to their text on narrow panels instead of wrapping.
5. **The same layout everywhere.** The Review tab, the bottom panel and parallel tasks already
   share `review_panel.gd`. The chat card shows a tiny picture, the facts line and Discard /
   Accept (`sidebar_review_row.gd`) when the change is small: one asset at most, 40 changed
   lines at most, at most three scene nodes and no reviewer warning or block. Otherwise it
   offers Review, which opens the panel. Both decide through the panel, so the optimistic
   outcome, errors and rollback stay in one place.
6. **Motion only for state changes**: the split line slides when a side is chosen (a mask
   reveal), sides crossfade, notes fade in when they arrive. Dragging follows the pointer with
   no tween. Reduce motion turns all of it off.

## Consequences

- Most changes are decided from the chat or from one look at the panel; reading files is
  opt-in.
- A model review costs one more Workbench render per side inside the same Blender run.
- Measured facts are hints from pixels and lengths, not semantics; the agent's facts carry the
  meaning ("20% wider"). Two renders of a model are framed separately, so a size change shows in
  the facts, not in the pictures.
- The viewport snapshot is whatever the 3D viewport last drew. With another main screen in front
  (the Joinery workspace) that frame can be older than the edit; the viewport itself stays the
  live truth.
