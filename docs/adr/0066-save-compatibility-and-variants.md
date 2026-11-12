# 0066. Old saves keep loading, and scene variants

Status: accepted (2026-11-12)

## Context

Two things a game made with Joinery needs once it has players and a growing cast of scenes.

**Saves.** The save-load feature (`SaveGame`) writes every `saveable` node's
`save_state()` into versioned slot files and already upgrades older slots through
migration steps (`save/data_version`, `add_migration`, an adapter's `migrations()`). Nothing
checked that a change kept old saves loading. Renaming the key a coin writes
(`"collected"` to `"taken"`) passes every test, imports cleanly and replays the same; a
player's save then loads with every coin back in place, silently. The agent that made the
change can't see it, and the user only finds out from a player.

**Variants.** "A red goblin" or "the same crate, bigger" was done by copying a scene, so a
later fix to the goblin never reached the red one, and the review showed a whole new scene
instead of the two things that differ. Godot has the right tool for this: inherited scenes,
which keep only the overrides and follow their base.

## Decision

### Kept saves (`joinery/saves.py`)

1. **Saves come from playtests.** The playtest bot saves through the game's own save system
   (slot files in `.joinery/playtests/<run>/saves/`); `joinery playtest` now lists them and
   says how to keep them. `joinery saves add <slot file|run folder> --project P [--name N]
   [--version V]` copies them to `tests/saves/<version>/<name>.json`, where `<version>` is the
   game's `application/config/version` (else `save-v<data version>`). Each save is loaded in
   the game as it is now before it is kept (`--no-load` skips that), and what came back is
   kept beside it as `<name>.summary.json`: the level, play time, every saveable node's state,
   the extra data and the slot list's one line, in plain JSON a person can read. A save that
   doesn't load now is refused. `joinery saves list` and `joinery saves check` (load each one
   here and compare) complete the command. Adding is the user's (or the playtester's) action,
   like `joinery replay add`; nothing is written into `tests/` behind the user's back.
2. **Loading one** (`joinery/save_check.gd`, run from outside the game with
   `--script`, so every game can be checked without shipping it) copies the save into slot 1
   and loads it the way the Load page does. Once the level is ready it prints the save after
   the game's migration steps beside what the game holds: `Game.capture_state()`, the
   registered extras, the level, the play time and the adapter's line. Engine values are
   plain text (`Vector2(1, 2)`), so the comparison is Python's.
3. **What counts as broken.** A save *fails* when the game refuses it (damaged, newer, from
   another game, or older with no migration step for its version) or its level never starts.
   It *loses data* when a node it saved isn't in the game after loading, a key it saved is
   gone, a value it saved loads as another (floats within 1e-4), an extra isn't restored, it
   loads into another level, or the slot list's line differs from the one kept with it.
4. **On every staged change that touches what saves are made of** (`saves.relevant`): the
   project settings, the save-load feature, `res://game/save_migrations.gd`, or a script or
   scene that has `save_state`, `load_state`, `SaveGame`, `save_id` or the `saveable` group,
   before or after the change. The check runs in the offline worker after Godot's import,
   with the project's tests and replays, when `[checks] saves` is on (the default once
   `tests/saves/` has any; `false` turns it off). Each save is also loaded in the project
   without the change (cached in `.joinery/saves/` until the game changes), and only what the
   change makes worse counts, so a save that already drifted on the worker's build doesn't
   block an unrelated change. A broken save is a fixable `saves` problem, in `precheck` too,
   naming the save and what broke (`tests/saves/1.0/forest.json: loads but loses data:
   coin_3.collected is gone (was true); the slot list said "Coins: 3 of 5" and now says
   "Coins: 0 of 5"`), with items `saves.load_failed` / `saves.data_lost` whose fix is the
   migration.
5. **The migration hook.** The save envelope's `version` and `save/data_version` were already
   the version field. The game core now also loads `res://game/save_migrations.gd`
   (`extends RefCounted`, `func migrations() -> Dictionary` returning `{old_version:
   Callable}`), the game's own steps beside the blueprint adapter's. Every agent's shared
   instructions say: when a change reshapes saved state, raise `save/data_version` and add
   the step for the old version there, in the same change. The send-back says the same,
   with the version numbers.

### Scene variants (`joinery/scene_variants.py`)

6. **A variant is Godot's inherited scene.** Its root is an instance of the base
   (`[node name="Goblin" instance=ExtResource("1_base")]`), followed by property lines on
   the base's nodes (`[node name="Sprite" parent="."]`, no type) and any nodes it adds. Stock
   Godot opens, instantiates and re-saves it as its own (`test_scene_variant_godot.py`).
7. **Making one.** `make_variant(base, path?, overrides?)` on the joinery-registry proxy,
   gateway RPC `make_variant`, for every agent that changes things (lead and workers;
   read-only roles never see it): it stages the variant in the turn's change, beside the base
   as `<name>_variant.tscn` unless a path is given. Overrides are `{node, property, value}`
   with the value as Godot text (`Color(1, 0.3, 0.3, 1)`), a number or a bool; plain words
   become a string. It refuses what Godot couldn't open: a node the base (with what it
   inherits and instances) doesn't have, `script`, resource references (change those on the
   staged variant with `edit_scene`), half a value, a path under `addons/` or outside the
   project, an existing file. In the sidebar, `/variant` makes a variant of the scene open
   in the editor without an agent (`variant_make`, a scripted task like the Mixer's Save,
   reviewed like any change); `/variant <what differs>` asks the agent, which calls
   `make_variant`.
8. **Validation in every submit.** A changed inherited scene must have its base in the
   project, must not inherit itself, and may only set properties on nodes its base has
   (`variants.missing_base`, `variants.cycle`, `variants.unknown_node`).
9. **The review shows only the overrides.** For every changed inherited scene, however it was
   made, the review item lists what it overrides against its base (and the base's own base),
   with the base's value and the variant's, read as a person would (a transform as "at (0,
   0.8, 0), ×1.6", a resource as its file, a colour with a swatch), then the nodes and
   connections it adds, stopping at eight rows with a count (`review_overrides.gd`). Above
   it is the usual split picture: the base (or the variant as it was) against the variant,
   drawn by the editor in offscreen viewports with one shared framing
   (`review_scene_shot.gd`). Without a display there is no picture, and the review says so
   instead of waiting. The facts line says "variant of goblin.tscn, 2 overrides".

## Consequences

- A change that reshapes saved state comes back until it carries its migration, and the
  test on two blueprints shows the rename caught, the missing step refused and the step
  fixing it.
- Saves are only as good as the ones kept: a game with no kept saves is not checked, and a
  save made where nothing interesting happened checks little. The playtest output suggests
  keeping its saves; deciding which is the user's.
- The check costs one headless load per kept save per relevant change, plus one per save on
  the project without the change whenever the game changed since the last baseline.
- Comparison is by key and value. A migration that deliberately drops a value shows as lost
  data; the change has to say so, and the user can keep a new save.
- Variant pictures are drawn by the editor that shows the review, so a variant whose base is
  new in the same change (not in the project yet) has no picture until it is accepted; its
  overrides still show.
