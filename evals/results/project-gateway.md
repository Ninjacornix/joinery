# Project gateway verification

Run on the local Apple M2 development machine, 2026-11-08. Native editor build:
`4.7.2.stable.custom_build.558b2b809`, UUID
`8545a4c7-ae22-3ba1-b6df-f69e3858855a`.

| Check | Outcome |
|---|---|
| Python suite | 333 passed |
| Ruff / strict mypy | Passed; 61 typed source files |
| Shared policy check | `joinery check all`: passed |
| Native editor, existing/empty scenes | Passed |
| Real Codex create through live chat | Passed; exact 1 m BoxMesh; reject restores empty editor |
| Real Codex delete through live chat | Passed; reject restores original instance identity |
| Native generic operation families | Duplicate, rename, reparent, resource creation, script attachment, persistent signal serialization, source instancing/serialization and undo/redo passed |
| Real offline Godot MCP | Create/add/reject/accept/read passed; original untouched before acceptance |
| Actual model through saved-file gateway | Passed: actual Codex -> discovery/schema -> staged Node3D/Marker3D -> import validation -> reject |
| Blender native fixture | Save/GLB export, source-part update, current source registry, measured replay and reject passed |
| Krita native fixture | Layered save/PNG export, layer inspection, canonical layer/pixel replay and reject passed |
| Material Maker native fixture | Native graph/Godot material export, measured replay and reject passed |
| SuperCollider native fixture | Native score/NRT WAV, measured replay and reject passed |
| Blender to Godot | Native source -> immediate import -> saved scene instance -> reviewed acceptance -> full revert passed |
| Owned game runtime | Real offline runtime startup, scene-tree inspection and ephemeral autoload/script cleanup passed |
| Server discovery/probes | Godot, official Blender and Inkscape inspection passed; SuperCollider tool discovery passed |
| Permission/recovery fixtures | Model approval refusal, latched full-turn STOP, schema confinement, read-only provider boundary, exclusive project lease, post-review conflict, interrupted commit recovery, folder restoration and stale combined-preview compensation passed |
| Claude live transport | Provider returned its account quota limit; no successful model/tool operation claimed |
| Gemini/OpenCode | ACP stream/permission fixtures pass; real CLIs unavailable locally, so live authentication/model paths are unverified |
| Aseprite | Adapter exists; licensed worker executable unavailable, so real execution is unverified |
| Source/engine bundle | Extracted file manifest verified; matching engine, CLI, fresh project creation and policy checks passed from a path containing spaces |
| User project delivery | Copied addon updated with reversible backup; installed/source addon hashes and protocol 2 match |
| Art direction approval flow | Accepting a draft now opens its explicit approval review; content/hash binding and stale-content refusal passed. Saving the draft alone never silently approves it. Existing sessions can use `/art`. |
| Review layout and model visibility | Native checks pass: separate bottom Review panel, 250-line diff scrolls, fixed Accept/Reject, command popup above input, Enter submits, accepted GLB imports and opens actual 3D preview |
| Tool discovery and expected errors | Sentence queries return relevant tools with compact readiness; real SDK proxy preserves failure flags; missing native sources return actionable reasons; incomplete recipe-output replacement is refused before mutation |
| Addon UID delivery | All script UID files ship with the addon; updater repairs missing UID files even when source scripts have the same version |

Fixtures are temporary technical documents, not finished game assets and not an
approval of any user's art direction. Game projects require their own explicit
approval, independent of technical fixtures. First-party MCP packages
are now actual entry points rather than empty readiness placeholders.

Reproduce:

```sh
uv run pytest
uv run ruff check .
uv run mypy joinery servers bridge
uv run joinery check all
uv run python tests/godot/run_scene_edit_test.py --live
uv run python tests/integration/mcp_round_trip.py
uv run python tests/integration/agent_gateway.py
uv run python tests/integration/cross_tool.py
uv run python tests/integration/runtime_session.py
uv run python tests/integration/native_documents.py blender
uv run python tests/integration/native_documents.py krita
uv run python tests/integration/native_documents.py material-maker
uv run python tests/integration/native_documents.py supercollider
```
