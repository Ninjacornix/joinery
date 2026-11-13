@tool
extends VBoxContainer
## The app rail beside the viewport. The fork provides the column (JoineryNavigation); the
## addon fills it: Godot, All assets, then each project app, and the viewport tools toggle at
## the bottom.
##
## Each app shows its official logo in its own colours (icons/apps, see licenses/tool-logos.md);
## an app without one (and All assets, the viewport tools toggle and custom tools) shows a
## monochrome glyph tinted text, or accent when selected. The selected item gets an accent bar
## on the rail's left edge and a raised pill behind its icon; a logo is never recoloured. Icons
## are rendered again whenever the theme turns light or dark. Each app shows its readiness from
## `joinery status --catalog --background --json`, the one readiness source (ADR 0030): a dot
## only when the app needs something, and one pulse when it becomes ready. Hovering or focusing
## an app shows a card with the plain sentence, its one action, Check again and when it was
## last checked. Every command runs as a job, off the UI thread.

signal tool_selected(tool: String)
signal toolbar_requested

const UI_THEME := preload("ui_theme.gd")
const UI_COMPONENTS := preload("ui_components.gd")
const UI_MOTION := preload("ui_motion.gd")
const COMMAND_SCRIPT := preload("command_job.gd")
## The id of the "All assets" item: the workspace's unfiltered view.
const ALL_ASSETS := ""
## Readiness words, the same as ui_components.READINESS (kept here for callers that read them).
const STATUS_TEXT := {
	"ready": "Ready",
	"preparing": "Refreshing…",
	"needs_docker": "Needs Docker",
	"needs_licence": "Needs your licence",
	"unavailable": "Not available yet",
}
## Wording and dot per readiness come from ui_components.READINESS, the same as project setup:
## an accent dot while the app is getting ready, a red dot when it needs you, a hollow ring
## while it can't be used yet. Ready (and an unknown status) shows no mark, so the rail stays
## quiet until an app needs something.
const DOCKER_HOWTO := "Opening Docker Desktop. Joinery checks again by itself once it runs."
## Fallback glyph (ui_theme icon) by catalog domain, for apps without their own icon.
const DOMAIN_ICONS := {"3d": "model", "2d": "image", "audio": "audio", "materials": "material"}
## Seconds between readiness checks while an app is still preparing, and otherwise.
const PREPARING_INTERVAL := 5.0
const IDLE_INTERVAL := 60.0
## After Start Docker: ask Docker itself (not the cached answer) every 3 s, for 2 minutes.
const DOCKER_INTERVAL := 3.0
const DOCKER_POLLS := 40
## Seconds the card stays after the pointer and focus leave both the app and the card.
const CARD_GRACE := 0.15
## The card's text width in multiples of the body font.
const CARD_EM := 22

## The command that runs the Joinery CLI and its leading arguments. Empty: the engine
## checkout's `uv run … joinery`. Tests point it at a fixture.
var cli_prefix := PackedStringArray()
## What "Start Docker" runs besides showing how (empty: only the how-to).
var start_docker_argv := PackedStringArray(
	["open", "-a", "Docker"] if OS.get_name() == "macOS" else []
)

var _buttons: Dictionary = {}
var _items: Dictionary = {}
var _tools: VBoxContainer
var _toolbar: RailItem
var _group := ButtonGroup.new()
var _selected := "godot"
var _signature: Array = []
var _readiness: Dictionary = {}
var _job: RefCounted
var _action_job: RefCounted
var _action_tool := ""
var _since_check := 0.0
## Questions left to ask Docker after Start Docker (0: not waiting for it).
var _docker_polls := 0
## The app whose card a click dismissed; it stays hidden until the pointer enters again.
## Whether the open card came from keyboard focus, which keeps it open while focused.
var _card_by_keys := false
var _card_dismissed := ""
var _card: Card
var _card_layer: Control
var _card_tool := ""
var _card_idle := 0.0


## The readiness card beside an app: name, the plain sentence, the one action, Check again
## and when it was last checked.
class Card:
	extends PanelContainer
	var heading: Label
	var sentence: Label
	var action: Button
	var again: Button
	var checked: Label


