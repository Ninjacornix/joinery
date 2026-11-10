class_name EnetTransport
extends NetTransport
## Godot's ENet peer: plain UDP on a port, for LAN and direct internet games.


func create_host(port: int, max_clients: int) -> MultiplayerPeer:
	var peer: ENetMultiplayerPeer = ENetMultiplayerPeer.new()
	last_error = peer.create_server(port, max_clients)
	return peer if last_error == OK else null


func create_client(address: String, port: int) -> MultiplayerPeer:
	var peer: ENetMultiplayerPeer = ENetMultiplayerPeer.new()
	last_error = peer.create_client(address, port)
	return peer if last_error == OK else null


func describe() -> String:
	return "enet"
