extends SceneTree
## Confirms the installed GodotSteam extension has every name the Steam
## feature uses: the init methods, the other singleton methods, the lobby
## signals with their argument counts, and SteamMultiplayerPeer's
## create_host and create_client. Steam itself is never started.
## Run: godot --headless --script res://tests/features/steam/extension_check.gd

const SERVICE := "res://features/steam/steam_service.gd"
const SINGLETON := "Steam"
const PEER_CLASS := &"SteamMultiplayerPeer"

var _failures: int = 0


func _initialize() -> void:
	_run.call_deferred()


func _run() -> void:
	var service: GDScript = load(SERVICE) as GDScript
	var names: Dictionary = service.get_script_constant_map() if service != null else {}
	_expect(not names.is_empty(), "the Steam service script loads")
	_expect(Engine.has_singleton(SINGLETON), "the Steam singleton is registered")
	if names.is_empty() or not Engine.has_singleton(SINGLETON):
		_finish()
		return
	var steam: Object = Engine.get_singleton(SINGLETON)
	var methods: Dictionary = {}
	for info: Dictionary in steam.get_method_list():
		methods[str(info.get("name", ""))] = (info.get("args", []) as Array).size()
	for method: String in names["INIT_METHODS"]:
		_expect(methods.has(method), "Steam has %s" % method)
		_expect(int(methods.get(method, -1)) == 2, "%s takes app_id and embed_callbacks" % method)
	for method: String in names["METHODS"]:
		_expect(methods.has(method), "Steam has %s" % method)
	var signals: Dictionary = {}
	for info: Dictionary in steam.get_signal_list():
		signals[str(info.get("name", ""))] = (info.get("args", []) as Array).size()
	var counts: Dictionary = names["SIGNAL_ARGS"]
	for signal_name: String in names["SIGNALS"]:
		_expect(signals.has(signal_name), "Steam has the %s signal" % signal_name)
		_expect(
			int(signals.get(signal_name, -1)) == int(counts.get(signal_name, -2)),
			(
				"%s carries %s arguments (%s)"
				% [signal_name, counts.get(signal_name), signals.get(signal_name)]
			)
		)
	_expect(ClassDB.class_exists(PEER_CLASS), "SteamMultiplayerPeer exists")
	_expect(ClassDB.is_parent_class(PEER_CLASS, &"MultiplayerPeer"), "it is a MultiplayerPeer")
	for method: String in names["PEER_METHODS"]:
		_expect(
			ClassDB.class_has_method(PEER_CLASS, method), "SteamMultiplayerPeer has %s" % method
		)
	_finish()


func _expect(condition: bool, what: String) -> void:
	if not condition:
		_failures += 1
		print("FAIL ", what)


func _finish() -> void:
	if _failures == 0:
		print("steam extension: ok")
	quit(0 if _failures == 0 else 1)
