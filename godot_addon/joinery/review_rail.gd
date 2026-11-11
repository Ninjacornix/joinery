@tool
extends VBoxContainer
## The review's rail, beside the stage (review_layout.gd), top to bottom: where this change sits
## among those waiting ("2 of 5" with previous and next, and All approvals back to the list);
## the request as the title, up to two lines; one line of facts and the checks as chips
## (review_summary.gd); the ways of a request tried several ways (variant_strip.gd, placed here
## by review_tasks.gd); then, anchored at the foot: Details, which slides a drawer over the
## stage, Ask for changes and, for a scene edit, Open in editor, as quiet links on one line, and
## under them Discard and a large Accept. The middle scrolls when it is long, so the actions are
## always on screen.
##
## It only builds and lays out; the panel (review_panel.gd) decides what the buttons do.

## All approvals: back to the list.
signal list_requested
## Previous (-1) or next (1) change waiting.
signal step_requested(step: int)

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const INDICATOR := preload("activity_indicator.gd")
const SUMMARY := preload("review_summary.gd")

var nav: HBoxContainer
var back: Button
var previous: Button
var next: Button
var place: Label
## The title block: the landing spot of the chat card's shared-element entrance.
var head: VBoxContainer
var title: Label
var summary: SUMMARY
var details_button: Button
## Where the ways of a request tried several ways go (empty otherwise).
var slot: VBoxContainer
var error_row: HBoxContainer
var error_icon: TextureRect
var error: Label
var actions: VBoxContainer
var links: HFlowContainer
var revise: Button
var open_link: Button
var buttons: HBoxContainer
var reject: Button
var accept: Button
var applying: HBoxContainer
var applying_dots: INDICATOR
var _scroll: ScrollContainer
var _info: VBoxContainer
var _total := -1


func _init() -> void:
	name = "Rail"
	theme_type_variation = "JoineryStack"
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	size_flags_vertical = Control.SIZE_EXPAND_FILL
	accessibility_name = "The change and its actions"
	_build_nav()
	_scroll = ScrollContainer.new()
	_scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	_scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_scroll.follow_focus = true
	_scroll.accessibility_name = "The change"
	UI_MOTION.rubber_band(_scroll)
	add_child(_scroll)
	_info = VBoxContainer.new()
	_info.theme_type_variation = "JoineryStack"
	_info.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_scroll.add_child(_info)
	head = VBoxContainer.new()
	head.theme_type_variation = "JoineryTight"
	_info.add_child(head)
	title = UI.label("", "JoineryBodyLarge", true)
	title.max_lines_visible = 2
	title.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	title.mouse_filter = Control.MOUSE_FILTER_PASS
	head.add_child(title)
	summary = SUMMARY.new()
	_info.add_child(summary)
	slot = VBoxContainer.new()
	slot.name = "Ways"
	slot.theme_type_variation = "JoineryTight"
	_info.add_child(slot)
	_build_error()
	_build_actions()


## The queue around this change: [param index] (0-based) of [param total] waiting. A negative
## total hides the row (a panel outside the Review tab); one change shows All approvals only.
func set_queue(index: int, total: int) -> void:
	_total = total
	nav.visible = total >= 0
	var several := total > 1 and index >= 0
	place.text = "%d of %d" % [index + 1, total] if several else ""
	for part: Control in [previous, place, next]:
		part.visible = several
	previous.disabled = not several or index <= 0
	next.disabled = not several or index >= total - 1


## True when previous and next have somewhere to go.
func has_queue() -> bool:
	return _total > 1


## The height the rail would like: everything without scrolling.
func wanted_height() -> float:
	var gap := float(get_theme_constant("separation"))
	var total := 0.0
	var shown := 0
	for part: Control in [nav, error_row, actions]:
		if part.visible:
			total += part.get_combined_minimum_size().y
			shown += 1
	total += _info.get_combined_minimum_size().y
	return total + gap * shown


func _build_nav() -> void:
	nav = HBoxContainer.new()
	nav.name = "Queue"
	nav.theme_type_variation = "JoineryRowTight"
	nav.hide()
	add_child(nav)
	back = UI.quiet("All approvals")
	back.alignment = HORIZONTAL_ALIGNMENT_LEFT
	back.tooltip_text = "Back to every change waiting for you and the ones you decided"
	UI.bind_icon(back, "back")
	UI.bind_height(back, "sm")
	back.pressed.connect(func() -> void: list_requested.emit())
	nav.add_child(back)
	var push := Control.new()
	push.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	push.mouse_filter = Control.MOUSE_FILTER_IGNORE
	nav.add_child(push)
	previous = _arrow("previous", "Previous change waiting (←)", -1)
	nav.add_child(previous)
	place = UI.label("", "JoineryNumeric")
	place.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	place.text_overrun_behavior = TextServer.OVERRUN_NO_TRIMMING
	nav.add_child(place)
	next = _arrow("next", "Next change waiting (→)", 1)
	nav.add_child(next)


