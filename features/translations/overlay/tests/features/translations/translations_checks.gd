extends RefCounted
## The translations checks, run by translations_test.gd once the game's
## autoloads exist (so this file can name `Language`, `Menus` and `Settings`).

const DE_PO := "res://locale/de.po"
## A language made in the test, so the checks never depend on shipped files.
const FRENCH := {
	"Play": "Jouer",
	"Settings": "Paramètres",
	"Language": "Langue",
	"Back": "Retour",
}

var tree: SceneTree
var h: PlayHarness


func _init(scene_tree: SceneTree, harness: PlayHarness) -> void:
	tree = scene_tree
	h = harness


func run(phase: String, expected: PackedStringArray) -> void:
	h.expect(tree.root.get_node_or_null(^"Language") != null, "the Language autoload is loaded")
	if h.failures.size() > 0:
		return
	for code: String in expected:
		h.expect(Language.available().has(code), "%s is one of the languages (from locale/)" % code)
	match phase:
		"verify":
			await _verify()
		"pseudo-arg":
			await _pseudo_arg()
		_:
			await _play()


func _play() -> void:
	h.expect(Language.available().size() > 0, "there is at least one language")
	h.expect(Language.available()[0] == "en", "English (the fallback) is listed first")
	h.expect(TranslationServer.get_loaded_locales().has("en"), "locale/en.po is loaded")
	h.expect(not Language.is_pseudo(), "the pseudo-locale starts off")
	await h.boot(false)
	await h.process_frames(2)

	var french: Translation = Translation.new()
	french.locale = "fr"
	for msgid: String in FRENCH:
		french.add_message(msgid, FRENCH[msgid])
	TranslationServer.add_translation(french)
	h.expect(Language.available().has("fr"), "a translation added at run time is listed")

	var play: Button = _menu_button(&"start")
	h.expect(play != null, "the main menu has its Play entry")
	Language.set_locale("fr")
	await h.process_frames(2)
	h.expect(Language.current() == "fr", "the current language is French")
	if play != null:
		h.expect(play.atr(play.text) == "Jouer", "the Play entry reads Jouer")
	h.expect(str(Settings.get_value("locale", "code")) == "fr", "the choice is in Settings")
	h.expect(_saved("locale", "code") == "fr", "the choice is saved to the settings file")

	Menus.open_settings()
	await h.process_frames(2)
	var title: Label = _title(Menus.current_page())
	h.expect(title != null, "the Settings page has a title")
	if title != null:
		h.expect(
			title.get_total_character_count() == "Paramètres".length(),
			(
				"the open Settings page shows Paramètres (got %d characters)"
				% title.get_total_character_count()
			)
		)
	Language.set_locale("en")
	await h.process_frames(2)
	if title != null:
		h.expect(
			title.get_total_character_count() == "Settings".length(),
			"switching back updates the open page at once"
		)

	await _check_pseudo(title)
	await _check_page()
	await _check_german()

	# Leave a choice for the verify phase: German when its file is there.
	Menus.close_all()
	Language.set_locale("de" if FileAccess.file_exists(DE_PO) else "fr")
	Language.set_pseudo(true)


func _check_pseudo(title: Label) -> void:
	Language.set_pseudo(true)
	await h.process_frames(2)
	h.expect(Language.is_pseudo(), "the pseudo-locale is on")
	h.expect(TranslationServer.pseudolocalization_enabled, "Godot's pseudolocalisation is on")
	var stretched: String = TranslationServer.translate("Settings")
	h.expect(stretched != "Settings", "pseudo text differs from the original")
	h.expect(
		stretched.length() >= int("Settings".length() * 1.3),
		"pseudo text is at least 30 %% longer (%s)" % stretched
	)
	var kept: String = TranslationServer.translate("Coins: %d")
	h.expect(kept.contains("%d"), "pseudo text keeps placeholders (%s)" % kept)
	if title != null:
		h.expect(
			title.get_total_character_count() > "Settings".length(),
			"the open page shows pseudo text"
		)
	h.expect(bool(Settings.get_value("locale", "pseudo")), "the pseudo-locale is saved")
	Language.set_pseudo(false)
	await h.process_frames(2)
	h.expect(not TranslationServer.pseudolocalization_enabled, "the pseudo-locale is off again")
	if title != null:
		h.expect(
			title.get_total_character_count() == "Settings".length(),
			"the page reads Settings again"
		)


