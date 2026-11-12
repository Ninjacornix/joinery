@tool
extends Control
## The playtest's coverage map, top-down: the level's floor as the bot mapped it, the parts
## the player stood on in a quiet tone and the parts nobody reached in the danger colour, with
## a small ring where the run started. Drawn from the report's grid in the theme's colours, so
## it reads in light and dark; the same map is saved as a picture for agents (coverage.png).
##
## Heat layers from the last few playtests (joinery/playtest_heat.py) paint on the same map:
## time spent as an accent wash (stronger where the bot stood longer), deaths as filled dots
## and stuck points as open rings, each sized by how often. They differ in shape as well as
## colour. While a layer is on, the unreached floor steps back to a faint tone so the heat
## reads first.

const UI_THEME := preload("ui_theme.gd")
## Widest the map draws, in theme units, and tallest.
const WIDTH_UNITS := 240.0
const HEIGHT_UNITS := 96.0
## How strong the unreached cells are: marked, without drowning the map in alarm.
const MISSED_ALPHA := 0.72
## The heat layers, in drawing order.
const LAYERS: Array[String] = ["time", "deaths", "stuck"]
## Time spent: the faintest and strongest wash.
const TIME_ALPHA := Vector2(0.18, 0.85)

var _grid: PackedStringArray = PackedStringArray()
var _cols := 0
var _rows := 0
var _start := Vector2i(-1, -1)
## Layer -> {Vector2i: value}.
var _heat: Dictionary = {}
var _on: Dictionary = {}


func _init() -> void:
	name = "CoverageMap"
	mouse_filter = Control.MOUSE_FILTER_PASS
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	accessibility_name = "Map of the level: reached and not reached"


## Show [param coverage] (the report's "coverage": {grid, cols, rows, start, percent}).
func set_coverage(coverage: Dictionary) -> void:
	_grid = PackedStringArray()
	for line: Variant in coverage.get("grid", []):
		_grid.append(str(line))
	_rows = _grid.size()
	_cols = 0
	for line: String in _grid:
		_cols = maxi(_cols, line.length())
	var start: Variant = coverage.get("start", [-1, -1])
	if start is Array and (start as Array).size() == 2:
		_start = Vector2i(int(start[0]), int(start[1]))
	_fit()
	queue_redraw()


## Heat over recent runs ([code]joinery playtest --heatmap --json[/code]): each layer a list of
## [col, row, value] on this map's grid. A heatmap drawn on another grid is ignored.
func set_heat(heat: Dictionary) -> void:
	_heat.clear()
	var grid: Variant = heat.get("grid", [])
	if grid is Array and not (grid as Array).is_empty() and (grid as Array).size() != _rows:
		queue_redraw()
		return
	for layer: String in LAYERS:
		var cells := {}
		for entry: Variant in heat.get(layer, []):
			if entry is Array and (entry as Array).size() >= 3:
				var at := Vector2i(int(entry[0]), int(entry[1]))
				if at.x >= 0 and at.y >= 0 and at.x < _cols and at.y < _rows:
					cells[at] = float(cells.get(at, 0.0)) + float(entry[2])
		_heat[layer] = cells
	queue_redraw()


## Show or hide a heat layer ("time", "deaths" or "stuck").
func set_layer(layer: String, on: bool) -> void:
	_on[layer] = on
	queue_redraw()


func layer_on(layer: String) -> bool:
	return bool(_on.get(layer, false))


## Cells a heat layer marks.
func layer_cells(layer: String) -> int:
	return (_heat.get(layer, {}) as Dictionary).size()


## The layer's total: deaths or stuck points, or seconds spent.
func layer_total(layer: String) -> float:
	var total := 0.0
	for value: Variant in (_heat.get(layer, {}) as Dictionary).values():
		total += float(value)
	return total


## Cells shown: [reached, not reached].
func counts() -> Array[int]:
	var reached := 0
	var missed := 0
	for line: String in _grid:
		reached += line.count("#")
		missed += line.count(".")
	return [reached, missed]


func _notification(what: int) -> void:
	if what in [NOTIFICATION_THEME_CHANGED, NOTIFICATION_ENTER_TREE]:
		_fit()
		queue_redraw()
	elif what == NOTIFICATION_RESIZED:
		queue_redraw()


