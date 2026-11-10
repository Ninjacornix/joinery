@tool
extends RefCounted
## Joinery design tokens, the shared Theme, icons and appearance preferences.
##
## Every size and colour in the addon comes from here. Screens never pass literal pixel values
## or palette colours to overrides: they use the type variations built by [method build]
## (for example [code]theme_type_variation = "JoineryCaption"[/code]) or read a role with
## [code]get_theme_color(role, "Joinery")[/code] while drawing.
##
## Sizes scale with the editor: [method unit] is [code]max(font_size / 14, 1)[/code], where the
## font size already includes the editor scale. Theme and accent are user preferences stored
## in [code]journeyman.cfg[/code]; [method watch] re-themes a root live when they change.

const _MOTION := preload("ui_motion.gd")
const _SELF := preload("ui_theme.gd")
const _BUILD := preload("ui_theme_build.gd")

## Spacing scale in logical pixels, multiplied by [method unit]. Read with [method space].
const SPACE := {1: 4, 2: 8, 3: 12, 4: 16, 5: 24, 6: 32}
## Corner radii in logical pixels, multiplied by [method unit]. Read with [method radius].
const RADIUS := {"xs": 4, "sm": 6, "md": 8, "lg": 12, "xl": 16, "full": 999}
## Type scale as factors of the editor font size. Read with [method font_size].
const TYPE := {"caption": 0.85, "body": 1.0, "body_lg": 1.15, "title": 1.3, "display": 1.7}
## Control heights as factors of the editor font size. Read with [method control_h].
## xs: inline text buttons; sm: chips and the composer picker; md: buttons, inputs and
## settings rows; lg: provider cards and wizard rows.
const CONTROL_H := {"xs": 1.75, "sm": 2.0, "md": 2.5, "lg": 3.0}
## The named size exceptions (design direction v2 #62), the only sizes outside the scales above.
## Icons in logical pixels × unit: sm for inline glyphs, md for the app rail, tile for icon
## tiles. Read with [method icon_px].
const ICON := {"sm": 16, "md": 20, "tile": 28}
## A status dot's diameter in logical pixels × unit. Read with [method dot_px].
const DOT := 8
## The composer's Send and Stop button side in logical pixels × unit. Read with [method send_px].
const SEND := 32
## The audio transport's play button side in logical pixels × unit. Read with
## [method transport_px].
const TRANSPORT := 40
## Dialog sizes in em (multiples of the body font): the agent connect dialog and document
## dialogs (art direction, brief). Read with [method dialog_size].
const DIALOG := {"connect": Vector2(32, 36), "doc": Vector2(48, 36)}
## Durations in seconds. fast: hover and focus colour; base: fades and reveals; confirm: success
## flash; enter/exit: entrances and exits (exits are 20% shorter); menu: popovers and menus;
## stagger: delay between list rows. Animation helpers live in ui_motion.gd.
const MOTION := {
	"fast": 0.12,
	"base": 0.2,
	"confirm": 0.6,
	"enter": 0.2,
	"exit": 0.16,
	"menu": 0.18,
	"stagger": 0.03,
}
## Accent options: [dark, light]. All pass 4.5:1 on every surface and with on_accent.
const ACCENTS := {
	"brass": [Color("e6b66b"), Color("875309")],
	"blue": [Color("6aaee0"), Color("1c64a0")],
	"green": [Color("79c48f"), Color("2a7044")],
	"violet": [Color("b59cf0"), Color("6a4bc4")],
}
## Display names for the accents, also the values of the fork's joinery_accent setting.
const ACCENT_NAMES := {"brass": "Brass", "blue": "Blue", "green": "Green", "violet": "Violet"}
## Text size choices (settings "Text size") and their transcript scale.
const TEXT_SIZES := {"Small": 0.9, "Normal": 1.0, "Large": 1.15}
## Allowed values for every preference handled by [method set_pref]; the first is the default.
const PREF_CHOICES := {
	"theme": ["match", "dark", "light"],
	"accent": ["brass", "blue", "green", "violet"],
	"reduce_motion": ["off", "on"],
	"text_size": ["Normal", "Small", "Large"],
}
## Icon names accepted by [method icon] and the matching editor icon. The bundled glyph in
## icons/ui/<name>.svg is used first because it is white and tints cleanly in both themes;
## the editor icon is the fallback.
const ICONS := {
	"menu": "GuiTabMenuHl",
	"back": "Back",
	"disclosure_closed": "GuiTreeArrowRight",
	"disclosure_open": "GuiTreeArrowDown",
	"close": "Close",
	"check": "StatusSuccess",
	"success": "StatusSuccess",
	"warning": "StatusWarning",
	"error": "StatusError",
	"send": "ArrowUp",
	"stop": "Stop",
	"play": "Play",
	"pause": "Pause",
	"loop": "Loop",
	"volume": "AudioStreamPlayer",
	"model": "MeshInstance3D",
	"image": "ImageTexture",
	"audio": "AudioStreamPlayer",
	"sound": "AudioStreamPlayer",
	"material": "StandardMaterial3D",
	"scene": "PackedScene",
	"chevron": "GuiOptionArrow",
	"external": "ExternalLink",
	"reload": "Reload",
}
const DARK := {
	"surface": Color("202123"),
	"surface_sunken": Color("18191b"),
	"surface_raised": Color("2b2d30"),
	"border_subtle": Color("404247"),
	"border_strong": Color("7c7f86"),
	"text": Color("eeede8"),
	"text_muted": Color("b0b0aa"),
	"text_faint": Color("9d9d97"),
	"success": Color("6cc38a"),
	"danger": Color("f08a7e"),
}
const LIGHT := {
	"surface": Color("f7f6f3"),
	"surface_sunken": Color("ffffff"),
	"surface_raised": Color("ebe9e2"),
	"border_subtle": Color("c9c6bd"),
	"border_strong": Color("7c7e76"),
	"text": Color("25262a"),
	"text_muted": Color("53554e"),
	"text_faint": Color("5f615a"),
	"success": Color("2a6f43"),
	"danger": Color("b4372b"),
}
## Number of in-between switch frames stored in the theme ("frame_0" … "frame_6" on
## JoinerySwitch); ui_motion plays them while the knob travels.
const SWITCH_FRAMES := 6
const _CONFIG := "Joinery/journeyman.cfg"
const _BUS_META := "joinery_ui_bus"
const _PREFS_META := "joinery_ui_prefs"
const _PATH_META := "joinery_ui_config"
const _WATCH_META := "joinery_ui_watcher"
const _APPEARANCE_KEYS := ["theme", "accent", "reduce_motion"]
const _FONTS_META := "joinery_ui_fonts"
## Bundled faces (OFL, fonts/LICENSE.Inter.txt), the same files the editor is built with.
const _FONT_FILES := {"main": "fonts/Inter_Regular.woff2", "bold": "fonts/Inter_Bold.woff2"}
const _MEDIUM_EMBOLDEN := 0.35
## A FontVariation emboldened at least this much stands for the bold weight.
const _BOLD_EMBOLDEN := 0.5
## Base types whose text takes the Joinery font when the context's default font is not it.
const _TEXT_TYPES := {
	"Label": ["font"],
	"Button": ["font"],
	"CheckBox": ["font"],
	"CheckButton": ["font"],
	"OptionButton": ["font"],
	"MenuButton": ["font"],
	"LinkButton": ["font"],
	"LineEdit": ["font"],
	"TextEdit": ["font"],
	"ItemList": ["font"],
	"Tree": ["font", "title_button_font"],
	"TabBar": ["font"],
	"TabContainer": ["font"],
	"PopupMenu": ["font", "font_separator"],
	"TooltipLabel": ["font"],
	"ProgressBar": ["font"],
	"RichTextLabel": ["normal_font"],
}


