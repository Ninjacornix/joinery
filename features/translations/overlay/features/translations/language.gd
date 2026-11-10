extends Node
## The game's languages. English text in the code is the message id; each
## other language is a translation file in `res://locale/`: a gettext `.po`
## (or `.mo`), or a CSV that Godot imports into `.translation` files. Every
## such file found there is loaded at start, so adding a language is adding
## its file. `res://features/translations/tools/extract_pot.gd` collects the
## text to translate and starts new languages.
##
## Player settings (section `locale` in `Settings`):
##   code     the chosen language, such as "de" or "pt_BR" (applied by Settings)
##   pseudo   pseudo-locale on (development builds only)
##
## Start with `-- --pseudo-locale` to turn the pseudo-locale on for one run.

signal locale_changed(code: String)
signal pseudo_changed(on: bool)

const SECTION := "locale"
const KEY_CODE := "code"
const KEY_PSEUDO := "pseudo"
const LOCALE_DIR := "res://locale"
const PSEUDO_ARG := "--pseudo-locale"
const EXTENSIONS: Array[String] = ["po", "mo", "translation"]
const LanguagePage := preload("res://features/translations/language_page.gd")
## Names of languages in their own language, for the language list. Others
## fall back to Godot's English name of the locale.
const NATIVE_NAMES := {
	"ar": "العربية",
	"cs": "Čeština",
	"da": "Dansk",
	"de": "Deutsch",
	"el": "Ελληνικά",
	"en": "English",
	"es": "Español",
	"es_419": "Español (Latinoamérica)",
	"fi": "Suomi",
	"fr": "Français",
	"hu": "Magyar",
	"id": "Bahasa Indonesia",
	"it": "Italiano",
	"ja": "日本語",
	"ko": "한국어",
	"nb": "Norsk bokmål",
	"nl": "Nederlands",
	"pl": "Polski",
	"pt": "Português",
	"pt_BR": "Português (Brasil)",
	"ro": "Română",
	"ru": "Русский",
	"sv": "Svenska",
	"th": "ไทย",
	"tr": "Türkçe",
	"uk": "Українська",
	"vi": "Tiếng Việt",
	"zh_CN": "简体中文",
	"zh_TW": "繁體中文",
}

## Translations this node loaded from `res://locale/`.
var loaded: Array[Translation] = []


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	load_folder(LOCALE_DIR)
	if Settings.has_value(SECTION, KEY_CODE):
		TranslationServer.set_locale(str(Settings.get_value(SECTION, KEY_CODE)))
	var pseudo: bool = OS.is_debug_build() and bool(Settings.get_value(SECTION, KEY_PSEUDO, false))
	if PSEUDO_ARG in OS.get_cmdline_user_args():
		pseudo = true
	if pseudo:
		_set_pseudo(true, false)
	Menus.add_settings_page(&"language", "Language", _build_page, 50)


## Loads every translation file in `folder` that is not loaded yet. Returns
## how many were added.
func load_folder(folder: String) -> int:
	if not DirAccess.dir_exists_absolute(folder):
		return 0
	var added: int = 0
	var files: PackedStringArray = DirAccess.get_files_at(folder)
	files.sort()
	for file: String in files:
		var path: String = folder.path_join(file.trim_suffix(".remap"))
		if not path.get_extension().to_lower() in EXTENSIONS:
			continue
		if not ResourceLoader.exists(path):
			continue
		var translation: Translation = ResourceLoader.load(path) as Translation
		if translation == null or TranslationServer.has_translation(translation):
			continue
		TranslationServer.add_translation(translation)
		loaded.append(translation)
		added += 1
	return added


## Language codes the game can show, the fallback language first.
func available() -> PackedStringArray:
	var fallback: String = fallback_locale()
	var codes: PackedStringArray = PackedStringArray()
	for code: String in TranslationServer.get_loaded_locales():
		if code != fallback and not codes.has(code):
			codes.append(code)
	codes.sort()
	codes.insert(0, fallback)
	return codes


## The available language in use (the closest match to the engine locale).
func current() -> String:
	var locale: String = TranslationServer.get_locale()
	var best: String = ""
	var best_score: int = 0
	for code: String in available():
		var score: int = TranslationServer.compare_locales(locale, code)
		if score > best_score:
			best = code
			best_score = score
	return best if not best.is_empty() else fallback_locale()


func fallback_locale() -> String:
	var fallback: String = str(
		ProjectSettings.get_setting("internationalization/locale/fallback", "en")
	)
	return fallback if not fallback.is_empty() else "en"


## Switches the game to `code` and saves the choice. Open menus update at once.
func set_locale(code: String) -> void:
	var locale: String = TranslationServer.standardize_locale(code)
	if locale.is_empty():
		return
	TranslationServer.set_locale(locale)
	Settings.set_value(SECTION, KEY_CODE, locale)
	locale_changed.emit(locale)


## The pseudo-locale lengthens and accents every translated text (by
## `internationalization/pseudolocalization/expansion_ratio`), so text that
## does not fit, or is not translated, stands out. Saved with the settings.
func set_pseudo(on: bool) -> void:
	_set_pseudo(on, true)


func is_pseudo() -> bool:
	return TranslationServer.pseudolocalization_enabled


## The name of a language in that language, for menus.
func display_name(code: String) -> String:
	if NATIVE_NAMES.has(code):
		return NATIVE_NAMES[code]
	var name_text: String = TranslationServer.get_locale_name(code)
	return name_text if not name_text.is_empty() else code


func _set_pseudo(on: bool, save: bool) -> void:
	if on:
		TranslationServer.reload_pseudolocalization()
	if TranslationServer.pseudolocalization_enabled != on:
		TranslationServer.pseudolocalization_enabled = on
	if save:
		Settings.set_value(SECTION, KEY_PSEUDO, on)
	pseudo_changed.emit(on)


func _build_page() -> Control:
	return LanguagePage.build(self)
