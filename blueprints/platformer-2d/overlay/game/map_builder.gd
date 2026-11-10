extends RefCounted
## Turns an ASCII level map into greybox geometry: flat-coloured polygons with
## collision, no image files. One character is one tile:
##
##   .  empty                 #  solid ground
##   -  one-way platform      ^  spikes
##   o  coin                  C  checkpoint
##   F  goal flag             P  player start
##
## Rows are listed top to bottom and must all be the same length. Items stand
## on the bottom centre of their tile; coins float in its centre.

const EMPTY := "."
const SOLID := "#"
const ONE_WAY := "-"
const SPIKES := "^"
const COIN := "o"
const CHECKPOINT := "C"
const GOAL := "F"
const PLAYER := "P"
const LEGEND := ".#-^oCFP"

## Physics layers (see project settings `layer_names/2d_physics`).
const WORLD_LAYER := 1
const ONE_WAY_LAYER := 4

const GROUND_COLOUR := Color("#4d5869")
const GROUND_EDGE_COLOUR := Color("#7fae6a")
const ONE_WAY_COLOUR := Color("#a8875a")
const ONE_WAY_EDGE_COLOUR := Color("#d9b57c")
const EDGE_HEIGHT := 3.0
const ONE_WAY_THICKNESS := 4.0


## Problems that make a map unusable, as readable sentences (empty = fine).
static func problems(rows: Array[String]) -> PackedStringArray:
	var found: PackedStringArray = PackedStringArray()
	if rows.is_empty():
		found.append("the map has no rows")
		return found
	var width: int = rows[0].length()
	var players: int = 0
	var goals: int = 0
	for r: int in rows.size():
		var row: String = rows[r]
		if row.length() != width:
			found.append("row %d is %d tiles wide, expected %d" % [r, row.length(), width])
		for c: int in row.length():
			var tile: String = row[c]
			if not LEGEND.contains(tile):
				found.append("unknown tile '%s' at column %d, row %d" % [tile, c, r])
			elif tile == PLAYER:
				players += 1
			elif tile == GOAL:
				goals += 1
	if players != 1:
		found.append("the map needs exactly one player start P, found %d" % players)
	if goals < 1:
		found.append("the map needs a goal flag F")
	return found


## The tile at `cell`, or EMPTY outside the map.
static func tile_at(rows: Array[String], cell: Vector2i) -> String:
	if cell.y < 0 or cell.y >= rows.size():
		return EMPTY
	var row: String = rows[cell.y]
	if cell.x < 0 or cell.x >= row.length():
		return EMPTY
	return row[cell.x]


## Every cell holding `tile`, row by row.
static func cells(rows: Array[String], tile: String) -> Array[Vector2i]:
	var found: Array[Vector2i] = []
	for r: int in rows.size():
		var row: String = rows[r]
		for c: int in row.length():
			if row[c] == tile:
				found.append(Vector2i(c, r))
	return found


## Horizontal runs of `tile` in one row, as (first column, last column).
static func runs(row: String, tile: String) -> Array[Vector2i]:
	var found: Array[Vector2i] = []
	var start: int = -1
	for c: int in row.length() + 1:
		var inside: bool = c < row.length() and row[c] == tile
		if inside and start < 0:
			start = c
		elif not inside and start >= 0:
			found.append(Vector2i(start, c - 1))
			start = -1
	return found


## Runs of `tile` merged downwards into rectangles (in tiles), so a floor is
## one collision box instead of one per tile and nothing snags on seams.
static func merged_rects(rows: Array[String], tile: String) -> Array[Rect2i]:
	var rects: Array[Rect2i] = []
	var open: Dictionary = {}
	for r: int in rows.size():
		var still_open: Dictionary = {}
		for run: Vector2i in runs(rows[r], tile):
			if open.has(run):
				var index: int = open[run]
				var rect: Rect2i = rects[index]
				rect.size.y += 1
				rects[index] = rect
				still_open[run] = index
			else:
				rects.append(Rect2i(run.x, r, run.y - run.x + 1, 1))
				still_open[run] = rects.size() - 1
		open = still_open
	return rects


