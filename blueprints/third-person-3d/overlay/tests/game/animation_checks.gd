extends RefCounted
## Checks that the player's Animator plays the character model's clips: idle,
## walk, run, jump_start / jump_air and jump_land. Builds a tiny model in code
## (a skeleton and an AnimationPlayer whose clip names carry import prefixes and
## loop suffixes), drives the player with real input, then removes the model.

const SHORT := 0.2

var h: PlayHarness
var tree: SceneTree


func _init(harness: PlayHarness, scene_tree: SceneTree) -> void:
	h = harness
	tree = scene_tree


## [param place] stages the player standing still on open floor.
func run(player: CharacterBody3D, place: Callable) -> void:
	var animator: Node = player.get_node_or_null(^"Animator")
	h.expect(animator != null, "the player has an Animator")
	if animator == null:
		return
	h.expect(animator.get(&"current") == &"", "without a model's clips the Animator plays nothing")
	var model: Node3D = build_model()
	player.get_node(NodePath(str(animator.get(&"PLACE")))).add_child(model)
	h.expect(bool(animator.call(&"find_player")), "the Animator finds the model's AnimationPlayer")
	place.call()
	# A fall onto the floor plays jump_land first.
	await h.physics_frames(40)
	h.expect(animator.get(&"current") == &"idle", "standing still plays idle")
	h.press(&"move_forward")
	await h.physics_frames(25)
	h.expect(animator.get(&"current") == &"walk", "moving plays walk")
	h.press(&"sprint")
	await h.physics_frames(25)
	h.expect(animator.get(&"current") == &"run", "sprinting plays run")
	h.release(&"sprint")
	h.release(&"move_forward")
	await h.physics_frames(40)
	h.expect(animator.get(&"current") == &"idle", "stopping plays idle again")
	var seen: Array[StringName] = []
	h.press(&"jump")
	for i: int in 150:
		await tree.physics_frame
		if i == 20:
			h.release(&"jump")
		var clip: StringName = animator.get(&"current")
		if seen.is_empty() or seen[-1] != clip:
			seen.append(clip)
	h.release(&"jump")
	h.expect(seen.has(&"jump_start"), "a jump starts with jump_start (%s)" % [seen])
	h.expect(seen.has(&"jump_air"), "jump_air plays in the air (%s)" % [seen])
	h.expect(seen.has(&"jump_land"), "jump_land plays on landing (%s)" % [seen])
	h.expect(seen[-1] == &"idle", "the player is idle again after landing (%s)" % [seen])
	var loops: AnimationPlayer = model.get_node(^"AnimationPlayer") as AnimationPlayer
	h.expect(loops.get_animation(&"walk_loop").loop_mode == Animation.LOOP_LINEAR, "walk loops")
	model.get_parent().remove_child(model)
	model.free()
	h.expect(not bool(animator.call(&"find_player")), "without a model the Animator stops")
	await h.physics_frames(5)
	h.expect(animator.get(&"current") == &"", "and plays nothing")


## A one-bone model with clips named the ways glTF imports name them.
static func build_model() -> Node3D:
	var model := Node3D.new()
	model.name = "TestModel"
	var skeleton := Skeleton3D.new()
	skeleton.name = "Skeleton3D"
	skeleton.add_bone("Root")
	model.add_child(skeleton)
	var player := AnimationPlayer.new()
	player.name = "AnimationPlayer"
	model.add_child(player)
	var library := AnimationLibrary.new()
	var names: Array[String] = [
		"Idle", "walk_loop", "Armature|Run", "jump_start", "jump_air", "Jump_Land"
	]
	for name: String in names:
		var clip := Animation.new()
		clip.length = SHORT if name.to_lower().begins_with("jump_") else 1.0
		var track: int = clip.add_track(Animation.TYPE_ROTATION_3D)
		clip.track_set_path(track, NodePath("Skeleton3D:Root"))
		clip.rotation_track_insert_key(track, 0.0, Quaternion.IDENTITY)
		clip.rotation_track_insert_key(track, clip.length, Quaternion(Vector3.UP, 0.5))
		library.add_animation(StringName(name), clip)
	player.add_animation_library(&"", library)
	return model
