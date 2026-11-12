@tool
extends RefCounted
## "Remember this" on a chat reply (ADR 0077). A reply's right-click menu (Copy, Select All)
## gets one more item; it puts "/remember " and the selected words, or the reply's first line,
## in the message box, where the user can trim it before sending. Sent, it becomes one line
## in JOURNEYMAN.md as a change they review (sidebar_memory.gd).

## The menu item's id, clear of RichTextLabel's own (Copy 0, Select All 1).
const MENU_ID := 77
## A remembered line is one short line (joinery.memory.ENTRY_LIMIT).
const LIMIT := 300


## Add "Remember this" to [param label]'s menu; choosing it emits [param chosen] with the text.
static func offer(label: RichTextLabel, chosen: Signal) -> void:
	label.context_menu_enabled = true
	var menu := label.get_menu()
	menu.add_separator()
	menu.add_item("Remember this", MENU_ID)
	menu.id_pressed.connect(
		func(id: int) -> void:
			if id == MENU_ID:
				chosen.emit(words(label.get_selected_text(), label.get_parsed_text()))
	)


## What to remember: the selection, else the reply's first non-empty line, kept short.
static func words(selected: String, whole: String) -> String:
	var source := selected.strip_edges()
	if source.is_empty():
		for line: String in whole.split("\n", false):
			if not line.strip_edges().is_empty():
				source = line.strip_edges()
				break
	var line := " ".join(source.replace("\n", " ").replace("\t", " ").split(" ", false))
	return line.left(LIMIT).strip_edges()
