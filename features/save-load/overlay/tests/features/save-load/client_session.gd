extends GameSession
## Stands in for an online client's session in tests: the game is online and
## this instance is not the authority, so it may neither save nor load.


func is_online() -> bool:
	return true


func can_pause() -> bool:
	return false


func is_authority() -> bool:
	return false
