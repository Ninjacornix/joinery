# Blueprints and features

A new project used to be an empty Godot project. Users asked for something that
plays from the first minute and that they can then reshape, and for common
building blocks (online play, controller support, saving, translations, Steam)
without wiring them by hand into every game.

**Blueprints are small, playable, code-only games.** `blueprints/<id>/` holds a
`blueprint.toml` and an `overlay/` copied into the project: scenes and typed
GDScript under `game/`, play tests under `tests/game/`. Visuals are greybox made
in code (primitive meshes, CSG, `Polygon2D`, `StyleBoxFlat`, flat materials);
blueprints are silent. No image, model, font or audio file may ship in
`blueprints/**` or `features/**`, and the validator enforces the list. Real art
and sound come later from the user's tools, through the bible and the lock
gate, like any other asset. Every blueprint sits on the shared game core
(`blueprints/_core`: settings, events, menus, pause, sessions, a play-test
harness under `core/` and `tests/support/`).

**Features add one capability each.** `features/<id>/` writes only
`features/<id>/` and `tests/features/<id>/` (plus `locale/` for translations),
declares `requires`, `conflicts`, `enhances` and which blueprints it supports,
and may carry per-blueprint adapters and export preset fragments. Anything a
feature can use but never ships itself (the GodotSteam extension) is listed
under `[[external]]`; such code reaches a game only through its own approved,
pinned and hash-checked step (ADR 0027), never through the overlay.

**One merge language for project.godot.** Core, blueprints and features merge
through the same tables: `[project.settings]`, `[autoload]`, `[input.<action>]`
and `[append]`. When a feature's base table and its adapter both append to one
key, the manifest records the union of their values (and whether the first
merge created the key), so removing the feature takes all of them back.
A blueprint's `[hooks]` become `game/hooks/level_scene`, `player_scene` and
`spawnable_scenes`, which the engine writes and no unit may set. Blueprints use one action vocabulary (`move_left`, `jump`,
`interact`, …) so the controller feature binds pads generically. `ui_*` actions
are left to Godot, because writing one would replace its built-in bindings.
`joinery/godot_config.py` edits Godot's ConfigFile text the way Godot writes it
(sections sorted, blank-line layout, the full `Object(InputEvent…)` field list),
keeps every untouched byte, and is checked against stock Godot: an input map it
writes loads into `InputMap` with the right codes, and Godot re-saves it with
identical text.

**Applying is a reviewed, reversible file edit.** `joinery/blueprints.py`
resolves the choice (requires closed and reported, conflicts and unsupported
blueprints refused), plans every write without touching the project, and then
applies it. `.joinery/game-manifest.json` records what each unit wrote: file
hashes, settings with their previous values, autoloads, input events, appended
values and export presets. That record gives three guarantees:

- applying again changes no byte;
- a file the user made or edited is never overwritten: an unknown file in the
  way refuses the apply before anything is written, and an edited file the
  unit has not changed since is simply kept;
- removing a feature deletes only its unmodified files (with `.uid` and
  `.import` sidecars and empty folders), restores the settings it changed when
  they still hold its value, drops its input events and presets (renumbering
  the rest), and leaves `project.godot` byte-identical to before it was added.

A blueprint is never swapped or removed; starting over is a new project.
`joinery.toml [game]` records the blueprint, the features and their versions
by a text edit that keeps every other table, and uses no key named `enabled`,
so tool setup and the game record never touch each other. `joinery.toml` is
protected from model tools, so adding and removing features are user actions;
Journeyman may only suggest them.

**Transactions.** `apply`, `add_feature` and `remove_feature` take `root=`, the
transaction stage to write into. A stage is copied without `.joinery/`, so the
manifest is read from the live project when the stage has none, and written to
the stage; the transaction must carry `.joinery/game-manifest.json` over on
accept.

**Card screenshots are made by Godot, from the blueprint.** The new-project
cards show `blueprints/<id>/capture.png`. `release/capture_blueprints.py` makes
a temporary project from the blueprint and its default features, runs its main
scene with `--write-movie --fixed-fps 30 --quit-after N` (in a window, or under
`xvfb-run` on Linux), keeps the last frame and shrinks it with Godot's
`Image.resize`. Nothing draws, generates or upscales it. `capture.toml` beside
it records `origin = "tool-created"`, `tool = "godot"`, the engine version, the
exact command, the frame index and the picture's sha256. Both files sit outside
the overlay, so they never reach a game; they are the only files the validator
accepts there, and only with a matching hash. `blueprints --json` gives each
card its `capture` path (or null).

**Restarts go through the session.** `Game.restart_level()` first asks the
session (online play's `NetSession.restart_level()`), so a Retry button on any
player restarts the level for everyone; offline it reloads the level as before.

**The catalogue is checked as a whole.** `validate_catalogue()` checks
schemas, write roots, banned file types, `.gd.uid` sidecars and uid uniqueness,
autoload and setting clashes, the action vocabulary, requires cycles, catalog
tool ids, bible presets, shipped hook scenes, card captures, and every blueprint with every
subset of its features (192 combinations for six blueprints and five
features) for file, setting and preset clashes. Tests boot each blueprint in
Godot alone, with each feature, and with all of them; CI runs them in the built
engine on every push and boots every combination nightly.
