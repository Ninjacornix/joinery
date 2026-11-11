@tool
extends RefCounted
## The art direction, read as a document before it is approved. Top to bottom: what to fix
## first (one-line offers from the bible checks, each filling the message box), an "Update to
## the new layout" offer for a first-format bible, the five sections people decide (Feel,
## Look, Palette, Sound, Never), the references board (images from art/reference with their
## captions), and the technical rules folded under "Technical rules (preset for you)". The
## footer says what approving does, with Close and "Approve these rules" on its trailing edge.
##
## [method open_document] shows any other project document (the game brief) the same way,
## read-only, with "Ask Journeyman to change it": edits are reviewed changes like any other.

const UI_THEME := preload("ui_theme.gd")
const UI := preload("ui_components.gd")
const STYLE := preload("sidebar_style.gd")
const TITLE := "Art direction"
const APPROVE := "Approve these rules"
const CONSEQUENCE := (
	"Approving lets Journeyman start making art and sound that follows these rules. "
	+ "If the rules change, you'll be asked again."
)
## The sections people decide, and the bible headings that feed each one (lower case, without
## their numbers). Every other section is a technical rule.
const SECTIONS := [
	["Feel", ["vision", "pillars"]],
	["Look", ["visual style", "shape language"]],
	["Palette", ["palette"]],
	["Sound", ["audio", "sound"]],
	["Never", ["never"]],
]
## Bible checks that the dialog shows another way (the upgrade offer, the board).
const OWN_OFFERS := ["This bible uses the first format", "There is no art bible yet"]
## At most this many offers; the rest wait until these are done.
const MAX_OFFERS := 3
## Board thumbnails, in multiples of the font size.
const THUMB_EM := 7.0
const IMAGE_SUFFIXES := ["png", "jpg", "jpeg", "webp", "bmp", "tga", "svg"]


## Open the dialog on [param parent] at [param size]. [param content] is the art review event
## ({text, sha256, approved, version, references, suggestions}) or just the bible's text.
## [param on_approve] runs when the user approves. When [param parent] has
## fill_composer(text) and upgrade_bible(), the offers use them.
static func open(
	parent: Control, content: Variant, size: Vector2i, on_approve: Callable
) -> ConfirmationDialog:
	var dialog := ConfirmationDialog.new()
	dialog.title = TITLE
	dialog.ok_button_text = APPROVE
	dialog.cancel_button_text = "Close"
	dialog.theme = UI_THEME.build(parent)
	dialog.get_ok_button().theme_type_variation = "JoineryPrimaryButton"
	dialog.get_cancel_button().theme_type_variation = "JoinerySecondaryButton"
	var page := _page(dialog)
	var event := _event(parent, content)
	_fill(dialog, page, parent, event)
	if not content is Dictionary and not event.has("version"):
		# Given only the text, the bridge's full event may reach the sidebar a moment later
		# (the same signal, its next listener): rebuild from it then.
		_refill.call_deferred(dialog, page, parent, str(content))
	parent.add_child(dialog)
	_paint(dialog)
	_trail_buttons(dialog)
	dialog.confirmed.connect(on_approve)
	dialog.confirmed.connect(dialog.queue_free)
	dialog.canceled.connect(dialog.queue_free)
	dialog.popup_centered(size)
	# The dialog focuses Approve on popup, which draws its focus ring before the user has
	# done anything. Let it go; Tab still reaches the buttons.
	dialog.get_ok_button().release_focus.call_deferred()
	return dialog


static func _refill(
	dialog: ConfirmationDialog, page: VBoxContainer, parent: Control, text: String
) -> void:
	if not is_instance_valid(dialog) or not is_instance_valid(parent):
		return
	var event := _event(parent, text)
	if not event.has("version"):
		return
	for child: Node in page.get_children():
		page.remove_child(child)
		child.queue_free()
	_fill(dialog, page, parent, event)


