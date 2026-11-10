@tool
extends VBoxContainer
## "Older versions" in Project apps: how many accepted changes keep the older versions of the
## files they replaced, so they can be reverted (history_policy.gd). The saved choice comes
## from `joinery history policy --json`; picking a smaller one asks the same command with
## --dry-run and says what Save would remove. Nothing changes until Project apps saves, which
## passes [method save_args] to `joinery setup`. Hidden until the CLI answers, so an older
## CLI leaves history alone.

const COMMAND_SCRIPT := preload("command_job.gd")
const UI_COMPONENTS := preload("ui_components.gd")
const UI_MOTION := preload("ui_motion.gd")
const POLICY := preload("history_policy.gd")

## The Joinery CLI and its leading arguments (see project_setup.gd); empty: `uv run … joinery`.
var cli_prefix := PackedStringArray()
## The project folder the commands act on.
var project := ""

## The saved policy ({keep, max_mb, preset, label}); empty until read.
var policy: Dictionary = {}
var picker: OptionButton
var panel: POLICY.PolicyPanel
var note: Label
var _load_job: RefCounted
var _check_job: RefCounted
## The {keep, max_mb} the last check asked about.
var _checked: Dictionary = {}


func _init() -> void:
	name = "OlderVersions"
	theme_type_variation = "JoineryTight"
	add_child(UI_COMPONENTS.label("Older versions", "JoinerySection"))
	picker = UI_COMPONENTS.picker()
	POLICY.fill(picker)
	picker.accessibility_name = "Keep older versions"
	picker.tooltip_text = POLICY.HINT
	picker.item_selected.connect(_chosen)
	add_child(UI_COMPONENTS.setting_row("Keep", picker))
	panel = POLICY.panel(false)
	for field: UI_COMPONENTS.Field in [panel.keep, panel.space]:
		(field.control as LineEdit).text_changed.connect(func(_t: String) -> void: _check())
	add_child(panel)
	note = UI_COMPONENTS.label("", "JoineryCaption", true)
	note.name = "Note"
	note.hide()
	add_child(note)
	hide()


## Read the saved policy; the section shows once it arrives.
func reload() -> void:
	if _load_job != null:
		_load_job.call("cancel")
	_load_job = _start(["history", "policy", "--json", "--project", project])


func _process(_delta: float) -> void:
	_load_job = _poll(_load_job, _loaded)
	_check_job = _poll(_check_job, _check_done)


func _exit_tree() -> void:
	for job: RefCounted in [_load_job, _check_job]:
		if job != null:
			job.call("cancel")
	_load_job = null
	_check_job = null


func _loaded(result: Dictionary) -> void:
	var saved: Variant = POLICY.last_json(str(result.get("output", ""))).get("policy")
	if int(result.get("code", 1)) != 0 or not saved is Dictionary:
		return
	policy = saved
	picker.select(POLICY.index_of(policy))
	panel.show_custom(policy)
	panel.visible = picker.selected == POLICY.PRESETS.size()
	note.hide()
	show()
	UI_MOTION.fade_in(self)


func _chosen(index: int) -> void:
	var custom := index == POLICY.PRESETS.size()
	if custom and not panel.visible:
		panel.show_custom(policy)
		panel.show()
		UI_MOTION.reveal(panel)
	panel.visible = custom
	_check()


## The chosen {keep, max_mb}; {} while Custom's fields don't hold numbers yet.
func chosen() -> Dictionary:
	var index := picker.selected
	if index >= 0 and index < POLICY.PRESETS.size():
		var preset: Dictionary = POLICY.PRESETS[index]
		return {"keep": int(preset["keep"]), "max_mb": int(preset["max_mb"])}
	var parsed := POLICY.parse(
		(panel.keep.control as LineEdit).text, (panel.space.control as LineEdit).text
	)
	return {} if parsed.has("error") else parsed


func _differs(choice: Dictionary) -> bool:
	return (
		not choice.is_empty()
		and not policy.is_empty()
		and (
			int(choice["keep"]) != int(policy.get("keep", 0))
			or int(choice["max_mb"]) != int(policy.get("max_mb", 0))
		)
	)


## Ask what Save would remove under the chosen policy (a dry run), to say so before saving.
func _check() -> void:
	note.hide()
	if _check_job != null:
		_check_job.call("cancel")
		_check_job = null
	var choice := chosen()
	if not _differs(choice):
		return
	_checked = choice
	var args := ["history", "policy", "--json", "--dry-run", "--project", project]
	args.append_array(["--keep", choice["keep"], "--max-mb", choice["max_mb"]])
	_check_job = _start(args)


func _check_done(result: Dictionary) -> void:
	var data := POLICY.last_json(str(result.get("output", "")))
	var pruned := int(data.get("pruned", 0))
	if pruned == 0 or chosen() != _checked:
		return
	var freed := POLICY.human_size(float(data.get("freed_bytes", 0)))
	note.text = (
		"Saving frees %s. %d older change%s can no longer be reverted."
		% [freed, pruned, "" if pruned == 1 else "s"]
	)
	note.show()
	UI_MOTION.fade_in(note)


## `joinery setup` arguments for a changed policy, [] when unchanged, or null when Custom's
## fields need fixing (the error is shown under the field).
func save_args() -> Variant:
	if not visible:
		return []
	var choice := chosen()
	if panel.visible and choice.is_empty():
		panel.values()
		return null
	if not _differs(choice):
		return []
	return ["--history-keep", choice["keep"], "--history-max-mb", choice["max_mb"]]


func _start(arguments: Array) -> RefCounted:
	return COMMAND_SCRIPT.joinery(cli_prefix, arguments)


static func _poll(job: RefCounted, done: Callable) -> RefCounted:
	return COMMAND_SCRIPT.finish(job, done)
