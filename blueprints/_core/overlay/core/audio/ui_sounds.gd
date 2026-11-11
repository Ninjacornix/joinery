extends Node
## Interface sounds (autoload `UiSounds`): a click on every BaseButton press
## and a hover sound when a button is pointed at or gets keyboard focus, on
## the UI bus. Every button in the game is covered, including ones made later
## (it listens to the tree's `node_added`). The sounds are the project settings
## `audio/ui/click` and `audio/ui/hover` (res:// paths), or `click` and `hover`
## set from code; silent until there is one. A button with metadata
## `ui_sounds = false` is left alone.

signal played(kind: StringName)

const BUS := &"UI"
const VOICES := 4

var click: AudioStream
var hover: AudioStream
## Turn off to silence every interface sound (for example during a cutscene).
var enabled: bool = true

var _players: Array[AudioStreamPlayer] = []
var _next: int = 0


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	click = _setting_stream("audio/ui/click")
	hover = _setting_stream("audio/ui/hover")
	for index: int in VOICES:
		var player: AudioStreamPlayer = AudioStreamPlayer.new()
		player.name = "Voice%d" % index
		player.bus = BUS if AudioServer.get_bus_index(BUS) >= 0 else &"Master"
		add_child(player)
		_players.append(player)
	get_tree().node_added.connect(_watch)
	_watch_tree(get_tree().root)


## Plays `kind` (&"click" or &"hover") now. False when it has no sound.
func play(kind: StringName) -> bool:
	if not enabled:
		return false
	var stream: AudioStream = click if kind == &"click" else hover
	if stream == null:
		return false
	var player: AudioStreamPlayer = _players[_next]
	_next = (_next + 1) % _players.size()
	player.stream = stream
	player.play()
	played.emit(kind)
	return true


func _watch_tree(node: Node) -> void:
	_watch(node)
	for child: Node in node.get_children():
		_watch_tree(child)


func _watch(node: Node) -> void:
	if not node is BaseButton or not bool(node.get_meta(&"ui_sounds", true)):
		return
	var button: BaseButton = node
	if button.pressed.is_connected(_on_pressed):
		return
	button.pressed.connect(_on_pressed)
	button.mouse_entered.connect(_on_hovered.bind(button))
	button.focus_entered.connect(_on_hovered.bind(button))


func _on_pressed() -> void:
	play(&"click")


func _on_hovered(button: BaseButton) -> void:
	if button.disabled or not button.is_visible_in_tree():
		return
	play(&"hover")


func _setting_stream(setting: String) -> AudioStream:
	var path: String = str(ProjectSettings.get_setting(setting, ""))
	if path.is_empty():
		return null
	if not ResourceLoader.exists(path):
		push_warning("UiSounds: %s (%s) is missing; it stays silent." % [path, setting])
		return null
	var stream: AudioStream = load(path) as AudioStream
	if stream == null:
		push_warning("UiSounds: %s (%s) is not an audio stream." % [path, setting])
	return stream
