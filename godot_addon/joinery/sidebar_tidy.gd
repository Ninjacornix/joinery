@tool
extends Node
## "/tidy" from the sidebar, Unused content in Settings, or Tidy on the Review tab's Game
## checks row. The check is `joinery tidy --json`, run off the main thread with no model; the
## card (tidy_card.gd) sits with the agents at work.
##
## Remove sends `tidy_remove` to the chat host (chat_bridge.gd's send_tidy), which makes the
## removal one change in Review: nothing is deleted until the user accepts it there, and Discard
## keeps everything. Keep writes `[tidy] keep` through `joinery tidy --keep`. "Free … of
## Joinery caches" runs `joinery tidy --clear-caches`: Joinery's own caches, no review.

const CARD := preload("tidy_card.gd")
const UI_MOTION := preload("ui_motion.gd")
const COMMAND_SCRIPT := preload("command_job.gd")
const SLASH := preload("slash_commands.gd")
const NOT_CONNECTED := "Connect Journeyman to remove: the removal is a change you review."

## The sidebar (journeyman_dock.gd) this serves.
var dock: Control
## The task list (sidebar_tasks.gd): the card sits in it, and its bridge is the chat's.
var tasks: Object
## The tidy card, while there is one.
var card: Control
## Runs `joinery tidy …`; tests replace it with Callable(args) -> Dictionary (the JSON).
var runner := Callable()
## Running CLI jobs: {job: Callable(data)}.
var _jobs: Dictionary = {}


static func attach(sidebar: Control, list: Object) -> Node:
	var tidier: Node = load("res://addons/joinery/sidebar_tidy.gd").new()
	tidier.set("dock", sidebar)
	tidier.set("tasks", list)
	(list as Node).add_child(tidier)
	var slash: Object = sidebar.get("_slash")
	if slash != null and slash.has_signal("tidy_requested"):
		slash.connect("tidy_requested", tidier.open)
	return tidier


func _init() -> void:
	name = "Tidy"
	set_process(false)


## Show the card and check the project (again).
func open() -> void:
	_ensure_card()
	check()


## `joinery tidy --project <this project> --json`.
func check() -> void:
	if not is_instance_valid(card):
		return
	card.call("set_checking")
	_cli(["--json"], _take)


## Remove: one change in Review, made by the chat host.
func remove(ids: Array) -> void:
	var bridge: Object = tasks.get("bridge") if tasks != null else null
	if bridge == null or not bridge.has_method("send_tidy") or not bridge.call("send_tidy", ids):
		card.call("restore", ids, NOT_CONNECTED)
		return
	card.call("set_sent", ids)


## Keep: `[tidy] keep` in joinery.toml, the user's own list.
func keep(finding: Dictionary) -> void:
	var id := str(finding.get("id", ""))
	_cli(
		["--keep", str(finding.get("key", id)), "--json"],
		func(data: Dictionary) -> void:
			if data.has("error") and is_instance_valid(card):
				card.call("restore", [id], "Couldn't keep it: " + str(data["error"]))
	)


## Joinery's own caches: cleared at once, and the card says what went.
func free_caches() -> void:
	_cli(
		["--clear-caches", "--json"],
		func(data: Dictionary) -> void:
			if not is_instance_valid(card):
				return
			if data.has("error"):
				card.call("apply_failed", str(data["error"]))
				return
			card.call("show_caches", str(data.get("message", "Cleared Joinery's caches.")))
	)


func close() -> void:
	var leaving := card
	card = null
	tasks.set("tidy_card", null)
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
	card.connect("remove_requested", remove)
	card.connect("keep_requested", keep)
	card.connect("caches_requested", free_caches)
	card.connect("recheck_requested", check)
	card.connect("close_pressed", close)
	(tasks as Node).add_child(card)
	tasks.set("tidy_card", card)
	(tasks as Control).show()
	UI_MOTION.enter(card)


func _cli(extra: Array, done: Callable) -> void:
	var args := PackedStringArray(
		["run", "--frozen", "--quiet", "--project", SLASH.engine_dir(), "joinery", "tidy"]
	)
	args.append_array(["--project", ProjectSettings.globalize_path("res://").rstrip("/")])
	for item: Variant in extra:
		args.append(str(item))
	if runner.is_valid():
		done.call(runner.call(args))
		return
	var job: RefCounted = COMMAND_SCRIPT.new()
	if job.start("uv", args) != OK:
		done.call({"error": "Couldn't start Joinery."})
		return
	_jobs[job] = done
	set_process(true)


func _process(_delta: float) -> void:
	for job: RefCounted in _jobs.keys():
		var result: Dictionary = job.poll()
		if result.is_empty():
			continue
		var done: Callable = _jobs[job]
		_jobs.erase(job)
		var output := str(result.get("output", ""))
		if int(result.get("code", 1)) != 0:
			print("[Joinery] tidy\n", output, "\n", result.get("error", ""))
		var data := SLASH.parse_json(output)
		if data.is_empty():
			data = {"error": "Joinery didn't answer. Details are in Output."}
		done.call(data)
	set_process(not _jobs.is_empty())


func _take(data: Dictionary) -> void:
	if not is_instance_valid(card):
		return
	if not data.has("findings"):
		card.call(
			"apply_failed",
			str(data.get("error", "Couldn't look for unused content. Details are in Output."))
		)
		return
	card.call("show_report", data)