## Per-root state for [method watch]; stored as root meta so it dies with the root.
class _Watcher:
	extends RefCounted
	var root: Control
	var signature: Array = []

	func _init(target: Control) -> void:
		root = target

	func on_pref(_key: String, _value: String) -> void:
		refresh_now()

	func on_event() -> void:
		refresh_now.call_deferred()

	func refresh_now() -> void:
		if is_instance_valid(root):
			_SELF._refresh(root, self)


# --- Tokens ----------------------------------------------------------------------------------


## Scale factor for every logical size: max(font_size / 14, 1).
static func unit(context: Node) -> float:
	return maxf(_base_size(context) / 14.0, 1.0)


## A spacing step (1–6) in pixels: 4, 8, 12, 16, 24 or 32 × unit.
static func space(context: Node, step: int) -> int:
	return roundi(int(SPACE.get(step, 0)) * unit(context))


## A corner radius ("xs", "sm", "md", "lg", "xl", "full") in pixels.
static func radius(context: Node, name: String) -> int:
	return roundi(int(RADIUS.get(name, 0)) * unit(context))


## Radius for a child inside a container with radius [param outer] and padding [param pad]:
## max(RADIUS.xs, outer - pad), so nested corners stay concentric.
static func nested(outer: float, pad: float) -> float:
	return maxf(float(RADIUS["xs"]), outer - pad)


