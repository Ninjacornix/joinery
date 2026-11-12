# Blueprints

Playable starting points for new projects. Each one is a small, complete,
code-only greybox game that opens in stock Godot 4.7. `joinery/blueprints.py`
applies them; `docs/adr/0024-blueprints-and-features.md` explains the design.

```
blueprints/
  _core/            shared game core, applied with every blueprint
    unit.toml       same schema as blueprint.toml, kind = "core"
    overlay/        -> project root: core/, tests/support/, default_bus_layout.tres
  <id>/
    blueprint.toml
    overlay/        -> project root: game/, tests/game/
    capture.png     the new-project card's screenshot (never copied into a game)
    capture.toml    how Godot made it: command, engine version, frame, sha256
```

`capture.png` is the one picture a blueprint folder may hold, and only because
Godot made it by running the blueprint: `uv run python
release/capture_blueprints.py` makes every capture again (or `--blueprint <id>`
for one). Re-run it after a blueprint's look changes; `validate_catalogue()`
refuses a capture whose sha256 does not match its note.

## Rules

- **Rule Zero.** Visuals are made in code: primitive meshes, CSG, `Polygon2D`,
  `ColorRect`, `StyleBoxFlat`, flat `StandardMaterial3D`, small `.gdshader`
  text. No image, model, font or audio file (`.png .jpg .webp .svg .glb .blend
  .wav .ogg .ttf .otf` and similar), and no picture, sound, font or mesh
  embedded in a `.tscn`/`.tres`. Overlays hold only code and text Godot reads
  (`.gd .uid .tscn .tres .gdshader .cfg .po .pot .csv .json .txt .md .dlg`);
  anything else is refused and never copied into a game. Blueprints are silent.
- A blueprint writes only `game/` and `tests/game/`; the core only `core/`,
  `tests/support/` and `default_bus_layout.tres`. `project.godot` is merged,
  never copied.
- Every `.gd` ships its `.gd.uid` with a fresh uid; every `.tscn` header has
  its own `uid="uid://…"`. `joinery.blueprints.new_uid()` makes one.
- Input actions come from the standard vocabulary: `move_left`, `move_right`,
  `move_up`, `move_down`, `move_forward`, `move_back`, `look_left`,
  `look_right`, `look_up`, `look_down`, `jump`, `interact`, `attack`, `dash`,
  `sprint`, `crouch` (never with `dash`), `restart`, and the core's `pause`.
  Keyboard bindings are the blueprint's; pad bindings belong to the controller
  feature. Never declare `ui_*` actions: that would replace Godot's own.
- Every user-visible string goes through `tr()` or a Control's auto-translate.
  A Label filled from `tr("…") % n` turns auto-translate off (it would
  translate the result a second time) and is drawn again on
  `NOTIFICATION_TRANSLATION_CHANGED`, so a language switch updates it live.

## blueprint.toml

```toml
id = "platformer-2d"            # same as the folder name
name = "2D platformer"
pitch = "Run, jump and collect coins across a short level to reach the flag."
version = 1                     # bump when shipped files change
dimension = "2d"                # 2d | 3d | ui
icon = "CharacterBody2D"        # stock editor icon name
default_features = ["controller", "save-load"]
bible_preset = "pixel-2d"       # templates/bible_presets/<id>.toml, or "none"
suggested_tools = ["godot", "krita", "gimp", "surge-xt"]  # policy/preferred_tools.toml ids
main_scene = "res://core/main.tscn"

[hooks]
level_scene = "res://game/level.tscn"
player_scene = "res://game/player/player.tscn"
spawnable_scenes = []

[brief]                         # drafting defaults for design/brief.md
genre = ["platformer"]
camera = "…"
core_loop = "…"
progression = "…"
verbs = [["Jump", "jump", "Squash and stretch"]]

[project.settings]              # "section/key" = bool | int | float | string | [strings] | { raw = "<variant>" }
"display/window/size/viewport_width" = 1280

[autoload]                      # Name = "res://…"  (written as "*res://…")

[input.jump]                    # keys, mouse, pad_buttons, pad_axes ("0-", "5+"), deadzone, create
keys = ["Space", "W", "Up"]

[append]                        # "section/key" = ["…"], added to a string array without duplicates
```

The engine writes `run/main_scene`, the `4.7` feature tag, `game/blueprint`
and `game/hooks/*`; units never set them.

## Checking

```sh
uv run python -c "from joinery import blueprints; print(*blueprints.validate_catalogue(), sep='\n')"
uv run pytest tests/blueprints
```

`validate_catalogue()` must print nothing. `JOINERY_COMBO_MATRIX=full` boots
every blueprint × feature combination in Godot instead of the default sample.
