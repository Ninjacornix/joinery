extends RefCounted
## The "Language" settings page: the list of languages, each named in its own
## language, and in development builds the pseudo-locale switch with a short
## explanation. Built with the core UiStyle; works with the ui_* actions alone.


static func build(language: Node) -> Control:
	var column: VBoxContainer = VBoxContainer.new()
	column.name = "LanguageSettings"

	var choice: OptionButton = OptionButton.new()
	choice.name = "locale_code"
	choice.focus_mode = Control.FOCUS_ALL
	choice.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	choice.custom_minimum_size = Vector2(UiStyle.px(320), UiStyle.px(64))
	# Language names are shown as written, never translated or pseudo-localised.
	choice.auto_translate_mode = Node.AUTO_TRANSLATE_MODE_DISABLED
	var codes: PackedStringArray = language.call(&"available")
	var current: String = language.call(&"current")
	for index: int in codes.size():
		choice.add_item(str(language.call(&"display_name", codes[index])), index)
		choice.set_item_metadata(index, codes[index])
		if codes[index] == current:
			choice.select(index)
	choice.item_selected.connect(
		func(index: int) -> void: language.call(&"set_locale", str(choice.get_item_metadata(index)))
	)
	var line: HBoxContainer = UiStyle.row("Language", choice)
	line.name = "locale_code_row"
	column.add_child(line)

	if OS.is_debug_build():
		var pseudo: CheckButton = UiStyle.toggle(bool(language.call(&"is_pseudo")))
		pseudo.name = "locale_pseudo"
		pseudo.toggled.connect(func(on: bool) -> void: language.call(&"set_pseudo", on))
		var pseudo_line: HBoxContainer = UiStyle.row("Pseudo-locale", pseudo)
		pseudo_line.name = "locale_pseudo_row"
		column.add_child(pseudo_line)
		var note: Label = UiStyle.hint(
			"Makes every text longer and accented, to spot text that does not fit."
		)
		note.name = "PseudoHint"
		note.custom_minimum_size = Vector2(UiStyle.px(760), 0)
		column.add_child(note)
		if codes.size() < 2:
			var more: Label = UiStyle.hint(
				"Only English so far. Add a language as a .po file in locale/."
			)
			more.name = "AddHint"
			more.custom_minimum_size = Vector2(UiStyle.px(760), 0)
			column.add_child(more)
	return column
