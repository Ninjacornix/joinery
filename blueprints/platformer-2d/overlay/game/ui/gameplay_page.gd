extends RefCounted
## The "Gameplay" settings page: forgiving jumps (longer coyote time and jump
## buffer) and whether the HUD shows the timer. Values live in the Settings
## section "platformer" and apply at once.

const SECTION := "platformer"


static func build() -> Control:
	var tree: SceneTree = Engine.get_main_loop() as SceneTree
	var settings: Node = tree.root.get_node_or_null(^"Settings") if tree != null else null
	var column: VBoxContainer = VBoxContainer.new()
	column.name = "GameplaySettings"
	column.add_child(
		_toggle_row(settings, "Forgiving jumps", "ForgivingJumps", "forgiving_jumps", false)
	)
	column.add_child(_toggle_row(settings, "Show timer", "ShowTimer", "show_timer", true))
	column.add_child(UiStyle.hint(
		"Forgiving jumps give you twice as long to jump after running off a ledge or before landing."
	))
	return column


static func _toggle_row(
	settings: Node, text: String, node_name: String, key: String, default: bool
) -> Control:
	var toggle: CheckButton = UiStyle.toggle(bool(_read(settings, key, default)))
	toggle.name = node_name
	toggle.toggled.connect(func(on: bool) -> void: _write(settings, key, on))
	return UiStyle.row(text, toggle)


static func _read(settings: Node, key: String, default: Variant) -> Variant:
	if settings == null:
		return default
	return settings.call(&"get_value", SECTION, key, default)


static func _write(settings: Node, key: String, value: Variant) -> void:
	if settings != null:
		settings.call(&"set_value", SECTION, key, value)