## Font size in pixels for a TYPE step ("caption", "body", "body_lg", "title", "display").
static func font_size(context: Node, type_name: String) -> int:
	return roundi(_base_size(context) * float(TYPE.get(type_name, 1.0)))


## Control height in pixels for a CONTROL_H step ("xs", "sm", "md", "lg").
static func control_h(context: Node, name: String) -> int:
	return roundi(_base_size(context) * float(CONTROL_H.get(name, CONTROL_H["md"])))


## Icon size in pixels for an ICON step ("sm" 16, "md" 20, "tile" 28, × unit).
static func icon_px(context: Node, name := "sm") -> int:
	return roundi(int(ICON.get(name, ICON["sm"])) * unit(context))


## Status dot diameter in pixels (DOT × unit).
static func dot_px(context: Node) -> int:
	return roundi(DOT * unit(context))


## Send and Stop button side in pixels (SEND × unit).
static func send_px(context: Node) -> int:
	return roundi(SEND * unit(context))


## Audio transport button side in pixels (TRANSPORT × unit).
static func transport_px(context: Node) -> int:
	return roundi(TRANSPORT * unit(context))


## A DIALOG size ("connect", "doc") in pixels: em × the body font size.
static func dialog_size(context: Node, name: String) -> Vector2i:
	var em: Vector2 = DIALOG.get(name, DIALOG["connect"])
	var body := float(font_size(context, "body"))
	return Vector2i(roundi(em.x * body), roundi(em.y * body))


# --- Colour ----------------------------------------------------------------------------------


## True when the Joinery UI is dark: forced by the Theme preference, or, under "match", when
## the editor base colour's luminance is below 0.45. Headless without an editor theme: dark.
static func is_dark(context: Node) -> bool:
	var choice: String = prefs()["theme"]
	if choice != "match":
		return choice == "dark"
	if context is Control and (context as Control).has_theme_color("base_color", "Editor"):
		return (context as Control).get_theme_color("base_color", "Editor").get_luminance() < 0.45
	if context is Window and (context as Window).has_theme_color("base_color", "Editor"):
		return (context as Window).get_theme_color("base_color", "Editor").get_luminance() < 0.45
	return true


