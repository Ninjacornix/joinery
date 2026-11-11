@tool
extends VBoxContainer
## The Mixer tab in the editor's bottom panel: the game's buses on top, its music below.
##
## - Buses (mixer_strip.gd): one strip per bus with a fader, a live meter, Mute and Solo, and a
##   reverb and a compressor with one amount each; "Duck music under" picks Off, Voice or SFX
##   (the Music compressor's sidechain). The game core's buses the project lacks can be added.
## - Music (mixer_music.gd): a track's layers with a fader, Mute and Solo each, and Intensity to
##   audition the stems the way the game plays them.
##
## Every change plays at once in the editor (mixer_buses.gd) and nothing is written: Save mix
## sends the mix to Journeyman (chat_bridge.gd send_mixer), which writes the bus layout and the
## track descriptions as a reviewed change (bridge/mixer_save.py). Revert puts back what the
## project's files say. Save needs a connected agent; without one the tab says so.

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const BUSES := preload("mixer_buses.gd")
const STRIP := preload("mixer_strip.gd")
const MUSIC := preload("mixer_music.gd")
const SELF_PATH := "res://addons/joinery/mixer_tab.gd"
const NO_AGENT := "Connect Journeyman to save: the mix is written as a reviewed change."
const SENT := "Sent for review. Accept it in Review to write the files."

## The chat bridge Save mix goes through; found through the plugin's sidebar when unset.
var bridge: Node
## The bus states on show (mixer_buses.gd), in index order, and as last saved.
var buses: Array[Dictionary] = []
var saved_buses: Array[Dictionary] = []
var music: MUSIC
var save_button: Button
var revert_button: Button
var duck_buttons: Dictionary = {}
var _plugin: EditorPlugin
var _loaded := false
var _snapshot: AudioBusLayout
var _strips: Dictionary = {}
var _status: HBoxContainer
var _note: Label
var _strip_row: HFlowContainer
var _missing: PanelContainer
var _sent := false


## Add the Mixer tab after Share; it removes itself when the plugin leaves the tree.
static func install(plugin: EditorPlugin) -> Control:
	var tab: Control = load(SELF_PATH).new()
	tab.set("_plugin", plugin)
	plugin.add_control_to_bottom_panel(tab, "Mixer")
	plugin.tree_exiting.connect(
		func() -> void:
			if is_instance_valid(tab):
				plugin.remove_control_from_bottom_panel(tab)
				tab.queue_free(),
		CONNECT_ONE_SHOT
	)
	return tab


func _init() -> void:
	name = "Mixer"
	theme_type_variation = "JoineryStack"
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	size_flags_vertical = Control.SIZE_EXPAND_FILL
	accessibility_name = "Mixer: bus levels, effects, ducking and the music layers"
	var bar := HBoxContainer.new()
	bar.theme_type_variation = "JoineryRow"
	add_child(bar)
	_status = UI.status("Unsaved changes", "attention")
	_status.name = "Unsaved"
	bar.add_child(_status)
	_note = UI.label("", "JoineryCaption")
	_note.name = "Note"
	_note.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_note.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	bar.add_child(_note)
	revert_button = UI.quiet("Revert")
	revert_button.name = "Revert"
	revert_button.tooltip_text = "Put back the mix the project's files have"
	revert_button.pressed.connect(revert)
	UI_MOTION.press_feedback(revert_button)
	bar.add_child(revert_button)
	save_button = UI.primary("Save mix")
	save_button.name = "Save"
	save_button.pressed.connect(save)
	UI_MOTION.press_feedback(save_button)
	bar.add_child(save_button)
	var scroll := ScrollContainer.new()
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	UI_MOTION.rubber_band(scroll)
	add_child(scroll)
	var gutter := MarginContainer.new()
	gutter.theme_type_variation = "JoineryGutter"
	gutter.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	scroll.add_child(gutter)
	var content := VBoxContainer.new()
	content.theme_type_variation = "JoineryStack"
	content.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	gutter.add_child(content)
	content.add_child(_build_buses())
	music = MUSIC.new()
	music.changed.connect(_render)
	content.add_child(music)