static func _fill(
	dialog: ConfirmationDialog, page: VBoxContainer, parent: Control, event: Dictionary
) -> void:
	var approved := bool(event.get("approved", false))
	dialog.ok_button_text = "Approved" if approved else APPROVE
	dialog.get_ok_button().disabled = approved
	dialog.get_ok_button().tooltip_text = (
		"This version of the rules is already approved." if approved else ""
	)
	var head := VBoxContainer.new()
	head.theme_type_variation = "JoineryTight"
	page.add_child(head)
	head.add_child(UI.label(TITLE, "JoineryTitle"))
	var state := "Approved" if approved else "Draft: not approved yet"
	head.add_child(UI.label(state, "JoineryCaption"))
	var text := str(event.get("text", ""))
	if int(event.get("version", 2)) == 1:
		page.add_child(_upgrade_offer(parent, dialog))
	var offers := _offers(parent, dialog, event.get("suggestions", []))
	if offers != null:
		page.add_child(offers)
	var parts := sections(text)
	page.add_child(_decided(parts))
	var references: Array = event.get("references", [])
	page.add_child(_board(references))
	page.add_child(_technical(parts))
	var foot := VBoxContainer.new()
	foot.theme_type_variation = "JoineryTight"
	page.add_child(foot)
	foot.add_child(UI.label(CONSEQUENCE, "JoineryCaption", true))
	if parent.has_method("fill_composer"):
		var ask := UI.link("Ask Journeyman to change it")
		ask.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
		STYLE.hug_left(ask)
		ask.pressed.connect(
			func() -> void:
				parent.call("fill_composer", "Change the art direction: ")
				dialog.hide()
				dialog.queue_free()
		)
		foot.add_child(ask)
	if approved and parent.has_method("run_command"):
		var tidy := UI.link("Make the project match these rules")
		tidy.tooltip_text = "Fix what drifts, as reviewed changes (/match)"
		tidy.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
		STYLE.hug_left(tidy)
		tidy.pressed.connect(
			func() -> void:
				parent.call("run_command", "/match")
				dialog.hide()
				dialog.queue_free()
		)
		foot.add_child(tidy)


## A document view's size: 46 by 40 font sizes, at most 90% of [param parent]'s window.
static func document_size(parent: Control) -> Vector2i:
	var em := float(parent.get_theme_default_font_size())
	var window := parent.get_window().size if parent.is_inside_tree() else Vector2i(1280, 800)
	return Vector2i(
		mini(roundi(46 * em), roundi(window.x * 0.9)), mini(roundi(40 * em), roundi(window.y * 0.9))
	)


## A project document ([param text], Markdown) read-only under [param title], with Close and,
## when [param parent] has fill_composer(), "Ask Journeyman to change it", which starts a
## message with [param ask].
static func open_document(
	parent: Control, title: String, text: String, size: Vector2i, ask := ""
) -> AcceptDialog:
	var dialog := AcceptDialog.new()
	dialog.title = title
	dialog.ok_button_text = "Close"
	dialog.theme = UI_THEME.build(parent)
	dialog.get_ok_button().theme_type_variation = "JoinerySecondaryButton"
	var page := _page(dialog)
	var head := VBoxContainer.new()
	head.theme_type_variation = "JoineryTight"
	page.add_child(head)
	head.add_child(UI.label(title, "JoineryTitle"))
	(
		head
		. add_child(
			(
				UI
				. label(
					"Journeyman reads this before every request. Changes to it are reviewed like any other.",
					"JoineryCaption",
					true
				)
			)
		)
	)
	var body := _rich(to_bbcode(text))
	body.name = "Document"
	page.add_child(body)
	if not ask.is_empty() and parent.has_method("fill_composer"):
		var button := dialog.add_button("Ask Journeyman to change it", true, "ask")
		button.theme_type_variation = "JoineryPrimaryButton"
		dialog.custom_action.connect(
			func(action: StringName) -> void:
				if action == &"ask":
					parent.call("fill_composer", ask)
					dialog.hide()
					dialog.queue_free()
		)
	parent.add_child(dialog)
	_paint(dialog)
	_trail_buttons(dialog)
	dialog.confirmed.connect(dialog.queue_free)
	dialog.canceled.connect(dialog.queue_free)
	dialog.popup_centered(size)
	dialog.get_ok_button().release_focus.call_deferred()
	return dialog