## Where something standing in `cell` puts its feet.
static func cell_bottom(cell: Vector2i, tile_size: int) -> Vector2:
	return Vector2((float(cell.x) + 0.5) * tile_size, float(cell.y + 1) * tile_size)


static func cell_centre(cell: Vector2i, tile_size: int) -> Vector2:
	return (Vector2(cell) + Vector2(0.5, 0.5)) * tile_size


## The cell that contains a world position.
static func world_to_cell(at: Vector2, tile_size: int) -> Vector2i:
	return Vector2i(floori(at.x / tile_size), floori(at.y / tile_size))


## Solid ground: one static body with a box per merged rectangle, a flat fill
## and a lighter edge on every top surface.
static func build_solids(parent: Node2D, rows: Array[String], tile_size: int) -> StaticBody2D:
	var body: StaticBody2D = StaticBody2D.new()
	body.name = "Solids"
	body.collision_layer = 0
	body.collision_mask = 0
	body.set_collision_layer_value(WORLD_LAYER, true)
	parent.add_child(body)
	for rect: Rect2i in merged_rects(rows, SOLID):
		var area: Rect2 = Rect2(Vector2(rect.position * tile_size), Vector2(rect.size * tile_size))
		var shape: CollisionShape2D = CollisionShape2D.new()
		var box: RectangleShape2D = RectangleShape2D.new()
		box.size = area.size
		shape.shape = box
		shape.position = area.get_center()
		body.add_child(shape)
		body.add_child(_quad(area, GROUND_COLOUR))
	for r: int in rows.size():
		for run: Vector2i in _top_runs(rows, r, SOLID):
			var edge: Rect2 = Rect2(
				float(run.x * tile_size), float(r * tile_size),
				float((run.y - run.x + 1) * tile_size), EDGE_HEIGHT
			)
			body.add_child(_quad(edge, GROUND_EDGE_COLOUR))
	return body


## Thin platforms you can jump up through and drop down from.
static func build_one_ways(parent: Node2D, rows: Array[String], tile_size: int) -> void:
	for r: int in rows.size():
		for run: Vector2i in runs(rows[r], ONE_WAY):
			var area: Rect2 = Rect2(
				float(run.x * tile_size), float(r * tile_size),
				float((run.y - run.x + 1) * tile_size), ONE_WAY_THICKNESS
			)
			var body: StaticBody2D = StaticBody2D.new()
			body.name = "OneWay_%d_%d" % [run.x, r]
			body.add_to_group(&"one_way")
			body.collision_layer = 0
			body.collision_mask = 0
			body.set_collision_layer_value(ONE_WAY_LAYER, true)
			var shape: CollisionShape2D = CollisionShape2D.new()
			var box: RectangleShape2D = RectangleShape2D.new()
			box.size = area.size
			shape.shape = box
			shape.position = area.get_center()
			shape.one_way_collision = true
			body.add_child(shape)
			body.add_child(_quad(area, ONE_WAY_COLOUR))
			body.add_child(_quad(Rect2(area.position, Vector2(area.size.x, 1.0)), ONE_WAY_EDGE_COLOUR))
			parent.add_child(body)


static func _top_runs(rows: Array[String], r: int, tile: String) -> Array[Vector2i]:
	var exposed: String = ""
	var row: String = rows[r]
	for c: int in row.length():
		var covered: bool = tile_at(rows, Vector2i(c, r - 1)) == tile
		exposed += tile if row[c] == tile and not covered else EMPTY
	return runs(exposed, tile)


static func _quad(area: Rect2, colour: Color) -> Polygon2D:
	var polygon: Polygon2D = Polygon2D.new()
	polygon.color = colour
	polygon.polygon = PackedVector2Array([
		area.position,
		Vector2(area.end.x, area.position.y),
		area.end,
		Vector2(area.position.x, area.end.y),
	])
	return polygon