func _fit() -> void:
	if _cols == 0 or _rows == 0 or not is_inside_tree():
		custom_minimum_size = Vector2.ZERO
		return
	var unit := UI_THEME.unit(self)
	var side := minf(WIDTH_UNITS * unit / _cols, HEIGHT_UNITS * unit / _rows)
	custom_minimum_size = Vector2(0, ceilf(side * _rows))


func _heat_shown() -> bool:
	for layer: String in LAYERS:
		if layer_on(layer) and layer_cells(layer) > 0:
			return true
	return false


func _draw() -> void:
	if _cols == 0 or _rows == 0:
		return
	var unit := UI_THEME.unit(self)
	var side := minf(
		minf(size.x, WIDTH_UNITS * unit) / _cols, minf(size.y, HEIGHT_UNITS * unit) / _rows
	)
	if side <= 0.0:
		return
	var heat := _heat_shown()
	var reached := UI_THEME.color(self, "text_muted")
	reached.a = 0.32
	var missed := UI_THEME.color(self, "danger")
	missed.a = MISSED_ALPHA
	if heat:
		# The heat reads first: unreached floor is only floor now.
		missed = UI_THEME.color(self, "text_faint")
		missed.a = 0.22
	var gap := 1.0 if side >= 4.0 else 0.0
	for row: int in _rows:
		var line := _grid[row]
		for col: int in line.length():
			var mark := line[col]
			if mark == " ":
				continue
			var box := Rect2(col * side, row * side, side - gap, side - gap)
			draw_rect(box, missed if mark == "." else reached)
	if layer_on("time"):
		_draw_time(side, gap)
	if layer_on("deaths"):
		_draw_marks("deaths", side, unit)
	if layer_on("stuck"):
		_draw_marks("stuck", side, unit)
	if _start.x >= 0 and _start.y >= 0 and _start.x < _cols and _start.y < _rows:
		var centre := (Vector2(_start) + Vector2(0.5, 0.5)) * side
		draw_arc(
			centre,
			maxf(side * 0.45, 3.0),
			0.0,
			TAU,
			24,
			UI_THEME.color(self, "text"),
			maxf(1.0, unit * 1.5),
			true
		)


func _draw_time(side: float, gap: float) -> void:
	var cells: Dictionary = _heat.get("time", {})
	var most := 0.0
	for value: Variant in cells.values():
		most = maxf(most, float(value))
	if most <= 0.0:
		return
	var ink := UI_THEME.color(self, "accent")
	for at: Vector2i in cells:
		# Square root: a few long waits don't wash out everywhere else.
		var share := sqrt(float(cells[at]) / most)
		ink.a = lerpf(TIME_ALPHA.x, TIME_ALPHA.y, share)
		draw_rect(Rect2(at.x * side, at.y * side, side - gap, side - gap), ink)


## Deaths: filled dots. Stuck: open rings. Bigger where it happened more often.
func _draw_marks(layer: String, side: float, unit: float) -> void:
	var cells: Dictionary = _heat.get(layer, {})
	var most := 1.0
	for value: Variant in cells.values():
		most = maxf(most, float(value))
	var smallest := maxf(side * 0.28, 2.5 * unit)
	var largest := maxf(side * 0.62, smallest + unit)
	var ink := UI_THEME.color(self, "danger" if layer == "deaths" else "text")
	var halo := UI_THEME.color(self, "surface")
	for at: Vector2i in cells:
		var centre := (Vector2(at) + Vector2(0.5, 0.5)) * side
		var radius := lerpf(smallest, largest, sqrt(float(cells[at]) / most))
		if layer == "deaths":
			# A thin halo keeps a dot apart from the wash under it.
			draw_circle(centre, radius + maxf(1.0, unit), halo, true, -1.0, true)
			draw_circle(centre, radius, ink, true, -1.0, true)
		else:
			var width := maxf(1.5, unit * 1.75)
			draw_arc(centre, radius, 0.0, TAU, 24, halo, width + maxf(1.0, unit) * 2.0, true)
			draw_arc(centre, radius, 0.0, TAU, 24, ink, width, true)
