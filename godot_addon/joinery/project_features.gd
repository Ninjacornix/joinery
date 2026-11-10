@tool
extends VBoxContainer
## "Game features" in Project apps: one switch per feature the game has or could add.
##
## The list comes from `joinery feature list --json` (the project's blueprint, its features and
## what it can add) and `joinery features --json` (names and summaries). Turning a switch
## asks `joinery feature add|remove <id> --plan --json` first and shows what would change;
## only Apply runs the change (`--json --yes`), so nothing in the project moves until the user
## says so. Cancel puts the switch back. Every command is a command_job.gd job.

signal applied(message: String)

const COMMAND_SCRIPT := preload("command_job.gd")
const INDICATOR := preload("activity_indicator.gd")
const UI_COMPONENTS := preload("ui_components.gd")
const UI_MOTION := preload("ui_motion.gd")
## How many changed files a plan lists before "and N more".
const PLAN_FILES := 8

## The Joinery CLI and its leading arguments (see project_setup.gd); empty: `uv run … joinery`.
var cli_prefix := PackedStringArray()
## The project folder the commands act on.
var project := ""

var _body: VBoxContainer
var _rows: Dictionary = {}
var _names: Dictionary = {}
var _game: Dictionary = {}
var _plan: Plan
var _list_job: RefCounted
var _names_job: RefCounted
var _plan_job: RefCounted
var _apply_job: RefCounted


## What turning one feature on or off would change, with Apply and Cancel.
class Plan:
	extends PanelContainer
	var feature := ""
	var adding := true
	var lines: VBoxContainer
	var problem: Label
	var working: HBoxContainer
	var apply: Button
	var cancel: Button


func _init() -> void:
	name = "Features"
	theme_type_variation = "JoineryTight"
	add_child(UI_COMPONENTS.label("Game features", "JoinerySection"))
	_body = VBoxContainer.new()
	_body.theme_type_variation = "JoineryTight"
	add_child(_body)
	hide()


## Ask for the project's features and their names; the section shows once they arrive.
func reload() -> void:
	if _list_job == null:
		_list_job = _start(["feature", "list", "--project", project, "--json"])
	if _names_job == null and _names.is_empty():
		_names_job = _start(["features", "--json"])


func _process(_delta: float) -> void:
	_list_job = _poll(_list_job, _list_done)
	_names_job = _poll(_names_job, _names_done)
	_plan_job = _poll(_plan_job, _plan_done)
	_apply_job = _poll(_apply_job, _apply_done)


func _exit_tree() -> void:
	for job: RefCounted in [_list_job, _names_job, _plan_job]:
		if job != null:
			job.call("cancel")
	_list_job = null
	_names_job = null
	_plan_job = null


func _list_done(result: Dictionary) -> void:
	var data: Variant = _json(result)
	_game = data["game"] if data is Dictionary and data.get("game") is Dictionary else {}
	_build()


func _names_done(result: Dictionary) -> void:
	var data: Variant = _json(result)
	if data is Dictionary:
		for entry: Variant in data.get("features", []):
			if entry is Dictionary:
				_names[str(entry.get("id", ""))] = entry
	_build()


## A switch per feature this game has or could add; the others say why they can't be added.
func _build() -> void:
	for child: Node in _body.get_children():
		_body.remove_child(child)
		child.queue_free()
	_rows.clear()
	_plan = null
	if _game.is_empty():
		hide()  # Not started from a blueprint: there are no features to switch.
		return
	var applied: Array = _game.get("features", [])
	var addable: Array = _game.get("can_add", [])
	var blueprint := str(_game.get("blueprint", ""))
	var ids: Array[String] = []
	for id: Variant in applied + addable:
		if not str(id) in ids:
			ids.append(str(id))
	for id: String in _names:
		var fits: Array = (_names[id] as Dictionary).get("blueprints", ["*"])
		if not id in ids and ("*" in fits or blueprint in fits):
			ids.append(id)
	ids.sort()
	for id: String in ids:
		var info: Dictionary = _names.get(id, {})
		var row := UI_COMPONENTS.switch_row(
			title_of(id), str(info.get("summary", "")), id in applied
		)
		row.name = "Feature_" + id.replace("-", "_")
		# Flush with the app rows above: no settings-page inset in this list.
		row.theme_type_variation = &""
		if not id in applied and not id in addable:
			row.set_disabled_reason(_why_not(info, applied))
		row.toggled.connect(_on_toggled.bind(id))
		_body.add_child(row)
		_rows[id] = row
	visible = not ids.is_empty()