## One rail entry: a flat square icon button with the selection pill and accent bar drawn
## behind it, and an optional readiness dot on the pill's corner.
class RailItem:
	extends Container
	var button: Button
	var dot: Control
	## Draw the accent bar when selected (apps), or only the pill (the toolbar toggle).
	var marker := true
	## Where the icon comes from: {"logo": app id}, {"svg": path}, {"editor": icon name} or
	## {"ui": icon name}.
	var source: Dictionary = {}
	var hover_amount := 0.0:
		set(value):
			hover_amount = value
			queue_redraw()
	var select_amount := 0.0:
		set(value):
			select_amount = value
			queue_redraw()

	func _init(target: Button, with_marker: bool) -> void:
		button = target
		marker = with_marker
		add_child(button)
		dot = UI_COMPONENTS.status_dot()
		dot.mouse_filter = Control.MOUSE_FILTER_IGNORE
		dot.hide()
		add_child(dot)
		button.mouse_entered.connect(_hover.bind(true))
		button.mouse_exited.connect(_hover.bind(false))
		button.focus_entered.connect(_hover.bind(true))
		button.focus_exited.connect(_hover.bind(false))

	func _get_minimum_size() -> Vector2:
		return button.get_combined_minimum_size()

	func _notification(what: int) -> void:
		if what == NOTIFICATION_SORT_CHILDREN:
			fit_child_in_rect(button, Rect2(Vector2.ZERO, size))
			var pill := pill_rect()
			var dot_size := dot.get_combined_minimum_size()
			var inset := UI_THEME.space(self, 1) / 2.0
			fit_child_in_rect(dot, Rect2(pill.end - dot_size - Vector2(inset, inset), dot_size))

	## The pill behind the icon: a square of the button's height less SPACE.1 on each side,
	## centred in the rail.
	func pill_rect() -> Rect2:
		var side := size.y - 2.0 * UI_THEME.space(self, 1)
		return Rect2(Vector2((size.x - side) / 2.0, (size.y - side) / 2.0), Vector2(side, side))

	func _draw() -> void:
		var amount := maxf(hover_amount, select_amount)
		if amount > 0.0:
			var fill := get_theme_color("surface_raised", "Joinery")
			fill.a *= amount
			var box := StyleBoxFlat.new()
			box.bg_color = fill
			box.set_corner_radius_all(UI_THEME.radius(self, "md"))
			draw_style_box(box, pill_rect())
		if marker and select_amount > 0.0:
			var accent := get_theme_color("accent", "Joinery")
			accent.a *= select_amount
			var width := roundf(3.0 * UI_THEME.unit(self))
			var height := pill_rect().size.y * 0.5 * select_amount
			var bar := StyleBoxFlat.new()
			bar.bg_color = accent
			bar.corner_radius_top_right = int(width)
			bar.corner_radius_bottom_right = int(width)
			draw_style_box(bar, Rect2(0.0, (size.y - height) / 2.0, width, height))

	func _hover(on: bool) -> void:
		var target := 1.0 if on or button.has_focus() or button.is_hovered() else 0.0
		UI_MOTION.tween_to(self, self, "hover_amount", target)


func _ready() -> void:
	size_flags_vertical = Control.SIZE_EXPAND_FILL
	UI_THEME.watch(self)
	_tools = VBoxContainer.new()
	_tools.theme_type_variation = "JoineryTight"
	add_child(_tools)
	var spacer := Control.new()
	spacer.size_flags_vertical = Control.SIZE_EXPAND_FILL
	spacer.mouse_filter = Control.MOUSE_FILTER_IGNORE
	add_child(spacer)
	var toggle := _button("ViewportToolbarToggle", "Viewport tools")
	_toolbar = _item(toggle, {"editor": "Ruler"}, false)
	toggle.pressed.connect(func() -> void: toolbar_requested.emit())
	add_child(_toolbar)
	_build_card()
	_refresh_readiness()


func _exit_tree() -> void:
	for job: RefCounted in [_job, _action_job]:
		if job != null:
			job.call("cancel")
	_job = null
	_action_job = null


