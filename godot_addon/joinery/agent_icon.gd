@tool
extends RefCounted
## Provider marks on one shared tile, loaded directly so the Project Manager needs no import
## cache. Every provider sits on the same JoineryIconTile (surface_raised, RADIUS.xs) with its
## mark scaled into an 18 × unit box, so the list reads as one column of equal tiles.

const UI_THEME := preload("ui_theme.gd")
## Mark files in icons/providers. Monochrome marks are drawn white and tinted with the text
## colour so they read in both themes; the others keep their own colours.
const FILES := {
	"claude": "claude.svg",
	"codex": "codex.png",
	"gemini": "gemini.png",
	"opencode": "opencode.svg",
}
const MONOCHROME := ["opencode"]
## Logical size of the mark inside its tile (the tile adds its own padding, 5 × unit a side).
const MARK := 18


## The mark for [param id] rendered at [param pixels] square, or null for an unknown id.
## [param directory] is the addon directory (the one holding icons/).
static func texture(id: String, pixels: int, directory: String) -> Texture2D:
	if not FILES.has(id):
		return null
	var path := directory.path_join("icons/providers").path_join(FILES[id])
	var rendered := Image.new()
	var error := OK
	if path.ends_with(".svg"):
		var svg := FileAccess.get_file_as_string(path)
		# Render the vector at the target size rather than scaling pixels up.
		error = rendered.load_svg_from_string(svg, maxf(pixels / 16.0, 0.1))
	else:
		error = rendered.load(path)
	if error != OK:
		return null
	if rendered.get_width() != pixels or rendered.get_height() != pixels:
		rendered.resize(pixels, pixels, Image.INTERPOLATE_LANCZOS)
	return ImageTexture.create_from_image(rendered)


## A provider tile for [param id]: the mark on a JoineryIconTile, re-rendered at the editor
## scale on every theme change. Unknown ids show the first letter of [param fallback] instead.
static func tile(id: String, fallback := "") -> PanelContainer:
	var result := PanelContainer.new()
	result.theme_type_variation = "JoineryIconTile"
	result.mouse_filter = Control.MOUSE_FILTER_IGNORE
	result.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	result.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	if not FILES.has(id):
		var letter := Label.new()
		letter.text = (fallback if not fallback.is_empty() else id).left(1).to_upper()
		letter.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
		letter.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
		result.add_child(letter)
		result.theme_changed.connect(
			func() -> void:
				var side := roundf(MARK * UI_THEME.unit(result))
				letter.custom_minimum_size = Vector2(side, side)
		)
		return result
	var mark := TextureRect.new()
	mark.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	mark.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
	mark.mouse_filter = Control.MOUSE_FILTER_IGNORE
	result.add_child(mark)
	var script: Resource = UI_THEME
	var directory := script.resource_path.get_base_dir()
	var apply := func() -> void:
		var side := roundi(MARK * UI_THEME.unit(result))
		mark.custom_minimum_size = Vector2(side, side)
		mark.texture = texture(id, side, directory)
		mark.self_modulate = (UI_THEME.color(result, "text") if id in MONOCHROME else Color.WHITE)
	result.theme_changed.connect(apply)
	if result.is_inside_tree():
		apply.call()
	return result
