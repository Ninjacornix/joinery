extends RefCounted
## Reads and writes gettext PO and POT files the way Godot's PO loader reads
## them, and writes the same messages as a CSV for spreadsheet work.
##
## An entry is a Dictionary: `msgid`, `plural` ("" when none), `context`,
## `refs` (PackedStringArray of res:// paths) and, for PO files, `msgstr`
## (PackedStringArray: one form, or one per plural form).

## The language message ids are written in.
const SOURCE_LOCALE := "en"
const DEFAULT_PLURAL_RULE := "nplurals=2; plural=(n != 1);"
const EOT := "\u0004"
const TEMPLATE_NOTE: Array[String] = [
	"# Every text of this game, collected from its scripts and scenes.",
	"# Rebuild after changing text or adding features:",
	"#   godot --headless --path . --script res://features/translations/tools/extract_pot.gd",
	"# Start a language with `-- --add=<code>`; merge new text into existing",
	"# languages with `-- --update-po`.",
]


static func key_of(context: String, msgid: String) -> String:
	return context + EOT + msgid


## A string as PO lines: `msgid "Play"`, or `msgid ""` plus one line per
## text line when the text has line breaks.
static func field(keyword: String, text: String) -> String:
	var parts: PackedStringArray = text.split("\n")
	if parts.size() <= 1:
		return '%s "%s"\n' % [keyword, escape(text)]
	var out: String = '%s ""\n' % keyword
	for index: int in parts.size():
		var part: String = parts[index]
		if index < parts.size() - 1:
			part += "\n"
		if not part.is_empty():
			out += '"%s"\n' % escape(part)
	return out


static func escape(text: String) -> String:
	return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t")


static func header_text(lines: PackedStringArray) -> String:
	var out: String = 'msgid ""\nmsgstr ""\n'
	for line: String in lines:
		out += '"%s\\n"\n' % escape(line)
	return out


## One entry. `forms` is how many msgstr[] a plural entry gets.
static func entry_text(
	entry: Dictionary, forms: int, with_refs: bool, prefix: String = ""
) -> String:
	var out: String = ""
	if with_refs:
		for ref: String in entry.get("refs", PackedStringArray()):
			out += "#: %s\n" % ref
	var body: String = ""
	var context: String = str(entry.get("context", ""))
	if not context.is_empty():
		body += field("msgctxt", context)
	body += field("msgid", str(entry["msgid"]))
	var msgstr: PackedStringArray = entry.get("msgstr", PackedStringArray())
	var plural: String = str(entry.get("plural", ""))
	if plural.is_empty():
		body += field("msgstr", msgstr[0] if msgstr.size() > 0 else "")
	else:
		body += field("msgid_plural", plural)
		for index: int in maxi(forms, 1):
			body += field("msgstr[%d]" % index, msgstr[index] if index < msgstr.size() else "")
	if prefix.is_empty():
		return out + body
	for line: String in body.trim_suffix("\n").split("\n"):
		out += prefix + line + "\n"
	return out


## The translation template: every entry with empty translations.
static func template(entries: Array[Dictionary]) -> String:
	var out: String = "\n".join(TEMPLATE_NOTE) + "\n"
	out += header_text(
		PackedStringArray(
			[
				"MIME-Version: 1.0",
				"Content-Type: text/plain; charset=UTF-8",
				"Content-Transfer-Encoding: 8bit",
			]
		)
	)
	for entry: Dictionary in entries:
		out += "\n" + entry_text(entry, 2, true)
	return out


## Plural rule of a locale, as a PO `Plural-Forms` value.
static func plural_rule(locale: String) -> String:
	var rule: String = TranslationServer.get_plural_rules(locale).strip_edges()
	if rule.is_empty():
		return DEFAULT_PLURAL_RULE
	return rule if rule.ends_with(";") else rule + ";"


static func plural_count(rule: String) -> int:
	var found: RegExMatch = RegEx.create_from_string("nplurals\\s*=\\s*(\\d+)").search(rule)
	return int(found.get_string(1)) if found != null else 2