## The bible's level-2 sections as [[heading, body]], headings without their numbers
## ("7. Palette (checked)" -> "Palette (checked)"). The title line is left out.
static func sections(markdown: String) -> Array:
	var found: Array = []
	var numbered := RegEx.create_from_string("^##\\s+(?:\\d+[a-z]?\\.\\s*)?(.*)$")
	for raw: String in markdown.replace("\r\n", "\n").split("\n"):
		var heading := numbered.search(raw)
		if heading != null and not raw.begins_with("###"):
			found.append([heading.get_string(1).strip_edges(), ""])
		elif not found.is_empty():
			found[-1][1] += raw + "\n"
	return found


## A section's own text, without guidance quotes, comments, unfilled placeholders or the
## reference table. "" when nothing is written yet.
static func clean(body: String) -> String:
	var lines := PackedStringArray()
	var comment := false
	for raw: String in body.split("\n"):
		var line := raw.strip_edges()
		if line.begins_with("<!--"):
			comment = not line.ends_with("-->")
			continue
		if comment:
			comment = not line.ends_with("-->")
			continue
		if line.begins_with(">") or line.begins_with("|") or line.contains("{{"):
			continue
		lines.append(raw)
	return "\n".join(lines).strip_edges()


## The decided sections' text, by name ("Feel" -> its words).
static func decided(parts: Array) -> Dictionary:
	var result := {}
	for spec: Array in SECTIONS:
		var words := PackedStringArray()
		for part: Array in parts:
			if _belongs(str(part[0]), spec[1]):
				var text := clean(str(part[1]))
				if not text.is_empty():
					words.append(text)
			elif str(spec[0]) == "Never" and str(part[0]).to_lower() == "references":
				var avoid := _subsection(str(part[1]), "never look like")
				if not avoid.is_empty():
					words.append(avoid)
		result[spec[0]] = "\n".join(words)
	return result


## A small Markdown subset as BBCode: headings become bold lines (a leading top heading is
## dropped, the dialog already names the document), list markers become bullets and
## **bold** stays bold. HTML comments are notes for writers and are left out. Everything
## else is plain text, with "[" escaped.
static func to_bbcode(markdown: String) -> String:
	var lines := PackedStringArray()
	var heading := RegEx.create_from_string("^#{1,6}\\s+(.*)$")
	var bullet := RegEx.create_from_string("^\\s*[-*+]\\s+(.*)$")
	var bold := RegEx.create_from_string("\\*\\*(.+?)\\*\\*")
	var first := true
	for raw: String in _without_comments(markdown.replace("\r\n", "\n")).split("\n"):
		var line := raw.replace("[", "[lb]")
		var found := heading.search(line)
		if found != null:
			if first and raw.begins_with("# "):
				first = false
				continue
			line = "[b]%s[/b]" % found.get_string(1)
		else:
			var item := bullet.search(line)
			if item != null:
				line = "•  " + item.get_string(1)
		if not raw.strip_edges().is_empty():
			first = false
		lines.append(bold.sub(line, "[b]$1[/b]", true))
	while not lines.is_empty() and lines[0].strip_edges().is_empty():
		lines.remove_at(0)
	return "\n".join(lines)


