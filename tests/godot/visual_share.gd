extends RefCounted
## Stand-ins for the Share tab's Steam art in the visual suite: the layout of the set, not
## Krita's output (the creative worker isn't part of the suite).

const SIZES := {
	"header": ["Header capsule", 920, 430],
	"small": ["Small capsule", 462, 174],
	"main": ["Main capsule", 1232, 706],
	"vertical": ["Vertical capsule", 748, 896],
	"library_capsule": ["Library capsule", 600, 900],
	"library_hero": ["Library hero", 3840, 1240],
	"library_logo": ["Library logo", 1280, 360],
}
const SOFT := (
	"The screenshot is enlarged 3.0×, so it looks soft; " + "larger screenshots would be sharper."
)


## `joinery share status`'s "steam" entry, its pictures cut from [param picture] (index -> Image).
static func steam_status(cache: String, picture: Callable) -> Dictionary:
	var images: Array = []
	for id: String in SIZES:
		var spec: Array = SIZES[id]
		var art: Image = picture.call(images.size() % 5)
		if id == "library_logo":
			art = Image.create(320, 90, false, Image.FORMAT_RGBA8)
			art.fill_rect(Rect2i(30, 30, 260, 30), Color("f2efe8"))
		else:
			art.resize(int(spec[1]) / 4, int(spec[2]) / 4)
		var path := cache.path_join("steam-%s.png" % id)
		art.save_png(path)
		var hero := id == "library_hero"
		(
			images
			. append(
				{
					"id": id,
					"label": spec[0],
					"size": [spec[1], spec[2]],
					"path": path,
					"status": "warning" if hero else "ok",
					"problems": [SOFT] if hero else [],
				}
			)
		)
	return {"ready": true, "checked": "2026-11-12", "images": images}
