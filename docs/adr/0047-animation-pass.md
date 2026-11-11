# 0047. Animations made in Blender, browsed in the Blender tab

Status: accepted (2026-11-11)

## Context

Characters need walk, run, idle and jump clips before a 3D game feels alive, and
users want to see a model's animations, flip through them and point at one ("make
the run bouncier"). Rule Zero rules out motion-capture downloads and motion models:
motion has to be made by operating Blender, replayable from a recipe, and land in the
GLB under names Godot keeps.

## Decision

1. **Motions are procedural keyframes written by Joinery** (`joinery/tools/blender_anim.py`,
   run as ordinary Blender edits). A rig is read once: a Rigify rig (also MPFB2's) is
   driven through its FK controls with each limb's `IK_FK` switch keyed to FK in the
   action, so the deform bones the game gets follow; a hand-made humanoid is driven
   through its own bones, found by name (hips, spine, chest, neck, head, thigh /
   upper_leg / UpLeg, shin / lower_leg / calf, foot, upper_arm, forearm / lower_arm,
   hand, with a side). Facing comes from the feet. Rotations are given in the
   armature's frame (forward swing about the side axis, raise about the forward axis)
   and converted to each bone's own frame, so rolls and rest poses don't matter. The
   legs use a two-bone solver in the leg's side view plus a hip turn for what is left
   sideways, refined against Blender's own evaluated pose until the ankle is within
   0.2 mm. Planted feet follow the ground: still for idle, attack and wave; moving
   back at the ground speed for in-place walk and run (stored on the action as
   `joinery_ground_speed`). The walk's hips ride as high as the planted legs allow (an
   inverted pendulum).
2. **Kinds and parameters**: walk, run, idle, jump (jump_start, jump_air, jump_land),
   attack (or swing) and wave; speed, length, stride, bounce, arm_swing, lean, loop,
   side, name, replace. Actions start at frame 0 and are sampled every frame with
   linear keys, so a loop's last key equals its first and Godot loops it without a
   held frame. Each action is stashed on a muted NLA track and has a fake user, so
   the glTF exporter writes every one under its own name; its custom properties
   (`joinery_kind`, `joinery_loop`, …) reach the GLB as animation extras.
3. **Edits** (`edit_action`): retime (resampled, so loops still meet), amplitude
   around the average pose per bone group or bones, bounce (then the feet are pinned
   again), offset (cyclic for loops) or a pose offset, foot_lock (contact found by
   height, pinned in the ground's frame, two blended frames at each end), loop_seam
   (linear drift removal), mirror (left and right swapped with the X-mirror sign
   rules), rename, copy, delete. Each edit reports what it measured:
   length, loop seam, hip bounce, stride and foot slide during contact.
4. **Parts**: each action is an `animation` part (id `…#object=<rig>&action=<name>`,
   with frames, fps, seconds, loop and kind). A target chip of kind Animation routes
   the request to `edit_action`; an action outside the selection is refused.
5. **The animation pass**: the clip names a game plays come from its blueprint
   (`[hooks] animations`) and any `ANIMATION_NAMES` a game script declares.
   `audit_animations` (read-only) compares them with the model's actions;
   `animation_pass` makes the missing ones in one edit.
6. **The Blender tab** (`animation_browser.gd`, owned by the parts panel) lists the
   animations of the shown model, merged by name from the imported AnimationPlayer
   and the parts report, and plays them on the preview by seeking the player itself
   (loop, ping-pong, once; 0.25×–2×), never changing the imported clips.
7. **Review**: each action a change touched (recorded in the stage's
   `.joinery/animation_review.json` with the edit's recipe, so only this change's
   rows count) and each picked one renders in the worker as a 12-frame strip with
   its measurements. The review plays before and after side by side; the facts line
   reads "Run: 0.8 s loop, bounce +30%".
8. **Worker slots**: `make_cycle`, `edit_action` and `animation_pass` run Blender
   (`OP_APPS`).
9. **Players**: the third-person and first-person blueprints get a
   `character_animator.gd` that plays idle, walk, run and the jump clips on the first
   AnimationPlayer under the player, tolerant of import prefixes and case, and does
   nothing without one.

## Consequences

- Motions are stylised and reproducible, not captured: good for prototypes and a
  consistent look; hand-tuning in Blender stays possible on the same actions.
- foot_lock and bounce on a Rigify rig need the action to move the legs through FK
  (every action Joinery makes does); an IK-driven action is refused with the reason.
- Posing evaluates the rig through Blender's depsgraph, so a Rigify human takes about
  20–40 s per motion in the worker.
- The GLB carries every deform bone's channels for every action (about 600 channels
  on an MPFB2 human).
