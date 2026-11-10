extends RefCounted
## Base of a starting point's online check: the adapter for a starting point
## ships `res://tests/features/online-play/blueprint_check.gd` (extends this
## script), and pair_test.gd runs its steps between its own. `prepare(level)`
## runs first on every side; then:
##
## 1. `client_check(level)` on the joining player, after the probe round trip.
## 2. `host_check(level)` on the host or dedicated server, once the client's
##    step is done.
## 3. `client_verify(level)` on the joining player, once the host's step is
##    done (to see what the host did arrive).
##
## Use `play.game`, `play.menus` and `play.events` rather than the autoload
## names: this script is compiled with the test, before the autoloads exist.

const LONG_MS := 30000
const SERVER := 1

var play: PlayHarness
var tree: SceneTree
var net: Node
var replication: Node


func setup(harness: PlayHarness, scene_tree: SceneTree) -> void:
	play = harness
	tree = scene_tree
	net = tree.root.get_node(^"Net")
	replication = net.get(&"replication") as Node


## Every side, once the level (and this side's own player) is there. Use it
## to make the test deterministic, for example to keep idle players alive.
func prepare(_level: Node) -> void:
	await tree.process_frame


func client_check(_level: Node) -> void:
	await tree.process_frame


func host_check(_level: Node) -> void:
	await tree.process_frame


func client_verify(_level: Node) -> void:
	await tree.process_frame


## True when the server is also a player (a host, not a dedicated server).
func server_plays() -> bool:
	return (replication.get(&"level_peers") as Dictionary).has(SERVER)


## This game instance's peer id.
func me() -> int:
	return tree.root.multiplayer.get_unique_id()


## The first player other than the server that has the level, or 0.
func client_id() -> int:
	for id: int in replication.call(&"ready_peers"):
		if id != SERVER:
			return id
	return 0


## The player node owned by `peer`, or null.
func player_of(peer: int) -> Node:
	for player: Node in play.game.players():
		if player.name == str(peer):
			return player
	return null


## The adapter node (`/root/Net/Adapter`), or null.
func adapter() -> Node:
	return net.get_node_or_null(^"Adapter")


## Waits (real time) until `condition` holds; records a failure after `limit_ms`.
func until(condition: Callable, message: String, limit_ms: int = LONG_MS) -> bool:
	var deadline: int = Time.get_ticks_msec() + limit_ms
	while not bool(condition.call()):
		if Time.get_ticks_msec() > deadline:
			play.expect(false, "%s (waited %d ms)" % [message, limit_ms])
			return false
		await tree.process_frame
	return true


func wait_ms(ms: int) -> void:
	var deadline: int = Time.get_ticks_msec() + ms
	while Time.get_ticks_msec() < deadline:
		await tree.process_frame
