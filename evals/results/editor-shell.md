# Editor shell

2026-11-07. Requested layout refinement from the user's screenshots and Cursor
reference; no game assets authored or modified.

- Built the native editor with the new shell; four exported engine patches.
- Python: 221 tests pass, including fork-patch guards. Ruff, strict mypy and
  `joinery check all` pass.
- Changed GDScript and the editor/viewer tests pass gdtoolkit 4.5.0 lint.
- Full editor test runs in a temporary copy of testbed, including an existing
  layout with Import, History and Inspector in their previous positions.
- Verified those utility docks move to the bottom, workspace selectors become
  a vertical icon rail, viewport toolbar/scene tabs start hidden and toggle,
  and clicking Blender selects only Blender-created assets.
- Rendered full-editor captures inspected for the 3D and Blender views.
- Stock Godot retains an internal creator selector and the same viewer.

The tool views open their external desktop applications; native application
window embedding and edit transactions remain outside this layout change.