func _process(delta: float) -> void:
	_since_check += delta
	_track_card(delta)
	if _action_job != null:
		var done: Dictionary = _action_job.call("poll")
		if not done.is_empty():
			_action_done(done)
	if _job == null:
		var waiting := _action_job != null
		for entry: Dictionary in _readiness.values():
			waiting = waiting or entry.get("status") == "preparing" or entry.get("checking", false)
		var every := PREPARING_INTERVAL if waiting else IDLE_INTERVAL
		if _since_check >= (DOCKER_INTERVAL if _docker_polls > 0 else every):
			_refresh_readiness()
		return
	var result: Dictionary = _job.call("poll")
	if result.is_empty():
		return
	_job = null
	_since_check = 0.0
	var readiness := _parse(result)
	if readiness.is_empty():
		return  # Keep the last known readiness; the rail still works without it.
	if _docker_polls > 0:
		var down := readiness.values().any(
			func(entry: Dictionary) -> bool: return entry.get("status") == "needs_docker"
		)
		_docker_polls = _docker_polls - 1 if down else 0
	var merged := _readiness.duplicate()
	merged.merge(readiness, true)
	_apply_readiness(merged)


## Readiness entries by app id from a `joinery status` reply; {} when it is unreadable.
static func _parse(result: Dictionary) -> Dictionary:
	var json := JSON.new()
	if int(result.get("code", 1)) != 0 or json.parse(str(result.get("output", ""))) != OK:
		return {}
	var payload: Variant = json.data
	if not payload is Dictionary:
		return {}
	var readiness := {}
	for tool: Variant in payload.get("tools", []):
		if tool is Dictionary and tool.has("id") and tool.get("status") is String:
			readiness[str(tool["id"])] = tool
	return readiness


## Rebuild the app list from the workspace catalog: every enabled app, and any app that
## already made assets. The list is rebuilt only when the set of apps changes.
func update_tools(tools: Array, assets: Array) -> void:
	var shown: Array[Dictionary] = []
	for tool: Dictionary in tools:
		var id := str(tool["id"])
		var count := 0
		for asset: Dictionary in assets:
			if str(asset.get("tool", "")) == id:
				count += 1
		if count > 0 or bool(tool.get("enabled", false)):
			shown.append(tool)
	var signature: Array = []
	for tool: Dictionary in shown:
		signature.append([str(tool["id"]), str(tool["name"]), str(tool.get("domain", ""))])
	if signature == _signature:
		return
	_signature = signature
	for child: Node in _tools.get_children():
		_tools.remove_child(child)
		child.queue_free()
	_buttons.clear()
	_items.clear()
	var added_all := false
	for tool: Dictionary in shown:
		var id := str(tool["id"])
		if id != "godot" and not added_all:
			_add_all_assets()
			added_all = true
		_add_tool(id, str(tool["name"]), _icon_source(id, str(tool.get("domain", ""))))
		if id == "godot":
			_add_all_assets()
			added_all = true
	if not added_all:
		_add_all_assets()
	_apply_readiness(_readiness)
	select_tool(_selected)


## Mark [param tool] as the current item ("godot", "" for All assets, or an app id).
func select_tool(tool: String) -> void:
	_selected = tool
	for id: String in _items:
		var item: RailItem = _items[id]
		item.button.set_pressed_no_signal(id == tool)
		_sync(item)


## Reflect the saved viewport-tools preference on the toggle at the bottom of the rail.
func set_toolbar_shown(shown: bool) -> void:
	if _toolbar != null:
		_toolbar.button.set_pressed_no_signal(shown)
		_sync(_toolbar)


func _on_theme_rebuilt() -> void:
	_restyle()


func _notification(what: int) -> void:
	# The palette can turn light or dark after the rail is built (the editor theme arrives or
	# changes later): render the icons again then, not only when the Joinery theme is rebuilt.
	if what == NOTIFICATION_THEME_CHANGED and is_inside_tree():
		_restyle.call_deferred(false)


