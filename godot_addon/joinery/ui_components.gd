@tool
extends RefCounted
## Building blocks for every Joinery screen. Use these instead of hand-built controls: they
## carry the type variations, take heights from CONTROL_H and insets from SPACE, scale icons
## with the editor, and wire press feedback, so every screen lines up the same way.
##
## Heights and icons are applied when a control enters the tree and again on every theme
## change, so a control can be built before it has a parent.

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const _SCRIPT := preload("ui_components.gd")
## Status kind -> [icon, colour role]: the one glyph and colour per outcome, shared by the
## sidebar's finished steps, change cards and the project setup step list.
const STATUS_ICONS := {
	"done": ["success", "success"],
	"failed": ["error", "danger"],
	"warning": ["warning", "text_muted"],
	"stopped": ["close", "text_muted"],
}


## A labelled field: the label above, the control at full width, an optional help caption and
## an error line that stays hidden until [method set_error].
class Field:
	extends VBoxContainer
	var label: Label
	var control: Control
	var help: Label
	var error: Label

	## Show [param text] under the field and outline it in the danger colour; "" clears it.
	func set_error(text: String) -> void:
		error.text = text
		error.visible = not text.is_empty()
		if control is LineEdit or control is TextEdit:
			var base := "JoineryLineEditError" if control is LineEdit else "JoineryTextEditError"
			if not text.is_empty() and control.theme_type_variation != base:
				control.set_meta("_joinery_variation", control.theme_type_variation)
				control.theme_type_variation = base
			elif text.is_empty() and control.has_meta("_joinery_variation"):
				control.theme_type_variation = control.get_meta("_joinery_variation")
				control.remove_meta("_joinery_variation")


## A titled group of rows. Add rows to [member body]. A collapsible section has a disclosure
## header and starts closed.
class Section:
	extends VBoxContainer
	signal toggled(open: bool)
	var header: Control
	var body: VBoxContainer
	var open := true

	## Open or close the body; rows settle in with a reveal and a stagger.
	func set_open(value: bool) -> void:
		open = value
		body.visible = value
		if header is Button:
			_SCRIPT.bind_icon(header, "disclosure_open" if value else "disclosure_closed")
			(header as Button).set_pressed_no_signal(value)
		if value:
			UI_MOTION.reveal(body)
			UI_MOTION.stagger(body.get_children())
		toggled.emit(value)


## A compact tag: optional icon, a label that ellipsizes past 16em, optional remove button.
## Removing animates the chip out, then emits [signal removed] and frees it.
class Chip:
	extends PanelContainer
	signal removed
	var label: Label
	var remove: Button

	## Animate out, then emit removed and free the chip.
	func dismiss() -> void:
		UI_MOTION.exit(self, null, func() -> void: removed.emit(), true)


## A small state dot: "ready" (success), "working" (accent), "attention" (danger) or "off"
## (a faint ring). Set [member state]; give it a tooltip that names the state.
class StatusDot:
	extends Control
	var state := "off":
		set(value):
			state = value
			queue_redraw()

	func _notification(what: int) -> void:
		if what == NOTIFICATION_THEME_CHANGED:
			var side := roundf(8 * UI_THEME.unit(self))
			custom_minimum_size = Vector2(side, side)

	func _draw() -> void:
		var roles := {"ready": "success", "working": "accent", "attention": "danger"}
		var radius := minf(size.x, size.y) / 2.0
		var center := size / 2.0
		if roles.has(state):
			var ink: Color = UI_THEME.color(self, roles[state])
			draw_circle(center, radius, ink, true, -1.0, true)
		else:
			var width := maxf(1.0, roundf(UI_THEME.unit(self)))
			var faint := UI_THEME.color(self, "text_faint")
			draw_circle(center, radius - width / 2.0, faint, false, width, true)


## A settings row: title on the left, a control on the right, CONTROL_H.md tall, with the
## shared SPACE.2 side inset so titles line up with row buttons and section labels.
class SettingRow:
	extends MarginContainer
	var box: HBoxContainer
	var title: Label


