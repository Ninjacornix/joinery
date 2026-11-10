extends Node2D
## The starting level: an empty, lit space to build your game in. Add scenes
## under `World`, spawn markers in group `player_spawn`, and set
## `game/hooks/player_scene` to have the core spawn a player under `Players`.


func _ready() -> void:
	Game.level_ready(self)
