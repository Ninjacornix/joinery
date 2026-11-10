extends Node
## Adds "Two players" to the main menu: a same-screen game where players take
## turns at one keyboard, pad or mouse. A match keeps the turn, a miss passes
## it, and whoever finds the most pairs wins.

const ENTRY := &"two_players"


func _ready() -> void:
	Menus.add_entry(&"main", ENTRY, "Two players", _start_two_players, 12)


func _start_two_players() -> void:
	Game.start_game({"seats": 2})
