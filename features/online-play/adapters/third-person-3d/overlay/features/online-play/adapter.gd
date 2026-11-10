extends NetAdapter
## Online play for the third-person arena. Coins, the lever, the checkpoint
## and the crate replicate through the game core, and the crate's position
## comes from the server (it is in group `net_synced`). A joining player's
## copy of the crate must not run its own physics as well, or it would fight
## the positions the server sends, so it is held kinematic there and only
## follows them, also after it is carried and put down.


func _setup() -> void:
	if server:
		return
	for node: Node in level.get_tree().get_nodes_in_group(&"carryable"):
		if in_level(node) and node is RigidBody3D:
			_follow_server(node as RigidBody3D)


func _follow_server(body: RigidBody3D) -> void:
	body.freeze_mode = RigidBody3D.FREEZE_MODE_KINEMATIC
	body.freeze = true
	if body.has_signal(&"carried_changed"):
		body.connect(&"carried_changed", _keep_frozen.bind(body))


func _keep_frozen(_carrier: Node3D, body: RigidBody3D) -> void:
	if is_instance_valid(body):
		body.freeze = true
