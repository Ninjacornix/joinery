# 0065. Dialogue, enemy behaviour and camera features

Status: accepted (2026-11-12)

## Context

Three things nearly every game made with Joinery asks for, and that each project
otherwise wires by hand: conversations, enemies that act on their own, and a camera
that follows well. Each has a popular Godot add-on (Dialogue Manager, Beehave or
LimboAI, Phantom Camera). Features (ADR 0024) are the place for such building blocks:
code-only, applied and removed as a reviewed change, tested on the blueprints.

Three constraints shape the choice between an add-on and our own code:

- What the agent edits must read and diff as text, and Journeyman must be able to
  learn the feature's API without a vendor's docs site.
- A feature ships no third-party code; anything outside Joinery is a separate,
  pinned, hash-checked download the user approves (ADR 0027), and the feature must
  work without it.
- The feature must follow the game core: its UI style (`UiStyle`), the Accessibility
  settings, the translations feature, replays (seeded randomness) and the playtester.

## Decision

Three features, `joinery feature add dialogue|behaviour|camera`, each our own small
MIT GDScript in `features/<id>/`, tested headless on a 2D and a 3D blueprint
(top-down and third-person-3d) and run by the blueprint matrix on every blueprint.

### Dialogue

- **Format.** Plain-text `.dlg` files, one statement a line, blocks indented:
  `~ node`, `Speaker: text`, narration, `{{variable}}`, `[if condition]`, `[ID:key]`,
  `set name = value` (`+= -= *= /=`), `do event`, `if`/`elif`/`else`,
  `- choice => node`, `=> node`, `=> END`. Close in spirit to Dialogue Manager (same
  `~`, `=>`, `- choice`, `[ID:]`, `{{}}`) and Yarn (nodes, jumps, commands), so writers
  who know either read it at once. Conditions and values are Godot `Expression`s over
  the dialogue's variables, run with `const_calls_only`, so a file cannot call into
  the game except through `do` events.
- **Runtime.** `dialogue_parser.gd` compiles a file to a flat instruction list;
  `dialogue_runner.gd` steps it to the next line, choice set or end. The `Dialogue`
  autoload drives a runner and a dialogue box built with `UiStyle`. The box follows
  the Accessibility settings directly through `Settings`: subtitle size and text size
  scale it (stored the way the accessibility feature stores HUD sizes, so it never
  scales twice), high contrast recolours it, reduced motion stops lines typing out,
  and each line is a polite screen-reader live region. While open it shows the mouse
  and takes clicks, so games that capture the mouse still let the player choose.
- **Resources.** `DialogueFileLoader` (a `ResourceFormatLoader` with a class name, which
  Godot registers by itself) makes `load("….dlg")` return a `DialogueFile`, so nodes can
  export one. `.dlg` joins the overlay's shippable suffixes.
- **Translations.** English is the message id and `[ID:key]` its gettext context, the
  translations feature's convention. Its extract tool reads `.dlg` files through the
  dialogue feature's own parser when that is in the game, so the template always
  matches what the game shows.
- **Review.** A changed `.dlg` file is a `dialogue` item in the review (`review.plan`),
  and `.dlg` is in the text diff. The stage (`review_dialogue.gd`) runs the game's own
  parser and runner from `res://features/dialogue/`: Before or After, from any node,
  with choices to click and "Start with" variables, and the file's problems by line.
  Without the feature in the game it shows the text and says so.

**Dialogue Manager (MIT) was evaluated and not adopted.** It is the best-known option
and its syntax inspired ours, but it is an editor plugin with an import plugin, its
own balloon scenes and state model, optional C#, and a release cadence tied to
Godot versions; adding it means third-party code in `addons/` that our review, UI
style, accessibility settings and translations would each have to be adapted to,
and that the user would have to approve and keep pinned. Our parser and runner are
about 750 lines (1,200 with the box and autoload) that the agent can read in one
sitting; they run in the review without a game running and need nothing else. No pin is written: there is nothing to download. A user who
prefers Dialogue Manager can still add it as a custom tool; the formats are close
enough to port by hand.