func _ready() -> void:
	UI_THEME.watch(self)
	set_process(false)


func _notification(what: int) -> void:
	if what != NOTIFICATION_VISIBILITY_CHANGED:
		return
	var shown := is_visible_in_tree()
	set_process(shown)
	if shown and (not _loaded or not is_dirty()):
		refresh()
	elif not shown and music != null:
		music.stop()


func _process(_delta: float) -> void:
	for index: int in buses.size():
		var strip: STRIP = _strips.get(str(buses[index]["name"]))
		if strip != null:
			strip.set_level(BUSES.peak_db(index))


# --- Public ------------------------------------------------------------------------------------


## Read the buses and tracks again; what is heard now counts as saved.
func refresh() -> void:
	_loaded = true
	buses = BUSES.read()
	saved_buses = buses.duplicate(true)
	_snapshot = AudioServer.generate_bus_layout().duplicate(true) as AudioBusLayout
	_build_strips()
	music.refresh()
	_sent = false
	_render()


func is_dirty() -> bool:
	return buses != saved_buses or music.is_dirty()


## The bus strip for [param bus_name], or null.
func strip(bus_name: String) -> STRIP:
	return _strips.get(bus_name)


## Play [param state] on its bus as if its strip changed it.
func set_bus(state: Dictionary) -> void:
	var index := _index(str(state.get("name", "")))
	if index < 0:
		return
	buses[index] = state.duplicate(true)
	BUSES.apply(state)
	(_strips[str(state["name"])] as STRIP).show_state(buses[index])
	_sent = false
	_render()


## Duck music under [param source] ("", "Voice" or "SFX").
func set_ducking(source: String) -> void:
	var music_bus := BUSES.set_ducking(buses, source)
	if not music_bus.is_empty():
		(_strips["Music"] as STRIP).show_state(music_bus)
	_sent = false
	_render()


## Add the game core's buses the project lacks (they play at once; Save mix writes them).
func add_missing_buses() -> void:
	BUSES.add_missing()
	var keep := saved_buses
	buses = BUSES.read()
	saved_buses = keep
	_build_strips()
	music.rebus()
	_render()


## The save message: {"kind": "mixer_save", "bus_layout"?, "tracks"?} with only the parts
## that changed.
func message() -> Dictionary:
	var line := {"kind": "mixer_save"}
	if buses != saved_buses:
		line["bus_layout"] = BUSES.message(buses, BUSES.layout_path())
	var tracks := music.edits()
	if not tracks.is_empty():
		line["tracks"] = tracks
	return line


## Why Save is off ("" when it can save).
func save_blocked() -> String:
	if not is_dirty():
		return "Nothing changed since the last save."
	var link := _bridge()
	if link == null or not link.has_method("send_mixer") or not bool(link.call("is_ready")):
		return NO_AGENT
	return ""


## Send the mix for review. The tab treats it as saved; Revert brings back the files.
func save() -> void:
	if not save_blocked().is_empty():
		return
	if not bool(_bridge().call("send_mixer", message())):
		_note.text = NO_AGENT
		return
	saved_buses = buses.duplicate(true)
	music.mark_saved()
	_sent = true
	_render()
	UI_MOTION.pulse(_note)


## Put back the mix the project's files have (the snapshot when there is no layout file).
func revert() -> void:
	if not BUSES.load_layout(BUSES.layout_path()) and _snapshot != null:
		AudioServer.set_bus_layout(_snapshot.duplicate(true) as AudioBusLayout)
	buses = BUSES.read()
	saved_buses = buses.duplicate(true)
	_build_strips()
	music.rebus()
	music.revert()
	_sent = false
	_render()


# --- Building ----------------------------------------------------------------------------------