## One app in an app list (the Project Manager wizard and the editor's Project apps dialog
## draw the same row): a CheckBox carrying the app name, its description in secondary text
## SPACE.3 after it, and a readiness badge (dot plus words) at the trailing edge. CONTROL_H.md
## tall; stack rows in a JoineryFlush VBoxContainer so every list has the same row pitch.
## Screens may subclass it to keep their own per-row state; pass the instance to app_row().
class AppRow:
	extends HBoxContainer
	var check: CheckBox
	var blurb: Label
	var badge: HBoxContainer

	## Show readiness as words plus a dot ("Ready" / "ready", "Not installed" / "off", …);
	## empty [param text] hides the badge.
	func set_readiness(text: String, state := "off", reason := "") -> void:
		_SCRIPT.set_status(badge, text, state)
		badge.tooltip_text = reason if not reason.is_empty() else text
		badge.visible = not text.is_empty()

	## A required app (Godot): the checkbox stays full size and checked in the normal
	## colours but cannot change, and the badge reads "Always included".
	func set_required(reason := "Every project uses Godot.") -> void:
		_SCRIPT.lock_check(check, reason)
		set_readiness("Always included", "ready", reason)


## A Label with an optional type variation (JoineryCaption, JoineryTitle, …). [param wrap]
## turns on word-smart wrapping; otherwise long text ellipsizes and the tooltip holds it.
static func label(text: String, variation := "", wrap := false) -> Label:
	var result := Label.new()
	result.text = text
	result.theme_type_variation = variation
	if wrap:
		result.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
		result.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	else:
		result.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
		result.draw.connect(_fit_label.bind(result))
	return result


## An ellipsizing label has no width of its own, so in a slot that does not expand (a status
## word, an app name beside its icon) a row squeezes it to nothing. Such a label keeps its
## text width, up to 16em, as its minimum; past that it ends in an ellipsis. Runs on draw, so
## it follows text and theme changes. A minimum width set by the caller is left alone.
static func _fit_label(label: Label) -> void:
	var owned := float(label.get_meta("_joinery_fit", 0.0))
	if label.has_meta("_joinery_fit_off") or label.custom_minimum_size.x != owned:
		label.set_meta("_joinery_fit_off", true)
		return
	var width := 0.0
	var fits := (
		label.text_overrun_behavior == TextServer.OVERRUN_TRIM_ELLIPSIS
		and label.autowrap_mode == TextServer.AUTOWRAP_OFF
		and (label.size_flags_horizontal & Control.SIZE_EXPAND) == 0
	)
	if fits and not label.text.is_empty():
		var font := label.get_theme_font("font")
		var size := label.get_theme_font_size("font_size")
		for line: String in label.text.split("\n"):
			width = maxf(width, font.get_string_size(line, HORIZONTAL_ALIGNMENT_LEFT, -1, size).x)
		width = minf(ceilf(width), 16.0 * size)
	if width != owned:
		label.set_meta("_joinery_fit", width)
		label.custom_minimum_size.x = width


## A field: [param title] above [param control], optional [param help] caption, hidden error.
static func field(title: String, control: Control, help := "") -> Field:
	var result := Field.new()
	result.theme_type_variation = "JoineryTight"
	result.label = label(title)
	result.add_child(result.label)
	result.control = control
	control.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	if not (control is TextEdit):
		bind_height(control, "md")
	if control.accessibility_name.is_empty():
		control.accessibility_name = title
	result.add_child(control)
	result.help = label(help, "JoineryCaption", true)
	result.help.visible = not help.is_empty()
	result.add_child(result.help)
	result.error = label("", "JoineryError", true)
	result.error.hide()
	result.add_child(result.error)
	return result


## A settings row with [param title] and an optional right-hand [param control].
static func setting_row(title: String, control: Control = null) -> SettingRow:
	var row := SettingRow.new()
	row.theme_type_variation = "JoineryInset"
	row.box = HBoxContainer.new()
	row.box.theme_type_variation = "JoineryRow"
	row.add_child(row.box)
	row.title = label(title)
	row.title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.box.add_child(row.title)
	if control != null:
		control.size_flags_vertical = Control.SIZE_SHRINK_CENTER
		if control.accessibility_name.is_empty():
			control.accessibility_name = title
		row.box.add_child(control)
	bind_height(row.box, "md", false)
	return row


