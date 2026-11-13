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
## Readiness status (design addendum S, ADR 0030) -> [wording, dot state]: the one vocabulary
## for project setup, the app rail and the asset workspace.
const READINESS := {
	"ready": ["Ready", "ready"],
	"preparing": ["Refreshing…", "working"],
	"needs_docker": ["Needs Docker", "attention"],
	"needs_licence": ["Needs your licence", "attention"],
	"unavailable": ["Not available yet", "off"],
}
const _INDICATOR := preload("activity_indicator.gd")


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


## A compact tag: optional icon, a label that ellipsizes past 16em (or past
## [member max_width]), optional remove button. Removing animates the chip out, then emits
## [signal removed] and frees it.
class Chip:
	extends PanelContainer
	signal removed
	var label: Label
	var remove: Button
	## The widest the whole chip may be, so it fits the row it sits in; 0 leaves only the 16em
	## cap on its name. A longer name ends in an ellipsis; the tooltip has the full name.
	var max_width := 0.0:
		set(value):
			max_width = value
			fit()

	## Animate out, then emit removed and free the chip.
	func dismiss() -> void:
		UI_MOTION.exit(self, null, func() -> void: removed.emit(), true)

	## Size the name to the text, up to 16em and [member max_width].
	func fit() -> void:
		if label == null:
			return
		var font := label.get_theme_font("font")
		var font_size := label.get_theme_font_size("font_size")
		var width := font.get_string_size(label.text, 0, -1, font_size).x
		var cap := 16.0 * font_size
		if max_width > 0.0:
			# Icon, gaps, × and the chip's own padding: everything but the name.
			var chrome := get_combined_minimum_size().x - label.get_combined_minimum_size().x
			cap = minf(cap, maxf(2.0 * font_size, max_width - chrome))
		label.custom_minimum_size.x = minf(width, cap)
		label.text_overrun_behavior = (
			TextServer.OVERRUN_TRIM_ELLIPSIS if width > cap else TextServer.OVERRUN_NO_TRIMMING
		)


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
			var side := float(UI_THEME.dot_px(self))
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


## A settings row with a switch: title (and an optional description) on the left, the switch
## on the trailing edge, CONTROL_H.md tall. When the switch cannot change, a caption under the
## title says why ([method set_disabled_reason]); a disabled switch never stands unexplained.
class SwitchRow:
	extends MarginContainer
	signal toggled(on: bool)
	var title: Label
	var description: Label
	var reason: Label
	var switch_button: CheckButton

	## Set the switch without emitting [signal toggled] (a programmatic change).
	func set_on(on: bool) -> void:
		switch_button.set_pressed_no_signal(on)

	func is_on() -> bool:
		return switch_button.button_pressed

	## Disable the switch and show [param text] under the title; "" enables it again.
	func set_disabled_reason(text: String) -> void:
		switch_button.disabled = not text.is_empty()
		reason.text = text
		reason.visible = not text.is_empty()
		switch_button.tooltip_text = text


## The one action a readiness state offers ("Start Docker", "Rebuild", "Check again",
## "Get it"), as a secondary button. [method set_action] takes the `action` field of a
## readiness entry ({id, label} or null) and hides the button when there is nothing to do.
class StatusAction:
	extends Button
	var action_id := ""

	func set_action(value: Variant) -> void:
		var spec: Dictionary = value if value is Dictionary else {}
		action_id = str(spec.get("id", ""))
		text = str(spec.get("label", ""))
		accessibility_name = text
		visible = not action_id.is_empty()