func _arrow(icon_name: String, hint: String, step: int) -> Button:
	var arrow := UI.quiet("")
	arrow.tooltip_text = hint
	arrow.accessibility_name = hint
	arrow.icon_alignment = HORIZONTAL_ALIGNMENT_CENTER
	UI.bind_icon(arrow, icon_name)
	UI.bind_height(arrow, "sm")
	UI.bind_width(arrow, "sm")
	arrow.pressed.connect(func() -> void: step_requested.emit(step))
	return arrow


func _build_error() -> void:
	error_row = HBoxContainer.new()
	error_row.theme_type_variation = "JoineryRowTight"
	error_row.hide()
	add_child(error_row)
	error_icon = TextureRect.new()
	error_icon.stretch_mode = TextureRect.STRETCH_KEEP_CENTERED
	error_icon.size_flags_vertical = Control.SIZE_SHRINK_BEGIN
	error_icon.mouse_filter = Control.MOUSE_FILTER_IGNORE
	UI.bind_icon(error_icon, "error", "texture")
	error_row.add_child(error_icon)
	error = UI.label("", "JoineryError", true)
	error.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	error.hide()
	error_row.add_child(error)


func _build_actions() -> void:
	actions = VBoxContainer.new()
	actions.name = "Actions"
	actions.theme_type_variation = "JoineryTight"
	add_child(actions)
	links = HFlowContainer.new()
	links.name = "Links"
	actions.add_child(links)
	# Details slide a drawer over the stage, from this side: the chevron points where it opens.
	details_button = _link("Details", "previous", "Files, checks and notes")
	details_button.toggle_mode = true
	details_button.accessibility_name = "Show or hide the files, checks and notes"
	revise = _link(
		"Ask for changes", "compose", "Say in the chat what to change, with this change attached"
	)
	open_link = _link("Open in editor", "external", "Select the changed nodes in the scene")
	open_link.accessibility_name = "Open the change in the editor: select the changed nodes"
	links.hide()
	buttons = HBoxContainer.new()
	buttons.theme_type_variation = "JoineryRow"
	actions.add_child(buttons)
	reject = UI.secondary("Discard")
	reject.accessibility_name = "Discard this change"
	accept = UI.primary("Accept")
	accept.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	# Follow the editor's OK/Cancel order: the primary action sits on the trailing edge
	# unless the platform puts OK first.
	var ordered := [accept, reject] if DisplayServer.get_swap_cancel_ok() else [reject, accept]
	for button: Button in ordered:
		buttons.add_child(button)
	applying = HBoxContainer.new()
	applying.theme_type_variation = "JoineryRowTight"
	applying.alignment = BoxContainer.ALIGNMENT_CENTER
	applying.mouse_filter = Control.MOUSE_FILTER_IGNORE
	applying.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	applying.hide()
	accept.add_child(applying)
	applying_dots = INDICATOR.new()
	applying.add_child(applying_dots)
	var label := UI.label("Applying…")
	label.text_overrun_behavior = TextServer.OVERRUN_NO_TRIMMING
	label.mouse_filter = Control.MOUSE_FILTER_IGNORE
	label.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	applying.add_child(label)


## Show the links row only when one of its links is on.
func sync_links() -> void:
	links.visible = details_button.visible or revise.visible or open_link.visible


func _notification(what: int) -> void:
	if what == NOTIFICATION_THEME_CHANGED and links != null:
		links.add_theme_constant_override("h_separation", UI_THEME.space(self, 5))
		links.add_theme_constant_override("v_separation", UI_THEME.space(self, 1))


## A quiet link on the actions' first line: muted ink after its glyph, no inset, so the first
## one starts on the rail's content edge.
func _link(text: String, icon_name: String, hint: String) -> Button:
	var link := UI.quiet(text)
	link.theme_type_variation = "JoinerySectionButton"
	link.alignment = HORIZONTAL_ALIGNMENT_LEFT
	link.tooltip_text = hint
	UI.bind_icon(link, icon_name)
	UI.bind_height(link, "sm")
	link.hide()
	links.add_child(link)
	return link
