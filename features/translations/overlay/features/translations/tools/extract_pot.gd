extends SceneTree
## Collects every text of the game into `res://locale/messages.pot`, and keeps
## the language files in `res://locale/` in step with it.
##
## Run from the project folder:
##   godot --headless --path . --script res://features/translations/tools/extract_pot.gd
##     -- [--out=<file>] [--dirs=res://core,res://game,res://features]
##        [--add=de,fr] [--update-po] [--csv=res://locale/messages.csv]
##
##   --out        where the template goes (default res://locale/messages.pot)
##   --dirs       folders to read (default: core, game and every feature)
##   --add        start a language: writes res://locale/<code>.po, untranslated
##   --update-po  merge new text into every res://locale/*.po, keeping each
##                translation; text no longer in the game stays as `#~`
##   --csv        also write the text as a CSV (keys, en, then each language
##                that has a PO file) for spreadsheet work; Godot imports it
##
## Without --dirs, files listed in the project setting
## `internationalization/locale/translations_pot_files` are read too.
## Prints `extract_pot: ok` when everything was written.

const Scanner := preload("res://features/translations/tools/msgid_scanner.gd")
const PoFile := preload("res://features/translations/tools/po_file.gd")

const NAME := "extract_pot"
const LOCALE_DIR := "res://locale"
const DEFAULT_OUT := "res://locale/messages.pot"
const DEFAULT_DIRS: Array[String] = ["res://core", "res://game", "res://features"]
## Never read: tests, editor plugins and this tool's own folder.
const SKIP: Array[String] = ["res://tests", "res://addons", "res://features/translations/tools"]
const POT_FILES_SETTING := "internationalization/locale/translations_pot_files"

var _failures: PackedStringArray = PackedStringArray()


func _initialize() -> void:
	var options: Dictionary = _options(OS.get_cmdline_user_args())
	var scanner: Scanner = Scanner.new()
	scanner.scan_dirs(options["dirs"], PackedStringArray(SKIP))
	if not bool(options["only_dirs"]):
		for path: String in ProjectSettings.get_setting(POT_FILES_SETTING, PackedStringArray()):
			if FileAccess.file_exists(path):
				scanner.scan_file(path)
	var entries: Array[Dictionary] = scanner.sorted_entries()
	_write(str(options["out"]), PoFile.template(entries))
	print(
		(
			"%s: %d texts from %d files -> %s"
			% [NAME, entries.size(), scanner.files.size(), options["out"]]
		)
	)
	for locale: String in options["add"]:
		_add_language(locale, entries)
	if bool(options["update_po"]):
		for path: String in _po_files():
			_update(path, entries)
	if not str(options["csv"]).is_empty():
		_write_csv(str(options["csv"]), entries, options["add"])
	if _failures.is_empty():
		print("%s: ok" % NAME)
		quit(0)
	else:
		for failure: String in _failures:
			print("FAIL: %s" % failure)
		quit(1)


func _options(args: PackedStringArray) -> Dictionary:
	var options: Dictionary = {
		"out": DEFAULT_OUT,
		"dirs": PackedStringArray(DEFAULT_DIRS),
		"only_dirs": false,
		"add": PackedStringArray(),
		"update_po": false,
		"csv": "",
	}
	for argument: String in args:
		if argument.begins_with("--out="):
			options["out"] = argument.substr(6)
		elif argument.begins_with("--dirs="):
			options["dirs"] = argument.substr(7).split(",", false)
			options["only_dirs"] = true
		elif argument.begins_with("--add="):
			var codes: PackedStringArray = PackedStringArray()
			for code: String in argument.substr(6).split(",", false):
				codes.append(TranslationServer.standardize_locale(code.strip_edges()))
			options["add"] = codes
		elif argument == "--update-po":
			options["update_po"] = true
		elif argument.begins_with("--csv="):
			options["csv"] = argument.substr(6)
	return options


func _write(path: String, text: String) -> void:
	var folder: String = path.get_base_dir()
	if not folder.is_empty() and not DirAccess.dir_exists_absolute(folder):
		DirAccess.make_dir_recursive_absolute(folder)
	var tmp: String = path + ".tmp"
	var file: FileAccess = FileAccess.open(tmp, FileAccess.WRITE)
	if file == null:
		_failures.append(
			"could not write %s (%s)" % [path, error_string(FileAccess.get_open_error())]
		)
		return
	file.store_string(text)
	file.close()
	var error: Error = DirAccess.rename_absolute(tmp, path)
	if error != OK:
		_failures.append("could not write %s (%s)" % [path, error_string(error)])


func _po_files() -> PackedStringArray:
	var found: PackedStringArray = PackedStringArray()
	if not DirAccess.dir_exists_absolute(LOCALE_DIR):
		return found
	for file: String in DirAccess.get_files_at(LOCALE_DIR):
		if file.get_extension().to_lower() == "po":
			found.append(LOCALE_DIR.path_join(file))
	found.sort()
	return found


func _locale_of(path: String) -> String:
	var parsed: Dictionary = PoFile.parse(FileAccess.get_file_as_string(path))
	var locale: String = PoFile.header_value(str(parsed["header"]), "Language")
	return locale if not locale.is_empty() else path.get_file().get_basename()


func _add_language(locale: String, entries: Array[Dictionary]) -> void:
	if locale.is_empty():
		_failures.append("--add needs language codes such as de or pt_BR")
		return
	var path: String = LOCALE_DIR.path_join("%s.po" % locale)
	if FileAccess.file_exists(path):
		print("%s: %s already exists; use --update-po to add new text to it" % [NAME, path])
		return
	_write(path, PoFile.new_po(locale, entries))
	print("%s: started %s (%s)" % [NAME, path, TranslationServer.get_locale_name(locale)])


func _update(path: String, entries: Array[Dictionary]) -> void:
	if _locale_of(path) == PoFile.SOURCE_LOCALE:
		return  # The source language: message ids are its text.
	var before: String = FileAccess.get_file_as_string(path)
	var after: String = PoFile.merge(before, entries)
	if after != before:
		_write(path, after)
		print("%s: updated %s" % [NAME, path])


func _write_csv(path: String, entries: Array[Dictionary], added: PackedStringArray) -> void:
	var locales: PackedStringArray = PackedStringArray([PoFile.SOURCE_LOCALE])
	var known: Dictionary = {}
	for po: String in _po_files():
		var locale: String = _locale_of(po)
		if locale == PoFile.SOURCE_LOCALE:
			continue
		known[locale] = PoFile.translations(PoFile.parse(FileAccess.get_file_as_string(po)))
		if not locales.has(locale):
			locales.append(locale)
	for locale: String in added:
		if not locales.has(locale):
			locales.append(locale)
	_write(path, PoFile.csv(entries, locales, known))
	print("%s: wrote %s (%s)" % [NAME, path, ", ".join(locales)])