## Every colour role (see the design spec A1) for the current theme and accent. Keys: surface,
## surface_sunken, surface_raised, border_subtle, border_strong, text, text_muted, text_faint,
## accent, accent_hover, accent_pressed, on_accent, accent_tint, success, danger, danger_tint.
static func palette(context: Node) -> Dictionary:
	var dark := is_dark(context)
	var colors: Dictionary = (DARK if dark else LIGHT).duplicate()
	var accent: Color = ACCENTS[prefs()["accent"]][0 if dark else 1]
	colors["accent"] = accent
	colors["accent_hover"] = accent.lightened(0.08) if dark else accent.darkened(0.08)
	colors["accent_pressed"] = accent.darkened(0.08) if dark else accent.darkened(0.16)
	colors["on_accent"] = Color("1a1a1a") if dark else Color.WHITE
	colors["accent_tint"] = (colors["surface_sunken"] as Color).lerp(accent, 0.14)
	colors["danger_tint"] = (colors["surface"] as Color).lerp(colors["danger"], 0.12)
	return colors


## One colour role from [method palette]. Unknown roles return magenta so they are noticed.
static func color(context: Node, role: String) -> Color:
	return palette(context).get(role, Color.MAGENTA)


# --- Fonts and icons -------------------------------------------------------------------------


## The Joinery text font: the editor's main font inside the editor (Inter unless the user
## chose another), otherwise the bundled Inter Regular, so a dock outside the editor and the
## editor itself share one typeface.
static func text_font(context: Node) -> Font:
	return _joinery_font(context, "main")


## The bold weight of [method text_font]: the editor bold font, or the bundled Inter Bold.
static func bold_font(context: Node) -> Font:
	return _joinery_font(context, "bold")


## The medium weight of [method text_font] (Inter has no bundled medium file, so the regular
## face is lightly emboldened): user messages, row titles and emphasis inside body text.
static func medium_font(context: Node) -> Font:
	var font := FontVariation.new()
	font.base_font = text_font(context)
	font.variation_embolden = _MEDIUM_EMBOLDEN
	return font


## The default font with tabular digits (OpenType "tnum"), for every number that changes.
static func numeric_font(context: Node) -> Font:
	var font := FontVariation.new()
	font.base_font = text_font(context)
	var server := TextServerManager.get_primary_interface()
	font.opentype_features = {server.name_to_tag("tnum"): 1}
	return font


## Monospace font for code, raw diffs, CLI details and source previews: the editor "source"
## font when available, otherwise a system monospace font.
static func mono_font(context: Node) -> Font:
	if context is Control and (context as Control).has_theme_font("source", "EditorFonts"):
		return (context as Control).get_theme_font("source", "EditorFonts")
	var font := SystemFont.new()
	font.font_names = PackedStringArray(
		["SF Mono", "Menlo", "Consolas", "DejaVu Sans Mono", "Liberation Mono", "monospace"]
	)
	return font


## A monochrome icon from the ICONS map, rendered at 16 × unit pixels. White by default: tint
## it with a palette colour (button icon colours, or self_modulate on a TextureRect), or pass
## [param tint] to bake the colour in (for theme icons that cannot be modulated).
static func icon(context: Node, name: String, tint := Color.WHITE) -> Texture2D:
	return _icon(context, name, tint)


## The official logo of app [param app_id] (icons/apps/<id>.svg, licences in
## licenses/tool-logos.md) in its own colours at 20 × unit pixels, or null when Joinery has none.
## A logo with a variant for dark backgrounds (<id>-dark.svg) uses it in the dark theme.
## Logos are never tinted: selection and hover show around them, not on them.
static func app_logo(context: Node, app_id: String) -> Texture2D:
	if not has_app_logo(app_id):
		return null
	var path := _app_logo_path(app_id)
	var dark_path := _app_logo_path(app_id + "-dark")
	if is_dark(context) and FileAccess.file_exists(dark_path):
		path = dark_path
	var image := Image.new()
	if image.load_svg_from_string(FileAccess.get_file_as_string(path), unit(context)) != OK:
		return null
	return ImageTexture.create_from_image(image)


## True when Joinery ships the official logo of app [param app_id].
static func has_app_logo(app_id: String) -> bool:
	return not app_id.is_empty() and FileAccess.file_exists(_app_logo_path(app_id))


static func _app_logo_path(name: String) -> String:
	return _directory().path_join("icons/apps/%s.svg" % name)


