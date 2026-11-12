@tool
class_name TerrainLayer
extends Resource
## One of a Terrain's up to four ground layers. Layer 0 covers everything; layers
## 1-3 go on top where the mask paints them (r, g, b) and, with `use_rule`, where
## the ground is within their height and slope bands (rock on steep slopes, snow
## up high). Textures are the project's own (Krita, Material Maker, a Blender
## bake); without one the layer is its colour.

@export var albedo: Texture2D
@export var color: Color = Color(0.42, 0.5, 0.37)
## Texture repeats per metre.
@export_range(0.01, 4.0, 0.01) var uv_scale: float = 0.25
@export_range(0.0, 1.0) var roughness: float = 0.9
## Layers 1-3: also cover ground inside these bands, not only where painted.
@export var use_rule: bool = false
## Lowest and highest height, in metres above the Terrain node.
@export var height_range: Vector2 = Vector2(-100000.0, 100000.0)
## Least and most slope, in degrees (0 flat, 90 a wall).
@export var slope_range: Vector2 = Vector2(0.0, 90.0)