## Render every icon again when the palette or scale it was rendered for has changed.
func _restyle(force := true) -> void:
	if not force and _icon_signature() == get_meta("icon_signature", []):
		return
	for item: RailItem in _all_items():
		if item.is_inside_tree():
			_style(item)


func _add_all_assets() -> void:
	_add_tool(ALL_ASSETS, "All assets", {"svg": _glyph("all")})


func _add_tool(id: String, title: String, source: Dictionary) -> void:
	var button := _button("AllAssets" if id == ALL_ASSETS else id, title)
	button.button_group = _group
	button.set_meta("title", title)
	button.pressed.connect(
		func() -> void:
			# A click opens the app; its card has done its job until the pointer comes back.
			_card_dismissed = id
			_hide_card_now()
			tool_selected.emit(id)
	)
	button.mouse_entered.connect(
		func() -> void:
			_card_dismissed = ""
			_card_by_keys = false
			_show_card(id)
	)
	button.focus_entered.connect(
		func() -> void:
			# Only keyboard focus keeps a card open; a click also focuses the button.
			_card_by_keys = not button.is_hovered()
			_show_card(id)
	)
	var item := _item(button, source, true)
	_tools.add_child(item)
	_buttons[id] = button
	_items[id] = item


func _button(node_name: String, title: String) -> Button:
	var button := Button.new()
	button.name = node_name
	button.toggle_mode = true
	button.flat = true
	button.focus_mode = Control.FOCUS_ALL
	button.tooltip_text = title
	button.accessibility_name = title
	button.icon_alignment = HORIZONTAL_ALIGNMENT_CENTER
	button.vertical_icon_alignment = VERTICAL_ALIGNMENT_CENTER
	button.expand_icon = true
	# Glyphs carry their tint (text, or accent when selected) and logos their own colours, so
	# the button must not tint them. White is the identity multiplier, not a palette colour.
	for state: String in ["normal", "hover", "pressed", "hover_pressed", "focus", "disabled"]:
		button.add_theme_color_override("icon_%s_color" % state, Color.WHITE)
	UI_MOTION.press_feedback(button)
	return button


func _item(button: Button, source: Dictionary, marker: bool) -> RailItem:
	var item := RailItem.new(button, marker)
	item.source = source
	item.mouse_filter = Control.MOUSE_FILTER_PASS
	button.toggled.connect(func(_on: bool) -> void: _sync(item))
	item.ready.connect(_style.bind(item))
	return item


## Sizes and icons for the current editor scale and palette.
func _style(item: RailItem) -> void:
	var side := float(UI_THEME.control_h(self, "lg"))
	item.button.custom_minimum_size = Vector2(side, side)
	# Rail icons are drawn on a 20 px grid: the ICON.md token.
	var icon_size := UI_THEME.icon_px(self, "md")
	item.button.add_theme_constant_override("icon_max_width", icon_size)
	if item.source.has("logo"):
		# A logo keeps its colours when selected: the pill and the accent bar show selection.
		var logo := UI_THEME.app_logo(self, str(item.source["logo"]))
		item.set_meta("icons", [logo, logo])
	else:
		item.set_meta("icons", [_render(item.source, "text"), _render(item.source, "accent")])
	set_meta("icon_signature", _icon_signature())
	_sync(item, true)


## What the icons were rendered for: light or dark, the accent and the scale.
func _icon_signature() -> Array:
	return [UI_THEME.is_dark(self), UI_THEME.prefs()["accent"], UI_THEME.unit(self)]


func _sync(item: RailItem, instant := false) -> void:
	var on := item.button.button_pressed
	var icons: Array = item.get_meta("icons", [])
	if icons.size() == 2:
		item.button.icon = icons[1 if on else 0]
	if instant or not item.is_inside_tree():
		item.select_amount = 1.0 if on else 0.0
	else:
		UI_MOTION.tween_to(item, item, "select_amount", 1.0 if on else 0.0)


func _all_items() -> Array[RailItem]:
	var items: Array[RailItem] = []
	for item: RailItem in _items.values():
		items.append(item)
	if _toolbar != null:
		items.append(_toolbar)
	return items


