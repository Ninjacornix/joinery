class_name LanDiscovery
extends Node
## Finds games on the local network. A host sends a small JSON beacon
## `{game, name, port, players, max, version}` every second to the broadcast
## address; a browser listens on the same UDP port and lists every host it
## heard from in the last three seconds. Nothing leaves the local network.

signal hosts_changed

enum Mode { IDLE, BEACON, LISTEN }

const INTERVAL_MS := 1000
const EXPIRY_MS := 3000

var mode: Mode = Mode.IDLE
var port: int = 0
var broadcast_address: String = ""
## Called for each beacon in beacon mode; returns the beacon Dictionary.
var make_beacon: Callable = Callable()

var _udp: PacketPeerUDP = null
var _next_send: int = 0
var _hosts: Dictionary = {}


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS


func _exit_tree() -> void:
	stop()


## Starts sending beacons to `address`:`lan_port` (a host).
func start_beacon(lan_port: int, address: String, beacon: Callable) -> Error:
	stop()
	_udp = PacketPeerUDP.new()
	_udp.set_broadcast_enabled(true)
	var error: Error = _udp.set_dest_address(address, lan_port)
	if error != OK:
		_udp = null
		return error
	port = lan_port
	broadcast_address = address
	make_beacon = beacon
	mode = Mode.BEACON
	_next_send = 0
	return OK


## Starts listening for beacons on `lan_port` (a player looking for games).
func start_listening(lan_port: int) -> Error:
	stop()
	_udp = PacketPeerUDP.new()
	var error: Error = _udp.bind(lan_port, "0.0.0.0")
	if error != OK:
		_udp = null
		return error
	port = lan_port
	mode = Mode.LISTEN
	return OK


func stop() -> void:
	if _udp != null:
		_udp.close()
	_udp = null
	mode = Mode.IDLE
	if not _hosts.is_empty():
		_hosts.clear()
		hosts_changed.emit()


func is_active() -> bool:
	return mode != Mode.IDLE


## Hosts heard from recently, oldest first. Each entry is the beacon plus
## `address` (where it came from) and `compatible` (same game and version).
func hosts() -> Array[Dictionary]:
	var found: Array[Dictionary] = []
	for entry: Dictionary in _hosts.values():
		found.append(entry)
	return found


func _process(_delta: float) -> void:
	if _udp == null:
		return
	var now: int = Time.get_ticks_msec()
	if mode == Mode.BEACON:
		if now >= _next_send:
			_next_send = now + INTERVAL_MS
			_send_beacon()
	elif mode == Mode.LISTEN:
		var changed: bool = _receive(now)
		if _expire(now) or changed:
			hosts_changed.emit()


func _send_beacon() -> void:
	if not make_beacon.is_valid():
		return
	var beacon: Variant = make_beacon.call()
	if beacon is Dictionary:
		_udp.put_packet(JSON.stringify(beacon).to_utf8_buffer())


func _receive(now: int) -> bool:
	var changed: bool = false
	while _udp.get_available_packet_count() > 0:
		var data: PackedByteArray = _udp.get_packet()
		var address: String = _udp.get_packet_ip()
		var parsed: Variant = JSON.parse_string(data.get_string_from_utf8())
		if not parsed is Dictionary:
			continue
		var beacon: Dictionary = parsed
		var host_port: int = int(beacon.get("port", 0))
		if host_port <= 0 or host_port > 65535 or address.is_empty():
			continue
		var key: String = "%s:%d" % [address, host_port]
		var entry: Dictionary = {
			"game": str(beacon.get("game", "")),
			"name": str(beacon.get("name", "")),
			"port": host_port,
			"players": int(beacon.get("players", 0)),
			"max": int(beacon.get("max", 0)),
			"version": int(beacon.get("version", 0)),
			"address": address,
			"seen": now,
		}
		entry["compatible"] = (
			entry["game"] == str(ProjectSettings.get_setting("application/config/name", ""))
			and entry["version"] == int(ProjectSettings.get_setting("online/protocol_version", 1))
		)
		if not _hosts.has(key) or _differs(_hosts[key], entry):
			changed = true
		_hosts[key] = entry
	return changed


func _expire(now: int) -> bool:
	var gone: Array = []
	for key: Variant in _hosts:
		if now - int((_hosts[key] as Dictionary)["seen"]) > EXPIRY_MS:
			gone.append(key)
	for key: Variant in gone:
		_hosts.erase(key)
	return not gone.is_empty()


static func _differs(a: Dictionary, b: Dictionary) -> bool:
	for key: String in ["name", "players", "max", "version", "game"]:
		if a.get(key) != b.get(key):
			return true
	return false