## [param markdown] without its "<!-- ... -->" comments. A line that held only a comment
## is dropped; an unclosed comment runs to the end.
static func _without_comments(markdown: String) -> String:
	var out := PackedStringArray()
	var inside := false
	for raw: String in markdown.split("\n"):
		var kept := ""
		var rest := raw
		var had := inside
		while true:
			if inside:
				var close := rest.find("-->")
				if close < 0:
					rest = ""
					break
				rest = rest.substr(close + 3)
				inside = false
			var open := rest.find("<!--")
			if open < 0:
				kept += rest
				break
			had = true
			kept += rest.left(open)
			rest = rest.substr(open + 4)
			inside = true
		if had and kept.strip_edges().is_empty():
			continue
		out.append(kept)
	return "\n".join(out)


## A lint suggestion as one short line for people: Journeyman's own hints ("Ask about it.")
## are dropped.
static func offer_text(suggestion: String) -> String:
	var line := suggestion.strip_edges()
	for tail: String in [" Ask about it.", " Ask about them."]:
		line = line.trim_suffix(tail)
	return line


static func _event(parent: Control, content: Variant) -> Dictionary:
	if content is Dictionary:
		return content
	var text := str(content)
	var known: Variant = parent.get("art_review_event") if parent != null else null
	if known is Dictionary and str(known.get("text", "")) == text:
		return known
	return {"text": text}


## A scrolling column inside the dialog, SPACE.5 between its groups, kept clear of the
## scrollbar.
static func _page(dialog: AcceptDialog) -> VBoxContainer:
	var scroll := ScrollContainer.new()
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	dialog.add_child(scroll)
	var gutter := MarginContainer.new()
	gutter.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	STYLE.on_theme_overrides(
		gutter,
		func() -> void:
			gutter.add_theme_constant_override("margin_right", UI_THEME.space(gutter, 4))
	)
	scroll.add_child(gutter)
	var page := VBoxContainer.new()
	page.theme_type_variation = "JoinerySections"
	page.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	gutter.add_child(page)
	return page


static func _decided(parts: Array) -> Control:
	var grid := GridContainer.new()
	grid.name = "Rules"
	grid.columns = 2
	STYLE.on_theme_overrides(
		grid,
		func() -> void:
			grid.add_theme_constant_override("h_separation", UI_THEME.space(grid, 4))
			grid.add_theme_constant_override("v_separation", UI_THEME.space(grid, 3))
	)
	var words := decided(parts)
	for spec: Array in SECTIONS:
		var name := str(spec[0])
		# Rich text like its value beside it, so the two share a baseline.
		var title := _rich(name)
		title.name = name + "Title"
		title.size_flags_horizontal = Control.SIZE_FILL
		title.size_flags_vertical = Control.SIZE_SHRINK_BEGIN
		STYLE.on_theme(
			title,
			func() -> void: title.custom_minimum_size.x = title.get_theme_default_font_size() * 5.0
		)
		grid.add_child(title)
		var text := str(words.get(name, ""))
		var body := _rich(to_bbcode(text) if not text.is_empty() else "Not set yet")
		body.name = name
		if text.is_empty():
			body.theme_type_variation = "JoineryFaintRichText"
		grid.add_child(body)
	return grid


static func _board(references: Array) -> Control:
	var group := VBoxContainer.new()
	group.name = "References"
	group.theme_type_variation = "JoineryStack"
	var head := VBoxContainer.new()
	head.theme_type_variation = "JoineryTight"
	group.add_child(head)
	head.add_child(UI.label("References"))
	if references.is_empty():
		(
			head
			. add_child(
				(
					UI
					. label(
						"No references yet. Add your own photos, sketches or Krita boards to art/reference.",
						"JoineryCaption",
						true
					)
				)
			)
		)
		return group
	var board := HFlowContainer.new()
	board.name = "Board"
	STYLE.on_theme_overrides(
		board,
		func() -> void:
			board.add_theme_constant_override("h_separation", UI_THEME.space(board, 3))
			board.add_theme_constant_override("v_separation", UI_THEME.space(board, 3))
	)
	group.add_child(board)
	for reference: Variant in references:
		if reference is Dictionary:
			board.add_child(_reference(reference))
	return group


