# Tool workspace

2026-11-07. Viewer and native desktop handoff; no asset authoring or engine patches.

- Python: 218 tests pass; ruff, strict mypy and `joinery check all` pass.
- GDScript: gdtoolkit 4.5.0 lint passes for the changed scripts and viewer test.

- Stock Godot 4.7.2: addon loads; real model and image previews, audio loading and
  playback cleanup, resource selection and asynchronous CLI refresh pass.
- Joinery Godot 4.7.2 build: the same viewer integration test passes, and editor
  startup completes with the addon enabled.
- Rendered viewer capture inspected with the testbed door selected.
- Desktop launch tests cover Blender, Krita, SuperCollider IDE, Audacity and a
  custom viewer, including unavailable apps, exact native-source selection and
  preserving provenance. Launches use mocked processes; every named desktop app
  has not been installed or opened during validation.
- Viewing does not run recipe verification; its status explicitly says so.
- Very short fork editor runs initially hit a background-initialization shutdown
  race. The completed check allows initialization to settle before exiting.

Run the viewer integration test after normal Godot editor import:

```sh
joinery workspace --project testbed > /tmp/joinery-workspace-index.json
JOINERY_WORKSPACE_INDEX=/tmp/joinery-workspace-index.json JOINERY_HOME="$PWD" \
  godot --headless --path testbed --script ../tests/godot/workspace_test.gd
```
