extends Node2D
## A tiny custom game with faults planted on purpose, for the playtest bot's tests:
##
## - at 2 s a script error (a call on a null instance), in this file;
## - at 4 s one frame that takes 300 ms (a hitch);
## - at 5 s the player stops responding to input for good (a softlock).
##
## Times are physics frames at 60 a second, so a seeded run always meets them.

const ERROR_FRAME := 120
const HITCH_FRAME := 240
const FREEZE_FRAME := 300

var _frames: int = 0
var _missing: Node = null

@onready var player: CharacterBody2D = $Player


func _physics_process(_delta: float) -> void:
	_frames += 1
	if _frames == ERROR_FRAME:
		_missing.queue_free()
	elif _frames == HITCH_FRAME:
		OS.delay_msec(300)
	elif _frames == FREEZE_FRAME:
		player.set_physics_process(false)