## A full-width row button: label on the left, [param icon_name] on the right (a right
## chevron by default), hover fill, CONTROL_H.md tall.
static func row_button(text: String, icon_name := "disclosure_closed") -> Button:
	var button := _button(text, "JoineryRowButton")
	button.alignment = HORIZONTAL_ALIGNMENT_LEFT
	button.icon_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	button.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	button.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	if not icon_name.is_empty():
		bind_icon(button, icon_name)
	return button


## A section with a JoinerySection title. Collapsible sections use the [method disclosure]
## header, start closed and emit Section.toggled.
static func section(title: String, collapsible := false) -> Section:
	var result := Section.new()
	result.theme_type_variation = "JoineryTight"
	if collapsible:
		# The one disclosure look ("Advanced", "Details"): chevron first on the content edge.
		var header := disclosure(title)
		header.toggled.connect(result.set_open)
		result.header = header
	else:
		result.header = label(title, "JoinerySection")
	result.add_child(result.header)
	result.body = VBoxContainer.new()
	result.body.theme_type_variation = "JoineryTight"
	result.add_child(result.body)
	if collapsible:
		result.open = false
		result.body.hide()
		bind_icon(result.header, "disclosure_closed")
	return result


## A chip: [param icon_name] (optional), [param text], and a remove button when
## [param removable] (tooltip "Remove"). CONTROL_H.sm tall.
static func chip(text: String, icon_name := "", removable := false) -> Chip:
	var result := Chip.new()
	result.theme_type_variation = "JoineryChip"
	var row := HBoxContainer.new()
	row.theme_type_variation = "JoineryRowTight"
	result.add_child(row)
	if not icon_name.is_empty():
		var picture := TextureRect.new()
		picture.stretch_mode = TextureRect.STRETCH_KEEP_CENTERED
		picture.mouse_filter = Control.MOUSE_FILTER_IGNORE
		bind_icon(picture, icon_name, "texture")
		_on_theme(
			picture, func() -> void: picture.self_modulate = UI_THEME.color(picture, "text_muted")
		)
		row.add_child(picture)
	result.label = label(text)
	result.label.tooltip_text = text
	result.label.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	_on_theme(
		result.label,
		func() -> void:
			var font := result.label.get_theme_font("font")
			var size := result.label.get_theme_font_size("font_size")
			var cap := 16.0 * size
			var width := font.get_string_size(result.label.text, 0, -1, size).x
			result.label.custom_minimum_size.x = minf(width, cap)
			# Names over 16em end in an ellipsis; the tooltip has the full name.
			result.label.text_overrun_behavior = (
				TextServer.OVERRUN_TRIM_ELLIPSIS if width > cap else TextServer.OVERRUN_NO_TRIMMING
			)
	)
	row.add_child(result.label)
	if removable:
		result.remove = icon_button("close", "Remove")
		result.remove.theme_type_variation = "JoineryChipClose"
		result.remove.pressed.connect(result.dismiss)
		row.add_child(result.remove)
		_on_theme(
			result.remove,
			func() -> void:
				var close := result.remove
				# The smallest hit target (CONTROL_H.xs), still inside the sm chip.
				var side := UI_THEME.control_h(close, "xs")
				close.custom_minimum_size = Vector2(side, side)
		)
	bind_height(result, "sm", false)
	return result


## A status dot in [param state] ("ready", "working", "attention", "off").
static func status_dot(state := "off") -> StatusDot:
	var dot := StatusDot.new()
	dot.state = state
	dot.mouse_filter = Control.MOUSE_FILTER_PASS
	dot.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	return dot


