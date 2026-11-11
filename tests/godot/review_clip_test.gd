extends SceneTree
## An animation in review (review_media.gd with review_clip.gd): Blender's before and after
## strips play as two clips side by side in sync, Before and After dim the other clip, a new
## action shows alone marked New, the filmstrip tile is a frame of the clip, and the facts line
## says "Run: 0.8 s loop, bounce +30%". The strips are made here, frame by frame, as PNGs.

const MEDIA := preload("res://addons/joinery/review_media.gd")
const CLIP := preload("res://addons/joinery/review_clip.gd")

var _failed := false


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


## A strip of [param frames] square frames, each a bar that rises with the frame, saved as PNG.
static func strip(path: String, frames: int, tone: Color, side := 48) -> String:
	var image := Image.create(side * frames, side, false, Image.FORMAT_RGBA8)
	image.fill(Color("1f1f1f"))
	for index: int in frames:
		var height := int(side * (0.3 + 0.6 * index / maxf(frames - 1, 1)))
		image.fill_rect(Rect2i(index * side + side / 3, side - height, side / 3, height), tone)
	image.save_png(path)
	return path


static func clip_item(folder: String, new := false) -> Dictionary:
	var item := {
		"path": "art/sources/hero.blend",
		"media": "clip",
		"part": "res://art/models/hero.glb#object=HeroRig&action=run",
		"label": "run",
		"after": strip(folder.path_join("run-after.png"), 8, Color("e0a040")),
		"after_clip": {"frames": 8, "fps": 10.0, "seconds": 0.8, "loop": true, "bounce": 0.13},
	}
	if not new:
		item["before"] = strip(folder.path_join("run-before.png"), 10, Color("7090c0"))
		item["before_clip"] = {
			"frames": 10, "fps": 10.0, "seconds": 0.8, "loop": true, "bounce": 0.1
		}
	return item


