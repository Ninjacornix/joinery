extends Node
## Camera rigs' shared hooks.
##
##   Cameras.shake(0.6)        # an explosion: every rig shakes (0-1, adds up to 1)
##   Cameras.current_rig()     # the rig the player sees, or null
##
## Shake follows the player's settings: it is scaled by the Screen shake slider
## (Settings.screen_shake(), on the Accessibility page) and is off with reduced
## motion. Rigs are CameraRig2D and CameraRig3D (camera_rig_2d.gd, camera_rig_3d.gd).

signal shaken(strength: float)

const GROUP := &"camera_rigs"


## Shakes every rig by `strength` (0-1).
func shake(strength: float = 0.5) -> void:
	for rig: Node in get_tree().get_nodes_in_group(GROUP):
		if rig.has_method(&"add_trauma"):
			rig.call(&"add_trauma", strength)
	shaken.emit(strength)


## How strongly shake shows: the screen shake setting, 0 with reduced motion.
func shake_scale() -> float:
	if Settings.reduced_motion():
		return 0.0
	return Settings.screen_shake()


func rigs() -> Array[Node]:
	return get_tree().get_nodes_in_group(GROUP)


func current_rig() -> Node:
	for rig: Node in rigs():
		if (
			(rig is Camera2D and (rig as Camera2D).is_current())
			or (rig is Camera3D and (rig as Camera3D).current)
		):
			return rig
	return null
