# 0012: Minimal asset viewer and application-only navigation

Status: accepted, 2026-11-07

## Decision

The viewer presents the selected asset at a large scale with search, readable
names and one Open action. Paths, owner/origin, fingerprints, recipe details,
configuration text and verification summaries are removed from the main view.
The registry and policy checks retain that information. Launch failures and
other actual errors remain visible; missing-app configuration is in tooltips.

The preview occupies the available height. Models are framed against their
projected bounds and viewport aspect ratio, and reframe when resized. Audio has
centered playback controls, seeking and elapsed time. Image previews fit the
available region.

Refresh is automatic. The addon reacts to Godot filesystem/reimport signals and
polls for changes while visible. `joinery workspace --since <stamp>` compares
file paths, nanosecond modification times and sizes, including recipes and
explicit native sources. Unchanged polls skip asset hashing and leave the
preview and camera intact. This stamp is change detection, never provenance
verification. Open requests arriving during a poll are queued.

The visible rail contains applications only. Godot opens the engine view;
Blender and the other project tools open their creator-filtered viewer. Native
2D/3D/Script/Game selectors remain registered inside a hidden container so
Godot's internal editor routing still works. All app buttons and the toolbar
toggle share the same dimensions and centered icon sizing.

The bottom version/status chrome is hidden. The assistant column does not offer
a docking popup, including on right click. These shell changes are a separate
Joinery-only patch; bottom engine tabs remain usable.