static func _reference(reference: Dictionary) -> Control:
	var item := VBoxContainer.new()
	item.theme_type_variation = "JoineryTight"
	var id := str(reference.get("id", ""))
	var name := str(reference.get("reference", ""))
	item.accessibility_name = "%s %s" % [id, name]
	item.set_meta("reference", reference)
	var frame := PanelContainer.new()
	frame.theme_type_variation = "JoineryMediaFrame"
	frame.clip_children = CanvasItem.CLIP_CHILDREN_AND_DRAW
	item.add_child(frame)
	var picture := TextureRect.new()
	picture.name = "Picture"
	picture.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	picture.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_COVERED
	picture.texture = _thumbnail(str(reference.get("file", "")))
	frame.add_child(picture)
	if picture.texture == null:
		var kind := UI.label(str(reference.get("kind", "")).capitalize(), "JoineryCaption")
		kind.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
		kind.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
		frame.add_child(kind)
	var caption := UI.label(("%s · %s" % [id, name]) if not id.is_empty() else name, "", true)
	item.add_child(caption)
	var take := str(reference.get("take", ""))
	var avoid := str(reference.get("avoid", ""))
	var detail := PackedStringArray()
	if not take.is_empty():
		detail.append("Take: " + take)
	if not avoid.is_empty():
		detail.append("Not: " + avoid)
	if not detail.is_empty():
		item.add_child(UI.label("\n".join(detail), "JoineryCaption", true))
	STYLE.on_theme(
		item,
		func() -> void:
			var side := float(item.get_theme_default_font_size()) * THUMB_EM
			item.custom_minimum_size.x = side
			frame.custom_minimum_size = Vector2(side, side * 0.75)
	)
	return item


## A reference image under art/reference, or null for files that aren't pictures (a Krita
## board, a missing file).
static func _thumbnail(file: String) -> Texture2D:
	if file.is_empty() or not file.get_extension().to_lower() in IMAGE_SUFFIXES:
		return null
	var path := ProjectSettings.globalize_path("res://").path_join(file)
	if not FileAccess.file_exists(path):
		return null
	var image := Image.load_from_file(path)
	if image == null or image.is_empty():
		return null
	return ImageTexture.create_from_image(image)


static func _technical(parts: Array) -> Control:
	var section := UI.section("Technical rules (preset for you)", true)
	section.name = "Technical"
	section.body.theme_type_variation = "JoineryStack"
	section.body.add_child(
		UI.label(
			"Joinery checks every asset against these before you review it.", "JoineryCaption", true
		)
	)
	var text := PackedStringArray()
	for part: Array in parts:
		var heading := str(part[0])
		if _is_decided(heading) or heading.to_lower() == "references":
			continue
		var body := clean(str(part[1]))
		if body.is_empty():
			continue
		text.append("## " + heading + "\n" + body)
	var rules := _rich(to_bbcode("\n\n".join(text)))
	rules.name = "Rules"
	section.body.add_child(rules)
	return section


static func _offers(parent: Control, dialog: AcceptDialog, suggestions: Array) -> Control:
	var lines: Array[String] = []
	for suggestion: Variant in suggestions:
		var text := offer_text(str(suggestion))
		if (
			text.is_empty()
			or OWN_OFFERS.any(func(own: String) -> bool: return text.begins_with(own))
		):
			continue
		lines.append(text)
	if lines.is_empty():
		return null
	var group := VBoxContainer.new()
	group.name = "Offers"
	group.theme_type_variation = "JoineryTight"
	group.add_child(UI.label("To do first"))
	for text: String in lines.slice(0, MAX_OFFERS):
		var row := HBoxContainer.new()
		row.theme_type_variation = "JoineryRow"
		row.set_meta("offer", text)
		group.add_child(row)
		var label := UI.label(text, "JoineryCaption", true)
		label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		row.add_child(label)
		if parent.has_method("fill_composer"):
			var ask := UI.link("Ask Journeyman")
			ask.name = "Ask"
			ask.size_flags_vertical = Control.SIZE_SHRINK_BEGIN
			ask.accessibility_name = "Ask Journeyman: " + text
			ask.pressed.connect(
				func() -> void:
					parent.call("fill_composer", "Help me with the art direction. " + text)
					dialog.hide()
					dialog.queue_free()
			)
			row.add_child(ask)
	return group


