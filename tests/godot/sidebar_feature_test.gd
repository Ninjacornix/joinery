extends SceneTree
## /feature, /brief and /tools in the sidebar, against a real blueprint game and the real CLI:
## a feature is planned first and changes nothing until Apply; Apply runs `joinery feature`
## through a job. Brief suggestions are offered one line at a time and only ever plan.

const DOCK_PATH := "res://addons/joinery/journeyman_dock.gd"
const MOTION_PATH := "res://addons/joinery/ui_motion.gd"
const CONTROLLER := "res://features/controller/controller.gd"
const ONLINE := "res://features/online-play"

var _failed := false


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _wait(condition: Callable, seconds := 30.0) -> bool:
	var deadline := Time.get_ticks_msec() + int(seconds * 1000)
	while not condition.call() and Time.get_ticks_msec() < deadline:
		await process_frame
	return condition.call()


func _toml() -> String:
	return FileAccess.get_file_as_string("res://joinery.toml")


func _run() -> void:
	load(MOTION_PATH).set_enabled(false)
	root.size = Vector2i(900, 900)
	var chat := load(DOCK_PATH).new() as Control
	root.add_child(chat)
	chat.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	await process_frame
	chat.set_chat_agent("codex")
	chat.set_chat_state("connected", "")
	_check(not FileAccess.file_exists(CONTROLLER), "The game starts without controller support")
	var before := _toml()

	# /feature add controller: a plan card, nothing written.
	chat._on_submit("/feature add controller")
	_check(chat._slash.running() == "/feature", "The plan runs as a job")
	_check(
		await _wait(func() -> bool: return is_instance_valid(chat._plan_card)),
		"/feature add controller shows the plan card"
	)
	var card: Control = chat._plan_card
	var texts: PackedStringArray = card.texts()
	_check(card.title_label.text == "Add controller support", "The plan names the feature")
	_check(texts.has("Nothing changes until you apply it."), "The plan says nothing changes yet")
	_check(texts.has("features/controller/controller.gd"), "The plan lists the files it adds")
	_check(not texts.has("features/controller/controller.gd.uid"), "Sidecar files stay out")
	_check(texts.has("Adds autoload: Controller"), "The plan lists settings in plain words")
	_check(card._apply.text == "Apply" and not card._apply.disabled, "Apply is offered")
	await create_timer(1.0).timeout
	_check(not FileAccess.file_exists(CONTROLLER), "Nothing changes until Apply")
	_check(_toml() == before, "joinery.toml is untouched until Apply")
	_check(not chat._slash._jobs.has("apply"), "Nothing is applied on its own")

	# Apply: `joinery feature add controller` through a job.
	card._apply.pressed.emit()
	_check(chat._slash._jobs.has("apply"), "Apply runs the CLI through a job")
	_check(card.state == "applying" and card._apply.disabled, "Apply waits, with its dots")
	_check(await _wait(func() -> bool: return card.state != "applying", 60.0), "The apply finishes")
	_check(card.state == "applied", "The card says the feature was added")
	_check(FileAccess.file_exists(CONTROLLER), "Apply wrote the feature's files")
	_check(_toml().contains("controller"), "Apply recorded the feature")
	_check(
		not card.transaction_id.is_empty() and card._revert.visible, "The change can be reverted"
	)
	_check(
		card._outcome_text.text.begins_with("Added controller support."),
		"The outcome says what changed"
	)

	# /feature with no words lists what the game has.
	chat._on_submit("/feature")
	_check(
		await _wait(func() -> bool: return chat._slash.running().is_empty()),
		"/feature list finishes"
	)
	var said: String = chat._transcript.notice_texts()[-1]
	_check(said.contains("Controller support"), "/feature lists the features the game has")
	_check(said.contains("online-play"), "/feature lists what can be added")

	# A suggestion from the brief: one line, and it only plans.
	(
		chat
		. _on_suggestions(
			[
				{
					"text": "The brief says online play, but online play is not added.",
					"command": "/feature add online-play"
				},
				{"text": "The brief lists 2 languages.", "command": "/feature add translations"},
			]
		)
	)
	_check(chat._transcript.offer_texts().size() == 1, "Suggestions come one line at a time")
	_check(
		chat._transcript.offer_texts()[0].begins_with("The brief says online play"),
		"The first suggestion is offered first"
	)
	var links: Array = chat._offer_row.find_children("*", "Button", true, false)
	_check(links[0].text == "/feature add online-play", "The offer names its command")
	links[0].pressed.emit()
	await process_frame
	_check(
		chat._transcript.offer_texts() == PackedStringArray(["The brief lists 2 languages."]),
		"Taking an offer shows the next one"
	)
	_check(
		await _wait(
			func() -> bool: return chat._plan_card != card and is_instance_valid(chat._plan_card)
		),
		"The offer plans the feature"
	)
	var online: Control = chat._plan_card
	_check(online.title_label.text == "Add online play", "The plan is for online play")
	await create_timer(0.5).timeout
	_check(not DirAccess.dir_exists_absolute(ONLINE), "An offer never applies anything")
	online._cancel.pressed.emit()
	_check(online.state == "cancelled", "Cancel closes the plan")
	_check(not DirAccess.dir_exists_absolute(ONLINE), "Cancel changes nothing")

	# /brief opens the brief read-only, in the document view.
	chat._on_submit("/brief")
	await process_frame
	var dialogs := chat.find_children("*", "AcceptDialog", true, false)
	_check(dialogs.size() == 1 and dialogs[0].title == "Game brief", "/brief opens the brief")
	if dialogs.size() == 1:
		var body := dialogs[0].find_child("Document", true, false) as RichTextLabel
		_check(body != null and not body.text.is_empty(), "The brief is shown")
		_check(not body.has_method("insert_text_at_caret"), "The brief is read-only")
		dialogs[0].queue_free()

	# Without a brief, /brief offers to write it.
	DirAccess.rename_absolute("res://design/brief.md", "res://design/brief.moved")
	chat._on_submit("/brief")
	_check(
		chat._transcript.offer_texts().has("This project has no game brief yet."),
		"Without a brief, /brief offers to write it"
	)
	DirAccess.rename_absolute("res://design/brief.moved", "res://design/brief.md")

	# /tools in plain sentences, and the header's one line.
	var report := {
		"tools":
		[
			{
				"name": "Godot",
				"summary": "Godot 4.7 is ready",
				"status": "ready",
				"action": null,
				"selected": true
			},
			{
				"name": "Blender",
				"summary":
				"Blender runs offline in Docker. Start Docker Desktop, then check again.",
				"status": "needs_docker",
				"status_label": "Needs Docker",
				"action": {"id": "start_docker", "label": "Start Docker"},
				"selected": true
			},
		]
	}
	var slash: Script = load("res://addons/joinery/slash_commands.gd")
	var reply: String = slash.readiness_reply(report)
	_check(reply.begins_with("Godot 4.7 is ready."), "/tools shows each app's summary")
	_check(reply.contains("Start Docker Desktop."), "/tools says what to do")
	chat._on_readiness(report)
	_check(
		chat._header.readiness_text() == "Blender needs Docker. Start Docker Desktop.",
		"The header names the app that needs something, with its action"
	)
	_check(chat._header._readiness_action.text == "Check again", "The header offers Check again")
	chat._on_readiness({"tools": [report["tools"][0]]})
	await create_timer(0.3).timeout
	_check(chat._header.readiness_text().is_empty(), "The line leaves once every app is ready")

	chat.queue_free()
	await process_frame
	print("sidebar features: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)
