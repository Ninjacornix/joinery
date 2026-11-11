extends StaticBody3D
## A big push button on a post, used through `Game.request_action(self,
## &"use")`: by the desktop player's interact, or the VR pointer and trigger.
## The cap sinks and lights up; the level opens the glass case.

signal pressed

const OFF_COLOUR := Color(0.8, 0.2, 0.18)
const ON_COLOUR := Color(0.3, 0.8, 0.4)
const TRAVEL := 0.025

@export var save_id: String = "button"

## True once pressed.
var is_pressed: bool = false

var _material: StandardMaterial3D

@onready var cap: MeshInstance3D = $Cap


func _ready() -> void:
	add_to_group(&"saveable")
	add_to_group(&"interactable")
	_material = StandardMaterial3D.new()
	_material.roughness = 0.5
	cap.material_override = _material
	_refresh()


func can_use() -> bool:
	return true


func prompt() -> String:
	return tr("Press the button")


## Runs on the authority (directly offline, on the server online).
func use() -> void:
	_sink()
	if is_pressed:
		return
	is_pressed = true
	_refresh()
	pressed.emit()
	GameEvents.world_changed.emit(self)


func save_state() -> Dictionary:
	return {"pressed": is_pressed}


func load_state(state: Dictionary) -> void:
	var was: bool = is_pressed
	is_pressed = bool(state.get("pressed", is_pressed))
	_refresh()
	if is_pressed and not was:
		pressed.emit()


func _sink() -> void:
	var rest: float = 0.04
	if Settings.reduced_motion():
		return
	var tween: Tween = create_tween()
	tween.tween_property(cap, "position:y", rest - TRAVEL, 0.08)
	tween.tween_property(cap, "position:y", rest, 0.15)


func _refresh() -> void:
	if _material == null:
		return
	var colour: Color = ON_COLOUR if is_pressed else OFF_COLOUR
	_material.albedo_color = colour
	_material.emission_enabled = true
	_material.emission = colour
	_material.emission_energy_multiplier = 0.8 if is_pressed else 0.3