# --- Styles ----------------------------------------------------------------------------------


## A flat stylebox. Kept for existing callers; prefer the type variations from [method build].
static func panel(
	color_value: Color, border: Color, padding: float, radius_value: float, width: int = 1
) -> StyleBoxFlat:
	var box := StyleBoxFlat.new()
	box.bg_color = color_value
	box.border_color = border
	box.set_border_width_all(width)
	box.set_corner_radius_all(int(radius_value))
	box.set_content_margin_all(padding)
	return box


## Fade a node in (modulate alpha 0 to 1 over MOTION.base). Instant when motion is off.
static func fade_in(node: CanvasItem) -> void:
	_MOTION.fade_in(node)


## Confirm success: an accent tint behind the node's children that fades over MOTION.confirm.
static func flash(node: Control) -> void:
	_MOTION.flash(node)


# --- Preferences and live theming ------------------------------------------------------------


## Shared notifier with [code]signal changed(key: String, value: String)[/code], emitted by
## [method set_pref]. Connect to it to react to preference changes outside [method watch].
static func bus() -> RefCounted:
	if not Engine.has_meta(_BUS_META):
		# A plain native object: Engine meta outlives the script language at exit, so it must
		# not hold a script instance.
		var notifier := RefCounted.new()
		notifier.add_user_signal(
			"changed",
			[{"name": "key", "type": TYPE_STRING}, {"name": "value", "type": TYPE_STRING}]
		)
		Engine.set_meta(_BUS_META, notifier)
	return Engine.get_meta(_BUS_META)


## Appearance preferences: {"theme": "match|dark|light", "accent": "brass|blue|green|violet",
## "reduce_motion": "off|on", "text_size": "Small|Normal|Large"}. Cached; invalid values fall
## back to the defaults.
static func prefs() -> Dictionary:
	var path := _config_path()
	var cached: Dictionary = Engine.get_meta(_PREFS_META, {})
	if cached.get("path", "") == path:
		return (cached["values"] as Dictionary).duplicate()
	var file := ConfigFile.new()
	file.load(path)
	var values := {}
	for key: String in PREF_CHOICES:
		var choices: Array = PREF_CHOICES[key]
		var stored := str(file.get_value(_section(key), key, choices[0]))
		values[key] = stored if stored in choices else choices[0]
	Engine.set_meta(_PREFS_META, {"path": path, "values": values})
	return values.duplicate()


## Save one preference to journeyman.cfg ([appearance] theme/accent/reduce_motion,
## [journeyman] text_size), update the editor theme for theme and accent, and notify watchers.
## Returns OK, or the error; on error nothing changes, so the caller can roll its UI back.
static func set_pref(key: String, value: String) -> Error:
	if not PREF_CHOICES.has(key) or not value in PREF_CHOICES[key]:
		push_warning("Unknown Joinery preference %s=%s" % [key, value])
		return ERR_INVALID_PARAMETER
	var path := _config_path()
	var file := ConfigFile.new()
	var loaded := file.load(path)
	if loaded != OK and loaded != ERR_FILE_NOT_FOUND and loaded != ERR_FILE_CANT_OPEN:
		return loaded
	file.set_value(_section(key), key, value)
	DirAccess.make_dir_recursive_absolute(path.get_base_dir())
	var saved := file.save(path)
	if saved != OK:
		return saved
	var values := prefs()
	values[key] = value
	Engine.set_meta(_PREFS_META, {"path": path, "values": values})
	if key in ["theme", "accent"]:
		_write_editor_settings(values)
	bus().emit_signal("changed", key, value)
	return OK


