# 0010: Shared tool workspace and native desktop handoff

Status: accepted, 2026-11-07

## Decision

The addon provides one **Tools** main screen, with an asset list filtered by
tool, model and image previews, audio playback, source and provenance details,
and an **Open in** application selector. Journeyman remains in its dock.
Workspace navigation stays visible in Focus mode. This uses stock plugin APIs;
the engine patch series is unchanged.

The workspace reads `joinery workspace --project <path>` JSON. Python resolves
the catalog, project tool selection, recipes, native sources and file hashes.
GDScript does not parse TOML or maintain a second tool catalog. Invalid recipes
are reported; an absent lock does not stop viewing an unrecorded file. Hash
agreement is labelled separately from recipe replay, which this screen does
not run or claim to have verified.

Desktop viewing is independent of MCP availability. A Krita document can open
in Krita while its automation server is still planned. A WAV made by
SuperCollider can open in Audacity or REAPER without changing its recorded
owner or origin, or enabling another MCP. Only catalog tools and the project's
explicit custom tools are offered.

Native documents come from a recipe's optional `sources` list. Without it,
native outputs of the same recipe are used, or the operation script when it is
itself a native document (SuperCollider). Blender's Python recipe is never
executed to open a model; `.scd` documents open in `scide`, never `sclang`.
An explicitly selected native output opens itself instead of a sibling.

`joinery open <asset> --tool <id> --project <path>` resolves the source again at
click time, rejects paths and symlinks outside the project, and launches with an
argument vector without a shell. Missing apps and sources have visible reasons.
PATH executables and conventional macOS app bundles are detected, and
`[desktop.<id>]` overrides support other install locations and custom viewers.
An MCP server command is never reused as a desktop executable.

## Limits and next steps

This is a viewer and handoff, not application-window embedding or a transaction
engine. Opening and refreshing do not rewrite assets, recipes or lock entries.
The user can save in a desktop tool and refresh to see file drift; accepting a
manual edit into a replayable workflow remains transaction work.

Images and Krita/OpenRaster merged thumbnails preview directly; supported Godot
audio formats play locally. Imported 3D scenes preview in an isolated viewport
with mouse orbit and scroll zoom.
Other native formats report that a preview is unavailable and still offer their
desktop app. Desktop applications must be installed separately. Model preview
requires the normal Godot import, including its Blender executable setting for
`.blend` files.

## Validation

Python tests cover source and alternate-viewer resolution, unchanged provenance,
custom viewers, app discovery, missing applications, argument-safe launching,
and traversal/symlink rejection. `tests/godot/workspace_test.gd` exercises real
model/image/audio previews, selection, stopping audio and asynchronous CLI
refresh against the testbed in stock Godot and the Joinery build.
