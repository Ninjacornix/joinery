extends NetTransport
## Online play over Steam networking, for the online-play feature's `Net`.
## `SteamService` loads this file only when online play is added (so
## `NetTransport` exists) and a Steam multiplayer peer can be made; it is never
## referenced otherwise, so projects without online play never compile it.
##
## Untested against the real extension: `SteamMultiplayerPeer.create_host
## (virtual_port)` and `create_client(steam_id, virtual_port)` follow the
## GodotSteam 4.x networking peer and are verified only with a fake peer until
## the user approves a GodotSteam version.
##
## Steam picks the route, so the ENet port is ignored; the client's address is
## the lobby owner's Steam id as text.

const PEER_CLASS := &"SteamMultiplayerPeer"

## `func(as_host: bool, address: String, port: int, max_clients: int)
## -> MultiplayerPeer`; replaces the extension's peer (tests).
var peer_factory: Callable = Callable()
## Steam networking virtual port (both sides must match).
var virtual_port: int = 0


static func is_supported() -> bool:
	return ClassDB.class_exists(PEER_CLASS)


func create_host(port: int, max_clients: int) -> MultiplayerPeer:
	if peer_factory.is_valid():
		return _made(peer_factory.call(true, "", port, max_clients))
	var peer: MultiplayerPeer = _new_peer(&"create_host")
	if peer == null:
		return null
	return _started(peer, int(peer.call(&"create_host", virtual_port)))


func create_client(address: String, port: int) -> MultiplayerPeer:
	if not address.is_valid_int() or address.to_int() <= 0:
		last_error = ERR_INVALID_PARAMETER
		return null
	if peer_factory.is_valid():
		return _made(peer_factory.call(false, address, port, 0))
	var peer: MultiplayerPeer = _new_peer(&"create_client")
	if peer == null:
		return null
	return _started(peer, int(peer.call(&"create_client", address.to_int(), virtual_port)))


func describe() -> String:
	return "Steam"


func _new_peer(method: StringName) -> MultiplayerPeer:
	last_error = ERR_UNAVAILABLE
	if not is_supported():
		return null
	var peer: MultiplayerPeer = ClassDB.instantiate(PEER_CLASS) as MultiplayerPeer
	if peer == null or not peer.has_method(method):
		return null
	last_error = OK
	return peer


func _made(value: Variant) -> MultiplayerPeer:
	var peer: MultiplayerPeer = value as MultiplayerPeer
	last_error = OK if peer != null else ERR_CANT_CREATE
	return peer


func _started(peer: MultiplayerPeer, error: int) -> MultiplayerPeer:
	last_error = error as Error
	return peer if error == OK else null