func _icon_source(id: String, domain: String) -> Dictionary:
	if UI_THEME.has_app_logo(id):
		return {"logo": id}
	if id == "godot":
		return {"editor": "GodotMonochrome"}
	# In-house glyphs stand in where Joinery ships no official logo (see licenses/tool-logos.md).
	var path := _glyph(id)
	if FileAccess.file_exists(path):
		return {"svg": path}
	return {"ui": DOMAIN_ICONS.get(domain.get_slice("/", 0), "scene")}


func _glyph(name: String) -> String:
	var script: Resource = get_script()
	return script.resource_path.get_base_dir().path_join("icons/tools/%s.svg" % name)


## A rail icon tinted with a palette role, at 20 × unit pixels.
func _render(source: Dictionary, role: String) -> Texture2D:
	var tint := UI_THEME.color(self, role)
	if source.has("svg") and FileAccess.file_exists(str(source["svg"])):
		var svg := FileAccess.get_file_as_string(str(source["svg"]))
		var image := Image.new()
		var hex := "#" + tint.to_html(false)
		if image.load_svg_from_string(svg.replace("#fff", hex), UI_THEME.unit(self)) == OK:
			return ImageTexture.create_from_image(image)
	if source.has("ui"):
		return UI_THEME.icon(self, str(source["ui"]), tint)
	var name := str(source.get("editor", ""))
	if not name.is_empty() and has_theme_icon(name, "EditorIcons"):
		return _recolor(get_theme_icon(name, "EditorIcons"), tint)
	return null


## Editor icons come in their own colours; keep the shape (alpha) and paint it [param tint].
func _recolor(texture: Texture2D, tint: Color) -> Texture2D:
	var image := texture.get_image()
	if image == null or image.is_empty():
		return texture
	image = image.duplicate() as Image
	if image.is_compressed():
		image.decompress()
	image.convert(Image.FORMAT_RGBA8)
	for y: int in image.get_height():
		for x: int in image.get_width():
			var alpha := image.get_pixel(x, y).a
			image.set_pixel(x, y, Color(tint, tint.a * alpha))
	return ImageTexture.create_from_image(image)


func _refresh_readiness() -> void:
	if _job != null:
		return
	_since_check = 0.0
	# While Docker starts, ask it now: the cached answer would still say it isn't running.
	var quick := "--background" if _docker_polls == 0 else "--json"
	_job = _start(["status", "--catalog", quick, "--json", "--project", _project()])


func _project() -> String:
	return ProjectSettings.globalize_path("res://").rstrip("/")


## Start the Joinery CLI with [param arguments] as a job; null when it can't start.
func _start(arguments: Array) -> RefCounted:
	var program := "uv"
	var args := PackedStringArray()
	if cli_prefix.is_empty():
		var engine_dir := OS.get_environment("JOINERY_HOME")
		if engine_dir.is_empty():
			engine_dir = ProjectSettings.globalize_path("res://").path_join("..").simplify_path()
		args = PackedStringArray(["run", "--frozen", "--quiet", "--project", engine_dir, "joinery"])
	else:
		program = cli_prefix[0]
		args = cli_prefix.slice(1)
	for argument: Variant in arguments:
		args.append(str(argument))
	var job: RefCounted = COMMAND_SCRIPT.new()
	return job if job.call("start", program, args) == OK else null


# --- The readiness card ----------------------------------------------------------------------


