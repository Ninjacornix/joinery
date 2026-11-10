extends Node
## Sets up what the platformer adds to the core menus, once at start-up, so
## the "Gameplay" settings page is there from the main menu as well as in the
## pause menu.

const GameplayPage := preload("res://game/ui/gameplay_page.gd")


func _ready() -> void:
	Menus.add_settings_page(&"gameplay", "Gameplay", GameplayPage.build, 35)