static func _upgrade_offer(parent: Control, dialog: AcceptDialog) -> Control:
	var panel := PanelContainer.new()
	panel.name = "Upgrade"
	panel.theme_type_variation = "JoineryCard"
	var row := HBoxContainer.new()
	row.theme_type_variation = "JoineryRow"
	panel.add_child(row)
	var words := VBoxContainer.new()
	words.theme_type_variation = "JoineryTight"
	words.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.add_child(words)
	words.add_child(UI.label("These rules use an older layout.", "", true))
	words.add_child(
		UI.label(
			"Updating keeps every line and adds the new sections. You'll need to approve it again.",
			"JoineryCaption",
			true
		)
	)
	var button := UI.secondary("Update to the new layout")
	button.name = "Update"
	button.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	button.disabled = not parent.has_method("upgrade_bible")
	button.pressed.connect(
		func() -> void:
			parent.call("upgrade_bible")
			dialog.hide()
			dialog.queue_free()
	)
	row.add_child(button)
	return panel


static func _rich(bbcode: String) -> RichTextLabel:
	var content := RichTextLabel.new()
	content.bbcode_enabled = true
	content.fit_content = true
	content.scroll_active = false
	content.selection_enabled = true
	content.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	content.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	content.text = bbcode
	return content


static func _belongs(heading: String, keys: Array) -> bool:
	var lower := heading.to_lower()
	for key: String in keys:
		if lower.begins_with(key) or (key == "never" and lower.contains("never")):
			return true
	return false


static func _is_decided(heading: String) -> bool:
	for spec: Array in SECTIONS:
		if _belongs(heading, spec[1]):
			return true
	return false


## The text of the "### …" subsection whose title contains [param title].
static func _subsection(body: String, title: String) -> String:
	var lines := PackedStringArray()
	var inside := false
	for raw: String in body.split("\n"):
		if raw.begins_with("###"):
			inside = raw.to_lower().contains(title)
			continue
		if inside:
			lines.append(raw)
	return clean("\n".join(lines))


## Paint the dialog in the Joinery surface, light or dark, with SPACE.4 around its content.
## Call once it is in the tree, so the theme's colours resolve.
static func _paint(dialog: AcceptDialog) -> void:
	var box := StyleBoxFlat.new()
	box.bg_color = dialog.get_theme_color("surface", "Joinery")
	var pad := float(UI_THEME.space(dialog.get_ok_button(), 4))
	box.content_margin_left = pad
	box.content_margin_right = pad
	box.content_margin_top = pad
	box.content_margin_bottom = pad
	dialog.add_theme_stylebox_override("panel", box)


## The dialog centres its buttons between spacers, and keeps each spacer's visibility in
## step with its button. Keep only the leading spacer expanding and move the others to the
## leading edge too, so the buttons sit flush with the trailing edge of the text, one
## SPACE.2 apart.
static func _trail_buttons(dialog: AcceptDialog) -> void:
	var row := dialog.get_ok_button().get_parent()
	var spacers: Array[Control] = []
	for child: Node in row.get_children(true):
		if not child is Button:
			spacers.append(child as Control)
	for index: int in spacers.size():
		var spacer := spacers[index]
		spacer.size_flags_horizontal = (
			Control.SIZE_EXPAND_FILL if index == 0 else Control.SIZE_FILL
		)
		row.move_child(spacer, index)
	var gap := UI_THEME.space(dialog, 2)
	dialog.add_theme_constant_override("buttons_separation", gap)
	row.add_theme_constant_override("separation", gap)