func _check_page() -> void:
	Menus.close_all()
	Menus.open_settings()
	await h.process_frames(2)
	var found: bool = await h.focus_button("Language")
	h.expect(found, "Settings lists a Language page")
	if not found:
		return
	await h.tap(&"ui_accept")
	await h.process_frames(2)
	var page: Control = Menus.current_page()
	var choice: OptionButton = page.find_child("locale_code", true, false) as OptionButton
	h.expect(choice != null, "the Language page has the language list")
	if choice == null:
		return
	h.expect(h.focused() == choice, "the language list has focus")
	var codes: PackedStringArray = Language.available()
	h.expect(choice.item_count == codes.size(), "every language is listed")
	h.expect(
		choice.get_item_text(choice.selected) == Language.display_name(Language.current()),
		"the language in use is selected"
	)
	h.expect(
		choice.get_item_text(codes.find("fr")) == "Français",
		"languages are named in their own language"
	)
	h.expect_min_text(page, "Language page")
	choice.select(codes.find("fr"))
	choice.item_selected.emit(codes.find("fr"))
	await h.process_frames(2)
	h.expect(Language.current() == "fr", "choosing Français switches the game to French")
	var row_label: Label = page.find_child("locale_code_row", true, false).get_child(0) as Label
	h.expect(
		row_label.get_total_character_count() == "Langue".length(), "the page itself is in French"
	)
	h.expect(
		choice.get_item_text(choice.selected) == "Français", "language names are never translated"
	)
	if OS.is_debug_build():
		var pseudo: CheckButton = page.find_child("locale_pseudo", true, false) as CheckButton
		h.expect(pseudo != null, "development builds show the pseudo-locale switch")
		if pseudo != null:
			pseudo.button_pressed = true
			await h.process_frames(1)
			h.expect(Language.is_pseudo(), "the switch turns the pseudo-locale on")
			pseudo.button_pressed = false
			await h.process_frames(1)
			h.expect(not Language.is_pseudo(), "and off")
	Language.set_locale("en")
	Menus.close_all()


func _check_german() -> void:
	if not FileAccess.file_exists(DE_PO):
		return
	h.expect(Language.available().has("de"), "locale/de.po is found without a project setting")
	Language.set_locale("de")
	h.expect(tr_text("Play") == "Spielen", "German from locale/de.po")
	h.expect(
		(
			TranslationServer.translate_plural("Player %d: %d pair", "Player %d: %d pairs", 2)
			== "Spieler %d: %d Paare"
		),
		"plural forms from locale/de.po"
	)
	h.expect(
		TranslationServer.translate("Quit") == "Quit", "untranslated text falls back to English"
	)
	print("translations: de.po checked")
	Language.set_locale("en")


func _verify() -> void:
	var saved: String = _saved("locale", "code")
	h.expect(not saved.is_empty(), "the play phase saved a language")
	h.expect(
		TranslationServer.get_locale() == saved, "the saved language %s is applied at start" % saved
	)
	h.expect(Language.is_pseudo() == OS.is_debug_build(), "the saved pseudo-locale is applied")
	Language.set_pseudo(false)
	await h.boot(false)
	await h.process_frames(2)
	if saved == "de":
		h.expect(Language.current() == "de", "German is the current language")
		var play: Button = _menu_button(&"start")
		h.expect(play != null and play.atr(play.text) == "Spielen", "the main menu is in German")
		print("translations: saved German checked")
	Language.set_locale("en")
	h.expect(_saved("locale", "code") == "en", "the choice is reset")


func _pseudo_arg() -> void:
	h.expect(Language.is_pseudo(), "--pseudo-locale turns the pseudo-locale on")
	h.expect(not Settings.has_value("locale", "pseudo"), "--pseudo-locale is not saved")
	await h.boot(false)
	await h.process_frames(2)
	var play: Button = _menu_button(&"start")
	h.expect(play != null and play.atr(play.text) != "Play", "the main menu shows pseudo text")
	h.expect_min_text(Menus.current_page(), "main menu with the pseudo-locale")


func tr_text(msgid: String) -> String:
	return TranslationServer.translate(msgid)


func _menu_button(id: StringName) -> Button:
	var page: Control = Menus.current_page()
	if page == null:
		return null
	return page.find_child(String(id), true, false) as Button


func _title(page: Control) -> Label:
	if page == null:
		return null
	var column: Node = page.find_child("Column", true, false)
	if column == null or column.get_child_count() == 0:
		return null
	return column.get_child(0) as Label


func _saved(section: String, key: String) -> String:
	var config: ConfigFile = ConfigFile.new()
	if config.load(str(Settings.get(&"path"))) != OK:
		return ""
	return str(config.get_value(section, key, ""))
