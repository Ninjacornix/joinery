# 0052. Complex models in Blender

Status: accepted (2026-11-11)

## Context

Asked for "a wooden chair next to the table", agents often reach for the
quickest thing Godot offers: five `BoxMesh` instances under a `Node3D`. It looks
like a chair from far away, but it is not one: the art bible's palette, budgets
and pivots never judge it, there is no recipe or `joinery.lock` entry, the
reviewer can't tell it from level geometry, and the next "make the chair
rounder" means stacking more boxes. Joinery already has the right tools for the
job: Blender for any model, and the parametric kits (ADR 0044) as the quick
path. The instructions asked for objects as one unit (ADR 0035's checks, the
object guide), but nothing held an agent to Blender, and every provider drifted
the same way.

Placeholder shapes still have a place: blocking out a level (ADR 0043), a
quick prototype, or when the user asks for a placeholder by name.

## Decision

1. **A project setting, Blender by default.** `[agents] complex_models` in
   `joinery.toml` is `"blender"` (the default, also when missing or unreadable)
   or `"placeholders"`. Settings → Project shows it as **Complex models in
   Blender**, on by default; off lets agents use Godot placeholder shapes for
   quick prototyping. `joinery complex-models --project <p> [--set
   blender|placeholders] [--json]` reads and saves it, keeping every other line
   of `joinery.toml` (the same `[agents]` writer as the reviewer setting).
2. **Agents read it in their context.** The chat host adds `complex_models` to
   the editor context of every request and task, for every provider. The object
   guide says: with `"blender"`, every object with more than one part is a kit
   model (the quick path) or a Blender model, placed as one instance; before the
   art bible is approved, say so and offer a placeholder; placeholder shapes only
   when the user asks for one, marked `metadata/joinery_placeholder = true`.
3. **The submit checks enforce it, in the shared core.** `submit_for_review`
   (`joinery/submission.py`, so Codex, Claude Code, Gemini CLI and opencode
   alike) parses every staged `.tscn` the change adds or modifies
   (`joinery/complex_models.py`). A *placeholder shape* is a `MeshInstance3D` or
   `CSGMesh3D` with a primitive mesh (Box, Cylinder, Sphere, Capsule, Prism,
   Torus, Plane) or a CSG shape. Shapes are grouped by their object (the parent
   of a chain of shapes and CSG combiners) and then by touch (their boxes,
   transformed into the object's space, overlap within 10 cm). Flagged:
   - two or more touching shapes under one object node;
   - three or more touching shapes loose at a level's root (two stacked crates
     are two things, a seat with legs is one);
   - a prop scene (only bodies, collision shapes, markers, plain nodes and
     shapes, nothing instanced) with two or more touching shapes.
4. **What is never judged.** Level geometry: a shape named as a floor, wall,
   platform, ramp, stairs, room, corridor, trigger, spawn, goal and similar, or
   3 m or longer along a side. Collision and trigger shapes (they aren't
   meshes). Scenes the blockout tool built (their root records
   `metadata/blockout_layout`). Nodes under `metadata/joinery_placeholder =
   true`, `metadata/joinery_editor_only = true`, the `editor_only` group or
   `visible = false`. With `"placeholders"` the check doesn't run.
5. **Only what the change touched.** An object found in the scene before the
   change, shape for shape (node text, place and size in its object), is not the
   change's: moving an old box chair, or adding a lamp beside it, passes.
   Changing one of its shapes, or adding a new pile, does not.
6. **A fixable problem to the author**, one line per object: "Chair is built
   from 6 placeholder shapes. Make it in Blender (or with a kit) and place it as
   one model. Only when the user asked for a placeholder, set
   metadata/joinery_placeholder = true on it. (scenes/main.tscn: Chair/Back,
   Chair/Leg1, …)". The author fixes and submits again in the same turn.
7. **Live edits of the scene on screen** are judged the same way from the
   `edit_scene` operations (`create_box`, and `create_node` of a CSG shape or a
   mesh instance with a primitive mesh), grouped by parent and touch. The editor
   gives new nodes identities Joinery can't match to names, so a live pile is
   reported once; the next submit judges only shapes added after it.

## Consequences

- A chair, lamp or car reaches review as a model with a recipe, a lock entry
  and the art bible's checks, whichever agent made it.
- A project in early prototyping turns the switch off; nothing else changes.
- The rule is a heuristic on shapes and names: a large object built from
  shapes 3 m or longer, or one named like level geometry, passes; two separate
  props placed touching under one parent are flagged and need a parent each.
- Existing projects are not rewritten or flagged: untouched scenes and
  untouched objects in changed scenes are never judged.