## A status line: a small dot in [param state] ("ready", "working", "attention", "off") and
## [param text] in the secondary text colour. The one way to show readiness ("Ready",
## "Needs Docker", …) on every screen; it is plain text, never a button-shaped pill.
static func status(text: String, state := "off") -> HBoxContainer:
	var row := HBoxContainer.new()
	row.theme_type_variation = "JoineryRowTight"
	row.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	row.mouse_filter = Control.MOUSE_FILTER_PASS
	row.tooltip_text = text
	var dot := status_dot(state)
	dot.name = "Dot"
	row.add_child(dot)
	var words := label(text, "JoineryCaption")
	words.name = "Text"
	words.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	row.add_child(words)
	return row


## Lock [param check] on: checked, not focusable, drawn with the normal checked box and text
## colour (not the greyed disabled look), at the same height as every other row's checkbox.
## [param reason] becomes its tooltip.
static func lock_check(check: CheckBox, reason := "") -> void:
	check.button_pressed = true
	check.disabled = true
	check.focus_mode = Control.FOCUS_NONE
	check.mouse_filter = Control.MOUSE_FILTER_PASS
	if not reason.is_empty():
		check.tooltip_text = reason
	if check.has_meta("_joinery_locked"):
		return
	check.set_meta("_joinery_locked", true)
	_on_theme(
		check,
		func() -> void:
			# Each override re-sends theme_changed, so only set what differs.
			var box := check.get_theme_icon("checked")
			if check.get_theme_icon("checked_disabled") != box:
				check.add_theme_icon_override("checked_disabled", box)
			var ink := check.get_theme_color("font_color")
			if check.get_theme_color("font_disabled_color") != ink:
				check.add_theme_color_override("font_disabled_color", ink)
	)


## An app row (see [AppRow]) for [param app_name] with [param description] and readiness
## [param status_text] in [param state]. Pass [param row] to fill a subclass instance.
static func app_row(
	app_name: String, description: String, status_text := "", state := "off", row: AppRow = null
) -> AppRow:
	var result := row if row != null else AppRow.new()
	result.theme_type_variation = "JoineryStack"
	bind_height(result, "md", false)
	result.check = CheckBox.new()
	result.check.text = app_name
	result.check.focus_mode = Control.FOCUS_ALL
	result.check.accessibility_name = app_name
	bind_height(result.check, "md")
	result.add_child(result.check)
	result.blurb = label(description, "JoineryCaption")
	result.blurb.tooltip_text = description
	result.blurb.mouse_filter = Control.MOUSE_FILTER_PASS
	result.blurb.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	result.blurb.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	result.add_child(result.blurb)
	result.badge = status(status_text, state)
	result.badge.name = "Readiness"
	result.add_child(result.badge)
	result.set_readiness(status_text, state)
	return result


## An outcome glyph for [param kind] ("done", "failed", "warning", "stopped"): the outlined
## icon in its semantic colour, one icon size, re-tinted on every theme change.
static func status_icon(kind := "done") -> TextureRect:
	var rect := TextureRect.new()
	rect.stretch_mode = TextureRect.STRETCH_KEEP_CENTERED
	rect.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	rect.mouse_filter = Control.MOUSE_FILTER_IGNORE
	set_status_icon(rect, kind)
	_on_theme(rect, func() -> void: _tint_status_icon(rect))
	return rect


## Switch an icon made by [method status_icon] to [param kind].
static func set_status_icon(rect: TextureRect, kind: String) -> void:
	var spec: Array = STATUS_ICONS.get(kind, STATUS_ICONS["done"])
	rect.set_meta("_joinery_status_kind", kind)
	bind_icon(rect, str(spec[0]), "texture")
	if rect.is_inside_tree():
		_tint_status_icon(rect)


static func _tint_status_icon(rect: TextureRect) -> void:
	var kind := str(rect.get_meta("_joinery_status_kind", "done"))
	var spec: Array = STATUS_ICONS.get(kind, STATUS_ICONS["done"])
	rect.self_modulate = UI_THEME.color(rect, str(spec[1]))


## Update a row made by [method status].
static func set_status(row: HBoxContainer, text: String, state: String) -> void:
	(row.get_node("Dot") as StatusDot).state = state
	(row.get_node("Text") as Label).text = text
	row.tooltip_text = text


