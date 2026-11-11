@tool
extends ScrollContainer
## The Chats page, shown in place of the conversation like Settings: every saved chat of this
## project, most recent first, with New chat as the one main action. A row is the chat's title
## (its first request until renamed) and a caption naming who answered and when; the open
## chat says "Open". Choosing a row opens that chat. The pencil renames it in place: Enter
## or leaving the field saves, Esc cancels.

signal closed
signal chosen(chat_id: String)
signal new_chat_requested
signal rename_requested(chat_id: String, title: String)

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const STYLE := preload("sidebar_style.gd")
const STORE := preload("chat_store.gd")
## Page body width cap, in multiples of the font size (as Settings).
const MAX_EM := 36.0

var _body: VBoxContainer
var _spacer: Control
var _list: VBoxContainer
var _empty: Label
var _new: Button
## chat id -> its row (a ChatRow).
var _rows: Dictionary = {}


func _ready() -> void:
	horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	size_flags_vertical = Control.SIZE_EXPAND_FILL
	accessibility_name = "Chats"
	UI_THEME.watch(self)
	UI_MOTION.rubber_band(self)
	var gutter := MarginContainer.new()
	gutter.theme_type_variation = "JoineryGutter"
	gutter.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	add_child(gutter)
	var column := HBoxContainer.new()
	gutter.add_child(column)
	_body = VBoxContainer.new()
	_body.theme_type_variation = "JoinerySections"
	_body.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	column.add_child(_body)
	_spacer = Control.new()
	_spacer.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_spacer.mouse_filter = Control.MOUSE_FILTER_IGNORE
	column.add_child(_spacer)
	var row := HBoxContainer.new()
	row.theme_type_variation = "JoineryRow"
	_body.add_child(row)
	var back := UI.icon_button("back", "Back to chat")
	back.accessibility_name = "Close chats and return to the conversation"
	var escape := InputEventKey.new()
	escape.keycode = KEY_ESCAPE
	back.shortcut = Shortcut.new()
	back.shortcut.events = [escape]
	back.shortcut_in_tooltip = false
	back.pressed.connect(func() -> void: closed.emit())
	row.add_child(back)
	var title := UI.label("Chats", "JoineryTitle")
	title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.add_child(title)
	_new = UI.primary("New chat")
	_new.accessibility_name = "Start a new chat"
	_new.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_new.pressed.connect(func() -> void: new_chat_requested.emit())
	row.add_child(_new)
	_list = VBoxContainer.new()
	_list.theme_type_variation = "JoineryTight"
	_body.add_child(_list)
	_empty = UI.label(
		"No saved chats yet. Each conversation is saved here as you go.", "JoineryCaption", true
	)
	var inset := MarginContainer.new()
	inset.theme_type_variation = "JoineryInset"
	inset.add_child(_empty)
	_body.add_child(inset)
	_on_theme_rebuilt()


func _notification(what: int) -> void:
	if (what == NOTIFICATION_RESIZED or what == NOTIFICATION_VISIBILITY_CHANGED) and _body != null:
		_fit.call_deferred()


func _on_theme_rebuilt() -> void:
	_fit()


## Cap the body at MAX_EM without raising the page's minimum width.
func _fit() -> void:
	var bar := get_v_scroll_bar()
	var available := size.x - UI_THEME.space(self, 3) - (bar.size.x if bar.visible else 0.0)
	var cap := MAX_EM * get_theme_default_font_size()
	_body.size_flags_stretch_ratio = maxf(1.0, minf(available, cap))
	_spacer.size_flags_stretch_ratio = maxf(0.0, available - cap)


## Show [param chats] (chat_store.gd's list); [param current] is the open chat's id.
## [param now] fixes the clock for tests (-1: the real time).
func show_chats(chats: Array[Dictionary], current: String, now := -1) -> void:
	for child: Node in _list.get_children():
		_list.remove_child(child)
		child.queue_free()
	_rows.clear()
	_empty.get_parent().visible = chats.is_empty()
	var rows: Array = []
	for chat: Dictionary in chats:
		var row := ChatRow.new(chat, chat["id"] == current, now)
		row.pressed.connect(func() -> void: chosen.emit(str(chat["id"])))
		row.title_changed.connect(
			func(title: String) -> void: rename_requested.emit(str(chat["id"]), title)
		)
		_list.add_child(row)
		_rows[str(chat["id"])] = row
		rows.append(row)
	UI_MOTION.stagger(rows)


## The row of [param chat_id], or null.
func row_for(chat_id: String) -> ChatRow:
	return _rows.get(chat_id)