## A new, untranslated PO file for `locale` with every entry.
static func new_po(locale: String, entries: Array[Dictionary]) -> String:
	var rule: String = plural_rule(locale)
	var out: String = (
		"# %s translation. Fill in each msgstr; keep %%d, %%s and the like.\n"
		% (TranslationServer.get_locale_name(locale))
	)
	out += header_text(
		PackedStringArray(
			[
				"Language: %s" % locale,
				"MIME-Version: 1.0",
				"Content-Type: text/plain; charset=UTF-8",
				"Content-Transfer-Encoding: 8bit",
				"Plural-Forms: %s" % rule,
			]
		)
	)
	var forms: int = plural_count(rule)
	for entry: Dictionary in entries:
		out += "\n" + entry_text(entry, forms, true)
	return out


## Parses PO text. Returns {"header": String, "entries": Array[Dictionary]};
## entries kept as `#~` (no longer used) have `obsolete = true`.
static func parse(text: String) -> Dictionary:
	var entries: Array[Dictionary] = []
	var header: String = ""
	var current: Dictionary = {}
	var target: String = ""
	var fuzzy: bool = false
	for raw: String in text.replace("\r\n", "\n").split("\n"):
		var line: String = raw.strip_edges()
		var obsolete: bool = false
		if line.begins_with("#~"):
			obsolete = true
			line = line.substr(2).strip_edges()
		elif line.begins_with("#"):
			if line.begins_with("#,") and line.contains("fuzzy"):
				fuzzy = true
			continue
		if line.is_empty():
			continue
		if line.begins_with("msgctxt"):
			current = _start(entries, current, obsolete, fuzzy)
			fuzzy = false
			current["context"] = _unquote(line.substr(7))
			target = "context"
		elif line.begins_with("msgid_plural"):
			current["plural"] = _unquote(line.substr(12))
			target = "plural"
		elif line.begins_with("msgid"):
			if current.is_empty() or bool(current["has_id"]):
				current = _start(entries, current, obsolete, fuzzy)
				fuzzy = false
			current["msgid"] = _unquote(line.substr(5))
			current["has_id"] = true
			target = "msgid"
		elif line.begins_with("msgstr["):
			var close: int = line.find("]")
			var index: int = int(line.substr(7, close - 7))
			_set_form(current, index, _unquote(line.substr(close + 1)))
			target = "msgstr%d" % index
		elif line.begins_with("msgstr"):
			_set_form(current, 0, _unquote(line.substr(6)))
			target = "msgstr0"
		elif line.begins_with('"') and not current.is_empty():
			var more: String = _unquote(line)
			if target.begins_with("msgstr"):
				var index: int = int(target.substr(6))
				var forms: PackedStringArray = current["msgstr"]
				_set_form(current, index, forms[index] + more)
			elif not target.is_empty():
				current[target] = str(current[target]) + more
	_start(entries, current, false, false)
	var kept: Array[Dictionary] = []
	for entry: Dictionary in entries:
		var msgstr: PackedStringArray = entry["msgstr"]
		if str(entry["msgid"]).is_empty() and str(entry["context"]).is_empty():
			if header.is_empty() and not bool(entry["obsolete"]):
				header = msgstr[0] if msgstr.size() > 0 else ""
			continue
		kept.append(entry)
	return {"header": header, "entries": kept}


static func _start(
	entries: Array[Dictionary], current: Dictionary, obsolete: bool, fuzzy: bool
) -> Dictionary:
	if not current.is_empty() and bool(current["has_id"]):
		entries.append(current)
	return {
		"msgid": "",
		"plural": "",
		"context": "",
		"msgstr": PackedStringArray(),
		"obsolete": obsolete,
		"fuzzy": fuzzy,
		"has_id": false,
		"refs": PackedStringArray(),
	}


static func _set_form(entry: Dictionary, index: int, text: String) -> void:
	if entry.is_empty():
		return
	var forms: PackedStringArray = entry["msgstr"]
	while forms.size() <= index:
		forms.append("")
	forms[index] = text
	entry["msgstr"] = forms


static func _unquote(text: String) -> String:
	var value: String = text.strip_edges()
	if value.length() >= 2 and value.begins_with('"') and value.ends_with('"'):
		return value.substr(1, value.length() - 2).c_unescape()
	return ""


## One value of a PO header, such as "Language" or "Plural-Forms".
static func header_value(header: String, name: String) -> String:
	for line: String in header.split("\n"):
		var split: int = line.find(":")
		if split > 0 and line.substr(0, split).strip_edges() == name:
			return line.substr(split + 1).strip_edges()
	return ""


