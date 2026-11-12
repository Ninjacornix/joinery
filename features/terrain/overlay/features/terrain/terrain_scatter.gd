@tool
class_name TerrainScatter
extends Resource
## Models scattered over a Terrain: grass, rocks, trees from the project's kits.
## Placement is seeded, so the same settings place the same instances. Instances
## are drawn with MultiMeshes, one per chunk, and have no collision: for props the
## player must bump into, place them as scenes (Joinery's scatter tool) instead.

## The model: a scene (a GLB or .tscn); every mesh in it is drawn. Or set `mesh`.
@export var scene: PackedScene
@export var mesh: Mesh
## Instances per 100 m² where every rule allows them.
@export_range(0.0, 400.0, 0.1) var density: float = 4.0
@export var placement_seed: int = 1
## Mask channel that allows them: -1 everywhere, 0-3 = r, g, b, a.
@export_range(-1, 3) var mask_channel: int = 3
## The mask value from which instances appear.
@export_range(0.0, 1.0) var mask_threshold: float = 0.5
@export var height_range: Vector2 = Vector2(-100000.0, 100000.0)
## Steepest ground, in degrees.
@export_range(0.0, 90.0) var max_slope: float = 35.0
@export var scale_range: Vector2 = Vector2(0.8, 1.2)
@export var random_turn: bool = true
## Tilt each instance to the ground instead of standing upright.
@export var align_to_ground: bool = false
## Metres pushed into the ground (roots, rocks half buried).
@export var sink: float = 0.0
## Hidden beyond this many metres from the camera; 0 keeps them with their chunk.
@export var visible_distance: float = 0.0