### Enemy behaviour

- A behaviour-tree runtime in `bt_nodes.gd`: `sequence`, `selector`, `invert`,
  `cooldown <s>`, `wait <s>`, `condition <name>`, `action <name>`, with `name=value`
  arguments. Sequences and selectors re-evaluate from the first child each tick and
  interrupt the running branch they no longer reach, so a higher-priority branch
  takes over at once.
- Trees are `.tres` `BehaviourTree` resources whose `source` is that text: a resource
  the inspector and `@export` understand, and a few lines that diff line by line.
  Four ready trees ship (guard, chaser, coward, wanderer).
- `BehaviourAgent`, a node under the enemy, runs a tree for CharacterBody2D/3D (with
  `move_and_slide`, gravity in 3D) or any Node2D/3D, with built-in conditions (sight,
  range, health, chance, blackboard) and actions (patrol, chase, flee, attack,
  wander, idle, remember); any other name calls the body's `bt_<name>()`. Randomness
  comes from `Replays.rng`, so replays and playtests repeat. Only the session's
  authority runs trees.
- Beehave (MIT, GDScript, visual editor) and LimboAI (MIT, C++ module or GDExtension,
  also state machines) are noted in the guide as larger alternatives; neither is
  shipped or pinned.

### Camera

- `CameraRig2D` (a Camera2D) and `CameraRig3D` (a Camera3D): follow with a dead zone
  and exponential smoothing, look ahead along the target's velocity, frame a group of
  targets (zoom out in 2D, pull back along the offset in 3D), and shake by trauma.
- Shake goes through `Cameras.shake()` or `add_trauma()` and is scaled by the core's
  `Settings.screen_shake()` (the Accessibility page's slider) and off with reduced
  motion. In 3D it moves `h_offset`/`v_offset`, never the transform. Noise is seeded
  from the replay seed.
- Blueprints keep their own cameras; a rig replaces one when the user asks. Phantom
  Camera (MIT) is noted as a larger alternative; not shipped or pinned.

### How the agent and the playtester learn about them

- **Guides.** A feature that game code calls into ships
  `overlay/features/<id>/GUIDE.md`: its API, formats and examples. It lands in the
  game, so any agent working there can read it (agent-agnostic), and the registry's
  `game_setup` returns `feature_guides` (path and first lines) for the features the
  game has; the shared instructions tell the agent to read a guide before using its
  feature. `feature.toml` stays unchanged.
- **Playtester state.** Any node in the group `playtest_state` with
  `playtest_state() -> Dictionary` is reported by the playtest bot under `states`
  (node path → state, at most 20) in `bot.json`, the report and what the agent reads.
  Each `BehaviourAgent` reports its tree, running branch
  (`selector > sequence > action chase`), every node's last status and blackboard;
  the `Dialogue` autoload reports file, node, line, nodes visited and variables.

### Catalogue checks

Ten features make 5,120 blueprint × feature combinations. Listing every overlay again
for each took two minutes; `blueprints.cached_scan()` keeps the listings and the loaded
catalogue while `validate_catalogue()` and the static matrix test run (2 s).

## Consequences

- Dialogue, enemy AI and cameras are a `/feature add` away, testable headless, and
  readable by the agent from the game itself.
- We maintain three small runtimes instead of tracking three add-ons. Features users
  expect from the add-ons (Dialogue Manager's balloon theming and mutations with
  arbitrary calls, Beehave's visual tree editor, Phantom Camera's priorities and
  tweened handovers) are not there; requests for them extend our code or, at the
  user's choice, bring the add-on in as a custom tool.
- Not verified yet: that an exported game includes `.dlg` files through the class-named
  loader (the editor recognises them; an export was not run); the dialogue box in a
  VR headset (it is a 2D layer); `Dialogue.variables` are not saved by save-load;
  behaviour trees in a two-process online game (they run on the authority only, as
  designed, but no online test covers them).
