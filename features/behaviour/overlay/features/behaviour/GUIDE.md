# Enemy behaviour

Behaviour trees for enemies, 2D and 3D. A tree is a few indented lines of
text; a `BehaviourAgent` node under the enemy runs it every physics frame.

## Giving an enemy a tree

Add a `BehaviourAgent` child to the enemy body and set its `tree`:

```
Enemy (CharacterBody2D or CharacterBody3D)
  Shape
  Behaviour (BehaviourAgent)   tree = res://features/behaviour/trees/guard.tres
```

In a `.tscn`:

```
[ext_resource type="Script" path="res://features/behaviour/behaviour_agent.gd" id="5_bt"]
[ext_resource type="Resource" path="res://game/enemies/guard_tree.tres" id="6_tree"]

[node name="Behaviour" type="Node" parent="."]
script = ExtResource("5_bt")
tree = ExtResource("6_tree")
```

Ready trees: `guard.tres` (attack close, chase in sight, else patrol),
`chaser.tres`, `coward.tres` (flees below half health), `wanderer.tres`.
To change one for a game, copy it to `res://game/…` and edit the copy's
`source`; leave the feature's own files alone.

The agent moves a `CharacterBody2D`/`3D` with `move_and_slide` (3D adds
gravity), or moves any other `Node2D`/`Node3D` directly. Set
`move_body = false` to read `desired` and move the body yourself (animations,
navigation). Only the session's authority runs trees (online: the server).

## The tree text

```
# Guard: hits when close, chases what it sees, otherwise patrols.
selector
	sequence
		condition target_near
		cooldown 1.2
			action attack
	sequence
		condition sees_target
		action chase
	action patrol
```

| Kind | Meaning |
|---|---|
| `sequence` | children in order; fails at the first that fails, runs while one runs |
| `selector` | children in order; succeeds at the first that does not fail |
| `invert` | one child; success and failure swap |
| `cooldown <s>` | one child; after it succeeds, fails for `<s>` seconds |
| `wait <s>` | running for `<s>` seconds, then success |
| `condition <name>` | a check (below) |
| `action <name>` | a move or deed (below) |

Arguments are `name=value` after the name (`action chase speed=200`); a bare
first value is `value` (`cooldown 1.5`, `condition health_below 0.3`).
Sequences and selectors start from their first child every frame, so a
higher branch takes over as soon as its condition holds.

Conditions: `has_target`, `sees_target range=`, `target_near range=`,
`target_far range=`, `health_below value=` (the body's `health / max_health`),
`chance value=`, `blackboard key= value=`.

Actions: `patrol` (between `patrol_points`, or back and forth around home),
`chase` (success in `attack_range`), `flee` (success beyond `flee_range`),
`attack` (the body's `attack(target)`, else the target's
`take_damage(damage)`), `wander` (a random point near home), `idle`,
`remember key= value=` (sets the blackboard).

Anything else calls the enemy body:

```gdscript
func bt_low_ammo(agent: BehaviourAgent, args: Dictionary) -> bool:
	return ammo < int(args.get("value", 3))

func bt_reload(agent: BehaviourAgent, args: Dictionary) -> int:
	return BehaviourAgent.RUNNING if reloading else BehaviourAgent.SUCCESS
```

## Tuning

`BehaviourAgent` exports: `speed`, `acceleration`, `sight_range`,
`attack_range`, `flee_range`, `patrol_radius`, `wander_radius`,
`patrol_points`, `damage`, `target_group` (empty: the players), `gravity`.
Zero means a sensible default in pixels (2D) or metres (3D).

## Checking and playtesting

`BehaviourTree.errors()` lists problems as `line N: …`; an agent with a bad
tree stays idle and says why in a warning. `agent.describe()` prints the tree
with each node's last status. Each agent is in the `playtest_state` group:
the playtester's report has, per enemy, the running branch (`selector >
sequence > action chase`), every status and the blackboard.

## Alternatives

Beehave (MIT, a GDScript addon with a visual editor) and LimboAI (MIT, a C++
module or GDExtension with state machines too) are larger options. Joinery
does not ship or install them; this runtime is small enough to read in one
sitting and needs nothing else.
