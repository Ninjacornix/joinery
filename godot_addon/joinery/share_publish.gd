@tool
extends ConfirmationDialog
## "Publish to itch.io…": the one place a publish starts, and only on the user's click.
##
## It names exactly what happens before anything does: the itch.io page (user/game), the
## channels, and each file butler will push (from `joinery share itch --dry-run`). butler is
## installed only when the user ticks the install box, which says what is downloaded and from
## where. Joinery never asks for an itch.io key: the user signs in with their own
## BUTLER_API_KEY or `butler login`; until then Publish stays off and says why.

signal publish_requested(target: String, channels: Array[String], install: bool)
signal dry_run_requested(target: String, channels: Array[String])

const UI_THEME := preload("ui_theme.gd")
const UI := preload("ui_components.gd")
const LABELS := {"web": "Web", "windows": "Windows", "macos": "macOS", "linux": "Linux"}
const TARGET_PATTERN := "^[A-Za-z0-9_-]+/[A-Za-z0-9_-]+$"

var _target: LineEdit
var _channels: HFlowContainer
var _checks: Dictionary = {}
var _summary: Label
var _plan: Label
var _install: CheckBox
var _note: Label
var _result: Label
var _itch: Dictionary = {}
var _pattern := RegEx.create_from_string(TARGET_PATTERN)
var _dry_timer: Timer


func _init() -> void:
	title = "Publish to itch.io"
	ok_button_text = "Publish"
	cancel_button_text = "Cancel"
	dialog_hide_on_ok = false
	exclusive = true
	var body := VBoxContainer.new()
	body.theme_type_variation = "JoineryStack"
	add_child(body)
	_target = LineEdit.new()
	_target.name = "Target"
	_target.placeholder_text = "your-name/your-game"
	_target.accessibility_name = "itch.io page, as user/game"
	_target.text_changed.connect(func(_text: String) -> void: _changed())
	body.add_child(
		UI.field("itch.io page", _target, "Your user name and the game's, as on itch.io.")
	)
	_channels = HFlowContainer.new()
	_channels.name = "Channels"
	_channels.theme_type_variation = "JoineryRow"
	body.add_child(UI.field("Builds", _channels))
	_summary = UI.label("", "", true)
	_summary.name = "Summary"
	body.add_child(_summary)
	_plan = UI.label("", "JoineryCaption", true)
	_plan.name = "Plan"
	body.add_child(_plan)
	_install = CheckBox.new()
	_install.name = "Install"
	_install.toggled.connect(func(_on: bool) -> void: _changed(false))
	_install.hide()
	body.add_child(_install)
	_note = UI.label("", "JoineryCaption", true)
	_note.name = "Note"
	_note.hide()
	body.add_child(_note)
	_result = UI.label("", "JoineryError", true)
	_result.name = "Result"
	_result.hide()
	body.add_child(_result)
	_dry_timer = Timer.new()
	_dry_timer.one_shot = true
	_dry_timer.wait_time = 0.4
	_dry_timer.timeout.connect(_ask_dry_run)
	add_child(_dry_timer)
	confirmed.connect(_confirm)


## Open over [param parent] for the [param built] targets; [param itch] is the status's itch
## part: {butler, version, pinned, signed_in, target, channels}.
func open(parent: Control, built: Array[String], itch: Dictionary) -> void:
	_itch = itch
	theme = UI_THEME.build(parent)
	get_ok_button().theme_type_variation = "JoineryPrimaryButton"
	get_cancel_button().theme_type_variation = "JoinerySecondaryButton"
	UI.dialog_footer(self)
	_target.text = str(itch.get("target", "")) if itch.get("target") != null else ""
	for child: Node in _channels.get_children():
		_channels.remove_child(child)
		child.queue_free()
	_checks.clear()
	var previous: Array = itch.get("channels", [])
	for id: String in built:
		var check := CheckBox.new()
		check.name = id
		check.text = str(LABELS.get(id, id))
		check.button_pressed = previous.is_empty() or _channel(id) in previous
		check.toggled.connect(func(_on: bool) -> void: _changed())
		_checks[id] = check
		_channels.add_child(check)
	var has_butler := itch.get("butler") != null
	_install.visible = not has_butler
	_install.button_pressed = false
	_install.disabled = not bool(itch.get("pinned", false))
	_install.text = (
		"Install butler %s, itch.io's upload tool, from itch.io (about 20 MB)"
		% str(itch.get("version", ""))
	)
	_result.hide()
	_plan.text = ""
	_changed()
	var size := UI_THEME.dialog_size(parent, "connect")
	min_size = Vector2i(size.x, 0)
	if not visible:
		popup_centered(Vector2i(size.x, 0))
	_fit.call_deferred()


