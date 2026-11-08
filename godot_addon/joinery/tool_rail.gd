@tool
extends VBoxContainer
## The native shell provides the rail; project tools are populated by the addon.

signal tool_selected(tool: String)
signal toolbar_requested

var _buttons: Dictionary = {}
var _tools: VBoxContainer


func _ready() -> void:
	_tools = VBoxContainer.new()
	add_child(_tools)
	var spacer := Control.new()
	spacer.size_flags_vertical = Control.SIZE_EXPAND_FILL
	add_child(spacer)
	size_flags_vertical = Control.SIZE_EXPAND_FILL
	var toolbar := Button.new()
	toolbar.name = "ViewportToolbarToggle"
	_style_button(toolbar)
	toolbar.icon = get_theme_icon("Tools", "EditorIcons")
	toolbar.tooltip_text = "Show or hide viewport toolbar"
	toolbar.pressed.connect(func() -> void: toolbar_requested.emit())
	add_child(toolbar)


func update_tools(tools: Array, assets: Array) -> void:
	for child: Node in _tools.get_children():
		_tools.remove_child(child)
		child.queue_free()
	_buttons.clear()
	for tool: Dictionary in tools:
		var id := str(tool["id"])
		var count := 0
		for asset: Dictionary in assets:
			if str(asset.get("tool", "")) == id:
				count += 1
		if count == 0 and not bool(tool.get("enabled", false)):
			continue
		var button := Button.new()
		button.name = id
		button.toggle_mode = true
		_style_button(button)
		button.icon = _tool_icon(tool)
		button.tooltip_text = str(tool["name"])
		button.accessibility_name = str(tool["name"])
		button.pressed.connect(func() -> void: tool_selected.emit(id))
		_tools.add_child(button)
		_buttons[id] = button


func _style_button(button: Button) -> void:
	var scale := EditorInterface.get_editor_scale()
	button.set_theme_type_variation("MainScreenButton")
	button.custom_minimum_size = Vector2(44, 44) * scale
	button.icon_alignment = HORIZONTAL_ALIGNMENT_CENTER
	button.vertical_icon_alignment = VERTICAL_ALIGNMENT_CENTER
	button.expand_icon = true
	button.add_theme_constant_override("icon_max_width", int(24 * scale))


func _tool_icon(tool: Dictionary) -> Texture2D:
	if str(tool["id"]) == "godot":
		return get_theme_icon("Godot", "EditorIcons")
	if str(tool["id"]) == "blender":
		# Code-native eye-and-spokes badge, rendered by Godot's SVG loader.
		var svg := (
			'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24"'
			+ ' viewBox="0 0 32 32"><g stroke="#ed7d20" stroke-width="4.5"'
			+ ' stroke-linecap="round"><path d="M19 12 10 4M16 13H4M15 16 3 25"/>'
			+ '</g><ellipse cx="20" cy="18" rx="11" ry="9" fill="#ed7d20"/>'
			+ '<ellipse cx="20" cy="18" rx="7" ry="5.7" fill="white"/>'
			+ '<ellipse cx="20" cy="18" rx="4.8" ry="3.8" fill="#287eb5"/></svg>'
		)
		var image := Image.new()
		image.load_svg_from_string(svg, EditorInterface.get_editor_scale())
		return ImageTexture.create_from_image(image)
	var icon_name: String = {
		"3d": "SphereMesh", "2d": "Paint", "audio": "AudioStream",
		"materials": "StandardMaterial3D", "engine": "Godot",
	}.get(str(tool.get("domain", "")), "File")
	return get_theme_icon(icon_name, "EditorIcons")


func select_tool(tool: String) -> void:
	for id: String in _buttons:
		var button: Button = _buttons[id]
		button.set_pressed_no_signal(id == tool)