func _why_not(info: Dictionary, applied: Array) -> String:
	for other: Variant in info.get("conflicts", []):
		if other in applied:
			return "Can't be used with %s." % title_of(str(other))
	for other: Variant in info.get("requires", []):
		if not other in applied:
			return "Needs %s first." % title_of(str(other))
	return "Not available for this game."


## A feature's name for people ("Online play"), from the catalogue when it is known.
func title_of(id: String) -> String:
	return str((_names.get(id, {}) as Dictionary).get("name", id.capitalize()))


## A switch changed: show what it would change before anything happens.
func _on_toggled(on: bool, id: String) -> void:
	if _apply_job != null:
		# One change at a time: closing the plan now would lose the running one.
		(_rows[id] as UI_COMPONENTS.SwitchRow).set_on(not on)
		return
	var pending := _plan != null and _plan.feature == id
	_close_plan(false)
	if pending and on == (id in (_game.get("features", []) as Array)):
		return  # Switched straight back: nothing to change.
	var row: Control = _rows[id]
	_plan = _plan_panel(id, on)
	_body.add_child(_plan)
	_body.move_child(_plan, row.get_index() + 1)
	UI_MOTION.reveal(_plan)
	_plan_job = _start(
		["feature", "add" if on else "remove", id, "--project", project, "--plan", "--json"]
	)
	if _plan_job == null:
		_plan_failed("Joinery's command line isn't available")


func _plan_panel(id: String, adding: bool) -> Plan:
	var plan := Plan.new()
	plan.name = "FeaturePlan"
	plan.feature = id
	plan.adding = adding
	plan.theme_type_variation = "JoineryCard"
	var stack := VBoxContainer.new()
	stack.theme_type_variation = "JoineryTight"
	plan.add_child(stack)
	var verb := "Adding" if adding else "Removing"
	stack.add_child(UI_COMPONENTS.label("%s %s" % [verb, title_of(id)], "", true))
	plan.working = HBoxContainer.new()
	plan.working.theme_type_variation = "JoineryRow"
	var spinner := INDICATOR.new()
	spinner.set("running", true)
	plan.working.add_child(spinner)
	var text := UI_COMPONENTS.label("Working out what changes…", "JoineryCaption")
	text.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	plan.working.add_child(text)
	stack.add_child(plan.working)
	plan.lines = VBoxContainer.new()
	plan.lines.theme_type_variation = "JoineryFlush"
	stack.add_child(plan.lines)
	plan.problem = UI_COMPONENTS.label("", "JoineryError", true)
	plan.problem.hide()
	stack.add_child(plan.problem)
	var buttons := HBoxContainer.new()
	buttons.theme_type_variation = "JoineryRow"
	buttons.alignment = BoxContainer.ALIGNMENT_END
	stack.add_child(buttons)
	plan.cancel = UI_COMPONENTS.quiet("Cancel")
	plan.cancel.pressed.connect(_cancel_plan)
	buttons.add_child(plan.cancel)
	plan.apply = UI_COMPONENTS.secondary("Apply")
	plan.apply.disabled = true
	plan.apply.pressed.connect(_apply_plan)
	buttons.add_child(plan.apply)
	return plan


func _plan_done(result: Dictionary) -> void:
	if _plan == null:
		return
	var data: Variant = _json(result)
	if not data is Dictionary or (data as Dictionary).has("error"):
		_plan_failed(_reason(result, data))
		return
	_plan.working.hide()
	var changes: Array = data.get("changes", [])
	var count := changes.size()
	var summary := "Nothing in the project changes."
	if count > 0:
		summary = "Changes %d file%s:" % [count, "" if count == 1 else "s"]
	_plan.lines.add_child(UI_COMPONENTS.label(summary, "JoineryCaption", true))
	for path: Variant in changes.slice(0, PLAN_FILES):
		_plan.lines.add_child(UI_COMPONENTS.label(str(path), "JoineryCaption"))
	if count > PLAN_FILES:
		var more := UI_COMPONENTS.label("and %d more" % (count - PLAN_FILES), "JoineryCaption")
		_plan.lines.add_child(more)
	for note: Variant in data.get("notes", []):
		_plan.lines.add_child(UI_COMPONENTS.label(str(note), "JoineryCaption", true))
	var conflicts := PackedStringArray()
	for conflict: Variant in data.get("conflicts", []):
		conflicts.append(str(conflict))
	_plan.problem.text = "\n".join(conflicts)
	_plan.problem.visible = not conflicts.is_empty()
	_plan.apply.disabled = not conflicts.is_empty()
	UI_MOTION.stagger(_plan.lines.get_children())


