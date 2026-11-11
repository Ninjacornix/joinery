@tool
extends Node
## The design check from the sidebar: "/design", or Design check in Settings. Joinery's checks
## are `joinery design --json`, run off the main thread with no model; the card
## (design_card.gd) sits with the agents at work. "Look deeper" asks the chat host for the
## designer agent's pass (chat_bridge.gd's send_design); its findings arrive in a design event
## with Joinery's. "Ask agent to fix" puts the finding's request in the composer, ready to edit
## or send as a normal reviewed request: nothing is ever fixed by itself.
##
## With `[agents] design_check_every` set (Settings), the host checks after every N accepted
## changes and the card shows when that check finds something.

const CARD := preload("design_card.gd")
const UI_MOTION := preload("ui_motion.gd")
const COMMAND_SCRIPT := preload("command_job.gd")
const SLASH := preload("slash_commands.gd")

## The sidebar (journeyman_dock.gd) this serves.
var dock: Control
## The task list (sidebar_tasks.gd): the card sits in it, and its bridge is the chat's.
var tasks: Object
## The design card, while there is one.
var card: Control
## Runs `joinery design`; tests replace it with Callable(args) -> Dictionary (the JSON).
var check_runner := Callable()
var _job: RefCounted
var _bridge: Object
## The host's run this card follows ("" for the CLI's own check).
var _design_id := ""


static func attach(sidebar: Control, list: Object) -> Node:
	var designer: Node = load("res://addons/joinery/sidebar_design.gd").new()
	designer.set("dock", sidebar)
	designer.set("tasks", list)
	(list as Node).add_child(designer)
	var slash: Object = sidebar.get("_slash")
	if slash != null and slash.has_signal("design_requested"):
		slash.connect("design_requested", designer.open)
	return designer


func _init() -> void:
	name = "Design"
	set_process(false)


## Show the card and check the project (again).
func open() -> void:
	_listen()
	_ensure_card()
	check()


## Joinery's checks: `joinery design --project <this project> --json`.
func check() -> void:
	if not is_instance_valid(card) or _job != null:
		return
	card.call("set_checking")
	var args := PackedStringArray(
		["run", "--frozen", "--quiet", "--project", SLASH.engine_dir(), "joinery", "design"]
	)
	args.append_array(["--project", ProjectSettings.globalize_path("res://").rstrip("/"), "--json"])
	if check_runner.is_valid():
		_take(check_runner.call(args))
		return
	_job = COMMAND_SCRIPT.new()
	if _job.start("uv", args) != OK:
		_job = null
		card.call("apply_failed", "Couldn't start Joinery.")
		return
	set_process(true)


## "Look deeper": the designer agent reads the game too (the chat host's design_start).
func deeper() -> void:
	_listen()
	if _bridge == null or not _bridge.has_method("send_design"):
		dock.call("show_status", "Journeyman isn't connected.", 3.0)
		return
	if not _bridge.call("send_design", true):
		dock.call("show_status", "Connect Journeyman to look deeper.", 3.0)
		return
	card.call("set_reading")


## "Ask agent to fix": the finding's request in the composer, for the user to send.
func ask(finding: Dictionary) -> void:
	var request := str(finding.get("request", ""))
	if request.is_empty():
		request = "Fix this design problem: %s" % str(finding.get("title", ""))
	tasks.call("prefill", request)


func close() -> void:
	var leaving := card
	card = null
	tasks.set("design_card", null)
	if not is_instance_valid(leaving):
		return
	UI_MOTION.exit(
		leaving,
		null,
		func() -> void:
			leaving.queue_free()
			tasks.call("sync_visible")
	)


func _ensure_card() -> void:
	if is_instance_valid(card):
		return
	card = CARD.new()
	card.connect("fix_requested", ask)
	card.connect("deeper_requested", deeper)
	card.connect("recheck_requested", check)
	card.connect("stop_pressed", _stop)
	card.connect("close_pressed", close)
	(tasks as Node).add_child(card)
	tasks.set("design_card", card)
	(tasks as Control).show()
	UI_MOTION.enter(card)


func _process(_delta: float) -> void:
	if _job == null:
		set_process(false)
		return
	var result: Dictionary = _job.poll()
	if result.is_empty():
		return
	_job = null
	set_process(false)
	var output := str(result.get("output", ""))
	if int(result.get("code", 1)) != 0:
		print("[Joinery] design\n", output, "\n", result.get("error", ""))
	_take(SLASH.parse_json(output))


func _take(data: Dictionary) -> void:
	if not is_instance_valid(card):
		return
	if not data.has("findings"):
		card.call(
			"apply_failed",
			str(data.get("error", "Couldn't check the design. Details are in Output."))
		)
		return
	_log_skipped(data)
	card.call("show_report", data, false)


func _listen() -> void:
	var bridge: Object = tasks.get("bridge") if tasks != null else null
	if bridge == _bridge or bridge == null:
		return
	_bridge = bridge
	if bridge.has_signal("design_event") and not bridge.is_connected("design_event", _on_design):
		bridge.connect("design_event", _on_design)


## One event from the host: {design_id, origin, deep, state, report, dropped, message}.
func _on_design(event: Dictionary) -> void:
	var state := str(event.get("state", ""))
	var data: Variant = event.get("report")
	var findings: Array = (data as Dictionary).get("findings", []) if data is Dictionary else []
	if str(event.get("origin", "")) == "after_change" and not is_instance_valid(card):
		# A check after accepted changes shows only when it found something.
		if state != "done" or findings.is_empty():
			return
	_ensure_card()
	_design_id = str(event.get("design_id", ""))
	match state:
		"checking":
			card.call("set_checking")
		"reading":
			if data is Dictionary:
				card.call("show_report", data, true)
			card.call("set_reading")
		"done":
			if data is Dictionary:
				_log_skipped(data)
				card.call(
					"show_report",
					data,
					bool(event.get("deep", false)),
					int(event.get("dropped", 0))
				)
			var message := str(event.get("message", ""))
			if not message.is_empty():
				print("[Joinery] design: ", message)
		"failed":
			card.call("apply_failed", str(event.get("message", "The design check didn't run.")))
		"stopped":
			if (
				card.get("report") is Dictionary
				and not (card.get("report") as Dictionary).is_empty()
			):
				card.call("show_report", card.get("report"), false)
			else:
				card.call("apply_failed", "Stopped.")


func _stop() -> void:
	if _bridge != null and _bridge.has_method("send_design"):
		_bridge.call("send_design", false, true, _design_id)


func _log_skipped(data: Dictionary) -> void:
	for line: Variant in data.get("skipped", []):
		print("[Joinery] design: not checked: ", line)
