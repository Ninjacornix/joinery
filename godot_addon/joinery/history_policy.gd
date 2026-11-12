@tool
extends RefCounted
## "Keep older versions": how many accepted changes keep the older versions of the files they
## replaced (models, textures, sounds), so they can be reverted (`joinery history policy`,
## ADR 0026). Shared by the settings page and Project apps. The ids and numbers are the CLI's
## presets (POLICY_PRESETS in joinery/transactions.py); anything else is Custom.

const PRESETS: Array[Dictionary] = [
	{
		"id": "keep-50",
		"name": "Last 50 changes",
		"detail": "up to 2 GB",
		"keep": 50,
		"max_mb": 2048
	},
	{
		"id": "keep-10",
		"name": "Last 10 changes",
		"detail": "up to 1 GB",
		"keep": 10,
		"max_mb": 1024
	},
	{"id": "latest", "name": "Only the newest", "detail": "", "keep": 1, "max_mb": 1024},
]
const CUSTOM := "Custom…"
const HINT := (
	"Older versions of models, textures and sounds that changes replaced,"
	+ " kept so those changes can be reverted"
)
const UI := preload("ui_components.gd")


## Custom's two fields ("Changes to keep", "Space, in GB") and, with buttons, a line of text
## beside Cancel and Apply. Settings uses the text to say what a smaller choice removes.
class PolicyPanel:
	extends MarginContainer
	## Apply was pressed, or Enter in a field.
	signal applied
	signal cancelled
	var custom: VBoxContainer
	var keep: UI.Field
	var space: UI.Field
	var text: Label
	var buttons: HBoxContainer
	var apply: Button

	## Show the fields filled with [param policy] ({keep, max_mb}).
	func show_custom(policy: Dictionary) -> void:
		custom.show()
		text.text = ""
		text.hide()
		apply.text = "Apply"
		apply.accessibility_name = "Apply these numbers"
		(keep.control as LineEdit).text = str(int(policy.get("keep", 50)))
		(space.control as LineEdit).text = _SCRIPT.gigabytes(int(policy.get("max_mb", 2048)))
		keep.set_error("")
		space.set_error("")

	## Hide the fields and ask [param message] before "Keep fewer".
	func show_check(message: String) -> void:
		custom.hide()
		text.text = message
		text.show()
		apply.text = "Keep fewer"
		apply.accessibility_name = "Keep fewer older versions"

	## The typed {keep, max_mb}, or {} with the error shown under its field.
	func values() -> Dictionary:
		var parsed := _SCRIPT.parse(
			(keep.control as LineEdit).text, (space.control as LineEdit).text
		)
		if not parsed.has("error"):
			return parsed
		var field := keep if parsed["field"] == "keep" else space
		field.set_error(str(parsed["error"]))
		field.control.grab_focus()
		return {}


const _SCRIPT := preload("history_policy.gd")


## A hidden [PolicyPanel]; without [param with_buttons] it is only the two fields (Project apps
## saves them with its own Save).
static func panel(with_buttons := true) -> PolicyPanel:
	var result := PolicyPanel.new()
	result.theme_type_variation = "JoineryInset"
	result.name = "PolicyPanel"
	result.hide()
	var stack := VBoxContainer.new()
	stack.theme_type_variation = "JoineryStack"
	result.add_child(stack)
	result.custom = VBoxContainer.new()
	result.custom.theme_type_variation = "JoineryStack"
	stack.add_child(result.custom)
	var keep := LineEdit.new()
	keep.placeholder_text = "e.g. 20"
	keep.virtual_keyboard_type = LineEdit.KEYBOARD_TYPE_NUMBER
	result.keep = UI.field("Changes to keep", keep)
	result.custom.add_child(result.keep)
	var space := LineEdit.new()
	space.placeholder_text = "e.g. 1.5"
	space.virtual_keyboard_type = LineEdit.KEYBOARD_TYPE_NUMBER_DECIMAL
	result.space = UI.field("Space, in GB", space)
	result.custom.add_child(result.space)
	for line: LineEdit in [keep, space]:
		line.text_submitted.connect(func(_text: String) -> void: result.applied.emit())
		line.text_changed.connect(
			func(_text: String) -> void:
				result.keep.set_error("")
				result.space.set_error("")
		)
	# What a smaller choice removes reads across the whole row, the buttons under it at the
	# trailing edge: beside them, a narrow sidebar would squeeze it to a word per line.
	result.text = UI.label("", "JoineryCaption", true)
	result.text.hide()
	stack.add_child(result.text)
	result.buttons = HBoxContainer.new()
	result.buttons.theme_type_variation = "JoineryRow"
	result.buttons.alignment = BoxContainer.ALIGNMENT_END
	result.buttons.visible = with_buttons
	stack.add_child(result.buttons)
	var cancel := UI.secondary("Cancel")
	cancel.accessibility_name = "Keep the history setting as it is"
	cancel.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	cancel.pressed.connect(func() -> void: result.cancelled.emit())
	result.buttons.add_child(cancel)
	result.apply = UI.primary("Apply")
	result.apply.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	result.apply.pressed.connect(func() -> void: result.applied.emit())
	result.buttons.add_child(result.apply)
	return result


