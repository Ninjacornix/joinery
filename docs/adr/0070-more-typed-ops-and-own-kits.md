# 0070. More typed operations, and the user's own Geometry Nodes groups as kits

Status: accepted (2026-11-12)

## Context

Typed operations come first and raw tool code is a logged fallback (CLAUDE.md). Blender
has many typed operations; the 2D and material tools had few. In Krita the agent could
add layers to a painting (`edit_image`, ADR 0045) but anything about the layer stack
itself (renaming, reordering, hiding, blending, merging, deleting) needed raw Krita
Python through `operate_document`. In Material Maker it could only rewrite a whole graph
(`operate_graph`), copying every node and link by hand to change one value, with nothing
checking a value against the node's own definition. In Inkscape it could only rewrite a
whole SVG (`operate_vector`). And kits (ADR 0044, 0051) were hand-written bmesh
generators; a user who already models procedurally in Geometry Nodes had no way to turn
their own node group into a kit.

## Decision

1. **Krita: `operate_layer`** (joinery-paint). Typed operations on a `.kra` under art/,
   in order, by layer name (a name must match exactly one layer anywhere in the stack):
   `add` (paint or group, `above` a layer or `into` a group, else on top), `rename`,
   `reorder` (`above` a layer, or `position` top / bottom), `hide`, `opacity`, `blend`
   (an allowlist of 18 modes mapped to Krita's composite ids, because Krita accepts any
   text as a mode), `merge` (into the layer below), `duplicate`, `delete`, `fill_layer`
   (a colour generator layer; the colour is written as the KoColor XML Krita itself
   writes) and `filter_layer` (the same five typed filters as `adjust`). Everything is
   checked in `joinery/tools/krita_ops.py` before Krita starts; the operations run as one
   loop over the checked rows, which is the recipe's Krita code, so `joinery rebuild`
   replays them to the same `kra:` fingerprint. A missing or ambiguous layer stops the
   run before anything is saved, and the tool reports just Krita's reason.
   Krita 5.2 drops a group's children when a group is taken out of the stack and added
   back, so a layer moves as its own duplicate (children, masks and settings included)
   and the original is removed. Krita can add a layer above another or on top, never at
   the bottom; `position: bottom` puts it above the lowest layer and then moves that one
   above it.

2. **Material Maker: `list_graph`, `set_node_parameters`, `wrap_image`**
   (joinery-materialmaker, `joinery/tools/mm_graph.py`). A node's definition is its own
   `shader_model` or subgraph when it carries one, otherwise Material Maker's library
   definition `nodes/<type>.mmg`, read in the offline worker (the host has no Material
   Maker) by a short script built from the same normalising functions the host uses. A
   subgraph node's parameters are its `remote` widgets; a linked control takes the type
   and range of the first parameter it drives, a configuration control is an enum.
   `set_node_parameters` checks each value against that definition: float inside its
   range, size as a whole power of two inside `first..last`, enum by index or choice
   name, boolean, colour, gradient, curve and polygon by shape. Pixel, lattice and spline
   parameters are refused ("change it in Material Maker"). `wrap_image` adds an image
   node for a project image under art/ with provenance and outside the export folder
   (the same checks `edit_graph` makes for every image input) and can wire it into input
   ports, replacing what fed them. Both write the whole graph through `edit_graph`, so
   the recipe holds the exact `.ptex` and the exports compare by `sha256:`; the recipe's
   note names the change. Exports go to the folder the graph's recipe already uses.

3. **Inkscape: `edit_vector_element`** (joinery-paint, `servers/paint/vector.py`). One
   element, by id: `attributes` (prefixed names through the document's own namespaces;
   null removes), `style` properties merged into the style attribute, a `transform` list
   checked function by function (matrix, translate, scale, rotate, skewX, skewY with
   their argument counts), or the `text` of a text, tspan, title or desc that holds no
   other elements. The id never changes; event handlers, `javascript:`, links outside the
   document and `url()` to anything but `#id` are refused, and the result passes the same
   self-contained check as `operate_vector`. The prolog (XML declaration, comments) is
   kept as written and comments inside the document survive. The edited document goes
   through `edit_vector`, so the recipe carries it and replays byte for byte.
   The Inkscape analysis worker (sandbox/inkscape.Dockerfile) now asserts
   `test -x /usr/bin/inkscape` at build time, so a creative worker built without
   Inkscape fails the build instead of producing a worker whose every call fails. It
   lands with the next worker rebuild; existing images are unchanged.

4. **The user's own Geometry Nodes group becomes a kit:
   `joinery kits from-blend <file.blend> --group <name>`** (`joinery/kit_groups.py`).
   In a transaction the user reviews (or `--yes`): the .blend is copied to
   `art/sources/groups/<id>.blend` and recorded in joinery.lock as a `human-import`
   (the user's own work; `--licence` names it), Blender reads the group in the offline
   worker, and a project kit `kits/blender/<id>.py` is written. Its typed parameters are
   the group's inputs: Float (with the input's range; an unbounded end gets ten times
   the default, or 1, either side; distances get the unit m; positive ranges vary by
   scale in a batch), Int and Boolean. An Int input called Seed takes the kit's seed, so
   each model of a set is different. Other inputs (vector, colour, object, menu, …) stay
   at the group's own values and are listed as fixed. Each material the group sets
   becomes a material role coloured from its base colour, so the art bible places it on
   the palette as for every kit. The SPEC carries an "As made" preset with the group's
   defaults and `sources: [the .blend]`, which a kit's recipe now lists as sources.
   The build appends the group from the copied .blend, runs it in a wrapper that
   realizes instances, applies the result to one mesh, swaps each of the group's
   materials for its role's material and hands the object to the shared kit code
   (budget, pivot, LODs, GLB). The kit then works like any project kit: kit_list and
   the Kits tab, kit_make, kit_batch, kit_edit, scatter and `joinery rebuild`.
   **Only plain Geometry Nodes**: the group and every group it uses must be
   `GeometryNodeTree`s made of nodes Blender itself defines. A node an add-on defines
   (Sverchok's, GPL-3, and absent from the worker) is refused, as is a group linked from
   another file, an image not packed into the .blend, and a link Blender shows red
   (the group would not make what it shows). The generated generator file contains only
   Joinery-written code and the user's parameters; the user's node group stays in their
   .blend, a separate human-import, so nothing GPL enters Joinery's MIT kits.

## Consequences

- Layer, graph-value and SVG-element changes no longer need raw tool code, and each one
  is a typed, checked, recorded step in the recipe log.
- `edit_graph` and `edit_vector` take a recipe note; recipes made through them before
  have none.
- A kit can now depend on a project file (`Kit.sources`); `kits.make` refuses to run
  when it is missing. Shared kits can't carry one yet: a kit archive (ADR 0051) does not
  package a generator's sources, so a project kit made from a .blend is not exportable
  as a shared kit until it does.
- Importing a group needs Docker (Blender reads the group in the worker); the Kits tab
  lists the new kit but importing is a terminal command for now.
- Material Maker's library definitions are read in the worker on each call that needs
  them (about a second); they are not cached on the host.

## Tests

`tests/test_typed_ops.py` and `tests/test_own_kits.py` cover the checks and generated
code. In the real workers: `tests/integration/typed_ops.py krita|material-maker|inkscape`
(every operation, refusals, and a `joinery rebuild` replay of each recipe) and
`tests/integration/own_kits.py` (a group built in Blender with a Seed, a switch,
instances and two materials; from-blend through the CLI, a set of three, kit_edit,
the provenance gate, replay and the refusals).
