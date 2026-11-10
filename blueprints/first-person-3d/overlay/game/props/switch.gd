extends StaticBody3D
## A wall switch the player uses through `Game.request_action(self, &"use")`.
## Red when off, green when on. Locks once the door it feeds has opened.

signal toggled(on: bool)

const OFF_COLOUR := Color(0.78, 0.22, 0.2)
const ON_COLOUR := Color(0.25, 0.75, 0.35)
const LOCKED_COLOUR := Color(0.35, 0.55, 0.4)

@export var save_id: String = "switch"
@export var on: bool = false:
	set(value):
		on = value
		_refresh()

var locked: bool = false:
	set(value):
		locked = value
		_refresh()

var _material: StandardMaterial3D


func _ready() -> void:
	add_to_group(&"saveable")
	add_to_group(&"interactable")
	add_to_group(&"fp_switch")
	var lamp: MeshInstance3D = $Lamp
	_material = StandardMaterial3D.new()
	_material.roughness = 0.6
	lamp.material_override = _material
	_refresh()


func can_use() -> bool:
	return not locked


func prompt() -> String:
	return tr("Turn the switch off") if on else tr("Turn the switch on")


## Runs on the authority (directly offline, on the server online).
func use() -> void:
	if locked:
		return
	on = not on
	toggled.emit(on)
	GameEvents.world_changed.emit(self)


func save_state() -> Dictionary:
	return {"on": on, "locked": locked}


func load_state(state: Dictionary) -> void:
	var was_on: bool = on
	on = bool(state.get("on", on))
	locked = bool(state.get("locked", locked))
	if on != was_on:
		toggled.emit(on)


func _refresh() -> void:
	if _material == null:
		return
	var colour: Color = OFF_COLOUR
	if on:
		colour = LOCKED_COLOUR if locked else ON_COLOUR
	_material.albedo_color = colour
	_material.emission_enabled = on
	_material.emission = colour
	_material.emission_energy_multiplier = 0.6