func _build_buses() -> VBoxContainer:
	var group := VBoxContainer.new()
	group.name = "Buses"
	group.theme_type_variation = "JoineryTight"
	var head := HBoxContainer.new()
	head.theme_type_variation = "JoineryRow"
	group.add_child(head)
	var title := UI.label("Buses", "JoinerySection")
	title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	title.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	head.add_child(title)
	var duck := UI.label("Duck music under", "JoineryCaption")
	duck.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	head.add_child(duck)
	var segment := HBoxContainer.new()
	segment.name = "Ducking"
	segment.theme_type_variation = "JoineryRowTight"
	segment.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	head.add_child(segment)
	var choices := ButtonGroup.new()
	for source: String in BUSES.DUCK_SOURCES:
		var button := Button.new()
		button.name = source if not source.is_empty() else "Off"
		button.text = button.name
		button.theme_type_variation = "JoinerySegment"
		button.toggle_mode = true
		button.button_group = choices
		button.focus_mode = Control.FOCUS_ALL
		button.accessibility_name = "Duck music under: " + button.name
		button.tooltip_text = (
			"Music plays at its own level"
			if source.is_empty()
			else "Music dips while the %s bus plays" % source
		)
		UI.bind_height(button, "sm")
		UI_MOTION.press_feedback(button)
		button.toggled.connect(
			func(on: bool) -> void:
				if on:
					set_ducking(source)
		)
		segment.add_child(button)
		duck_buttons[source] = button
	_missing = UI.notice("", "Add them", "JoineryCard")
	_missing.name = "Missing"
	(_missing.find_child("Action", true, false) as Button).pressed.connect(add_missing_buses)
	group.add_child(_missing)
	_strip_row = HFlowContainer.new()
	_strip_row.name = "Strips"
	_strip_row.theme_type_variation = "JoineryRow"
	group.add_child(_strip_row)
	return group


func _build_strips() -> void:
	for child: Node in _strip_row.get_children():
		_strip_row.remove_child(child)
		child.queue_free()
	_strips.clear()
	for state: Dictionary in buses:
		var bus_strip := STRIP.new()
		bus_strip.show_state(state)
		bus_strip.changed.connect(set_bus)
		_strip_row.add_child(bus_strip)
		_strips[str(state["name"])] = bus_strip
	UI_MOTION.stagger(_strip_row.get_children())


func _render() -> void:
	var dirty := is_dirty()
	_status.visible = dirty or _sent
	UI.set_status(
		_status,
		"In review" if _sent and not dirty else "Unsaved changes",
		"ready" if _sent and not dirty else "attention"
	)
	var blocked := save_blocked()
	save_button.disabled = not blocked.is_empty()
	save_button.tooltip_text = blocked if dirty else "Save the mix as a reviewed change"
	revert_button.disabled = not dirty
	_note.text = SENT if _sent and not dirty else (blocked if dirty else "")
	var source := BUSES.ducking(buses)
	for key: String in duck_buttons:
		var button: Button = duck_buttons[key]
		button.set_pressed_no_signal(key == source)
		button.disabled = _index("Music") < 0
	var missing := BUSES.missing()
	_missing.visible = not missing.is_empty()
	(_missing.find_child("Text", true, false) as Label).text = (
		"No %s bus%s yet. The game core plays its sound there."
		% [_names(missing), "" if missing.size() == 1 else "es"]
	)


func _index(bus_name: String) -> int:
	for index: int in buses.size():
		if str(buses[index]["name"]) == bus_name:
			return index
	return -1


static func _names(names: Array[String]) -> String:
	if names.size() <= 1:
		return "".join(names)
	return ", ".join(names.slice(0, -1)) + " and " + names[-1]


func _bridge() -> Node:
	if bridge == null and _plugin != null:
		var dock: Variant = _plugin.get("_dock")
		if dock is Object:
			var found: Variant = (dock as Object).get("_bridge")
			if found is Node:
				bridge = found
	if bridge != null and bridge.has_signal("status_changed"):
		var refresh_save := Callable(self, "_on_bridge_status")
		if not bridge.is_connected("status_changed", refresh_save):
			bridge.connect("status_changed", refresh_save)
	return bridge


func _on_bridge_status(_state: String, _detail: String) -> void:
	_render()