## Shrink to the content once the wrapped labels know their width.
func _fit() -> void:
	reset_size()
	if visible:
		move_to_center()


## The dry run's answer: {steps: [{channel, file, size}], notes} or {error}.
func apply_dry_run(data: Dictionary) -> void:
	if data.has("error"):
		_plan.text = str(data["error"])
		return
	var lines: Array[String] = []
	for step: Variant in data.get("steps", []):
		var item: Dictionary = step
		lines.append(
			(
				"%s → %s:%s (%s)"
				% [
					str(item.get("file", "")).get_file(),
					_target.text.strip_edges(),
					str(item.get("channel", "")),
					_size(float(item.get("size", 0)))
				]
			)
		)
	_plan.text = "\n".join(lines)
	_fit.call_deferred()


## The publish finished: close on success, else say why and stay open.
func apply_result(data: Dictionary) -> void:
	if bool(data.get("ok", false)):
		_result.hide()
		hide()
		return
	var failed := ""
	for step: Variant in data.get("steps", []):
		if not bool((step as Dictionary).get("ok", true)):
			failed = str((step as Dictionary).get("output", ""))
	_result.text = str(data.get("error", failed if not failed.is_empty() else "Publishing failed."))
	_result.show()
	get_ok_button().disabled = false


## The confirmation sentence for the current choice, for tests.
func summary_text() -> String:
	return _summary.text


func _chosen() -> Array[String]:
	var chosen: Array[String] = []
	for id: String in _checks:
		if (_checks[id] as CheckBox).button_pressed:
			chosen.append(id)
	return chosen


func _channel(id: String) -> String:
	return "mac" if id == "macos" else id


func _valid_target() -> bool:
	return _pattern.search(_target.text.strip_edges()) != null


func _changed(dry_run := true) -> void:
	var chosen := _chosen()
	var names: Array[String] = []
	for id: String in chosen:
		names.append(str(LABELS.get(id, id)))
	var listed := (
		", ".join(names.slice(0, -1)) + " and " + names[-1] if names.size() > 1 else "".join(names)
	)
	var target := _target.text.strip_edges()
	_summary.text = (
		"Push %s to itch.io as %s." % [listed, target]
		if _valid_target() and not chosen.is_empty()
		else "Name your itch.io page and pick the builds to push."
	)
	var notes: Array[String] = []
	if _itch.get("signed_in") == null:
		notes.append("Sign in first: set BUTLER_API_KEY, or run `butler login` in a terminal.")
	if _itch.get("butler") == null and not bool(_itch.get("pinned", false)):
		notes.append(
			(
				"butler isn't pinned for this computer yet, so Joinery won't download it. "
				+ "Install it yourself and set JOINERY_BUTLER."
			)
		)
	_note.text = "\n".join(notes)
	_note.visible = not notes.is_empty()
	var ready := (
		_valid_target()
		and not chosen.is_empty()
		and _itch.get("signed_in") != null
		and (_itch.get("butler") != null or _install.button_pressed)
	)
	get_ok_button().disabled = not ready
	if dry_run and _valid_target() and not chosen.is_empty():
		_dry_timer.start()


func _ask_dry_run() -> void:
	if _valid_target() and not _chosen().is_empty():
		dry_run_requested.emit(_target.text.strip_edges(), _chosen())


func _confirm() -> void:
	get_ok_button().disabled = true
	publish_requested.emit(_target.text.strip_edges(), _chosen(), _install.button_pressed)


static func _size(bytes: float) -> String:
	if bytes >= 1024.0 * 1024.0:
		return "%.1f MB" % (bytes / 1024.0 / 1024.0)
	return "%d KB" % maxi(1, roundi(bytes / 1024.0))
