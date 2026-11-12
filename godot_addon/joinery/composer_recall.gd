@tool
extends RefCounted
## Message history in the composer, as in a shell: Up with the caret on the field's first row
## brings back the message sent before, further Ups go further back and Down comes forward.
## Down past the newest, or Escape, puts back the draft that was in the field before, so a
## draft is never lost. In a message of several rows the arrows move the caret as usual until it
## reaches the first row (Up) or the last (Down).
##
## The history is the chat's own: the requests in the transcript, oldest first, so a saved chat
## that is opened again recalls what was sent in it. Commands that run in the sidebar ("/doctor")
## are never posted to the chat, so they are not recalled either; type `/` for those. A message
## sent twice in a row comes back once.
##
## A recalled message can be edited and sent: the edited text goes as a new message and the
## history keeps the original. Edits stay while browsing (Up and back down shows them again)
## and are dropped when browsing ends. Nothing is recalled while a menu over the field has the
## arrow keys ([member blocked]) or while the field is locked.

## The chat's sent messages, oldest first. Unset, there are none.
var source := Callable()
## True while a menu over the field (the `/` commands, the options) takes the arrow keys.
var blocked := Callable()
var _field: TextEdit
## The history while browsing, oldest first.
var _entries := PackedStringArray()
## The message on show, or -1 when not browsing.
var _index := -1
## What was in the field before browsing began.
var _draft := ""
## Edits to recalled messages while browsing: index -> text.
var _edits: Dictionary = {}
var _applying := false


func _init(field: TextEdit) -> void:
	_field = field
	# Text set from outside (sent and cleared, a queued message put back) ends browsing.
	field.text_set.connect(_on_text_set)


## The requests in [param transcript] (chat_history.gd), oldest first.
static func sent_in(transcript: Object) -> Array[String]:
	var sent: Array[String] = []
	var cards: Variant = transcript.get("_cards") if is_instance_valid(transcript) else null
	if not cards is Array:
		return sent
	for card: Variant in cards:
		if card is Object and is_instance_valid(card):
			sent.append(str((card as Object).get("request")))
	return sent


## The messages Up recalls, oldest first: [member source] without blanks, and a message sent
## twice in a row once.
func history() -> PackedStringArray:
	var entries := PackedStringArray()
	var sent: Variant = source.call() if source.is_valid() else []
	if not sent is Array:
		return entries
	for item: Variant in sent:
		var text := str(item).strip_edges()
		if not text.is_empty() and (entries.is_empty() or entries[-1] != text):
			entries.append(text)
	return entries


## Whether a recalled message is on show.
func browsing() -> bool:
	return _index >= 0


## Use [param event] when it moves through the history; true when it did.
func handle(event: InputEvent) -> bool:
	var key := event as InputEventKey
	if (
		key == null
		or not key.pressed
		or key.shift_pressed
		or key.ctrl_pressed
		or key.alt_pressed
		or key.meta_pressed
	):
		return false
	match key.keycode:
		KEY_UP:
			return _older()
		KEY_DOWN:
			return _newer()
		KEY_ESCAPE:
			var leave := browsing() and not key.echo
			if leave:
				_back_to_draft()
			return leave
	return false


func _older() -> bool:
	if not _free() or not _on_row(true):
		return false
	if not browsing():
		_entries = history()
		if _entries.is_empty():
			return false
		_draft = _field.text
		_edits.clear()
		_show(_entries.size() - 1, true)
		return true
	# At the oldest the key stays here, so the field keeps its focus and caret.
	if _index > 0:
		_keep_edit()
		_show(_index - 1, true)
	return true


func _newer() -> bool:
	if not browsing() or not _free() or not _on_row(false):
		return false
	_keep_edit()
	if _index + 1 < _entries.size():
		_show(_index + 1, false)
	else:
		_back_to_draft()
	return true


## The field takes the keys: editable, one caret, no selection, no menu over it.
func _free() -> bool:
	if not _field.editable or _field.get_caret_count() > 1 or _field.has_selection():
		return false
	return not (blocked.is_valid() and bool(blocked.call()))


## The caret is on the field's first visual row ([param first]) or its last.
func _on_row(first: bool) -> bool:
	var line := 0 if first else _field.get_line_count() - 1
	var row := 0 if first else _field.get_line_wrap_count(line)
	return _field.get_caret_line() == line and _field.get_caret_wrap_index() == row


func _keep_edit() -> void:
	if browsing() and _field.text != _entries[_index]:
		_edits[_index] = _field.text


## Show message [param index]. Coming from below ([param up]) the caret ends its first line, so
## the next Up goes on back; from above it ends the text, so the next Down goes on forward.
func _show(index: int, up: bool) -> void:
	_index = index
	_put(str(_edits.get(index, _entries[index])), up)


func _back_to_draft() -> void:
	var draft := _draft
	_stop()
	_put(draft, false)


func _put(text: String, up: bool) -> void:
	_applying = true
	_field.text = text
	_applying = false
	var line := 0 if up else _field.get_line_count() - 1
	_field.set_caret_line(line)
	_field.set_caret_column(_field.get_line(line).length())


func _stop() -> void:
	_index = -1
	_draft = ""
	_edits.clear()
	_entries = PackedStringArray()


func _on_text_set() -> void:
	if not _applying:
		_stop()