## An app's readiness at the trailing edge of its row: a dot, the plain wording, a busy
## indicator while an action runs, and the one action button. [method set_entry] takes an
## entry from `joinery status` (ADR 0030); its tooltip adds the summary and when it was last
## checked, so a stalled check never looks current.
class Readiness:
	extends HBoxContainer
	var entry: Dictionary = {}
	var dot: StatusDot
	var wording: Label
	var busy: Control
	var action: StatusAction

	## Show [param value]; an unknown status hides the badge and the action.
	func set_entry(value: Dictionary) -> void:
		var key := str(value.get("status", ""))
		if not _SCRIPT.READINESS.has(key):
			hide()
			action.set_action(null)
			return
		entry = value
		var spec: Array = _SCRIPT.READINESS[key]
		var checking := bool(value.get("checking", false)) and key == "ready"
		dot.state = "working" if checking else str(spec[1])
		wording.text = "Checking…" if checking else str(spec[0])
		var tip := str(value.get("summary", ""))
		var stamp := _SCRIPT.last_checked(str(value.get("checked_at", "")))
		var shown := tip if not tip.is_empty() else wording.text
		tooltip_text = shown if stamp.is_empty() else shown + "\n" + stamp
		action.set_action(value.get("action"))
		show()

	## While an action runs: the dots and [param text] (e.g. "Checking…"), the button off.
	func set_busy(on: bool, text := "") -> void:
		busy.visible = on
		busy.set("running", on)
		action.disabled = on
		if on and not text.is_empty():
			wording.text = text


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
	_on_theme(result.label, result.fit)
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


## "Last checked 14:02" in local time for a readiness `checked_at` (ISO 8601 with an offset),
## or "" when it is missing or unreadable. Shown beside readiness so a stalled check never
## looks current.
static func last_checked(iso: String) -> String:
	if iso.length() < 19:
		return ""
	var unix := Time.get_unix_time_from_datetime_string(iso.substr(0, 19))
	if unix <= 0:
		return ""
	var offset := iso.substr(19)
	if offset.length() >= 6 and offset[0] in ["+", "-"]:
		var minutes := offset.substr(1, 2).to_int() * 60 + offset.substr(4, 2).to_int()
		unix -= (1 if offset[0] == "+" else -1) * minutes * 60
	var bias := int(Time.get_time_zone_from_system().get("bias", 0)) * 60
	var local := Time.get_datetime_dict_from_unix_time(unix + bias)
	return "Last checked %02d:%02d" % [int(local["hour"]), int(local["minute"])]


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
## A small question on [param parent]: [param buttons] is [OK text, Cancel text]. The dialog
## frees itself once answered; [param on_ok] runs on OK.
static func confirm(
	parent: Control, title: String, text: String, buttons: Array, on_ok: Callable
) -> ConfirmationDialog:
	var dialog := ConfirmationDialog.new()
	dialog.title = title
	dialog.dialog_text = text
	dialog.dialog_autowrap = true
	dialog.ok_button_text = str(buttons[0])
	dialog.cancel_button_text = str(buttons[1])
	dialog.theme = UI_THEME.build(parent)
	dialog.get_ok_button().theme_type_variation = "JoineryPrimaryButton"
	dialog.get_cancel_button().theme_type_variation = "JoinerySecondaryButton"
	dialog_footer(dialog)
	dialog.confirmed.connect(on_ok)
	dialog.visibility_changed.connect(
		func() -> void:
			if not dialog.visible:
				dialog.queue_free()
	)
	parent.add_child(dialog)
	var size := UI_THEME.dialog_size(parent, "connect")
	dialog.popup_centered(Vector2i(size.x, 0))
	return dialog


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


## A link-style action in the muted text colour (Check again, Show all, a row's Fix).
static func quiet_link(text: String) -> Button:
	return _button(text, "JoineryQuietLink")


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


## A [SwitchRow] titled [param title] with an optional [param description] caption, starting
## [param on]. Connect to SwitchRow.toggled; call set_disabled_reason() to lock it with a why.
static func switch_row(title: String, description := "", on := false) -> SwitchRow:
	var row := SwitchRow.new()
	row.theme_type_variation = "JoineryInset"
	var line := HBoxContainer.new()
	line.theme_type_variation = "JoineryRow"
	row.add_child(line)
	var words := VBoxContainer.new()
	words.theme_type_variation = "JoineryFlush"
	words.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	words.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	line.add_child(words)
	row.title = label(title)
	row.title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	words.add_child(row.title)
	row.description = label(description, "JoineryCaption", true)
	row.description.visible = not description.is_empty()
	words.add_child(row.description)
	row.reason = label("", "JoineryCaption", true)
	row.reason.name = "Reason"
	row.reason.hide()
	words.add_child(row.reason)
	row.switch_button = switch()
	row.switch_button.accessibility_name = title
	row.switch_button.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	row.switch_button.set_pressed_no_signal(on)
	row.switch_button.toggled.connect(func(value: bool) -> void: row.toggled.emit(value))
	line.add_child(row.switch_button)
	bind_height(line, "md", false)
	return row


