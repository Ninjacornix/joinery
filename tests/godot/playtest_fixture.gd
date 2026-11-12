extends RefCounted
## A seeded minute of the top-down blueprint as the playtest report has it: its coverage
## ("#" reached, "." never reached, " " not floor), for the visual suite.

const GRID := [
	"                                ",
	"  ########....................  ",
	" ################.............  ",
	"  .##...........#.............  ",
	"  ..##..........#.............  ",
	"  ...##..  .....#....  .......  ",
	"  ....##.  .....#....  .......  ",
	"  .....##.......#.............  ",
	" #########..##########........  ",
	"  ..###....###################  ",
	"  ..#####.......#.##........#.  ",
	"  ########..#######........##.  ",
	"  ...##..  .....#....  ...##..  ",
	"  ..##...  .....#....  ####...  ",
	"  ..###############....####...  ",
	"  ..............#.#....##.##..  ",
	"  ..............#.############  ",
	"  ..............#.............  ",
	"                                ",
]


static func coverage() -> Dictionary:
	return {"percent": 34.0, "visited": 157, "navigable": 462, "start": [16, 9], "grid": GRID}


## The last five runs' heat on that grid (joinery playtest --heatmap --json): deaths by the
## spikes in the east room, a stuck point in the doorway, most time in the corridor.
static func heat() -> Dictionary:
	var time: Array = []
	for row: int in GRID.size():
		var line: String = GRID[row]
		for col: int in line.length():
			if line[col] == "#":
				# Longest along the corridor (row 9), then in the rooms off it.
				time.append([col, row, 40.0 if row == 9 else 6.0 + (col * 7 + row * 3) % 11])
	return {
		"runs": 5,
		"grid": GRID,
		"deaths": [[26, 13, 4], [27, 12, 2], [5, 8, 1]],
		"stuck": [[16, 5, 2], [11, 11, 1]],
		"time": time,
	}