func _build_card() -> void:
	# A plain, top-level layer: the rail's column never lays the card out.
	_card_layer = Control.new()
	_card_layer.top_level = true
	_card_layer.mouse_filter = Control.MOUSE_FILTER_IGNORE
	add_child(_card_layer)
	_card = Card.new()
	_card.name = "ReadinessCard"
	_card.theme_type_variation = "JoineryHoverCard"
	_card.hide()
	_card_layer.add_child(_card)
	var stack := VBoxContainer.new()
	stack.theme_type_variation = "JoineryTight"
	_card.add_child(stack)
	_card.heading = UI_COMPONENTS.label("", "", true)
	stack.add_child(_card.heading)
	_card.sentence = UI_COMPONENTS.label("", "JoineryCaption", true)
	_card.sentence.name = "Sentence"
	stack.add_child(_card.sentence)
	var buttons := HBoxContainer.new()
	buttons.theme_type_variation = "JoineryRow"
	stack.add_child(buttons)
	_card.action = UI_COMPONENTS.status_action()
	# The card is a view of its own: its one action is the primary one.
	_card.action.theme_type_variation = "JoineryPrimaryButton"
	_card.action.name = "Action"
	_card.action.pressed.connect(_on_card_action)
	buttons.add_child(_card.action)
	_card.again = UI_COMPONENTS.quiet("Check again")
	_card.again.name = "CheckAgain"
	_card.again.pressed.connect(_check_again)
	buttons.add_child(_card.again)
	var gap := Control.new()
	gap.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	gap.mouse_filter = Control.MOUSE_FILTER_IGNORE
	buttons.add_child(gap)
	_card.checked = UI_COMPONENTS.label("", "JoineryFaint")
	_card.checked.name = "LastChecked"
	_card.checked.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	buttons.add_child(_card.checked)
	_card.theme_changed.connect(
		func() -> void:
			if _card.visible:
				_place_card.call_deferred()
	)


## Show the card beside [param id]'s rail item (hover or focus).
func _show_card(id: String) -> void:
	if not _readiness.has(id) or not _items.has(id) or _card_dismissed == id:
		return
	if _card_tool != id:
		_hide_card_now()
	_card_tool = id
	_card_idle = 0.0
	_fill_card()
	var item: RailItem = _items[id]
	item.button.tooltip_text = ""
	_place_card()
	if not _card.visible:
		UI_MOTION.enter(_card, item)
	# Wrapped text knows its height only once it has its width: settle again next frame.
	_place_card.call_deferred()


## Fit the card to its text and put it beside its app, inside the window.
func _place_card() -> void:
	var item: RailItem = _items.get(_card_tool)
	if item == null or not is_instance_valid(_card):
		return
	var width := UI_THEME.font_size(_card, "body") * CARD_EM
	_card.sentence.custom_minimum_size.x = width
	_card.heading.custom_minimum_size.x = width
	_card.reset_size()
	var gap := float(UI_THEME.space(self, 2))
	var at := item.get_global_rect().position + Vector2(item.size.x + gap, 0.0)
	var limit := get_viewport_rect().size - _card.size
	_card.global_position = Vector2(minf(at.x, limit.x), clampf(at.y, 0.0, maxf(limit.y, 0.0)))


func _fill_card() -> void:
	var entry: Dictionary = _readiness.get(_card_tool, {})
	var item: RailItem = _items.get(_card_tool)
	var title := str(item.button.get_meta("title", _card_tool)) if item != null else _card_tool
	var status := str(entry.get("status", ""))
	var words: Array = UI_COMPONENTS.READINESS.get(status, ["Checking…"])
	_card.heading.text = "%s · %s" % [title, words[0]]
	var sentence := str(entry.get("status_reason", ""))
	if sentence.is_empty():
		sentence = str(entry.get("summary", ""))
	if _action_job != null and _action_tool == _card_tool:
		sentence = str(_card.get_meta("busy_text", sentence))
	elif _card.has_meta("howto") and status == "needs_docker":
		sentence = DOCKER_HOWTO
	_card.sentence.text = sentence
	var action: Variant = entry.get("action")
	if action is Dictionary and str((action as Dictionary).get("id", "")) in ["check_again", "get"]:
		action = null  # Check again has its own button; licences are bought in setup.
	_card.action.set_action(action)
	var busy := _action_job != null
	_card.action.disabled = busy
	_card.again.disabled = busy
	_card.checked.text = UI_COMPONENTS.last_checked(str(entry.get("checked_at", "")))
	# A ready app needs nothing: no Check again, no check time, just its name and what it does.
	var ready := status == "ready" and not busy
	_card.again.visible = not ready
	_card.checked.visible = not ready
	(_card.again.get_parent() as Control).visible = not ready or _card.action.visible
	if _card.visible:
		_place_card.call_deferred()