## Show or hide the page in place of the conversation, restoring what was there.
func set_open(open: bool) -> void:
	var content := get_parent()
	if content == null:
		visible = open
		return
	for child: Node in content.get_children():
		if child == self or not child is CanvasItem:
			continue
		var item := child as CanvasItem
		if open:
			if not item.has_meta("before_chats"):
				item.set_meta("before_chats", item.visible)
			item.visible = false
		elif item.has_meta("before_chats"):
			item.visible = item.get_meta("before_chats")
			item.remove_meta("before_chats")
	visible = open
	if open:
		scroll_vertical = 0


## One saved chat: the title over "Codex · 3 h ago", the whole row opens it; the pencil renames.
class ChatRow:
	extends HBoxContainer

	signal pressed
	signal title_changed(title: String)

	var title: Label
	var caption: Label
	var _open: Button
	var _field: LineEdit
	var _rename: Button

	func _init(chat: Dictionary, current: bool, now: int) -> void:
		theme_type_variation = "JoineryRowTight"
		_open = UI.row_button("", "")
		_open.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		_open.accessibility_name = "Open chat: " + str(chat.get("title", ""))
		_open.pressed.connect(func() -> void: pressed.emit())
		add_child(_open)
		var inset := MarginContainer.new()
		inset.theme_type_variation = "JoineryInset"
		inset.mouse_filter = Control.MOUSE_FILTER_IGNORE
		inset.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
		# SPACE.2 above and below the two lines, inside the hover fill: rows read as items.
		STYLE.on_theme_overrides(
			inset,
			func() -> void:
				var pad := UI_THEME.space(inset, 2)
				inset.add_theme_constant_override("margin_top", pad)
				inset.add_theme_constant_override("margin_bottom", pad)
		)
		_open.add_child(inset)
		var lines := VBoxContainer.new()
		lines.theme_type_variation = "JoineryTight"
		lines.alignment = BoxContainer.ALIGNMENT_CENTER
		lines.mouse_filter = Control.MOUSE_FILTER_IGNORE
		inset.add_child(lines)
		title = UI.label(str(chat.get("title", "")), "")
		title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		title.mouse_filter = Control.MOUSE_FILTER_IGNORE
		title.tooltip_text = title.text
		lines.add_child(title)
		_field = LineEdit.new()
		_field.accessibility_name = "Chat name"
		_field.max_length = 80
		_field.select_all_on_focus = true
		_field.hide()
		_field.text_submitted.connect(func(_text: String) -> void: _commit())
		_field.focus_exited.connect(_commit)
		_field.gui_input.connect(_on_field_input)
		lines.add_child(_field)
		var parts := PackedStringArray()
		if not str(chat.get("provider", "")).is_empty():
			parts.append(STORE.provider_name(str(chat["provider"])))
		parts.append("Open" if current else STORE.when(int(chat.get("updated", 0)), now))
		caption = UI.label(" · ".join(parts), "JoineryCaption")
		caption.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		caption.mouse_filter = Control.MOUSE_FILTER_IGNORE
		lines.add_child(caption)
		# The row is as tall as its two lines and the inset around them.
		lines.minimum_size_changed.connect(_fit)
		_rename = UI.icon_button("rename", "Rename")
		_rename.accessibility_name = "Rename chat: " + title.text
		_rename.size_flags_vertical = Control.SIZE_SHRINK_CENTER
		_rename.pressed.connect(begin_rename)
		add_child(_rename)
		_fit.call_deferred()

	func _fit() -> void:
		if is_instance_valid(_open) and _open.get_child_count() > 0:
			var inset := _open.get_child(0) as Control
			_open.custom_minimum_size.y = inset.get_combined_minimum_size().y

	## Put the title in an editable field, selected.
	func begin_rename() -> void:
		_field.text = title.text
		title.hide()
		_field.show()
		_open.mouse_filter = Control.MOUSE_FILTER_IGNORE
		_field.grab_focus.call_deferred()

	func _commit() -> void:
		if not _field.visible:
			return
		var text := " ".join(_field.text.strip_edges().split(" ", false))
		_end_rename()
		if not text.is_empty() and text != title.text:
			title.text = text
			title.tooltip_text = text
			title_changed.emit(text)

	func _end_rename() -> void:
		_field.hide()
		title.show()
		_open.mouse_filter = Control.MOUSE_FILTER_STOP

	func _on_field_input(event: InputEvent) -> void:
		var key := event as InputEventKey
		if key != null and key.pressed and key.keycode == KEY_ESCAPE:
			_end_rename()
			_field.accept_event()
