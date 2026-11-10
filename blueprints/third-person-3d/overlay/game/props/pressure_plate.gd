extends Area3D
## A plate that holds its targets open while a prop rests on it.

signal pressed_changed(pressed: bool)

const UP_COLOUR := Color("#8a6d3b")
const DOWN_COLOUR := Color("#4fb06d")

@export var targets: Array[NodePath] = []

var pressed: bool = false
var _count: int = 0

@onready var top: MeshInstance3D = $Top


func _ready() -> void:
	body_entered.connect(_on_body_entered)
	body_exited.connect(_on_body_exited)
	_show_state()


func _on_body_entered(body: Node3D) -> void:
	if body.is_in_group(&"carryable"):
		_count += 1
		_update()


func _on_body_exited(body: Node3D) -> void:
	if body.is_in_group(&"carryable"):
		_count = maxi(_count - 1, 0)
		_update()


func _update() -> void:
	var now: bool = _count > 0
	if now == pressed:
		return
	pressed = now
	_show_state()
	for path: NodePath in targets:
		var target: Node = get_node_or_null(path)
		if target != null and target.has_method(&"set_active"):
			target.call(&"set_active", pressed, false)
	pressed_changed.emit(pressed)


func _show_state() -> void:
	top.position.y = 0.03 if pressed else 0.08
	var material: StandardMaterial3D = StandardMaterial3D.new()
	material.albedo_color = DOWN_COLOUR if pressed else UP_COLOUR
	top.material_override = material