## Re-theme [param root] live. Sets [code]root.theme = build(root)[/code] now when it has no
## theme, then again whenever the dark flag, accent or editor font size changes (preference
## bus, EditorSettings.settings_changed and root.theme_changed), calling
## [code]root._on_theme_rebuilt()[/code] after each rebuild when the root defines it.
static func watch(root: Control) -> void:
	if root.has_meta(_WATCH_META):
		return
	var watcher := _Watcher.new(root)
	root.set_meta(_WATCH_META, watcher)
	if root.theme == null:
		root.theme = build(root)
	watcher.signature = _signature(root)
	bus().connect("changed", watcher.on_pref)
	root.theme_changed.connect(watcher.on_event)
	var settings := _editor_settings()
	if settings != null and settings.has_signal("settings_changed"):
		settings.connect("settings_changed", watcher.on_event)


# --- Theme -----------------------------------------------------------------------------------


## The Joinery theme for [param context]: palette colours under the "Joinery" type, base type
## styles (Label, RichTextLabel, LineEdit, TextEdit, Button, OptionButton, CheckButton,
## PopupMenu, ItemList, separators, tooltips, check box and radio icons, scroll bars) and these
## type variations. LineEdit, TextEdit
## and OptionButton share one field box (sunken fill, hairline border, radius md, SPACE.3
## insets); dialogs get their footer from ui_components.dialog_footer().
## Labels: JoineryCaption, JoineryFaint, JoineryBodyLarge, JoineryTitle, JoineryDisplay,
## JoineryError, JoineryNumeric, JoinerySection, JoineryMono.
## Buttons: JoineryPrimaryButton, JoinerySecondaryButton, JoineryLinkButton,
## JoineryQuietButton, JoineryDisclosureButton, JoineryRowButton, JoineryIconButton,
## JoinerySendButton, JoineryStopButton, JoinerySegment, JoineryCardButton, JoineryChipClose,
## JoinerySectionButton, JoineryPicker (OptionButton/MenuButton), JoinerySwitch (CheckButton).
## Inputs: JoineryLineEditError, JoineryTextEditError.
## Panels (PanelContainer): JoineryCard, JoineryCardSelected, JoineryBanner, JoineryChip,
## JoineryPopup, JoineryHoverCard, JoineryComposer, JoineryComposerFocused, JoineryBubble,
## JoineryChangeCard, JoineryMediaFrame, JoineryLoadingFrame, JoineryIconTile, JoineryDock.
## Containers: JoineryFlush (0), JoineryTight (4), JoineryRowTight (4), JoineryRow (8),
## JoineryStack (12), JoinerySections (24) for Box containers; HSplitContainer keeps a 12
## gutter between its columns; JoineryInset (side margins 8), JoineryPage
## (12 all round) and JoineryGutter (12 on the right, keeps scrollbars off controls) for
## MarginContainer.
## JoinerySectionButton is drawn exactly like JoineryDisclosureButton: every disclosure
## ("Advanced", "Details") has one look, a chevron on the content edge then semibold
## secondary text at body size.
## Every text face is [method text_font] / [method bold_font], so the dock reads the same
## outside the editor; RichTextLabel bold and italics stay at the body size.
static func build(context: Node) -> Theme:
	var source := _font_context(context)
	var t := _BUILD.theme(source, palette(context))
	if source != context:
		source.free()
	_unify_fonts(t, context)
	_unify_disclosures(t, context)
	return t


## Outside the editor the builder measures control heights with the context's default font
## (Open Sans), not the Joinery one. Give it a detached stand-in whose default font, font size
## and EditorFonts are the Joinery ones, so insets come out right for Inter.
static func _font_context(context: Node) -> Node:
	var regular := text_font(context)
	if regular == _BUILD.default_font(context):
		return context
	var fonts := Theme.new()
	fonts.default_font = regular
	fonts.default_font_size = _base_size(context)
	fonts.set_font("main", "EditorFonts", regular)
	fonts.set_font("bold", "EditorFonts", bold_font(context))
	fonts.set_font("source", "EditorFonts", mono_font(context))
	var stand_in := Control.new()
	stand_in.theme = fonts
	return stand_in


