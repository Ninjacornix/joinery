@tool
extends RefCounted
## Teammates in the Review tab (ADR 0071, joinery/team.py).
##
## When a pull finds a file both you and a teammate changed, your version stays and theirs waits
## here as an ordinary change: Accept reads "Use Ana's" and Discard "Keep mine", and opening the
## row shows both versions side by side (the picture of an image, a Krita painting or a Blender
## file's saved preview; sizes for a sound; a note for text). The game brief a teammate changed
## waits here too, with Approve; the art bible's row says who changed it and opens /art.

const UI := preload("ui_components.gd")
const UI_MOTION := preload("ui_motion.gd")
const COMMAND := preload("command_job.gd")
const BIBLE := "art/bible.md"
## The tallest a version's picture is drawn, in base units.
const PICTURE_UNITS := 40.0


## "Ana" for "Ana Lima"; "Your teammate" when the name is missing.
static func first_name(name: String) -> String:
	var parts := name.strip_edges().split(" ", false)
	return parts[0] if not parts.is_empty() else "Your teammate"


## The teammate facts of a `transactions list` row ({} for an ordinary change).
static func facts(entry: Dictionary) -> Dictionary:
	var team: Variant = entry.get("team", {})
	return team if team is Dictionary else {}


## Name the row's buttons after the choice they make for a teammate's version.
static func label_decision(entry: Dictionary, accept: Button, discard: Button) -> void:
	var team := facts(entry)
	if team.is_empty():
		return
	var who := first_name(str(team.get("theirs", "")))
	accept.text = "Use %s's" % who
	accept.tooltip_text = "Take %s's version. Yours stays in the Timeline." % who
	accept.accessibility_name = "Use %s's version" % who
	discard.text = "Keep mine"
	discard.tooltip_text = "Keep your version. %s's stays in the Timeline." % who
	discard.accessibility_name = "Keep your version"


## Both versions of each file under an opened row, or null for an ordinary change.
static func conflict_view(entry: Dictionary) -> Control:
	var team := facts(entry)
	if team.is_empty():
		return null
	var who := first_name(str(team.get("theirs", "")))
	var box := VBoxContainer.new()
	box.name = "Versions"
	box.theme_type_variation = "JoineryTight"
	for file: Variant in team.get("files", []):
		if file is Dictionary:
			box.add_child(_file_view(file, who))
	return box


static func _file_view(file: Dictionary, who: String) -> Control:
	var box := VBoxContainer.new()
	box.theme_type_variation = "JoineryFlush"
	var path := str(file.get("path", ""))
	box.add_child(UI.label(path, "JoineryCaption"))
	var mine := str(file.get("mine_picture", ""))
	var theirs := str(file.get("theirs_picture", ""))
	if not mine.is_empty() or not theirs.is_empty():
		var pictures := HBoxContainer.new()
		pictures.name = "Pictures"
		pictures.theme_type_variation = "JoineryRow"
		pictures.add_child(_side(mine, "Yours"))
		pictures.add_child(_side(theirs, "%s's" % who))
		box.add_child(pictures)
	elif str(file.get("kind", "")) == "text":
		box.add_child(
			UI.label(
				"You both changed the same lines. Use %s's takes %s lines there." % [who, who],
				"JoineryFaint",
				true
			)
		)
	else:
		var sizes := (
			"Yours %s · %s's %s"
			% [
				size_text(int(file.get("mine_size", -1))),
				who,
				size_text(int(file.get("theirs_size", -1))),
			]
		)
		box.add_child(UI.label(sizes, "JoineryNumeric"))
	return box


static func _side(path: String, caption: String) -> VBoxContainer:
	var column := VBoxContainer.new()
	column.name = caption
	column.theme_type_variation = "JoineryFlush"
	var picture := TextureRect.new()
	picture.name = "Picture"
	picture.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	picture.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
	picture.texture = _texture(path)
	var side := PICTURE_UNITS * 4.0
	picture.custom_minimum_size = Vector2(side, side)
	picture.accessibility_name = caption
	column.add_child(picture)
	var words := UI.label(caption if picture.texture != null else caption + ": no picture")
	words.theme_type_variation = "JoineryCaption"
	words.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	column.add_child(words)
	return column