## Give an AcceptDialog or ConfirmationDialog the shared Joinery footer, the one footer
## pattern for every dialog: an expanding spacer, then the secondary actions (Cancel and any
## extra buttons), then the OK action, all flush with the content's trailing edge and SPACE.2
## apart (OK comes first on platforms that swap OK and Cancel). Every button is CONTROL_H.md
## tall with one shared minimum width. Buttons without a type variation (or with
## JoineryQuietButton) get JoineryPrimaryButton (OK) or JoinerySecondaryButton (the rest).
## Opening the dialog focuses OK without drawing the focus ring (the ring shows once the
## keyboard moves focus), or the first field while OK is disabled. Call it
## after adding the dialog's buttons; buttons added later are arranged too.
static func dialog_footer(dialog: AcceptDialog) -> void:
	var ok := dialog.get_ok_button()
	var row := ok.get_parent() as HBoxContainer
	if row == null or row.has_meta("_joinery_footer"):
		return
	var spacer := Control.new()
	spacer.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	spacer.mouse_filter = Control.MOUSE_FILTER_IGNORE
	row.add_child(spacer)
	row.set_meta("_joinery_footer", spacer)
	row.alignment = BoxContainer.ALIGNMENT_BEGIN
	_arrange_footer(dialog, row)
	row.child_entered_tree.connect(
		func(_node: Node) -> void: _arrange_footer(dialog, row), CONNECT_DEFERRED
	)
	dialog.visibility_changed.connect(
		func() -> void:
			if dialog.visible and ok.is_inside_tree():
				_initial_focus.call_deferred(dialog)
	)
	_on_theme(row, func() -> void: _footer_sizes(dialog, row))


## Focus OK without drawing the ring; while OK is disabled (a list still loading), focus the
## first editable field instead, so a disabled button never carries the focus ring.
static func _initial_focus(dialog: AcceptDialog) -> void:
	if not is_instance_valid(dialog) or not dialog.visible:
		return
	var ok := dialog.get_ok_button()
	_sync_focus(ok)
	if not ok.disabled:
		if ok.focus_mode != Control.FOCUS_NONE:
			ok.grab_focus(true)
		return
	var field := _first_field(dialog)
	if field != null:
		field.grab_focus()


static func _first_field(node: Node) -> Control:
	for child: Node in node.get_children():
		if child is LineEdit or child is TextEdit:
			var field := child as Control
			var editable := bool(field.get("editable"))
			if field.is_visible_in_tree() and editable and field.focus_mode != Control.FOCUS_NONE:
				return field
		var found := _first_field(child)
		if found != null:
			return found
	return null


static func _arrange_footer(dialog: AcceptDialog, row: HBoxContainer) -> void:
	if not is_instance_valid(row):
		return
	var ok := dialog.get_ok_button()
	var spacer: Control = row.get_meta("_joinery_footer")
	for child: Node in row.get_children():
		if child is Button:
			var button := child as Button
			if button.has_meta("_joinery_footer_button"):
				continue
			button.set_meta("_joinery_footer_button", true)
			# Footer actions are buttons, never bare text: OK is primary, a quiet or plain
			# Cancel becomes secondary so it matches OK's height and weight.
			var variation := button.theme_type_variation
			if variation.is_empty() or variation == "JoineryQuietButton":
				button.theme_type_variation = (
					"JoineryPrimaryButton" if button == ok else "JoinerySecondaryButton"
				)
			button.focus_mode = Control.FOCUS_ALL
			_steady_states(button)
			button.minimum_size_changed.connect(_footer_sizes.bind(dialog, row), CONNECT_DEFERRED)
			button.visibility_changed.connect(_footer_sizes.bind(dialog, row), CONNECT_DEFERRED)
		elif child != spacer and child is Control:
			# Godot's own centring spacers: hidden, and kept hidden when the dialog shows them
			# again with their button, so the buttons stay together on the trailing edge.
			var godot_spacer := child as Control
			godot_spacer.hide()
			if not godot_spacer.has_meta("_joinery_hidden"):
				godot_spacer.set_meta("_joinery_hidden", true)
				godot_spacer.visibility_changed.connect(godot_spacer.hide, CONNECT_DEFERRED)
	row.move_child(spacer, 0)
	row.move_child(ok, 1 if DisplayServer.get_swap_cancel_ok() else -1)
	_footer_sizes(dialog, row)


