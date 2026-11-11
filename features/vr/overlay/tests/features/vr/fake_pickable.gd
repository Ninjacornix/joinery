extends Node3D
## A stand-in pickable for the hand checks: records grab and release.

var holder: Node3D = null
var allowed: bool = true
var grabs: int = 0
var last_velocity: Vector3 = Vector3.ZERO


func _ready() -> void:
	add_to_group(&"pickable")


func can_grab() -> bool:
	return allowed and holder == null


func grab(by: Node3D, _snap: bool = false) -> bool:
	if not can_grab():
		return false
	holder = by
	grabs += 1
	return true


func release(velocity: Vector3 = Vector3.ZERO) -> void:
	holder = null
	last_velocity = velocity
