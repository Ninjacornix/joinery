@tool
extends VBoxContainer
## Messages waiting for Journeyman, oldest first, at the end of the transcript: one "Queued"
## caption over quiet cards (sidebar_queued_card.gd). It holds at most [constant MAX]. After a
## failed or stopped turn it is paused: nothing goes out until the user presses "Send queued
## message" beside the caption. The dock decides when to send ([method take]).

## A card was added; the transcript scales its text and follows it.
signal added(card: Control)
## Edit on a card: the dock puts its text and targets back in the message box.
signal edit_requested(card: Control)
## "Send queued message" resumed a paused queue.
signal send_requested

const CARD := preload("sidebar_queued_card.gd")
const SLASH := preload("slash_commands.gd")
const UI := preload("ui_components.gd")
const UI_MOTION := preload("ui_motion.gd")
## Messages that can wait at once.
const MAX := 5

var paused := false
var heading: Label
var send: Button
var _list: VBoxContainer
var _cards: Array[Control] = []


func _init() -> void:
	theme_type_variation = "JoineryTight"
	accessibility_name = "Queued messages"
	hide()
	# The caption sits on the transcript's text inset, like notices.
	var inset := MarginContainer.new()
	inset.theme_type_variation = "JoineryInset"
	add_child(inset)
	var head := HBoxContainer.new()
	head.theme_type_variation = "JoineryRow"
	# One height with or without "Send queued message", so the cards never shift.
	UI.bind_height(head, "sm", false)
	inset.add_child(head)
	heading = UI.label("Queued", "JoineryCaption")
	heading.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	heading.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	head.add_child(heading)
	send = UI.link("Send queued message")
	send.accessibility_name = "Send queued message"
	send.tooltip_text = "The last reply stopped, so the queue waits for you"
	UI.bind_height(send, "sm")
	send.hide()
	send.pressed.connect(_on_send)
	head.add_child(send)
	_list = VBoxContainer.new()
	_list.theme_type_variation = "JoineryTight"
	add_child(_list)


func size_queued() -> int:
	return _cards.size()


func is_full() -> bool:
	return _cards.size() >= MAX


## Queue [param text] with its [param targets]. Returns false when the queue is full.
func push(text: String, targets: Array) -> bool:
	if is_full():
		return false
	var card := CARD.new(text, targets)
	card.remove_pressed.connect(remove.bind(card))
	card.edit_pressed.connect(func() -> void: edit_requested.emit(card))
	_cards.append(card)
	_list.add_child(card)
	if not visible:
		UI_MOTION.fade_in(self)
	added.emit(card)
	UI_MOTION.fade_in(card)
	_sync()
	return true


## The oldest message as {text, targets}, taken out of the queue ({} when empty).
func take() -> Dictionary:
	if _cards.is_empty():
		return {}
	var card: Control = _cards[0]
	var entry := {"text": str(card.get("text")), "targets": card.get("targets")}
	remove(card)
	return entry


## Take [param card] away at once; it fades out after leaving the list.
func remove(card: Control) -> void:
	if not _cards.has(card):
		return
	_cards.erase(card)
	if _cards.is_empty():
		paused = false
	_sync()
	UI_MOTION.exit(card, null, _forget.bind(card))


## Pause after a failed or stopped turn (only when something waits).
func pause() -> void:
	paused = not _cards.is_empty()
	_sync()


func resume() -> void:
	paused = false
	_sync()


## Whether [param entry] waits in the queue while Journeyman is busy. Editor commands (/art …)
## run at once, and unknown commands only get a notice; messages and /feature … wait.
static func waits(entry: String) -> bool:
	if not entry.begins_with("/"):
		return true
	var known := SLASH.COMMANDS.has(entry.split(" ")[0].to_lower())
	return known and not SLASH.is_local(entry)


## The queued texts, oldest first.
func texts() -> PackedStringArray:
	var found := PackedStringArray()
	for card: Control in _cards:
		found.append(str(card.get("text")))
	return found


## The card of the oldest message, or null.
func first() -> Control:
	return _cards[0] if not _cards.is_empty() else null


## The card at [param index] (negative counts from the newest), or null.
func card_at(index: int) -> Control:
	if index < -_cards.size() or index >= _cards.size():
		return null
	return _cards[index]


func _on_send() -> void:
	resume()
	send_requested.emit()


func _forget(card: Control) -> void:
	if is_instance_valid(card):
		card.queue_free()
	if _cards.is_empty():
		hide()


func _sync() -> void:
	send.visible = paused
