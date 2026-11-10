@tool
extends RefCounted
## The art direction dialog: the project's art bible as readable text under one heading, with
## Close and Approve on the trailing edge of the footer.

const UI_THEME := preload("ui_theme.gd")
const UI := preload("ui_components.gd")
const TITLE := "Art direction"


## Open the dialog on [param parent] at [param size]. [param on_approve] runs when the user
## approves.
static func open(
	parent: Control, text: String, size: Vector2i, on_approve: Callable
) -> ConfirmationDialog:
	var dialog := ConfirmationDialog.new()
	dialog.title = TITLE
	dialog.ok_button_text = "Approve"
	dialog.cancel_button_text = "Close"
	dialog.theme = UI_THEME.build(parent)
	dialog.get_ok_button().theme_type_variation = "JoineryPrimaryButton"
	dialog.get_cancel_button().theme_type_variation = "JoinerySecondaryButton"
	var body := VBoxContainer.new()
	body.theme_type_variation = "JoineryStack"
	body.add_child(UI.label(TITLE, "JoineryTitle"))
	var frame := PanelContainer.new()
	frame.theme_type_variation = "JoineryCard"
	frame.size_flags_vertical = Control.SIZE_EXPAND_FILL
	body.add_child(frame)
	var content := RichTextLabel.new()
	content.bbcode_enabled = true
	content.fit_content = false
	content.scroll_active = true
	content.selection_enabled = true
	content.accessibility_name = TITLE
	content.text = to_bbcode(text)
	frame.add_child(content)
	dialog.add_child(body)
	parent.add_child(dialog)
	_trail_buttons(dialog)
	dialog.confirmed.connect(on_approve)
	dialog.confirmed.connect(dialog.queue_free)
	dialog.canceled.connect(dialog.queue_free)
	dialog.popup_centered(size)
	# The dialog focuses Approve on popup, which draws its focus ring before the user has
	# done anything. Let it go; Tab still reaches the buttons.
	dialog.get_ok_button().release_focus.call_deferred()
	return dialog


## The dialog centres its buttons between spacers, and keeps each spacer's visibility in
## step with its button. Keep only the leading spacer expanding and move the others to the
## leading edge too, so the buttons sit flush with the trailing edge of the text, one
## SPACE.2 apart.
static func _trail_buttons(dialog: ConfirmationDialog) -> void:
	var row := dialog.get_ok_button().get_parent()
	var spacers: Array[Control] = []
	for child: Node in row.get_children(true):
		if not child is Button:
			spacers.append(child as Control)
	for index: int in spacers.size():
		var spacer := spacers[index]
		spacer.size_flags_horizontal = (
			Control.SIZE_EXPAND_FILL if index == 0 else Control.SIZE_FILL
		)
		row.move_child(spacer, index)
	var gap := UI_THEME.space(dialog, 2)
	dialog.add_theme_constant_override("buttons_separation", gap)
	row.add_theme_constant_override("separation", gap)


## A small Markdown subset as BBCode: headings become bold lines (a leading top heading is
## dropped, the dialog already names the document), list markers become bullets and
## **bold** stays bold. Everything else is plain text, with "[" escaped.
static func to_bbcode(markdown: String) -> String:
	var lines := PackedStringArray()
	var heading := RegEx.create_from_string("^#{1,6}\\s+(.*)$")
	var bullet := RegEx.create_from_string("^\\s*[-*+]\\s+(.*)$")
	var bold := RegEx.create_from_string("\\*\\*(.+?)\\*\\*")
	var first := true
	for raw: String in markdown.replace("\r\n", "\n").split("\n"):
		var line := raw.replace("[", "[lb]")
		var found := heading.search(line)
		if found != null:
			if first and raw.begins_with("# "):
				first = false
				continue
			line = "[b]%s[/b]" % found.get_string(1)
		else:
			var item := bullet.search(line)
			if item != null:
				line = "•  " + item.get_string(1)
		if not raw.strip_edges().is_empty():
			first = false
		lines.append(bold.sub(line, "[b]$1[/b]", true))
	while not lines.is_empty() and lines[0].strip_edges().is_empty():
		lines.remove_at(0)
	return "\n".join(lines)
