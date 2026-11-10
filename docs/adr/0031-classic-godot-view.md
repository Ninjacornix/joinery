# Classic Godot view

Joinery's editor layout (ADR 0005) moves Godot's docks to the bottom, puts a
tool rail in the left column, trims the run bar and gives the Journeyman
sidebar the right column. People who already know Godot sometimes want
Godot's own arrangement back, without leaving Joinery. The fork therefore has
one switch between the two layouts (patch `0019`, Joinery-only, not meant for
upstream).

## One setting, read at startup

`interface/joinery/editor_layout` is an editor setting (per user, across
projects): `Joinery` (0, the default) or `Classic Godot` (1).
`EditorSettings::is_joinery_layout()` reads it once per session and every
layout change in the fork asks it. In Classic Godot:

- Scene, FileSystem, Import, History, Inspector, Signals and Groups default to
  their stock slots, and the default layout is Godot's own.
- The 2D, 3D, Script, Game, Asset Store and Tools buttons sit centred in the
  title bar with their names; there is no navigation column, so no tool rail.
- The run bar shows all its buttons (run current and custom scene, Movie
  Maker) in its stock place.
- The bottom panel keeps its overflow arrows, dock menu and status strip, and
  its docks start open as in stock Godot.
- The right column's docks have their usual docking menu.

The theme is not part of the layout: the Joinery presets, the accent and the
spacing stay as the user chose them.

The layout is built while the editor starts (dock slots are set in the docks'
constructors, the title bar in `EditorNode`'s), so a change applies after a
restart. Rebuilding the title bar, moving every dock and re-creating the
bottom panel live would touch far more of the editor than this switch is
worth, and a half-applied layout is worse than an honest restart. The setting
is restart-if-changed, so Editor Settings offers to restart too.

## Where the switch lives

- **Editor > Switch to classic Godot view** (in Classic Godot: **Switch to
  Joinery view**), also in the command palette. It asks first: Restart, On
  next start, or Cancel. Restart goes through Godot's own reload, which asks
  about unsaved scenes.
- **Journeyman settings > Appearance > Layout** (Joinery or Classic
  Godot). Choosing the other one saves it and shows "Applies after restart"
  with a Restart button; choosing back clears the note.
- **Editor Settings > Interface > Joinery > Editor Layout**, the setting itself.

## Each layout keeps its own docks

`editor_layout.cfg` stores the docks in `[docks]` as Godot always has, plus a
copy for the running layout in `[docks_joinery]` or `[docks_classic]`, and
`joinery_view` names the layout that saved it. When the editor starts in the
other layout, it loads that layout's copy, or its default the first time.
Switching back therefore finds the docks as they were left. A layout saved by
stock Godot (no `joinery_view`) is used as is in Classic Godot and migrated
once into Joinery's, as before.

## The addon

The addon reads the running layout from the editor: the fork's navigation
column exists only in Joinery's layout. Without it (Classic Godot, or stock
Godot, which is always classic):

- the Journeyman sidebar is an ordinary dock below the Inspector, with its
  tab, so it can be moved or closed (Editor > Editor Docks brings it back);
- the viewport toolbar and scene tabs are always shown, and the toolbar menu
  item is not added;
- a saved Focus mode is not re-applied at startup. Hide or show panels still
  works on request.

On stock Godot the settings row reads Classic Godot, cannot be changed, and
says "Joinery's layout needs the Joinery editor."

## Checked by

`tests/godot/run_classic_view_test.py` opens one project three times per
theme (Joinery, Classic Godot, Joinery again), checks the dock slots, title
bar, run bar, Editor menu item, sidebar tab and settings row, and that each
run saved its own layout, and captures the editor, the settings page and the
switch dialog to `out/classic-view`. The visual suite runs the same pass as its
`classic-view` harness, and any `ui_checks` finding fails it. In Classic Godot
the sidebar's minimum width drops from 24 to 19 times the font size, so the
shared right column fits the editor in a 1470-point-wide window. `tests/test_fork_patches.py` checks that
one patch adds the setting and guards the dock moves.
