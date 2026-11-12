# 0057. Batched typed operations

Status: accepted (2026-11-12)

## Context

Bringing one model in line usually takes several typed operations: apply the
scale, move the pivot, decimate, set a roughness, adjust a map. Each is its own
tool call, so the agent waits for a round trip per step and reads a full result
per step (outputs, recipe, export facts). Each call also starts Blender, saves,
exports the GLB and fingerprints the outputs in a second Blender run. Work on
agent tool use (CodeAct; MCP code execution) points the same way: let the
agent hand over several steps at once and read back one short answer.

The steps must stay what they are today. Every edit of a document is one
recipe (script, preimage, note, fingerprints); the next edit moves it to
`recipes/history`, and `joinery rebuild` replays the current one from its
preimage (ADR 0009). A batch that wrote one merged recipe would change what
history shows and what rebuild replays.

## Decision

1. **`blender_batch(source, steps, exports?, keep_macro_shape_keys?)`** on
   joinery-blender-ops. `steps` is an ordered list of
   `{"op": name, "arguments": {...}}`, where the arguments are the operation's
   own tool arguments; the batch gives `source`/`model` (a .blend or its GLB)
   and `exports` once. Batchable: `transform_parts`, `set_material_value`,
   `adjust_map`, `bake_height_to_normal`, `apply_scale`, `set_pivot`,
   `reduce_triangles`, `set_texel_density`, `edit_bone`, `edit_shape_key`,
   `make_cycle`, `edit_action`, at most 16 steps. Not batchable: operations that
   also run another app (`make_map_editable`, `paint_map`, `replace_map`), read
   the model in Blender to decide (`split_material`, `animation_pass`, whose
   checks would see the model before the earlier steps) or make new documents
   (the kits). They stay single calls.
2. **One planner per operation.** Each batchable tool is split into a planner
   (`plan_*`: checks the arguments and the part selection, returns a `Step`
   with the operation code and recipe note) and `run_step`, which is the old
   `edit_document` call. The single tool and the batch use the same planner,
   so they cannot drift. The batch validates each step's arguments against the
   planner's signature with pydantic (`validate_call`), the same typing the MCP
   server applies to the tool's own call, then runs the planner. The first
   invalid step refuses the whole batch ("Step 3 (edit_bone) is not valid, so
   no step ran: …") before Blender starts.
3. **One Blender process, one recipe per step**
   (`joinery.authoring.edit_document_steps`, `joinery.tools.blender_batch`).
   The host writes each step's recipe script exactly as `edit_document` would
   (`Blender.script`), with a placeholder for the preimage of every step after
   the first. A driver runs inside one Blender: for each step it stores the
   document the step before saved as a content-named preimage (as
   `store_preimage` does), writes that path into the step's script, backs up
   the exports and texture files the step replaces, `exec`s the script, and
   measures the step's outputs the way `joinery.recipe.fingerprints` would (the
   .blend's semantic signature in the same Blender, the GLB and textures by
   sha256). The host then writes the recipes: every step but the last straight
   into `recipes/history` (where the next edit would have moved it), the last
   through `_record` (fingerprints, replaced recipes to history, lock). Same
   scripts, preimages, notes, outputs and fingerprints as the calls one by one.
4. **Stop at the first failure.** A step that raises (a scope-guard refusal, a
   missing object) stops the driver; the host puts back that step's document,
   exports and textures from the preimage and backups, removes the scripts of
   the steps that did not finish, and keeps the finished steps' recipes. The
   tool fails with "Step 3 of 5 (apply_scale) failed: <why>. Steps 1-2 are
   applied and staged (…). … steps 3-5 did not change anything." A failing
   first step changes nothing.
5. **One short result.** A line per step (`"2. set_pivot: Move the model's
   pivot to its bottom-centre"`, with an animation step's summary), the changed
   files, the current recipe and Blender's seconds; not each step's outputs and
   export facts.
6. **Same policy.** The gateway judges the call like any other: the argument
   denylist (`validate_arguments`) walks the nested steps, the art gate and the
   file claims apply, and `blender_batch` is a native operation holding the
   Blender worker slot (`OP_APPS`). Every step's code passes
   `validate_arguments` again in the core, and the part selection is checked by
   each planner. The batch lives in the shared core and the first-party MCP
   server, so every agent (Claude Code, Codex, Gemini CLI, opencode) gets it.
7. **Agents are told to prefer it.** The tool description and the shared part
   guide (`bridge/instructions.py`) say: when one model needs two or more of
   these operations, send one `blender_batch`.

Krita and sound get no batch tool: their typed operations already take an
ordered list run in one process per call (`edit_image` and `paint_map` ops,
`process_audio` steps, `set_surge_parameters` with many parameters at once). A
cross-operation sound batch would chain different apps (a Surge XT render, then
the audio chain), so one process would not save anything.

## Measurements

`tests/integration/blender_batch.py`: six operations (transform_parts,
apply_scale, set_pivot, reduce_triangles, set_material_value, adjust_map) on a
small textured model in the creative worker, through the MCP server, on an
Apple-silicon Mac (amd64 worker under emulation):

| | one by one | blender_batch |
|---|---|---|
| Wall clock, warm worker | 21.8 s | 4.6 s (Blender 2.9 s) |
| Wall clock, container per run | 25.2 s | 4.8 s |
| Tool calls | 6 | 1 |
| Result text the agent reads | 3 324 chars | 697 chars |

Both stages end with the same recipe log (scripts equal but for preimage
names, equal fingerprints) and byte-identical GLB and textures; all seven
recipes of the batched stage, history included, replay to their fingerprints.
These are direct server calls, not agent turns, so `joinery metrics` (which
records gateway spans inside a change) does not see them; the benchmark suite
(ADR 0054) can compare live turns once agents use the batch.

## Consequences

- Multi-step model work costs one round trip and about one Blender start, and
  the agent reads about a fifth of the text.
- History and rebuild are unchanged: a batch and the same calls one by one
  leave the same recipes.
- A failed batch leaves the steps before the failure staged, as the single
  calls would have. The message says which, so the agent resends the rest.
- New typed Blender operations get batched by adding their planner to
  `servers/blender_ops/batch.py` `PLANNERS`.
