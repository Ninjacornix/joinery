extends RefCounted
## The Save and Load pages: one row per slot with when it was saved, how long
## was played and the blueprint's one-line summary. Saving over a used slot and
## deleting a save each ask for a second press. Built with the core UiStyle;
## everything works with the ui_* actions alone.

const SAVE := &"save"
const LOAD := &"load"
const ARMED := &"armed"


## A full page for `Menus.open_page`. `mode` is SAVE or LOAD.
static func build(save_game: Node, mode: StringName) -> Control:
	var column: VBoxContainer = VBoxContainer.new()
	column.name = "Slots"
	fill(column, save_game, mode)
	var heading: String = "Save" if mode == SAVE else "Load"
	var screen: Control = UiStyle.page(heading, column, Menus.back)
	screen.name = "SavePage" if mode == SAVE else "LoadPage"
	screen.set_meta(&"menu", mode)
	return screen


## (Re)builds the slot rows inside `column`.
static func fill(column: VBoxContainer, save_game: Node, mode: StringName) -> void:
	for child: Node in column.get_children():
		column.remove_child(child)
		child.queue_free()
	var first: int = 1 if mode == SAVE else 0
	var slots: int = int(save_game.get(&"SLOTS"))
	for slot: int in range(first, slots):
		column.add_child(_row(save_game, mode, slot, column))


static func _row(save_game: Node, mode: StringName, slot: int, column: VBoxContainer) -> Control:
	var info: Dictionary = save_game.call(&"slot_info", slot)
	var exists: bool = bool(info["exists"])
	var loadable: bool = exists and not bool(info["damaged"])
	var line: HBoxContainer = HBoxContainer.new()
	line.name = "Slot%d" % slot

	var pick: Button = UiStyle.button(str(save_game.call(&"slot_name", slot)))
	pick.name = "Pick"
	pick.auto_translate_mode = Node.AUTO_TRANSLATE_MODE_DISABLED
	pick.set_meta(&"label", pick.text)
	pick.focus_exited.connect(_disarm.bind(pick))
	line.add_child(pick)

	var details: VBoxContainer = VBoxContainer.new()
	details.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	details.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	details.custom_minimum_size = Vector2(UiStyle.px(420), 0)
	var when: Label = UiStyle.hint(_describe(save_game, info))
	when.name = "Details"
	when.auto_translate_mode = Node.AUTO_TRANSLATE_MODE_DISABLED
	when.horizontal_alignment = HORIZONTAL_ALIGNMENT_LEFT
	details.add_child(when)
	var summary: String = str(info["summary"])
	if not summary.is_empty():
		var extra: Label = UiStyle.hint(summary)
		extra.name = "Summary"
		extra.auto_translate_mode = Node.AUTO_TRANSLATE_MODE_DISABLED
		extra.horizontal_alignment = HORIZONTAL_ALIGNMENT_LEFT
		details.add_child(extra)
	line.add_child(details)

	if mode == SAVE:
		pick.set_meta(&"overwrite", exists)
		pick.pressed.connect(_on_save.bind(save_game, slot, pick))
		return line

	if not loadable:
		pick.disabled = true
		pick.focus_mode = Control.FOCUS_NONE
	pick.pressed.connect(_on_load.bind(save_game, slot))
	if exists:
		var remove: Button = UiStyle.button("Delete")
		remove.name = "Delete"
		remove.custom_minimum_size = Vector2(UiStyle.px(220), UiStyle.px(64))
		remove.set_meta(&"label", "Delete")
		remove.focus_exited.connect(_disarm.bind(remove))
		remove.pressed.connect(_on_delete.bind(save_game, slot, column, remove))
		line.add_child(remove)
	return line


## "Empty", the reason it cannot be loaded, or when it was saved and how long
## was played. Already translated.
static func _describe(save_game: Node, info: Dictionary) -> String:
	if not bool(info["exists"]):
		return save_game.tr("Empty")
	if bool(info["damaged"]):
		return save_game.tr(str(info["reason"]))
	return save_game.tr("Saved {when}, played {time}").format(
		{"when": date_text(float(info["saved_at"])), "time": clock_text(float(info["playtime"]))}
	)


## Local date and time, "YYYY-MM-DD HH:MM".
static func date_text(unix: float) -> String:
	var bias: int = int(Time.get_time_zone_from_system().get("bias", 0))
	var at: Dictionary = Time.get_datetime_dict_from_unix_time(int(unix) + bias * 60)
	return "%04d-%02d-%02d %02d:%02d" % [at["year"], at["month"], at["day"], at["hour"], at["minute"]]


## "M:SS", or "H:MM:SS" from an hour on.
static func clock_text(seconds: float) -> String:
	var total: int = maxi(0, int(seconds))
	var hours: int = int(total / 3600.0)
	var minutes: int = int((total % 3600) / 60.0)
	if hours > 0:
		return "%d:%02d:%02d" % [hours, minutes, total % 60]
	return "%d:%02d" % [minutes, total % 60]


static func _on_save(save_game: Node, slot: int, pick: Button) -> void:
	var prompt: String = save_game.tr("Press again to overwrite")
	if bool(pick.get_meta(&"overwrite", false)) and not _arm(pick, prompt):
		return
	var error: Error = save_game.call(&"save_slot", slot)
	if error == OK:
		Menus.back()
		Menus.toast(save_game.tr("Saved"))
	else:
		_disarm(pick)
		Menus.toast(save_game.tr("Could not save."))


static func _on_load(save_game: Node, slot: int) -> void:
	var info: Dictionary = save_game.call(&"slot_info", slot)
	var error: Error = save_game.call(&"load_slot", slot)
	if error != OK:
		var reason: String = str(info["reason"])
		if reason.is_empty():
			reason = "Could not load the save."
		Menus.toast(save_game.tr(reason))


static func _on_delete(save_game: Node, slot: int, column: VBoxContainer, button: Button) -> void:
	if not _arm(button, save_game.tr("Press again to delete")):
		return
	save_game.call(&"delete_slot", slot)
	fill(column, save_game, LOAD)
	var page: Node = column.get_parent()
	var target: Control = _first_focusable(page)
	if target != null:
		target.grab_focus()


## First press: shows `prompt` (already translated) and returns false.
## Second press: true.
static func _arm(button: Button, prompt: String) -> bool:
	if bool(button.get_meta(ARMED, false)):
		button.set_meta(ARMED, false)
		return true
	button.set_meta(ARMED, true)
	button.text = prompt
	button.auto_translate_mode = Node.AUTO_TRANSLATE_MODE_DISABLED
	return false


static func _disarm(button: Button) -> void:
	if not is_instance_valid(button) or not bool(button.get_meta(ARMED, false)):
		return
	button.set_meta(ARMED, false)
	button.text = button.tr(str(button.get_meta(&"label", button.text)))


static func _first_focusable(node: Node) -> Control:
	for child: Node in node.get_children():
		if child is Control and not (child as Control).visible:
			continue
		if child is BaseButton and (child as BaseButton).is_queued_for_deletion():
			continue
		if child is Control and (child as Control).focus_mode == Control.FOCUS_ALL:
			if not (child is BaseButton and (child as BaseButton).disabled):
				return child as Control
		var found: Control = _first_focusable(child)
		if found != null:
			return found
	return null
