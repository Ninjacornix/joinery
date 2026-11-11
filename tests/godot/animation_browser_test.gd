extends SceneTree
## The animation browser (animation_browser.gd) on a model built in code: a mesh, a skeleton and
## an AnimationPlayer with three animations, shown in a model_preview the way the Blender tab
## shows an imported GLB, merged with a `joinery parts` report that adds the source's facts and
## an action the GLB lacks. Checks cycling with the arrow keys, scrubbing to a frame (the model
## is posed there), the loop modes, and that picking a row or "Use in message" sends the action
## as a target (kind Animation, with the model and the action).

const BROWSER := preload("res://addons/joinery/animation_browser.gd")
const MODEL := preload("res://addons/joinery/model_preview.gd")
const RESOURCE := "res://art/models/hero.glb"

var _failed := false


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


## Hero: a box that rises over each animation, under a skeleton, with an AnimationPlayer.
static func hero_scene() -> PackedScene:
	var hero := Node3D.new()
	hero.name = "Hero"
	var skeleton := Skeleton3D.new()
	skeleton.name = "Skeleton3D"
	skeleton.add_bone("hips")
	hero.add_child(skeleton)
	skeleton.owner = hero
	var body := MeshInstance3D.new()
	body.name = "Body"
	body.mesh = BoxMesh.new()
	hero.add_child(body)
	body.owner = hero
	var player := AnimationPlayer.new()
	player.name = "AnimationPlayer"
	hero.add_child(player)
	player.owner = hero
	var library := AnimationLibrary.new()
	for spec: Array in [["idle", 2.0, true], ["run", 0.5, true], ["wave", 1.2, false]]:
		var animation := Animation.new()
		animation.length = spec[1]
		animation.loop_mode = Animation.LOOP_LINEAR if spec[2] else Animation.LOOP_NONE
		var track := animation.add_track(Animation.TYPE_VALUE)
		animation.track_set_path(track, "Body:position")
		animation.track_insert_key(track, 0.0, Vector3.ZERO)
		animation.track_insert_key(track, spec[1], Vector3(0, 1, 0))
		library.add_animation(spec[0], animation)
	player.add_animation_library("", library)
	var scene := PackedScene.new()
	scene.pack(hero)
	hero.free()
	return scene


static func report() -> Dictionary:
	var parts: Array = []
	for spec: Array in [["run", "1,13", "0.5", "true"], ["jump_air", "1,15", "0.583", "true"]]:
		(
			parts
			. append(
				{
					"id": "%s#object=HeroRig&action=%s" % [RESOURCE, spec[0]],
					"kind": "Animation",
					"part_kind": "animation",
					"resource": RESOURCE,
					"source": "res://art/sources/hero.blend",
					"owner": "blender",
					"owner_source": "art/sources/hero.blend",
					"object": "HeroRig",
					"action": spec[0],
					"frames": spec[1],
					"fps": "24",
					"seconds": spec[2],
					"loop": spec[3],
					"animation_kind": spec[0],
					"ops": "edit_action,make_cycle",
				}
			)
		)
	return {"resource": RESOURCE, "source": "art/sources/hero.blend", "parts": parts}


func _key(code: Key) -> void:
	var event := InputEventKey.new()
	event.keycode = code
	event.physical_keycode = code
	event.pressed = true
	root.push_input(event)
	var release := event.duplicate() as InputEventKey
	release.pressed = false
	root.push_input(release)


