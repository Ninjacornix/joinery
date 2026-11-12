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

## Addendum: stage and rail (2026-11-11)

One job per row stacked five rows in a bottom panel that is wide and short: an animated
character came out a few dozen pixels tall, the media frame showed empty bands beside it, and
the title, facts, Details and actions each took a full-width row of height.

1. **Two parts, side by side** (`review_layout.gd`). The **stage**, about 70% on the leading
   side at the full height, is the change itself, edge to edge on its own background
   (`review_glance.gd`'s ground) with no card around it, as interactive as before (orbit,
   zoom, scrub, the split line). Its least height grows with the panel's width, so a wide
   bottom panel opens tall enough to judge the change. The **rail**, the rest on the trailing
   side (`review_rail.gd`), reads top to bottom: where the change sits among those waiting,
   the title (two lines at most), one line of facts, the checks as chips, a request's other
   ways, and at the foot the actions.
2. **One Before/After control on the stage** (`review_sides.gd`): a compact segmented pill in
   the stage's top trailing corner, Before | After | Both. Both appears only where both sides
   can be on screen at once (a split picture, two clips, two live models); model renders still
   crossfade one side at a time. A new or removed asset shows one quiet badge in that corner.
   While both sides show, a tiny label sits in the top corner of each so the halves are never
   ambiguous. The tags under each side are gone; clicking a clip still focuses it.
3. **The transport floats** over the foot of the stage on a small raised bar
   (`review_overlay.gd`) instead of taking a row. It is there when an animation opens, fades out
   when the pointer leaves the stage (never mid-scrub) and back in when it returns.
4. **The queue in the rail** (`review_queue.gd`): "2 of 5" with previous and next arrows beside
   All approvals, which replaces the Review tab's own back link. The queue is the Waiting for
   you order, counting the changes that open a full review (Journeyman's and each parallel
   task's). The next review crossfades in over the last one: a still of the old one fades out
   above it (`review_layout.gd`'s ghost).
5. **Checks as chips** (`review_summary.gd`): "Checks passed", "Reviewer checking…" while the
   reviewer runs, and one chip per kind of note ("1 warning", "2 notes", "1 blocker"); the worst
   note follows as one quiet line. Full texts stay in tooltips and Details.
6. **Actions anchored at the rail's foot**: Details, **Ask for changes** and (for a scene edit)
   Open in editor as quiet links on one line, then Discard and a large Accept that fills the
   rest of the rail's width. Ask for changes puts the focus in the chat's message box with the
   changed asset as its target (`review_flow.gd`); it shows only where the chat listens (the
   main request's review).
7. **Details are a drawer** (`review_drawer.gd`) that slides over the stage from the rail's side
   (where its link is), half the stage wide, eased out on the way in and 20% shorter, eased in,
   on the way out; Escape or its × closes it. With nothing to preview it is the whole stage and
   cannot be closed. Opening it moves neither the picture nor the actions.
8. **Variants in the rail** (`variant_strip.gd`'s dense mode): one way per line, a small square
   picture, its direction and Use this, with Mix… and Discard all under them.
9. **Narrow** (the sidebar's width, or a panel under 46 em): the rail stacks under the stage, the
   stage keeps at least 55% of the height, and the rail never gets less than its actions need;
   its middle scrolls instead.
10. **Keyboard, only once the review has the focus by a click**; it never takes the focus
    itself, so a review opening while the user types cannot be decided by a stray key. A
    accepts, D discards, B flips Before and After, Left and Right go to the previous or next
    change waiting, Escape closes Details. With a clicked animation, Space and Left/Right keep
    playing and stepping frames. The hints are
    in the tooltips. Enter, Backspace and Delete no longer decide (Enter on a focused button
    still presses it).

Consequences: the stage's pictures, clips and live views size themselves from the stage's width
rather than their own, so the stage can shrink as well as grow. Reduce motion makes the drawer,
the crossfade and the transport's fade instant. A rail crowded by many notes or ways scrolls in
its middle; the actions never scroll away. Ask for changes while a review waits queues the
message like any other (the composer says so); it does not discard the change.

## Addendum: a list of files, and pictures in their own shape (2026-11-12)

A model change with sixteen textures and its `.blend` came out as a filmstrip of small tiles
whose names only showed in tooltips, square textures sat in a wide frame between two dark
bands, and the Before/After pill lay over the top of tall pictures.

1. **A list of files beside the preview** (`review_files.gd`, laid out by `review_split.gd`)
   replaces the filmstrip for a change with several files. One row per file down the leading
   side of the stage: a small square thumbnail (the file's picture, else its kind's glyph or
   file type), the whole name (wrapped after its underscores, never cut short) and a quiet mark,
   New, Changed or Removed. The model or source comes first with its part views, clips and
   textures grouped under it (the only model, else the one whose name starts the texture's or
   that shares its folder; `review_items.gd`'s `grouped`), then the other files, text last. The
   shown row is filled with the accent tint; a long list scrolls and keeps it in view. Up and
   Down move through the files once the review has the focus; Left and Right still step through
   the changes waiting. The preview fills the rest; a single file keeps the whole stage.
2. **Narrow, the list folds above the preview** (below 36 em of stage): at most 30% of the
   stage's height and never less than one row, so the preview keeps its room and Accept and
   Discard stay on screen.
3. **No bands around a picture of another shape.** A picture with a background of its own (an
   even edge colour) fills the frame with it, as clips and model renders already did; a split
   pair on one background does too. A picture without one (a real texture) leaves the frame
   unfilled, so it sits on the panel in its own shape, square, wide or tall.
4. **The pill never covers the media.** When the media, fitted to the whole frame in its own
   shape, would reach under the pill, it gives up a strip at the top as tall as the pill and
   its margin (`review_sides.gd`'s `clearance`). The decision depends on the media's shape and
   the frame alone, so the strip does not flicker as the media shrinks into it; a wide picture
   that leaves room above it keeps the full height.

Consequences: a texture grouped under the wrong model is still one click away and its full path
is in the row's tooltip. A picture without an even background shows the panel around it rather
than a frame, so a texture with transparent pixels shows the panel through them.
