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

## Addendum: one Before/After control, and a change you can handle (2026-11-11)

The panel showed Before and After twice (a switch in the header and a tag on each animation
clip), squeezed the title and the switch onto one row and Details onto the actions row, and only
let the user look at pictures of the change.

1. **The tags on the media are the one Before/After control** (`review_sides.gd`). Each sits on
   the side it names, so it says which side is which and switches to it at once: under each clip
   or live model, on each half of a split picture, together under a model's renders (one side at
   a time), over a sound's waveform. Clicking the side in focus again shows both alike where both
   are on screen. A change with no original has one plain tag, **New**, on the media; the header
   no longer has a switch or a New chip. A or B still flips.
2. **One job per row**: the title alone; the media with its tags; facts, checks and notes;
   **Details** as its own disclosure; then the actions, Discard and Accept on the trailing edge,
   never squeezed below their text. Details keep the small control height so the media keeps
   the room.
3. **Models are live 3D** (`review_model_view.gd`). A `.blend` item names the GLB/glTF its recipe
   exports on each side (`before_model`, `after_model`, from `review.plan`); the panel loads them
   at runtime with `GLTFDocument`/`GLTFState` on a worker thread, textures embedded in memory,
   so nothing is imported into the project or written to it. Each side gets a small
   `SubViewport` with its own world, a neutral key and fill light and a ground; both share one
   orbit framed on the two models' bounds together, so a size change shows. Drag orbits, the
   wheel or a pinch zooms, a double-click resets. The renders show first; the live views
   replace them only if the files load within about a second, and are freed when the item
   changes or the panel hides.
4. **Animations play under one transport** (`review_transport.gd`): play/pause and a scrub bar
   drive both clips from one clock, Left and Right step a frame. When the item names GLB files
   and actions (`before_action`, `after_action`), the models play the action live in 3D, posed
   from the same clock.
5. **Pictures zoom and pan** (`review_zoom.gd`): the wheel or a pinch zooms about the pointer,
   a drag pans once zoomed (the split line still drags by its grip), a double-click fits. Both
   sides draw from one view, so they stay lined up; zoomed-in pixel art is crisp. A new
   picture uses the same view without a line.
6. **A scene edit offers "Open in editor"** on the actions row: it selects the changed nodes in
   the open scene (`review_snapshot.gd`, set by `scene_edits.gd`) and shows the first in the
   Inspector. The snapshot stays the preview.
7. **No focus is taken until the media is clicked.** Hovering, the wheel and playback never move
   the keyboard focus; a click on the media gives the review its keys.

Consequences: a model whose recipe exports no GLB, or a slow or broken file, keeps the renders;
the live view shows the exported asset, so it can differ from Blender's Workbench render in
lighting. Live views cost two small viewports while the panel shows, none when it is hidden.