## A banner with one sentence and one secondary action ("Lost connection. [Reconnect]",
## "Open Docker Desktop… [Check again]"). The text label is the "Text" child and the button
## the "Action" child; connect to the button's pressed signal. [param variation] is
## JoineryBanner for something that went wrong, JoineryCard for plain guidance.
static func notice(
	text: String, action_text: String, variation := "JoineryBanner"
) -> PanelContainer:
	var panel := PanelContainer.new()
	panel.theme_type_variation = variation
	var line := HBoxContainer.new()
	line.theme_type_variation = "JoineryRow"
	panel.add_child(line)
	var words := label(text, "", true)
	words.name = "Text"
	words.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	line.add_child(words)
	var button := secondary(action_text)
	button.name = "Action"
	button.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	line.add_child(button)
	return panel


## A centred empty state: a faint [param icon_name] icon, a [param title], an optional
## [param line] saying what to do, and an optional [param action] button. Fills the height.
static func empty_state(
	icon_name: String, title: String, line := "", action: Button = null
) -> VBoxContainer:
	var block := VBoxContainer.new()
	block.theme_type_variation = "JoineryTight"
	block.alignment = BoxContainer.ALIGNMENT_CENTER
	block.size_flags_vertical = Control.SIZE_EXPAND_FILL
	var icon := TextureRect.new()
	icon.stretch_mode = TextureRect.STRETCH_KEEP_CENTERED
	icon.mouse_filter = Control.MOUSE_FILTER_IGNORE
	bind_icon(icon, icon_name, "texture")
	icon.theme_changed.connect(
		func() -> void: icon.self_modulate = UI_THEME.color(icon, "text_faint")
	)
	block.add_child(icon)
	var heading := label(title, "JoineryBodyLarge")
	heading.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	block.add_child(heading)
	if not line.is_empty():
		var hint := label(line, "JoineryCaption", true)
		hint.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
		block.add_child(hint)
	if action != null:
		action.size_flags_horizontal = Control.SIZE_SHRINK_CENTER
		block.add_child(action)
	return block


## A [Readiness] badge, hidden until [method Readiness.set_entry] gets a known status.
static func readiness(entry: Dictionary = {}) -> Readiness:
	var badge := Readiness.new()
	badge.name = "Readiness"
	badge.theme_type_variation = "JoineryRow"
	badge.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	badge.mouse_filter = Control.MOUSE_FILTER_PASS
	var words := status("", "off")
	words.mouse_filter = Control.MOUSE_FILTER_IGNORE
	badge.dot = words.get_node("Dot") as StatusDot
	badge.dot.mouse_filter = Control.MOUSE_FILTER_IGNORE
	badge.wording = words.get_node("Text") as Label
	badge.add_child(words)
	badge.busy = _INDICATOR.new()
	badge.add_child(badge.busy)
	badge.action = status_action()
	badge.action.name = "Action"
	badge.add_child(badge.action)
	badge.hide()
	badge.set_entry(entry)
	return badge


## A [StatusAction] button for a readiness entry's [param action] ({id, label} or null).
static func status_action(action: Variant = null) -> StatusAction:
	var button := StatusAction.new()
	button.theme_type_variation = "JoinerySecondaryButton"
	button.focus_mode = Control.FOCUS_ALL
	bind_height(button, "md")
	UI_MOTION.press_feedback(button)
	_steady_states(button)
	button.set_action(action)
	return button


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
