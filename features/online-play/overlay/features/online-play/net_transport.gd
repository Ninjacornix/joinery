class_name NetTransport
extends RefCounted
## Makes the MultiplayerPeer that `Net` hosts or joins with. The default is
## `EnetTransport` (UDP, LAN or internet); another transport, such as one for
## a platform's relay network, extends this class and is handed to
## `Net.set_transport` before hosting or joining.

## The last error a create call met (OK when it worked).
var last_error: Error = OK


## A server peer listening on `port` for up to `max_clients` players, or null.
func create_host(_port: int, _max_clients: int) -> MultiplayerPeer:
	last_error = ERR_UNAVAILABLE
	return null


## A client peer connecting to `address`:`port`, or null.
func create_client(_address: String, _port: int) -> MultiplayerPeer:
	last_error = ERR_UNAVAILABLE
	return null


## A short name for menus and logs.
func describe() -> String:
	return "none"
