extends Node
## Planted on purpose: far too much work every frame.

var sink: float = 0.0


func _process(_delta: float) -> void:
	var total: float = 0.0
	for i: int in range(300000):
		total += sqrt(float(i))
	sink = total