## The dialog applies buttons_min_width/height to every footer button on each layout, so the
## shared size goes into those constants rather than into the buttons.
static func _footer_sizes(dialog: AcceptDialog, row: HBoxContainer) -> void:
	if not is_instance_valid(row) or not row.is_inside_tree():
		return
	var widest := 0
	for child: Node in row.get_children():
		if child is Button and (child as Button).visible:
			widest = maxi(widest, ceili((child as Button).get_minimum_size().x))
	var height := UI_THEME.control_h(row, "md")
	var gap := UI_THEME.space(row, 2)
	if dialog.get_theme_constant("buttons_separation") != gap:
		dialog.add_theme_constant_override("buttons_separation", gap)
	if row.get_theme_constant("separation") != gap:
		row.add_theme_constant_override("separation", gap)
	if dialog.get_theme_constant("buttons_min_width") != widest:
		dialog.add_theme_constant_override("buttons_min_width", widest)
	if dialog.get_theme_constant("buttons_min_height") != height:
		dialog.add_theme_constant_override("buttons_min_height", height)


## The one accent-filled action of a view.
static func primary(text: String) -> Button:
	return _button(text, "JoineryPrimaryButton")


## A secondary action (raised fill, no border).
static func secondary(text: String) -> Button:
	return _button(text, "JoinerySecondaryButton")


## A quiet action: muted text, fill only on hover.
static func quiet(text: String) -> Button:
	return _button(text, "JoineryQuietButton")


## An inline link-style action in the accent colour.
static func link(text: String) -> Button:
	return _button(text, "JoineryLinkButton")


## A disclosure toggle ("Details"): muted text after a chevron that turns when open, no fill
## in any state, no side inset (the chevron starts on the content edge), CONTROL_H.md tall
## like the buttons beside it. Connect to [signal BaseButton.toggled].
static func disclosure(text: String) -> Button:
	var button := _button(text, "JoineryDisclosureButton")
	button.toggle_mode = true
	button.alignment = HORIZONTAL_ALIGNMENT_LEFT
	button.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	bind_icon(button, "disclosure_closed")
	button.toggled.connect(
		func(open: bool) -> void:
			bind_icon(button, "disclosure_open" if open else "disclosure_closed")
	)
	return button


## A square icon-only button; [param tooltip] doubles as its accessible name.
static func icon_button(icon_name: String, tooltip: String) -> Button:
	var button := _button("", "JoineryIconButton")
	button.tooltip_text = tooltip
	button.accessibility_name = tooltip
	bind_icon(button, icon_name)
	_on_theme(
		button,
		func() -> void:
			var side := float(UI_THEME.control_h(button, "md"))
			button.custom_minimum_size = Vector2(side, side)
	)
	return button


## A card surface: one radius (lg), one padding (SPACE.3).
static func card() -> PanelContainer:
	var result := PanelContainer.new()
	result.theme_type_variation = "JoineryCard"
	return result


## An on/off switch with the knob travel animation; [param text] is its label (may be "" when
## a row title labels it, in which case set accessibility_name).
static func switch(text := "") -> CheckButton:
	var check := CheckButton.new()
	check.text = text
	check.theme_type_variation = "JoinerySwitch"
	check.focus_mode = Control.FOCUS_ALL
	UI_MOTION.attach_switch(check)
	return check


## A quiet picker (OptionButton, JoineryPicker) holding [param items]. [param height] is
## "md" for settings and forms, "sm" for the composer row. Its width follows the chosen item
## so the chevron hugs the text; where space is tight, also set text_overrun_behavior and a
## custom_minimum_size.x cap (an ellipsizing picker otherwise shrinks to its chevron).
static func picker(items: Array = [], height := "md") -> OptionButton:
	var result := OptionButton.new()
	result.theme_type_variation = "JoineryPicker"
	result.fit_to_longest_item = false
	for item: Variant in items:
		result.add_item(str(item))
	bind_height(result, height)
	return result