## Swap the context's default font (Open Sans outside the editor) for the Joinery faces in
## every font the builder set, and give the base text types the Joinery font explicitly. The
## theme's default_font stays unset: it would answer every EditorFonts lookup below the root.
static func _unify_fonts(t: Theme, context: Node) -> void:
	var regular := text_font(context)
	var bold := bold_font(context)
	var fallback := _BUILD.default_font(context)
	for type_name: String in t.get_font_type_list():
		for item: String in t.get_font_list(type_name):
			var current := t.get_font(item, type_name)
			var swapped := _swap_font(current, fallback, regular, bold)
			if swapped != current:
				t.set_font(item, type_name, swapped)
	if regular != fallback:
		for type_name: String in _TEXT_TYPES:
			for item: String in _TEXT_TYPES[type_name]:
				if not t.get_font_list(type_name).has(item):
					t.set_font(item, type_name, regular)
	# Bold and italic runs keep the body face and size, so a bold word never jumps in size.
	var body := font_size(context, "body")
	var italic := FontVariation.new()
	italic.base_font = regular
	italic.variation_transform = Transform2D(Vector2(1, 0), Vector2(0.2, 1), Vector2.ZERO)
	var bold_italic := FontVariation.new()
	bold_italic.base_font = bold
	bold_italic.variation_transform = italic.variation_transform
	t.set_font("normal_font", "RichTextLabel", regular)
	t.set_font("bold_font", "RichTextLabel", bold)
	t.set_font("italics_font", "RichTextLabel", italic)
	t.set_font("bold_italics_font", "RichTextLabel", bold_italic)
	for item: String in ["normal", "bold", "italics", "bold_italics"]:
		t.set_font_size(item + "_font_size", "RichTextLabel", body)


static func _swap_font(font: Font, fallback: Font, regular: Font, bold: Font) -> Font:
	if font == null or regular == fallback:
		return font
	if font == fallback:
		return regular
	if font is FontVariation and (font as FontVariation).base_font == fallback:
		var variation := font as FontVariation
		if (
			variation.variation_embolden >= _BOLD_EMBOLDEN
			and variation.opentype_features.is_empty()
		):
			return bold
		var copy := variation.duplicate() as FontVariation
		copy.base_font = regular
		return copy
	return font


static func _joinery_font(context: Node, weight: String) -> Font:
	var editor_theme := _editor_theme()
	if editor_theme != null and editor_theme.get_font_list("EditorFonts").has(weight):
		return editor_theme.get_font(weight, "EditorFonts")
	# Cached on the scene root, not Engine meta: Engine meta outlives the text server at exit,
	# and freeing a font after it crashes the process on quit.
	var tree := Engine.get_main_loop() as SceneTree
	var holder: Node = tree.root if tree != null else null
	var cache: Dictionary = holder.get_meta(_FONTS_META, {}) if holder != null else {}
	if not cache.has(weight):
		var path := _directory().path_join(_FONT_FILES[weight])
		if FileAccess.file_exists(path):
			var file := FontFile.new()
			file.data = FileAccess.get_file_as_bytes(path)
			cache[weight] = file
			if holder != null:
				holder.set_meta(_FONTS_META, cache)
	if cache.has(weight):
		return cache[weight]
	if weight == "bold":
		return _BUILD.bold_font(context)
	return _BUILD.default_font(context)


static func _editor_theme() -> Theme:
	if not Engine.is_editor_hint() or not Engine.has_singleton("EditorInterface"):
		return null
	var editor := Engine.get_singleton("EditorInterface")
	return editor.call("get_editor_theme") if editor != null else null


static func _unify_disclosures(t: Theme, context: Node) -> void:
	var source := "JoineryDisclosureButton"
	var semibold := medium_font(context)
	t.set_font("font", source, semibold)
	t.set_font_size("font_size", source, font_size(context, "body"))
	for type_name: String in ["JoinerySectionButton"]:
		for kind: int in Theme.DATA_TYPE_MAX:
			for item: String in t.get_theme_item_list(kind, type_name):
				t.clear_theme_item(kind, item, type_name)
			for item: String in t.get_theme_item_list(kind, source):
				t.set_theme_item(kind, item, type_name, t.get_theme_item(kind, item, source))
		t.set_type_variation(type_name, "Button")


