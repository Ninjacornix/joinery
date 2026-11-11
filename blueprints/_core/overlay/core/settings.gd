extends Node
## Player settings, saved to user://settings.cfg as soon as they change.
##
## Sections and keys the core applies:
##   audio/master, audio/music, audio/sfx  linear 0-1 on buses Master, Music, SFX,
##                                          on top of the bus layout's own volume
##   video/fullscreen, video/vsync          bool
##   access/screen_shake                    0-1, read by camera shake code
##   access/reduced_motion                  bool, read by tweens and effects
##   locale/code                            a locale such as "en" or "de"
## Features add their own sections (input, online, ...). Tests pass
## `-- --settings-path=<file>` to keep their settings apart.

signal changed(section: String, key: String)

const DEFAULT_PATH := "user://settings.cfg"
const PATH_ARG := "--settings-path="
const DEFAULTS := {
	"audio": {"master": 1.0, "music": 0.8, "sfx": 1.0},
	"video": {"fullscreen": false, "vsync": true},
	"access": {"screen_shake": 1.0, "reduced_motion": false},
}
const BUSES := {"master": &"Master", "music": &"Music", "sfx": &"SFX"}

var path: String = DEFAULT_PATH

var _config: ConfigFile = ConfigFile.new()
## Each bus's volume in the bus layout (dB), read once at start-up. The
## player's level is added to it, so a mix made in the layout survives.
var _base_db: Dictionary = {}


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	for bus_name: StringName in BUSES.values():
		var index: int = AudioServer.get_bus_index(bus_name)
		if index >= 0:
			_base_db[bus_name] = AudioServer.get_bus_volume_db(index)
	for argument: String in OS.get_cmdline_user_args():
		if argument.begins_with(PATH_ARG):
			path = argument.substr(PATH_ARG.length())
	reload()


## Reads the settings file again and applies every value.
func reload() -> void:
	_config = ConfigFile.new()
	if FileAccess.file_exists(path):
		var error: Error = _config.load(path)
		if error != OK:
			push_warning("Settings file %s could not be read; using defaults." % path)
			_config = ConfigFile.new()
	_apply_all()


func get_value(section: String, key: String, default: Variant = null) -> Variant:
	if _config.has_section_key(section, key):
		return _config.get_value(section, key)
	if default != null:
		return default
	var defaults: Dictionary = DEFAULTS.get(section, {})
	return defaults.get(key)


func set_value(section: String, key: String, value: Variant) -> void:
	_config.set_value(section, key, value)
	_save()
	_apply(section, key)
	changed.emit(section, key)


func has_value(section: String, key: String) -> bool:
	return _config.has_section_key(section, key)


## The bus layout's own volume (dB) of a bus the player's sliders scale.
func base_volume_db(bus_name: StringName) -> float:
	return float(_base_db.get(bus_name, 0.0))


## Screen shake strength to multiply camera shake by (0 = off).
func screen_shake() -> float:
	return clampf(float(get_value("access", "screen_shake")), 0.0, 1.0)


func reduced_motion() -> bool:
	return bool(get_value("access", "reduced_motion"))


func _save() -> void:
	var directory: String = path.get_base_dir()
	if not directory.is_empty() and not DirAccess.dir_exists_absolute(directory):
		DirAccess.make_dir_recursive_absolute(directory)
	var error: Error = _config.save(path)
	if error != OK:
		push_warning("Settings could not be saved to %s." % path)


func _apply_all() -> void:
	for key: String in BUSES:
		_apply("audio", key)
	_apply("video", "fullscreen")
	_apply("video", "vsync")
	_apply("locale", "code")


func _apply(section: String, key: String) -> void:
	match section:
		"audio":
			_apply_volume(key)
		"video":
			_apply_video(key)
		"locale":
			if key == "code" and has_value("locale", "code"):
				TranslationServer.set_locale(str(get_value("locale", "code")))


func _apply_volume(key: String) -> void:
	if not BUSES.has(key):
		return
	var bus: int = AudioServer.get_bus_index(BUSES[key])
	if bus < 0:
		return
	var level: float = clampf(float(get_value("audio", key)), 0.0, 1.0)
	AudioServer.set_bus_mute(bus, level <= 0.001)
	AudioServer.set_bus_volume_db(
		bus, base_volume_db(BUSES[key]) + linear_to_db(maxf(level, 0.001))
	)


func _apply_video(key: String) -> void:
	# Only values the player chose are applied, so project window settings stand
	# until then; a headless run has no window to change.
	if not has_value("video", key) or DisplayServer.get_name() == "headless":
		return
	var window: Window = get_window()
	if key == "fullscreen":
		var full: bool = bool(get_value("video", "fullscreen"))
		var is_full: bool = (
			window.mode in [Window.MODE_FULLSCREEN, Window.MODE_EXCLUSIVE_FULLSCREEN]
		)
		if full != is_full:
			window.mode = Window.MODE_FULLSCREEN if full else Window.MODE_WINDOWED
	elif key == "vsync":
		DisplayServer.window_set_vsync_mode(
			(
				DisplayServer.VSYNC_ENABLED
				if bool(get_value("video", "vsync"))
				else DisplayServer.VSYNC_DISABLED
			)
		)
