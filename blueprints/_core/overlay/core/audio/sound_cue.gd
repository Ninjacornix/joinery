class_name SoundCue
extends Node
## Plays a sound when a signal on its parent fires: put one under a coin with
## `trigger = &"collected"`, under a door with `trigger = &"opened"`.
##
## When the signal's first argument is a Node it must be in `only_group`
## (empty = any), so a coin's `body_entered` only sounds for the player. The
## sound is positional (AudioStreamPlayer3D/2D) when the parent is a
## Node3D/Node2D, and it is played from beside the parent, so it keeps playing
## when the parent frees itself straight after the signal. Silent until it is
## given a `stream`.

signal played(player: Node)

@export var stream: AudioStream
@export var bus: StringName = &"SFX"
## A signal on the parent node.
@export var trigger: StringName = &""
@export var only_group: StringName = &"player"
@export_range(-60.0, 12.0, 0.1, "suffix:dB") var volume_db: float = 0.0
## Each play picks a pitch in 1 ± this.
@export_range(0.0, 0.5, 0.01) var pitch_jitter: float = 0.05


func _ready() -> void:
	var parent: Node = get_parent()
	if trigger.is_empty() or parent == null:
		return
	if not parent.has_signal(trigger):
		push_warning("SoundCue: %s has no signal %s." % [parent.name, trigger])
		return
	parent.connect(trigger, _on_trigger)


## Plays the cue now. Returns the player node (it frees itself when done), or
## null when there is no stream.
func play() -> Node:
	if stream == null or not is_inside_tree():
		return null
	var parent: Node = get_parent()
	var player: Node
	if parent is Node3D:
		var player_3d: AudioStreamPlayer3D = AudioStreamPlayer3D.new()
		player_3d.volume_db = volume_db
		player_3d.pitch_scale = _pitch()
		player_3d.bus = bus
		player_3d.stream = stream
		player = player_3d
	elif parent is Node2D:
		var player_2d: AudioStreamPlayer2D = AudioStreamPlayer2D.new()
		player_2d.volume_db = volume_db
		player_2d.pitch_scale = _pitch()
		player_2d.bus = bus
		player_2d.stream = stream
		player = player_2d
	else:
		var flat: AudioStreamPlayer = AudioStreamPlayer.new()
		flat.volume_db = volume_db
		flat.pitch_scale = _pitch()
		flat.bus = bus
		flat.stream = stream
		player = flat
	player.name = "SoundCue_%s" % name
	_host(parent).add_child(player)
	if parent is Node3D:
		(player as Node3D).global_transform = (parent as Node3D).global_transform
	elif parent is Node2D:
		(player as Node2D).global_position = (parent as Node2D).global_position
	player.connect(&"finished", player.queue_free)
	player.call(&"play")
	played.emit(player)
	return player


func _on_trigger(...args: Array) -> void:
	if not only_group.is_empty() and not args.is_empty() and args[0] is Node:
		if not (args[0] as Node).is_in_group(only_group):
			return
	play()


## Where the player lives: beside the parent (same world, outlives it), or
## the scene root when the parent has no parent of its own.
func _host(parent: Node) -> Node:
	var host: Node = parent.get_parent() if parent != null else null
	while host != null and host.is_queued_for_deletion():
		host = host.get_parent()
	return host if host != null else get_tree().root


func _pitch() -> float:
	return 1.0 + randf_range(-pitch_jitter, pitch_jitter)
