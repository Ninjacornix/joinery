# Camera rig

Cameras for 2D and 3D games: follow a target with a dead zone and smoothing,
look ahead of where it is going, frame a group of targets, and shake.

## Using a rig

The blueprints keep their own cameras. To use a rig, add one to the level and
turn the old camera off (or remove it) when the user asks for it:

```
Level
  Camera (CameraRig2D)   target = ../Players/Player      # 2D
  Camera (CameraRig3D)   target = ../Players/Player      # 3D, follow_offset = (0, 6, 8)
```

Players usually spawn after the level loads; a rig with no target follows the
local player (`Game.local_player()`). From code:

```gdscript
rig.target_node = player
rig.snap()                 # jump there at once: after a respawn or a teleport
Cameras.shake(0.5)         # every rig; 0-1, adds up to 1, fades by itself
rig.add_trauma(0.3)        # one rig
```

A rig takes over as the current camera when it enters the tree
(`take_over = true`).

## Settings

| Export | 2D default | 3D default | Meaning |
|---|---|---|---|
| `dead_zone` | (64, 48) px | (1, 0.5, 1) m | half-size of the box the target moves in before the camera follows |
| `smoothing` | 6 | 5 | how quickly it catches up, per second; 0 snaps |
| `look_ahead` | 80 px | 2 m | how far ahead of a moving target it looks |
| `look_ahead_speed` | 300 px/s | 6 m/s | target speed for the full look-ahead |
| `group_targets`, `group_name` | | | two or more nodes are framed together |
| `framing_margin` | 120 px | 2 m | room around a framed group |
| `min_zoom`, `max_zoom` | 0.4, 1 | | 2D zoom range while framing |
| `max_pull_back` | | 3 | 3D: how far back (× `follow_offset`) it may go to frame |
| `follow_offset`, `look_height` | | (0, 6, 8), 1 | 3D: where the camera sits, and how high it looks |
| `max_shake_offset`, `trauma_decay` | (28, 20) px, 1.5 | (0.35, 0.25) m, 1.5 | shake size, and how fast it fades |

## Shake and accessibility

Shake is `trauma²` times the player's Screen shake setting
(`Settings.screen_shake()`, 0-1, on the Accessibility page), and none at all
with reduced motion. Never shake a camera another way; route every shake
through `Cameras.shake()` or `add_trauma()` so the setting always holds. In 3D
the shake moves the view (`h_offset`, `v_offset`), never the transform.
Shake noise is seeded from the replay seed, so replays shake the same way.

## Alternatives

Phantom Camera (MIT, an addon with priorities and tweened transitions between
cameras) is a larger option. Joinery does not ship or install it.
