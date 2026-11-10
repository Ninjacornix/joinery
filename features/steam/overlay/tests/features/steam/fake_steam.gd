# gdlint: disable=function-name
extends Object
## A stand-in for the GodotSteam singleton, registered by the steam test with
## `Engine.register_singleton("Steam", fake)`. It records every call and, like
## Steam, answers lobby requests on the next `run_callbacks`.

@warning_ignore("unused_signal")
signal lobby_created(result: int, lobby_id: int)
@warning_ignore("unused_signal")
signal lobby_joined(lobby_id: int, permissions: int, locked: bool, response: int)
@warning_ignore("unused_signal")
signal join_requested(lobby_id: int, steam_id: int)

const MY_ID := 76561190000000001

## steamInitEx status: 0 = OK.
var init_status: int = 0
var init_verbal: String = "Steamworks active."
## EResult for createLobby (1 = OK) and the id it hands out.
var create_result: int = 1
var next_lobby: int = 7001
## EChatRoomEnterResponse for joinLobby (1 = success).
var join_response: int = 1
var lobby_owner: int = MY_ID
## [method name, args...] in call order.
var calls: Array = []
var callbacks_run: int = 0

var _pending: Array[Callable] = []


# GodotSteam's method names are camelCase.
## Same arguments as GodotSteam 4.23: (app_id, embed_callbacks).
func steamInitEx(app_id: int, embed_callbacks: bool) -> Dictionary:
	calls.append(["steamInitEx", app_id, embed_callbacks])
	return {"status": init_status, "verbal": init_verbal}


func run_callbacks() -> void:
	callbacks_run += 1
	var due: Array[Callable] = _pending.duplicate()
	_pending.clear()
	for callback: Callable in due:
		callback.call()


func steamShutdown() -> void:
	calls.append(["steamShutdown"])


func setAchievement(id: String) -> bool:
	calls.append(["setAchievement", id])
	return true


func clearAchievement(id: String) -> bool:
	calls.append(["clearAchievement", id])
	return true


func setStatInt(stat: String, value: int) -> bool:
	calls.append(["setStatInt", stat, value])
	return true


func setStatFloat(stat: String, value: float) -> bool:
	calls.append(["setStatFloat", stat, value])
	return true


func storeStats() -> bool:
	calls.append(["storeStats"])
	return true


func setRichPresence(key: String, value: String) -> bool:
	calls.append(["setRichPresence", key, value])
	return true


func clearRichPresence() -> void:
	calls.append(["clearRichPresence"])


func createLobby(lobby_type: int, max_members: int) -> void:
	calls.append(["createLobby", lobby_type, max_members])
	var id: int = next_lobby if create_result == 1 else 0
	next_lobby += 1
	_pending.append(func() -> void: lobby_created.emit(create_result, id))
	if create_result == 1:
		_pending.append(func() -> void: lobby_joined.emit(id, 0, false, 1))


func joinLobby(lobby_id: int) -> void:
	calls.append(["joinLobby", lobby_id])
	_pending.append(func() -> void: lobby_joined.emit(lobby_id, 0, false, join_response))


func leaveLobby(lobby_id: int) -> void:
	calls.append(["leaveLobby", lobby_id])


func getLobbyOwner(lobby_id: int) -> int:
	calls.append(["getLobbyOwner", lobby_id])
	return lobby_owner


func getPersonaName() -> String:
	return "Fake Player"


func getSteamID() -> int:
	return MY_ID


func activateGameOverlay(kind: String) -> void:
	calls.append(["activateGameOverlay", kind])


func activateGameOverlayInviteDialog(lobby_id: int) -> void:
	calls.append(["activateGameOverlayInviteDialog", lobby_id])


## Arguments of the last call to `method`, or null when it was never called.
func last(method: String) -> Variant:
	for index: int in range(calls.size() - 1, -1, -1):
		var call_args: Array = calls[index]
		if call_args[0] == method:
			return call_args.slice(1)
	return null


func count(method: String) -> int:
	var found: int = 0
	for call_args: Array in calls:
		if call_args[0] == method:
			found += 1
	return found