func _run() -> void:
	root.size = Vector2i(900, 600)
	var folder := OS.get_user_data_dir().path_join("review-clip-test")
	DirAccess.make_dir_recursive_absolute(folder)
	_check_facts()
	var media: MEDIA = MEDIA.new()
	root.add_child(media)
	media.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	var item := clip_item(folder)
	media.show_media([item])
	await process_frame
	var clip: CLIP = media._clip
	_check(media.current_kind() == "clip", "A clip item: " + media.current_kind())
	_check(clip.visible, "The clips show")
	_check(clip.side_at(0).visible and clip.side_at(1).visible, "Before and After side by side")
	_check(clip.side_at(0).caption.text == "Before", clip.side_at(0).caption.text)
	_check(clip.side_at(1).caption.text == "After", clip.side_at(1).caption.text)
	_check(clip.side_at(0).frames == 10 and clip.side_at(1).frames == 8, "Frames per strip")
	_check(media.side == "compare", "Both alike to start: " + media.side)
	_check(
		media.frame_ground().to_html(false) == "1f1f1f",
		"The frame takes the clips' background: %s" % media.frame_ground().to_html(false)
	)
	await _check_fills(media, clip)
	clip.clock = 0.0  # the frames the sizing waited for played the clips on
	clip._process(0.35)
	_check(clip.frames_shown() == [3, 3], "In sync at 0.35 s: %s" % [clip.frames_shown()])
	clip._process(0.5)
	_check(clip.frames_shown() == [8, 0], "Each loops its own strip: %s" % [clip.frames_shown()])
	var region := (clip.side_at(1).picture.texture as AtlasTexture).region
	_check(region == Rect2(0, 0, 48, 48), "After shows its first frame again: %s" % region)
	media.set_side("before")
	_check(clip.side_at(1).picture.modulate.a < 0.5, "Before dims the change")
	_check(clip.side_at(0).picture.modulate.a == 1.0, "and keeps the original")
	media.set_side("after")
	_check(clip.side_at(0).picture.modulate.a < 0.5, "After dims the original")
	_check(clip.visible and clip.side_at(0).visible, "Neither clip is hidden")
	var facts := media.measured_facts()
	_check(facts == ["Run: 0.8 s loop, bounce +30%"], "Facts: %s" % [facts])
	var tile := media.thumbnail() as AtlasTexture
	_check(tile != null and tile.region == Rect2(4 * 48, 0, 48, 48), "Tile is the middle frame")
	# A new action: After alone, marked New.
	media.show_media([clip_item(folder, true)])
	await process_frame
	_check(not clip.side_at(0).visible and clip.side_at(1).visible, "Only the new clip")
	_check(clip.side_at(1).caption.text == "New", clip.side_at(1).caption.text)
	_check(media.current_is_new(), "Reads as new")
	facts = media.measured_facts()
	_check(facts == ["Run: new, 0.8 s loop"], "New facts: %s" % [facts])
	# Rendering: waits for the strips, then plays them.
	var pending := clip_item(folder)
	pending["after"] = folder.path_join("not-yet.png")
	DirAccess.remove_absolute(pending["after"])
	media.show_media([pending])
	await process_frame
	_check(media.is_loading() and not clip.is_visible_in_tree(), "Waits for the render")
	strip(pending["after"], 8, Color("e0a040"))
	media._waited = 0.0
	media._process(1.0)
	_check(not media.is_loading() and clip.is_visible_in_tree(), "Plays once rendered")
	# Other kinds hide the clips.
	media.show_media([{"path": "scripts/a.gd", "media": "text", "diff": "+a\n-b"}])
	_check(not clip.visible, "Text hides the clips")
	media.queue_free()
	await process_frame
	print("review clip: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)


## The clips fill the review frame like a picture: as tall as the frame when it is wide (the
## short bottom-panel layout), sharing its width when it is narrow; captions sit over them.
func _check_fills(media: MEDIA, clip: CLIP) -> void:
	for spec: Array in [[Vector2(900, 260), "short"], [Vector2(300, 600), "narrow"]]:
		media.set_anchors_preset(Control.PRESET_TOP_LEFT)
		media.size = spec[0]
		await process_frame
		await process_frame
		var frame := clip.size
		var gap := float(clip.get_theme_constant("separation"))
		var total := 0.0
		for index: int in 2:
			var side := clip.side_at(index)
			var shown := side.drawn()
			total += shown.size.x
			_check(is_equal_approx(shown.size.x, shown.size.y), "%s: square frames" % spec[1])
			_check(
				Rect2(Vector2.ZERO, side.size).encloses(Rect2(side.tag.position, side.tag.size)),
				"%s: the caption sits on its clip" % spec[1]
			)
			_check(
				side.tag.position.y + side.tag.size.y <= shown.end.y,
				"%s: the caption is over the picture, not below it" % spec[1]
			)
			if spec[1] == "short":
				_check(
					absf(shown.size.y - frame.y) <= 1.0,
					"short: a clip is as tall as the frame (%s of %s)" % [shown.size.y, frame.y]
				)
		_check(total + gap <= frame.x + 1.0, "%s: both clips fit across" % spec[1])
		if spec[1] == "narrow":
			_check(total + gap >= frame.x - 2.0, "narrow: the clips share the full width")
	media.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	await process_frame


func _check_facts() -> void:
	var before := {"seconds": 1.0, "loop": true, "bounce": 0.1}
	var after := {"seconds": 0.8, "loop": true, "bounce": 0.102}
	var line := CLIP.clip_fact("jump_air", before, after)
	_check(line == "Jump Air: 0.8 s loop (was 1.0 s)", line)
	line = CLIP.clip_fact("land", {"seconds": 0.4}, {"seconds": 0.41})
	_check(line == "Land: 0.4 s", line)
	line = CLIP.clip_fact("wave", {"seconds": 1.2}, {})
	_check(line == "Wave: removed", line)