func _plan_failed(reason: String) -> void:
	if _plan == null:
		return
	_plan.working.hide()
	var why := reason if not reason.is_empty() else "no reason given"
	_plan.problem.text = "Joinery couldn't work out the change: " + why
	_plan.problem.show()
	_plan.apply.disabled = true


func _apply_plan() -> void:
	if _plan == null or _apply_job != null:
		return
	var verb := "add" if _plan.adding else "remove"
	_apply_job = _start(["feature", verb, _plan.feature, "--project", project, "--json", "--yes"])
	if _apply_job == null:
		_plan_failed("Joinery's command line isn't available")
		return
	_plan.apply.disabled = true
	_plan.cancel.disabled = true
	_plan.apply.text = "Applying…"


func _apply_done(result: Dictionary) -> void:
	if _plan == null:
		return
	var data: Variant = _json(result)
	if (
		int(result.get("code", 1)) != 0
		or (data is Dictionary and (data as Dictionary).has("error"))
	):
		var why := _reason(result, data)
		_plan.apply.text = "Apply"
		_plan.apply.disabled = false
		_plan.cancel.disabled = false
		_plan.problem.text = "Couldn't apply: " + (why if not why.is_empty() else "no reason given")
		_plan.problem.show()
		return
	var message := "%s %s." % ["Added" if _plan.adding else "Removed", title_of(_plan.feature)]
	_close_plan(true)
	applied.emit(message)
	reload()


## Cancel puts the switch back where it was; nothing changed.
func _cancel_plan() -> void:
	_close_plan(false)


## Close the plan. Unless [param keep], its switch goes back to where it was.
func _close_plan(keep: bool) -> void:
	if _plan_job != null:
		_plan_job.call("cancel")
		_plan_job = null
	if _plan == null:
		return
	var row: UI_COMPONENTS.SwitchRow = _rows.get(_plan.feature)
	if row != null and not keep:
		row.set_on(not _plan.adding)
	var old := _plan
	_plan = null
	UI_MOTION.exit(old, null, Callable(), true)


func _start(arguments: Array) -> RefCounted:
	var program := "uv"
	var args := PackedStringArray()
	if cli_prefix.is_empty():
		var engine := OS.get_environment("JOINERY_HOME")
		if engine.is_empty():
			engine = ProjectSettings.globalize_path("res://").path_join("..").simplify_path()
		args = PackedStringArray(["run", "--frozen", "--quiet", "--project", engine, "joinery"])
	else:
		program = cli_prefix[0]
		args = cli_prefix.slice(1)
	for argument: Variant in arguments:
		args.append(str(argument))
	var job: RefCounted = COMMAND_SCRIPT.new()
	return job if job.call("start", program, args) == OK else null


static func _poll(job: RefCounted, done: Callable) -> RefCounted:
	if job == null:
		return null
	var result: Dictionary = job.call("poll")
	if result.is_empty():
		return job
	done.call(result)
	return null


static func _json(result: Dictionary) -> Variant:
	var json := JSON.new()
	return json.data if json.parse(str(result.get("output", ""))) == OK else null


## Why a command failed: its JSON error, else the first useful line it printed.
static func _reason(result: Dictionary, data: Variant) -> String:
	if data is Dictionary and not str((data as Dictionary).get("error", "")).is_empty():
		return str(data["error"])
	var text := str(result.get("error", "")) + "\n" + str(result.get("output", ""))
	for raw: String in text.split("\n", false):
		var line := raw.strip_edges()
		if not line.is_empty() and not line.begins_with("Traceback"):
			return line.trim_prefix("feature:").strip_edges().left(160)
	return ""
