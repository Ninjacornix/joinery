@tool
extends RefCounted
## Existing provider marks, loaded directly so the Project Manager needs no import cache.


static func texture(id: String, pixels: int, directory: String) -> Texture2D:
	var files := {
		"claude": "claude.svg", "codex": "codex.png",
		"gemini": "gemini.png", "opencode": "opencode.svg",
	}
	if not files.has(id):
		return null
	var path := directory.path_join("icons/providers").path_join(files[id])
	var rendered := Image.new()
	var error := rendered.load_svg_from_string(FileAccess.get_file_as_string(path)) if (
		path.ends_with(".svg")
	) else rendered.load(path)
	if error != OK:
		return null
	rendered.resize(pixels, pixels, Image.INTERPOLATE_LANCZOS)
	return ImageTexture.create_from_image(rendered)
