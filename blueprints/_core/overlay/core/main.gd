extends Node
## The main scene. Shows the main menu, or starts the level straight away when
## the game was launched with `-- --autostart` (tests and quick runs).


func _ready() -> void:
	if Game.take_autostart():
		Game.start_game.call_deferred()
	else:
		Menus.open_main()
