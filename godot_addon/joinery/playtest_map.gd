@tool
extends Control
## The playtest's coverage map, top-down: the level's floor as the bot mapped it, the parts
## the player stood on in a quiet tone and the parts nobody reached in the danger colour, with
## a small ring where the run started. Drawn from the report's grid in the theme's colours, so
## it reads in light and dark; the same map is saved as a picture for agents (coverage.png).

const UI_THEME := preload("ui_theme.gd")
## Widest the map draws, in theme units, and tallest.
const WIDTH_UNITS := 240.0
const HEIGHT_UNITS := 96.0
## How strong the unreached cells are: marked, without drowning the map in alarm.
const MISSED_ALPHA := 0.72

var _grid: PackedStringArray = PackedStringArray()
var _cols := 0
var _rows := 0
var _start := Vector2i(-1, -1)


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


func _draw() -> void:
	if _cols == 0 or _rows == 0:
		return
	var unit := UI_THEME.unit(self)
	var side := minf(
		minf(size.x, WIDTH_UNITS * unit) / _cols, minf(size.y, HEIGHT_UNITS * unit) / _rows
	)
	if side <= 0.0:
		return
	var reached := UI_THEME.color(self, "text_muted")
	reached.a = 0.32
	var missed := UI_THEME.color(self, "danger")
	missed.a = MISSED_ALPHA
	var gap := 1.0 if side >= 4.0 else 0.0
	for row: int in _rows:
		var line := _grid[row]
		for col: int in line.length():
			var mark := line[col]
			if mark == " ":
				continue
			var box := Rect2(col * side, row * side, side - gap, side - gap)
			draw_rect(box, missed if mark == "." else reached)
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
