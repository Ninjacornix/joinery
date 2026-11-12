# 0074. Tidy: unused content, found with evidence, removed as one reviewed change

Status: accepted (2026-11-12)

## Context

Users asked to clean up what a game no longer uses: models, textures, sounds, scripts,
scenes, leftover objects in scenes. A game made with Joinery collects these as it is
edited: a model replaced by a better one, a level that was cut, an input action from an
idea that went nowhere, a hidden test mesh. Each unused asset also carries its provenance
(ADR 0002, ADR 0009: native source, recipe, `joinery.lock` entry), so deleting the exported file
alone leaves a recipe that rebuilds it and a lock entry for a missing file. And "unused"
is easy to get wrong: Godot loads files by paths built at run time, by `uid://`, by class
name and from settings, and a false "unused" deletes something the game needs.

## Decision

1. **A walk, not a guess.** `joinery tidy --project P [--json]` (`joinery/tidy.py`) starts
   where Godot and Joinery start the game: every path `project.godot` names (main scene,
   autoloads, the bus layout, translations, hooks, the XR action map), the export presets,
   `joinery.toml`, the design files under `design/` (blockouts, puzzles, dialogue), data
   files a script may read (`.json`, `.dlg`, `.csv`, `.cfg`, `.toml`), editor plugins in
   `addons/`, the features' own tools (`features/<id>/tools/`), and the core and feature
   files Joinery's own code names (the sound pass's audio nodes, replays). From there it
   follows `res://` and `uid://` strings, paths relative to a script (`preload("x.gd")`),
   `extends "…"`, the `class_name`s a script or resource uses, and glTF buffers. What the
   walk never reaches is unused. Nothing is run and nothing is written.
2. **Built paths are "maybe used", never sure.** A reached script that builds a path
   (`"res://levels/level_%d.tscn" % n`, `"res://audio/" + name`, a folder it lists or loads
   from) makes every file it could name "maybe used", with the line as evidence. Maybe-used
   findings are never part of Remove all. Paths that a feature's tool or an editor plugin
   builds (the POT extractor reads every script) say nothing about the game and don't count.
3. **What is never a finding.** Files only the project's tests load are "used by tests"
   and listed apart. The core (`core/`) and each applied feature's folder belong to that
   feature: they come and go with it in Game features (ADR 0024), never one file at a time.
   The art bible, the brief, the user's references (`art/reference/`, `design/docs/`),
   Joinery's records and recipes, shared kits, and folders Godot ignores (`.gdignore`:
   native sources) are never findings. `[tidy] keep` (paths, `input:<action>`,
   `autoload:<name>`, `<scene>::<node>`, `<script>::empty`) and `[match] final` are left
   alone.
4. **Inside files too** (`joinery/tidy_nodes.py`). An input action no script, scene or
   design file names (Godot's `ui_*` never); "maybe" when a script builds action names or
   lists every action (a controls page). An autoload nothing names whose script has no
   engine callback, or whose file is gone. In a scene the game uses: a hidden subtree that
   only draws (meshes, sprites, labels, lights, particles) with no script; a disabled
   subtree of inert nodes; an empty `Node`/`Node2D`/`Node3D` with no children, script,
   groups or metadata. Its name appearing as `$Name`, `%Name`, `"Name"` or in a NodePath
   anywhere keeps it; a scene whose scripts reach nodes by a built name makes it "maybe".
   An attached script that only says `extends <engine class>` (no comment, no code).
5. **Each finding says what goes with it** (`joinery/tidy_remove.py`): its `.import` and
   `.uid`, the files only it uses (a model's textures, a scene's script), a kit model's LODs,
   and its provenance: the `joinery.lock` entries, and a recipe whose game outputs all go,
   with its script, native sources and history recipes. A recipe that also makes a file the
   game keeps stays, and the finding says so. Size is everything that goes.
6. **Removal is one reviewed change.** The chosen findings (every sure one by default)
   become one plan: files to delete and text edits (project.godot without the action or
   autoload, or without a removed file in its translation file list; a scene without the
   node, its connections and the resources only it used; a scene with the empty script
   taken off). A plan where a file that stays would still load one that goes is refused in
   plain words. The plan is applied to a staged copy and opens in Review like any change:
   the checks, the reviewer, Discard (everything stays) and the Timeline's revert apply.
   The card's Remove goes through the chat host (`bridge/tidy.py`, a scripted task of the
   crew, no agent); the CLI stages it with `--remove` (and `--yes` accepts).
7. **Agents: read freely, remove as a write.** `tidy_check` (joinery-registry) is open to
   every role, read-only ones included. `tidy_remove` stages the removal in the agent's
   own change, so it is the lead's and workers' only: the role tables, the policy hook and
   the gateway refuse it to read-only roles, the same for every provider. An agent's shell
   may run `joinery tidy` to read, but `--remove`, `--yes`, `--keep`, `--unkeep` and
   `--clear-caches` are the user's (`joinery.policy`).
8. **Glanceable card** (`tidy_card.gd`, `sidebar_tidy.gd`): `/tidy`, Unused content in
   Settings, or Tidy on the Review tab's Game checks row. A headline ("7 unused things ·
   38 MB"), what was read, findings grouped by kind with sizes, maybe-used ones apart; a
   row opens to say why, what goes with it, Keep and Remove. Remove leads; the rows that went
   to Review step out. The list scrolls in its own share of the window.
9. **Joinery's own clutter, quietly** (`joinery/tidy_caches.py`): review renders, project
   maps, part reports, kit previews, review previews and tool results older than a week,
   change metrics beyond the newest 500 and playtest runs beyond the newest 20. "Free … of
   Joinery caches" clears them at once, with no review (they are made again), and says
   what went. Transactions, restore data and the Timeline are history's (`joinery
   history`), never touched here.

## Consequences

- Every shipped starting point, alone, with each feature it supports and with all of
  them, has nothing unused (`tests/test_tidy_blueprints.py`); a prop a user takes out of
  the level is found and removed whole.
- A script loading a file by a path read from a save, a server or a computed string with
  no literal prefix is not seen: that file would be reported unused. The evidence says
  what was searched, removal is reviewed, and Discard or the Timeline bring it back.
- A node reached only through reflection (`get_child(2)`) in a scene whose scripts don't
  use any of the built-name calls could be reported. The same review applies.
- Files a feature ships but the game doesn't use yet stay until the feature is turned off.
- The walk reads every text file once; on a large project it takes seconds, not minutes,
  and nothing is cached.
