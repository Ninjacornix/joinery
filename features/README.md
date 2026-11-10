# Features

Optional capabilities added to a project made from a blueprint: online play,
Steam, controller support, saving, translations. `joinery/blueprints.py` adds
and removes them; `docs/adr/0024-blueprints-and-features.md` explains the
design. Adding or removing a feature is a user action with a before/after
review; Journeyman may suggest one, never apply it.

```
features/<id>/
  feature.toml
  overlay/                       -> project root
    features/<id>/…              res://features/<id>/
    tests/features/<id>/…        res://tests/features/<id>/
  adapters/<blueprint-id>/overlay/…   optional, applied only with that blueprint
  export_presets/*.cfg           optional [preset.0] fragments, renumbered on apply
```

## feature.toml

```toml
id = "online-play"
name = "Online play"
summary = "Host or join over LAN or the internet with Godot's high-level multiplayer."
version = 1
requires = []          # added automatically and reported
conflicts = []         # refused, in either direction
enhances = []          # informational
blueprints = ["*"]     # or explicit ids; other blueprints are refused
extra_roots = []       # only "locale" (translations)

[[external]]           # used if present, never installed by Joinery
name = "…"
source = "TBD, pending user approval"
version = "TBD, pending user approval"
install = "separate, user-approved step; not designed yet"
required = false

[project.settings]     # same tables as blueprint.toml
[autoload]
[input.jump]           # features add events only to actions that exist (create = false)
pad_buttons = [0]
[append]

[adapters.puzzle-cards.project.settings]   # extra merges for one blueprint
```

## Rules

- Same Rule Zero, uid and `tr()` rules as blueprints (see
  `blueprints/README.md`). No asset files, no downloads, no binaries
  (`.gdextension .dll .so .dylib` …): external extensions are installed by the
  user in a separate step, are never `required`, and every call degrades to a
  no-op without them.
- Write only your own folders. Two units never write the same path, set the
  same setting to different values, or add the same autoload or export preset
  name; `validate_catalogue()` checks every blueprint with every feature
  subset.
- Removal must leave the project as it was: keep everything a feature changes
  declared in `feature.toml` or shipped in its overlay, so the manifest can
  undo it.
