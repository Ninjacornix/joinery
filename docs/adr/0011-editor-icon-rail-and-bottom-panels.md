# 0011: Icon rail, bottom utility panels and creator views

Status: accepted, 2026-11-07

## Decision

Use a compact editor shell: a left icon rail, active view in the center,
Journeyman on the right, engine utility panels in the bottom tab row, and
centered run controls in the title bar. This follows the requested Cursor-like
layout while keeping the existing Godot editors and their shortcuts.

The shell restructure is a Joinery-only engine patch. Main-screen buttons use a
vertical BoxContainer, identify their plugins independently of their visible
text, and retain tooltips and accessibility names. The rail sits outside the
dock split containers so it does not change saved dock-width accounting.

The addon appends icons for the project's creation tools. Clicking one selects
the shared Tools workspace and filters it by recorded creator. Compatible
desktop applications remain in Open in; they do not count as creators. Imported
and unassigned assets are separate categories in the stock-addon selector.
In stock Godot the workspace retains its internal creator selector; in the fork
the external icon rail replaces it. Asset names omit extensions and replace
underscores with spaces. Matching names are distinguished by media kind.

Scene, FileSystem, Import, History, Inspector, Signals and Groups default to the
bottom. Existing projects migrate their utility dock positions once, retaining
other dock placement, closed-state and floating-window choices. The layout
version is saved with the editor configuration; later manual arrangements are
not reset on launch.

The addon independently hides the scene-tab strip and 3D viewport toolbar by
default. `/toolbar`, the rail footer button and the editor Tools menu toggle
them together. This preference is persisted and does not depend on Focus mode.
Focus mode preserves Journeyman and navigation, and no longer guesses which
toolbar rows to hide.

## Validation

`tests/godot/editor_layout_test.gd` runs as an editor plugin in a temporary
testbed copy. It checks migrated dock parents, icon-only vertical selectors,
hidden/toggleable viewport controls, rail-to-Blender navigation and creator-only
filtering, then captures both the 3D editor and the tool workspace. The ordinary
viewer test additionally checks readable-name search and creator filtering.