func _run() -> void:
	root.size = Vector2i(900, 700)
	var box := VBoxContainer.new()
	root.add_child(box)
	box.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	var viewer: MODEL = MODEL.new()
	box.add_child(viewer)
	viewer.show_scene(hero_scene())
	var browser: BROWSER = BROWSER.new()
	box.add_child(browser)
	var picked: Array[Dictionary] = []
	browser.part_selected.connect(func(target: Dictionary) -> void: picked.append(target))
	browser.attach(viewer, RESOURCE)
	await process_frame
	_check(browser.visible, "A model with animations shows the browser")
	_check(browser.rows().size() == 3, "Three animations from the GLB")
	browser.set_report(report())
	var names: Array = browser.rows().map(func(row: Dictionary) -> String: return row["name"])
	_check(names == ["idle", "run", "wave", "jump_air"], "Merged by name: %s" % [names])
	_check(browser.current_name() == "idle", "Starts on the first: " + browser.current_name())
	var facts := browser.tree.get_root().get_child(1).get_text(1)
	_check(facts == "0.50 s · Loop · 24 fps", "Run's facts: " + facts)
	facts = browser.tree.get_root().get_child(3).get_text(1)
	_check(facts.ends_with("Not in the game yet"), "Source only: " + facts)
	# Arrow keys cycle while focus is inside.
	browser.tree.grab_focus()
	_key(KEY_RIGHT)
	await process_frame
	_check(browser.current_name() == "run", "Right goes to run: " + browser.current_name())
	_key(KEY_LEFT)
	_key(KEY_LEFT)
	await process_frame
	_check(browser.current_name() == "jump_air", "Left wraps: " + browser.current_name())
	_check(browser.play_button.disabled, "An action the GLB lacks has nothing to play")
	browser.step(-2)
	_check(browser.current_name() == "run", "step(-2) from jump_air: " + browser.current_name())
	_check(picked.is_empty(), "Cycling only previews")
	# Scrubbing poses the model and names the frame (24 fps from the source).
	browser.scrub.value = 0.25
	_check(browser.current_frame() == 7, "Frame 7 at 0.25 s: %d" % browser.current_frame())
	_check(browser.frame_count() == 13, "13 frames: %d" % browser.frame_count())
	var body: Node3D = viewer.model.get_node("Body")
	_check(is_equal_approx(body.position.y, 0.5), "Posed halfway: %.3f" % body.position.y)
	_check(browser._frame_label.text == "frame 7 / 13", browser._frame_label.text)
	browser.seek_frame(13)
	_check(is_equal_approx(browser.playhead, 0.5), "Last frame is the end")
	# Playback: loop wraps, ping-pong turns, once stops at the end.
	browser.set_speed(2.0)
	browser.seek(0.4)
	browser.set_playing(true)
	browser._process(0.1)
	_check(is_equal_approx(browser.playhead, 0.1), "Loop wraps: %.3f" % browser.playhead)
	browser.set_mode("ping_pong")
	browser.playhead = 0.45
	browser._process(0.05)
	_check(is_equal_approx(browser.playhead, 0.45), "Ping-pong turns: %.3f" % browser.playhead)
	_check(browser._direction < 0.0, "and plays backwards")
	browser.set_mode("once")
	browser.set_speed(1.0)
	browser._process(1.0)
	_check(not browser.playing and is_equal_approx(browser.playhead, 0.5), "Once stops")
	_check(browser.speed_button.text == "1×", browser.speed_button.text)
	browser.set_speed(0.25)
	_check(browser.speed_button.text == "0.25×", browser.speed_button.text)
	# Use in message: the report's row, as a chip target.
	browser.use_button.pressed.emit()
	_check(picked.size() == 1, "Use in message sends one target")
	var target: Dictionary = picked[-1] if not picked.is_empty() else {}
	_check(target.get("part_kind") == "animation", "An animation target")
	_check(target.get("kind") == "Animation", "Kind Animation: %s" % target.get("kind"))
	_check(target.get("action") == "run" and target.get("object") == "HeroRig", str(target))
	_check(target.get("label") == "run · Hero", "Label: %s" % target.get("label"))
	_check(str(target.get("ops")).begins_with("edit_action"), "Routes to edit_action")
	_check(
		str(target.get("tooltip")).begins_with("Hero › HeroRig › run"), str(target.get("tooltip"))
	)
	# A click on a row picks that action; one the source does not list still works.
	browser.show_animation(0)
	browser.tree.item_mouse_selected.emit(Vector2.ZERO, MOUSE_BUTTON_LEFT)
	target = picked[-1]
	_check(target.get("action") == "idle", "Click picks idle")
	_check(str(target.get("id")) == RESOURCE + "#action=idle", "Id: %s" % target.get("id"))
	_check(target.get("resource") == RESOURCE and target.get("loop") == "true", str(target))
	# Another preview replaces this one: nothing to list.
	box.remove_child(viewer)
	viewer.queue_free()
	await process_frame
	_check(not browser.visible, "Hidden once the model is gone")
	box.queue_free()
	await process_frame
	print("animation browser: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)