# --- Helpers ---------------------------------------------------------------------------------


static func _base_size(context: Node) -> int:
	if context is Control:
		return (context as Control).get_theme_default_font_size()
	if context is Window:
		return (context as Window).get_theme_default_font_size()
	return ThemeDB.fallback_font_size


static func _icon(context: Node, name: String, tint: Color) -> Texture2D:
	if not ICONS.has(name):
		push_error("Unknown Joinery icon: " + name)
		return null
	var scale := unit(context)
	var texture: Texture2D = null
	var path := _directory().path_join("icons/ui/%s.svg" % name)
	if FileAccess.file_exists(path):
		var svg := FileAccess.get_file_as_string(path)
		if tint != Color.WHITE:
			svg = svg.replace("#fff", "#" + tint.to_html(false))
		var image := Image.new()
		if image.load_svg_from_string(svg, scale) == OK:
			texture = ImageTexture.create_from_image(image)
	if texture == null and context is Control:
		var control := context as Control
		if control.has_theme_icon(ICONS[name], "EditorIcons"):
			texture = control.get_theme_icon(ICONS[name], "EditorIcons")
	return texture


static func _directory() -> String:
	var script: Resource = _SELF
	return script.resource_path.get_base_dir()


static func _section(key: String) -> String:
	return "appearance" if key in _APPEARANCE_KEYS else "journeyman"


## journeyman.cfg in the user config dir. Tests point it elsewhere with
## Engine.set_meta("joinery_ui_config", path).
static func _config_path() -> String:
	var override := str(Engine.get_meta(_PATH_META, ""))
	return override if not override.is_empty() else OS.get_config_dir().path_join(_CONFIG)


static func _signature(root: Control) -> Array:
	return [is_dark(root), prefs()["accent"], _base_size(root)]


static func _refresh(root: Control, watcher: RefCounted) -> void:
	if not root.is_inside_tree():
		return
	var signature := _signature(root)
	if signature == watcher.get("signature"):
		return
	watcher.set("signature", signature)
	root.theme = build(root)
	if root.has_method("_on_theme_rebuilt"):
		root.call("_on_theme_rebuilt")


static func _editor_settings() -> Object:
	if not Engine.is_editor_hint() or not Engine.has_singleton("EditorInterface"):
		return null
	var editor := Engine.get_singleton("EditorInterface")
	return editor.call("get_editor_settings") if editor != null else null


## Dark or Light also switch the editor preset (fork: Joinery / Joinery Light plus the
## joinery_accent setting; stock Godot: Default / Light). Match editor only updates the
## fork accent when a Joinery preset is active.
static func _write_editor_settings(values: Dictionary) -> void:
	var settings := _editor_settings()
	if settings == null:
		return
	var preset_key := "interface/theme/color_preset"
	var fork := false
	for property: Dictionary in settings.get_property_list():
		if property.get("name") == preset_key:
			fork = str(property.get("hint_string", "")).contains("Joinery")
			break
	var accent_key := "interface/theme/joinery_accent"
	var accent_name: String = ACCENT_NAMES[values["accent"]]
	var choice: String = values["theme"]
	if choice == "match":
		var current := str(settings.call("get_setting", preset_key))
		if fork and current.begins_with("Joinery") and settings.call("has_setting", accent_key):
			settings.call("set_setting", accent_key, accent_name)
		return
	settings.call("set_setting", "interface/theme/follow_system_theme", false)
	var dark := choice == "dark"
	var preset := (
		("Joinery" if dark else "Joinery Light") if fork else ("Default" if dark else "Light")
	)
	settings.call("set_setting", preset_key, preset)
	if fork and settings.call("has_setting", accent_key):
		settings.call("set_setting", accent_key, accent_name)