## Picker items: the presets, then Custom.
static func names() -> Array[String]:
	var result: Array[String] = []
	for preset: Dictionary in PRESETS:
		result.append(str(preset["name"]))
	result.append(CUSTOM)
	return result


## "Last 50 changes, up to 2 GB": a preset's full wording, for its tooltip.
static func describe(index: int) -> String:
	if index < 0 or index >= PRESETS.size():
		return "Your own number of changes and space"
	var preset := PRESETS[index]
	var detail := str(preset["detail"])
	return str(preset["name"]) + ("" if detail.is_empty() else ", " + detail)


## Fill [param picker] with the choices and their tooltips.
static func fill(picker: OptionButton) -> void:
	picker.clear()
	var all := names()
	for index: int in all.size():
		picker.add_item(all[index])
		picker.set_item_tooltip(index, describe(index))


## The picker index for a policy from the CLI ({keep, max_mb, preset}); Custom when no
## preset matches.
static func index_of(policy: Dictionary) -> int:
	var id := str(policy.get("preset", ""))
	for index: int in PRESETS.size():
		if PRESETS[index]["id"] == id:
			return index
	if id.is_empty():
		for index: int in PRESETS.size():
			var preset := PRESETS[index]
			if (
				int(policy.get("keep", 0)) == int(preset["keep"])
				and int(policy.get("max_mb", 0)) == int(preset["max_mb"])
			):
				return index
	return PRESETS.size()


## Megabytes as the GB a person types: 2048 -> "2", 1536 -> "1.5", 300 -> "0.29".
static func gigabytes(max_mb: int) -> String:
	var value := max_mb / 1024.0
	return String.num(value, 0 if is_equal_approx(value, roundf(value)) else 2)


## Check typed Custom values. Returns {keep, max_mb} or {field: "keep"|"space", error}.
static func parse(keep_text: String, space_text: String) -> Dictionary:
	var keep := keep_text.strip_edges()
	if not keep.is_valid_int() or int(keep) < 1:
		return {"field": "keep", "error": "Enter a whole number, 1 or more"}
	var space := space_text.strip_edges().replace(",", ".")
	if not space.is_valid_float() or float(space) * 1024.0 < 1.0:
		return {"field": "space", "error": "Enter the space in GB, for example 1.5"}
	return {"keep": int(keep), "max_mb": maxi(1, roundi(float(space) * 1024.0))}


## The last JSON object line of a CLI reply, or {}.
static func last_json(output: String) -> Dictionary:
	var lines := output.split("\n", false)
	lines.reverse()
	for line: String in lines:
		if not line.strip_edges().begins_with("{"):
			continue
		var json := JSON.new()
		if json.parse(line) == OK and json.data is Dictionary:
			return json.data
	return {}


## Bytes for people, as `joinery history` counts them: "0 B", "512 KB", "340 MB", "1.2 GB".
static func human_size(bytes: float) -> String:
	var units := ["B", "KB", "MB", "GB", "TB"]
	var value := bytes
	var index := 0
	while value >= 1000.0 and index < units.size() - 1:
		value /= 1000.0
		index += 1
	if index >= 3 and value < 10.0:
		return "%.1f %s" % [value, units[index]]
	return "%d %s" % [roundi(value), units[index]]