## Keep [param control]'s [param property] ("icon" or "texture") set to the named icon at the
## current editor scale, now and after every theme change.
static func bind_icon(control: Control, icon_name: String, property := "icon") -> void:
	control.set_meta("_joinery_icon", icon_name)
	if control.has_meta("_joinery_icon_bound"):
		if control.is_inside_tree():
			control.set(property, UI_THEME.icon(control, icon_name))
		return
	control.set_meta("_joinery_icon_bound", true)
	_on_theme(
		control,
		func() -> void:
			control.set(property, UI_THEME.icon(control, str(control.get_meta("_joinery_icon"))))
	)


## Keep [param control] exactly CONTROL_H[[param height]] tall at its minimum, now and after
## every theme change. With [param shrink] it is centred in its row instead of stretched.
static func bind_height(control: Control, height := "md", shrink := true) -> void:
	if shrink:
		control.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_on_theme(
		control, func() -> void: control.custom_minimum_size.y = UI_THEME.control_h(control, height)
	)


## Keep [param control] at least CONTROL_H[[param width]] wide, re-applied on every theme
## change: a bare CheckBox stays a full hit target next to its separate label.
static func bind_width(control: Control, width := "xs") -> void:
	_on_theme(
		control, func() -> void: control.custom_minimum_size.x = UI_THEME.control_h(control, width)
	)


static func _button(text: String, variation: String) -> Button:
	var button := Button.new()
	button.text = text
	button.theme_type_variation = variation
	button.focus_mode = Control.FOCUS_ALL
	if not text.is_empty():
		button.accessibility_name = text
	bind_height(button, "md")
	UI_MOTION.press_feedback(button)
	_steady_states(button)
	return button


## Keep a button the same size and focus behaviour in every state:
## - a "disabled" stylebox override (a screen keeping the enabled look while busy) is
##   re-taken from the current "normal" box whenever its insets differ, so a box captured
##   before the theme arrived can never make one button of a row taller than its neighbour;
## - a disabled button takes no focus and drops it (no accent ring on a control that cannot
##   act); its focus mode comes back when it is enabled again.
static func _steady_states(button: Button) -> void:
	if button.has_meta("_joinery_steady"):
		return
	button.set_meta("_joinery_steady", true)
	_on_theme(button, func() -> void: _sync_disabled_box(button))
	button.draw.connect(func() -> void: _sync_focus.call_deferred(button))


static func _sync_disabled_box(button: Button) -> void:
	if not button.has_theme_stylebox_override("disabled"):
		return
	var normal := button.get_theme_stylebox("normal")
	var disabled := button.get_theme_stylebox("disabled")
	if normal == null or disabled == normal:
		return
	for side: int in [SIDE_LEFT, SIDE_TOP, SIDE_RIGHT, SIDE_BOTTOM]:
		if not is_equal_approx(disabled.get_margin(side), normal.get_margin(side)):
			# Keep the override's own look (its fill and text colour say "off"); only its
			# insets follow the normal box, so the button never changes size.
			var fixed := disabled.duplicate() as StyleBox
			for edge: int in [SIDE_LEFT, SIDE_TOP, SIDE_RIGHT, SIDE_BOTTOM]:
				fixed.set_content_margin(edge, normal.get_margin(edge))
			button.add_theme_stylebox_override("disabled", fixed)
			return


static func _sync_focus(button: Button) -> void:
	if not is_instance_valid(button):
		return
	if button.disabled:
		if button.focus_mode != Control.FOCUS_NONE:
			button.set_meta("_joinery_focus_off", button.focus_mode)
			button.focus_mode = Control.FOCUS_NONE
		if button.has_focus():
			button.release_focus()
	elif button.has_meta("_joinery_focus_off"):
		button.focus_mode = button.get_meta("_joinery_focus_off")
		button.remove_meta("_joinery_focus_off")


static func _on_theme(control: Control, apply: Callable) -> void:
	if control.is_inside_tree():
		apply.call()
	control.theme_changed.connect(apply)
