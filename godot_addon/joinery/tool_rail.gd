@tool
extends VBoxContainer
## The app rail beside the viewport. The fork provides the column (JoineryNavigation); the
## addon fills it: Godot, All assets, then each project app, and the viewport tools toggle at
## the bottom.
##
## One monochrome icon family (icons/tools/*.svg, drawn in-house on a 20 px grid), tinted
## text, and accent on the selected item. The selected item also gets an accent bar on the
## rail's left edge and a raised pill behind its icon. Each app shows its readiness from
## `joinery tools --json` (field `status`; a missing field shows nothing): a dot only when the
## app needs something, and a pulse when it becomes ready.

signal tool_selected(tool: String)
signal toolbar_requested

const UI_THEME := preload("ui_theme.gd")
const UI_COMPONENTS := preload("ui_components.gd")
const UI_MOTION := preload("ui_motion.gd")
const COMMAND_SCRIPT := preload("command_job.gd")
## The id of the "All assets" item: the workspace's unfiltered view.
const ALL_ASSETS := ""
## Rail glyphs are drawn on a 20 px grid, scaled by ui_theme.unit().
const ICON_SIZE := 20
## Readiness wording, the same as the setup screens and the /tools reply. "installed",
## "licence" and "missing" are the older names of the same states.
const STATUS_TEXT := {
	"ready": "Ready",
	"installed": "Ready",
	"preparing": "Downloading…",
	"needs_docker": "Needs Docker",
	"needs_licence": "Needs your licence",
	"licence": "Needs your licence",
	"missing": "Not installed",
	"unavailable": "Not available yet",
}
## Dot state per readiness, as in project setup and the asset list. Two marks, one meaning
## each: a filled accent dot while the app is getting ready, a hollow ring while it can't be
## used yet. The button's tooltip names the state ("Blender · Needs Docker") and why. Ready
## (and an unknown status) shows no mark, so the rail stays quiet until an app needs something.
const STATUS_DOT := {
	"preparing": "working",
	"needs_docker": "off",
	"needs_licence": "off",
	"licence": "off",
	"missing": "off",
	"unavailable": "off",
}
## Fallback glyph (ui_theme icon) by catalog domain, for apps without their own icon.
const DOMAIN_ICONS := {"3d": "model", "2d": "image", "audio": "audio", "materials": "material"}
## Seconds between readiness checks while an app is still preparing, and otherwise.
const PREPARING_INTERVAL := 5.0
const IDLE_INTERVAL := 60.0

var _buttons: Dictionary = {}
var _items: Dictionary = {}
var _tools: VBoxContainer
var _toolbar: RailItem
var _group := ButtonGroup.new()
var _selected := "godot"
var _signature: Array = []
var _readiness: Dictionary = {}
var _job: RefCounted
var _since_check := 0.0


## One rail entry: a flat square icon button with the selection pill and accent bar drawn
## behind it, and an optional readiness dot on the pill's corner.
class RailItem:
	extends Container
	var button: Button
	var dot: Control
	## Draw the accent bar when selected (apps), or only the pill (the toolbar toggle).
	var marker := true
	## Where the icon comes from: {"svg": path}, {"editor": icon name} or {"ui": icon name}.
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
	_refresh_readiness()


func _exit_tree() -> void:
	if _job != null:
		_job.cancel()
		_job = null


func _process(delta: float) -> void:
	_since_check += delta
	if _job == null:
		var preparing := false
		for entry: Dictionary in _readiness.values():
			preparing = preparing or entry.get("status") == "preparing"
		if _since_check >= (PREPARING_INTERVAL if preparing else IDLE_INTERVAL):
			_refresh_readiness()
		return
	var result: Dictionary = _job.poll()
	if result.is_empty():
		return
	_job = null
	_since_check = 0.0
	var payload: Variant = JSON.parse_string(str(result["output"]))
	if int(result["code"]) != 0 or not payload is Dictionary:
		return  # Keep the last known readiness; the rail still works without it.
	var readiness := {}
	for tool: Variant in payload.get("tools", []):
		if tool is Dictionary and tool.has("id") and tool.get("status") is String:
			readiness[str(tool["id"])] = {
				"status": str(tool["status"]), "reason": str(tool.get("status_reason", ""))
			}
	_apply_readiness(readiness)


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
	for item: RailItem in _all_items():
		_style(item)


func _add_all_assets() -> void:
	_add_tool(ALL_ASSETS, "All assets", {"svg": _glyph("all")})


func _add_tool(id: String, title: String, source: Dictionary) -> void:
	var button := _button("AllAssets" if id == ALL_ASSETS else id, title)
	button.button_group = _group
	button.set_meta("title", title)
	button.pressed.connect(func() -> void: tool_selected.emit(id))
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
	# Icons carry their tint (text, or accent when selected), so the button must not tint
	# them again. White is the identity multiplier, not a palette colour.
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
	var icon_size := roundi(ICON_SIZE * UI_THEME.unit(self))
	item.button.add_theme_constant_override("icon_max_width", icon_size)
	item.set_meta("icons", [_render(item.source, "text"), _render(item.source, "accent")])
	_sync(item, true)


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
	if id == "godot":
		return {"editor": "GodotMonochrome"}
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
	var engine_dir := OS.get_environment("JOINERY_HOME")
	if engine_dir.is_empty():
		engine_dir = ProjectSettings.globalize_path("res://").path_join("..").simplify_path()
	var arguments := PackedStringArray(
		[
			"run",
			"--frozen",
			"--quiet",
			"--project",
			engine_dir,
			"joinery",
			"tools",
			"--json",
			"--project",
			ProjectSettings.globalize_path("res://"),
		]
	)
	_job = COMMAND_SCRIPT.new()
	if _job.start("uv", arguments) != OK:
		_job = null


func _apply_readiness(readiness: Dictionary) -> void:
	var previous := _readiness
	_readiness = readiness
	for id: String in _items:
		var item: RailItem = _items[id]
		var title := str(item.button.get_meta("title", item.button.accessibility_name))
		var entry: Dictionary = readiness.get(id, {})
		var status := str(entry.get("status", ""))
		var wording := str(STATUS_TEXT.get(status, ""))
		var tip := title if wording.is_empty() else "%s · %s" % [title, wording]
		var reason := str(entry.get("reason", ""))
		var ready := status in ["ready", "installed"]
		if not reason.is_empty() and not ready:
			tip += "\n" + reason
		item.button.tooltip_text = tip
		item.button.accessibility_description = wording
		var dot := item.dot as UI_COMPONENTS.StatusDot
		dot.visible = STATUS_DOT.has(status)
		if dot.visible:
			dot.state = STATUS_DOT[status]
		var was := str((previous.get(id, {}) as Dictionary).get("status", ""))
		if (
			ready
			and not was.is_empty()
			and not was in ["ready", "installed"]
			and item.is_inside_tree()
		):
			UI_MOTION.pulse(item)