## Translations of a parsed PO file: key -> msgstr forms (active entries).
static func translations(parsed: Dictionary) -> Dictionary:
	var found: Dictionary = {}
	for entry: Dictionary in parsed["entries"]:
		if not bool(entry["obsolete"]):
			found[key_of(str(entry["context"]), str(entry["msgid"]))] = entry["msgstr"]
	return found


static func _has_text(forms: PackedStringArray) -> bool:
	for form: String in forms:
		if not form.is_empty():
			return true
	return false


## `existing` PO text brought up to date with `entries`: its header and every
## translation kept, new text added untranslated, text no longer in the game
## kept at the end as `#~` so nothing a translator wrote is lost.
static func merge(existing: String, entries: Array[Dictionary]) -> String:
	var parsed: Dictionary = parse(existing)
	var header: String = parsed["header"]
	var forms: int = plural_count(header_value(header, "Plural-Forms"))
	var known: Dictionary = {}
	for entry: Dictionary in parsed["entries"]:
		var key: String = key_of(str(entry["context"]), str(entry["msgid"]))
		if not bool(entry["obsolete"]) or not known.has(key):
			known[key] = entry
	var out: String = ""
	for line: String in existing.replace("\r\n", "\n").split("\n"):
		if line.begins_with("#") and not line.begins_with("#:") and not line.begins_with("#~"):
			out += line + "\n"
		else:
			break
	out += header_text(header.trim_suffix("\n").split("\n"))
	var used: Dictionary = {}
	for entry: Dictionary in entries:
		var key: String = key_of(str(entry["context"]), str(entry["msgid"]))
		used[key] = true
		var merged: Dictionary = entry.duplicate()
		if known.has(key):
			var old: Dictionary = known[key]
			merged["msgstr"] = old["msgstr"]
			if bool(old["fuzzy"]):
				out += "\n#, fuzzy"
		out += "\n" + entry_text(merged, forms, true)
	for key: String in known:
		var old: Dictionary = known[key]
		if not used.has(key) and _has_text(old["msgstr"]):
			out += "\n" + entry_text(old, forms, false, "#~ ")
	return out


## The entries as a CSV Godot's CSV translation importer reads: a `keys`
## column of message ids, `?context` and `?plural` when needed, then one
## column per locale (`en` first). `known` maps locale -> translations().
static func csv(
	entries: Array[Dictionary], locales: PackedStringArray, known: Dictionary
) -> String:
	var has_context: bool = false
	var has_plural: bool = false
	for entry: Dictionary in entries:
		has_context = has_context or not str(entry["context"]).is_empty()
		has_plural = has_plural or not str(entry["plural"]).is_empty()
	var columns: PackedStringArray = PackedStringArray(["keys"])
	if has_context:
		columns.append("?context")
	if has_plural:
		columns.append("?plural")
	columns.append_array(locales)
	var out: String = _csv_row(columns)
	for entry: Dictionary in entries:
		var key: String = key_of(str(entry["context"]), str(entry["msgid"]))
		var plural: String = str(entry["plural"])
		var rows: int = 1
		var per_locale: Dictionary = {}
		for locale: String in locales:
			var forms: PackedStringArray = PackedStringArray()
			if locale == SOURCE_LOCALE:
				forms.append(str(entry["msgid"]))
				if not plural.is_empty():
					forms.append(plural)
			elif (known.get(locale, {}) as Dictionary).has(key):
				forms = (known[locale] as Dictionary)[key]
			per_locale[locale] = forms
			if not plural.is_empty():
				rows = maxi(rows, maxi(forms.size(), plural_count(plural_rule(locale))))
		for row: int in rows:
			var cells: PackedStringArray = PackedStringArray(
				[str(entry["msgid"]) if row == 0 else ""]
			)
			if has_context:
				cells.append(str(entry["context"]) if row == 0 else "")
			if has_plural:
				cells.append(plural if row == 0 else "")
			for locale: String in locales:
				var forms: PackedStringArray = per_locale[locale]
				var text: String = forms[row] if row < forms.size() else ""
				cells.append(text.replace("\\", "\\\\"))
			out += _csv_row(cells)
	return out


static func _csv_row(cells: PackedStringArray) -> String:
	var quoted: PackedStringArray = PackedStringArray()
	for cell: String in cells:
		if cell.contains(",") or cell.contains('"') or cell.contains("\n"):
			quoted.append('"%s"' % cell.replace('"', '""'))
		else:
			quoted.append(cell)
	return ",".join(quoted) + "\n"