static func _texture(path: String) -> Texture2D:
	if path.is_empty() or not FileAccess.file_exists(path):
		return null
	var image := Image.load_from_file(path)
	return ImageTexture.create_from_image(image) if image != null else null


## "1.2 MB", "340 KB"; "deleted" for a side that has no file.
static func size_text(count: int) -> String:
	if count < 0:
		return "deleted"
	if count >= 1_000_000:
		return "%.1f MB" % (count / 1_000_000.0)
	return "%d KB" % maxi(1, roundi(count / 1000.0))


## The shared documents other than the art bible a teammate changed (the bible has its row).
static func documents(listing: Dictionary) -> Array[Dictionary]:
	var found: Array[Dictionary] = []
	for item: Variant in listing.get("team_documents", []):
		if item is Dictionary and str(item.get("path", "")) != BIBLE:
			found.append(item)
	return found


## What the art bible row says while it waits: who changed it, when a teammate did.
static func bible_waiting_text(bible: Dictionary) -> String:
	var who := str(bible.get("by_teammate", ""))
	if who.is_empty():
		return "Needs your approval before new art or audio"
	return "%s changed it. Approve before new art or audio" % first_name(who)


## One row per document a teammate changed: what changed, Open, and Approve. Approve runs
## `joinery team approve` when [param cli] is true, then [param done] (the tab's refresh).
static func document_rows(listing: Dictionary, cli: bool, done: Callable) -> Array[Control]:
	var rows: Array[Control] = []
	for item: Dictionary in documents(listing):
		rows.append(_document_row(item, cli, done))
	return rows


static func _document_row(item: Dictionary, cli: bool, done: Callable) -> Control:
	var path := str(item.get("path", ""))
	var label := str(item.get("label", path))
	var who := first_name(str(item.get("author", "")))
	var row := HBoxContainer.new()
	row.name = "Document"
	row.theme_type_variation = "JoineryRow"
	row.set_meta("path", path)
	UI.bind_height(row, "md", false)
	var dot := UI.status_dot("attention")
	dot.tooltip_text = "Waiting for your approval"
	row.add_child(dot)
	var words := UI.label("%s changed the %s" % [who, label])
	words.name = "Summary"
	words.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	words.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	row.add_child(words)
	var open := UI.quiet("Open")
	open.name = "Open"
	open.tooltip_text = "Read %s" % path
	open.accessibility_name = "Open the " + label
	open.pressed.connect(
		func() -> void: OS.shell_open(ProjectSettings.globalize_path("res://" + path))
	)
	row.add_child(open)
	var approve := UI.primary("Approve")
	approve.name = "Approve"
	approve.tooltip_text = "You read %s's change. Asset work goes on." % who
	approve.accessibility_name = "Approve the " + label
	UI_MOTION.press_feedback(approve)
	approve.pressed.connect(_approve.bind(row, approve, path, cli, done))
	row.add_child(approve)
	return row


static func _approve(row: Control, button: Button, path: String, cli: bool, done: Callable) -> void:
	button.disabled = true
	button.text = "Approving…"
	if not cli:
		return
	var project := ProjectSettings.globalize_path("res://").rstrip("/")
	var job := COMMAND.joinery(
		PackedStringArray(), ["team", "approve", "--project", project, "--path", path, "--json"]
	)
	if job == null:
		button.disabled = false
		button.text = "Approve"
		return
	var timer := Timer.new()
	timer.wait_time = 0.1
	row.add_child(timer)
	timer.timeout.connect(
		func() -> void:
			if COMMAND.finish(job, func(_result: Dictionary) -> void: done.call()) == null:
				timer.queue_free()
	)
	timer.start()
