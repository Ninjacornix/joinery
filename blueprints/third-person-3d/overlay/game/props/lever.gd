extends AnimatableBody3D
## A lever. Interacting flips it and switches every target mover. It is an
## AnimatableBody3D so interaction areas see it (Jolt areas skip static bodies).

signal switched(on: bool)

const OFF_ANGLE := 0.6
const OFF_COLOUR := Color("#b04a3a")
const ON_COLOUR := Color("#4fb06d")

@export var save_id: String = "lever"
@export var targets: Array[NodePath] = []

var on: bool = false

@onready var handle: Node3D = $Handle
@onready var lamp: MeshInstance3D = $Lamp


func _ready() -> void:
	_show_state()


func interact_prompt() -> String:
	return tr("Pull lever")


func interact() -> void:
	_set_on(not on, false)
	GameEvents.world_changed.emit(self)


func save_state() -> Dictionary:
	return {"on": on}


func load_state(state: Dictionary) -> void:
	_set_on(bool(state.get("on", false)), true)


func _set_on(value: bool, instant: bool) -> void:
	on = value
	_show_state()
	for path: NodePath in targets:
		var target: Node = get_node_or_null(path)
		if target != null and target.has_method(&"set_active"):
			target.call(&"set_active", on, instant)
	switched.emit(on)


func _show_state() -> void:
	handle.rotation.x = -OFF_ANGLE if on else OFF_ANGLE
	var material: StandardMaterial3D = StandardMaterial3D.new()
	material.albedo_color = ON_COLOUR if on else OFF_COLOUR
	material.emission_enabled = true
	material.emission = material.albedo_color
	material.emission_energy_multiplier = 0.6
	lamp.material_override = material
