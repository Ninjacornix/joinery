# Reviewed scene edits

2026-11-08. Reproduced the user's failed “create box 1 by 1 matre” request:
the previous developer instructions and transport only supported inspection.

- 297 Python tests pass, including typed-operation rejection cases and a fake
  Codex dynamic-tool round trip carrying the original scene/target context.
- Ruff and strict mypy (36 source files) pass. `joinery check all` passes.
- GDScript lint and stock Godot runtime scene-edit tests pass. Tests assert exact
  1 m dimensions, owner assignment, ground placement, reject restoring the scene,
  transform restoration, stale context and no partial application of invalid batches.
- Native full-editor tests pass for existing and completely empty scenes:
  live preview, Accept/Reject buttons, geometry ownership, accept with undo/redo,
  selected-object translation and scaling, exact transform undo, cancelled
  requests, a replacement object at the same path, save-before-accept rollback,
  unchanged scene-file bytes through previews/undo, and accepted operation records.
- The exact typo-containing request was submitted through the actual Codex
  account and sidebar in an isolated empty editor. Codex invoked the registered
  tool and a native 1 × 1 × 1 metre BoxMesh appeared. Captured image:
  `/tmp/joinery-live-box-preview.png`. The live test rejects this first preview,
  verifies the editor is empty again, then verifies acceptance and undo/redo.
- The earlier native layout/connection check also passes with these changes.

Test failures caught: EditorUndoRedoManager's internal undo/redo methods are not
script-bound, so the native harness must use its scene's UndoRedo history. The
harness now treats script errors on stderr as failures even when Godot exits 0.
A missing project policy file initially escaped dynamic-tool error framing; it
now returns a failed tool result rather than leaving a model call waiting.

Scope: primitive scene scaffolding and selected Node3D transforms. Arbitrary
files, finished art assets, Blender modelling, and cross-tool transactions remain
unimplemented. No existing game assets are modified by the tests.

The installed addon in `/Users/ninjacornix/test-proj` was backed up before updating.
The original failed request is posted visibly on the updated editor launch, with
no automatic acceptance or scene save.
