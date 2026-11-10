extends Area3D
## A checkpoint pad. Stepping on it makes it the respawn point.

signal reached(checkpoint: Node)

const IDLE_COLOUR := Color("#7d838c")
const ACTIVE_COLOUR := Color("#4fb06d")

@export var checkpoint_id: String = "checkpoint"

var active: bool = false

@onready var respawn_point: Marker3D = $Respawn
@onready var flag: MeshInstance3D = $Flag


func _ready() -> void:
	body_entered.connect(_on_body_entered)
	_update_look()


func set_active(on: bool) -> void:
	active = on
	_update_look()


func respawn_transform() -> Transform3D:
	return respawn_point.global_transform


func _update_look() -> void:
	var material: StandardMaterial3D = StandardMaterial3D.new()
	material.albedo_color = ACTIVE_COLOUR if active else IDLE_COLOUR
	flag.material_override = material


func _on_body_entered(body: Node3D) -> void:
	if body.is_in_group(&"player") and body.is_multiplayer_authority():
		reached.emit(self)
