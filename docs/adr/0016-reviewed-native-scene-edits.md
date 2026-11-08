# Reviewed native scene edits

The connected sidebar could describe a box but could not create it. Implement a
small end-to-end transaction before adding broader tool capabilities: a native
Godot BoxMesh and selected Node3D position, rotation and scale.

Register a typed `joinery_edit_scene` dynamic tool with Codex app-server. Keep raw
filesystem access read-only. The agent-neutral operation schema and shared MCP
policy check live in `joinery/editor_ops.py` and `joinery/hook.py`; adapters do not
change the allowlist or denylist. The local editor tool is an editor bridge,
not a claim that external Blender MCP modelling is integrated.

The Python host validates finite dimensions, operation names, field allowlists
and selected targets, then sends a framed editor action and awaits its result.
The addon validates the open scene instance, request revision, and the original
selected object's identity before applying anything. Scene switching, cancellation
or replacing an object at the same path invalidates old requests. Whole batches
are validated before any change is applied. There is no eval or arbitrary script
or property setter in this editor path.

Preview records original transforms and created nodes. Reject restores those
transforms and removes only newly created nodes. Accept registers a normal Godot
undo action without executing the preview a second time, records typed operations
and before/after states, and marks the scene unsaved. Saving remains a normal
Godot action, preserving pre-existing unsaved work. A pending preview is rejected
before saving/running, switching scenes or closing the plugin.

For a completely empty editor, attach an isolated Node3D preview to the editor
viewport. Reject leaves the editor empty. Accept adopts the preview as a normal
Godot scene and registers undo for its geometry. Undoing geometry in this new
scene leaves its empty root, as in a normal scene scaffold.

These are untextured scene scaffolding operations, not final art generation.
They do not produce standalone art/audio assets or bypass art-bible requirements
for asset work. Broader creative-tool operations still require ownership,
provenance, approved art direction and their own reviewed transactions.

`JOINERY_STARTUP_PROMPT` optionally posts one explicit launch request in the
visible chat. It is consumed and removed before child processes start. It never
accepts a preview automatically.