## Keep the card while the pointer or focus is on its app or on the card; else hide it.
func _track_card(delta: float) -> void:
	if _card == null or not _card.visible:
		return
	var item: RailItem = _items.get(_card_tool)
	var focus := get_viewport().gui_get_focus_owner()
	var inside := (
		(
			item != null
			and _card_dismissed != _card_tool
			and (item.button.is_hovered() or (_card_by_keys and item.button.has_focus()))
		)
		or _card.get_global_rect().has_point(_card.get_global_mouse_position())
		or (focus != null and _card.is_ancestor_of(focus))
	)
	_card_idle = 0.0 if inside else _card_idle + delta
	if _card_idle >= CARD_GRACE:
		_card.remove_meta("howto")
		# Gone at once: a fading card over the work area lags behind the pointer.
		_hide_card_now()


## Another app's card replaces this one at once.
func _hide_card_now() -> void:
	if _card != null and _card.visible:
		_restore_tip()
		_card.hide()


func _restore_tip() -> void:
	var item: RailItem = _items.get(_card_tool)
	if item != null:
		item.button.tooltip_text = str(item.button.get_meta("tip", item.button.tooltip_text))


func _on_card_action() -> void:
	match _card.action.action_id:
		"start_docker":
			if not start_docker_argv.is_empty():
				OS.create_process(start_docker_argv[0], start_docker_argv.slice(1))
			_card.set_meta("howto", true)
			_docker_polls = DOCKER_POLLS
			_since_check = 0.0
			_fill_card()
			_card.again.grab_focus()
		"rebuild":
			_run_action(
				["bootstrap", "--project", _project()],
				"Refreshing tools. The first time takes about an hour."
			)


func _check_again() -> void:
	_run_action(
		["status", "--tool", _card_tool, "--refresh", "--json", "--project", _project()],
		"Checking…"
	)


func _run_action(arguments: Array, busy_text: String) -> void:
	if _action_job != null:
		return
	_action_job = _start(arguments)
	if _action_job == null:
		return
	_action_tool = _card_tool
	_card.set_meta("busy_text", busy_text)
	_card.remove_meta("howto")
	_fill_card()


func _action_done(result: Dictionary) -> void:
	_action_job = null
	var readiness := _parse(result)
	if not readiness.is_empty():
		var merged := _readiness.duplicate()
		merged.merge(readiness, true)
		_apply_readiness(merged)
	if _card.visible:
		_fill_card()
	# A rebuild changes every worker: ask about all of them now.
	_since_check = IDLE_INTERVAL


func _apply_readiness(readiness: Dictionary) -> void:
	var previous := _readiness
	_readiness = readiness
	for id: String in _items:
		var item: RailItem = _items[id]
		var title := str(item.button.get_meta("title", item.button.accessibility_name))
		var entry: Dictionary = readiness.get(id, {})
		var status := str(entry.get("status", ""))
		var wording := str((UI_COMPONENTS.READINESS.get(status, [""]) as Array)[0])
		var reason := str(entry.get("status_reason", ""))
		var ready := status == "ready"
		var tip := title if wording.is_empty() else "%s · %s" % [title, wording]
		item.button.set_meta("tip", tip)
		# While its card is open the card explains the app; a tooltip would only cover it.
		var carded := _card != null and _card.visible and _card_tool == id
		item.button.tooltip_text = "" if carded else tip
		item.button.accessibility_description = (
			wording if ready or reason.is_empty() else "%s. %s" % [wording, reason]
		)
		var dot := item.dot as UI_COMPONENTS.StatusDot
		var was_shown := dot.visible
		dot.visible = UI_COMPONENTS.READINESS.has(status) and status != "ready"
		if dot.visible:
			dot.state = str(UI_COMPONENTS.READINESS[status][1])
		var was := str((previous.get(id, {}) as Dictionary).get("status", ""))
		if ready and not was.is_empty() and was != "ready" and item.is_inside_tree():
			# Turned Ready: the glyph pulses once and the badge fades.
			UI_MOTION.pulse(item)
			if was_shown:
				dot.show()
				UI_MOTION.exit(dot)
