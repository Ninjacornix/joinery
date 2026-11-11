extends SceneTree
## review_flow.gd: a live edit's review waits for Joinery's in_review, a dropped turn drops
## the preview, a file review opens on file_preview, and renders reach the panel and card.

const FLOW := preload("res://addons/joinery/review_flow.gd")
const CARD := preload("res://addons/joinery/sidebar_change_card.gd")

var _failed := false


## The chat's signals the flow follows (chat_bridge.gd).
class FakeChat:
	extends Node
	signal file_preview(event: Dictionary)
	signal turn_state(event: Dictionary)
	signal review_media(event: Dictionary)


## The live preview's pending state (scene_edits.gd).
class FakeEdits:
	extends RefCounted
	var waiting := true

	func pending() -> bool:
		return waiting


## The request's card in the sidebar (sidebar_change_card.gd).
class FakeCard:
	extends RefCounted
	var state := "ready"
	var media: Array = []

	func set_ready(items: Array) -> void:
		media = items


## The sidebar (journeyman_dock.gd).
class FakeDock:
	extends RefCounted
	var card := FakeCard.new()

	func _card() -> Object:
		return card


## The review panel (review_panel.gd).
class FakePanel:
	extends RefCounted
	var media: Array = []

	func update_media(items: Array) -> void:
		media = items


## The Review tab (review_tab.gd).
class FakeTab:
	extends RefCounted
	var panel := FakePanel.new()

	func detail() -> Object:
		return panel


## The plugin's review entry points (plugin.gd).
class FakePlugin:
	extends RefCounted
	var calls: Array = []
	var _file_review: Dictionary = {}
	var _edits := FakeEdits.new()
	var _dock := FakeDock.new()
	var _review_tab := FakeTab.new()

	func _open_review(summary: String) -> void:
		calls.append(["open", summary])

	func _reject_scene_edit(message: String, kind: String) -> void:
		_edits.waiting = false
		calls.append(["reject", message, kind])

	func _call_dock(method: String, args: Array) -> void:
		calls.append([method, args[0]])

	func _show_edit_review_panel() -> void:
		calls.append(["panel"])


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _run() -> void:
	var chat := FakeChat.new()
	root.add_child(chat)
	var plugin := FakePlugin.new()
	var flow: Node = FLOW.new()
	root.add_child(flow)
	flow.setup(plugin, chat)

	flow.live_ready("Rename the lamp")
	_check(flow.live_waiting(), "The live edit waits for its review")
	chat.turn_state.emit({"state": "working"})
	chat.turn_state.emit({"state": "submitting"})
	_check(plugin.calls.is_empty(), "Nothing opens before the change is in review")
	chat.turn_state.emit({"state": "in_review"})
	_check(plugin.calls == [["open", "Rename the lamp"]], "in_review opens the live review")
	_check(not flow.live_waiting(), "Opened once")

	plugin.calls.clear()
	plugin._edits.waiting = true
	flow.live_ready("Add a chair")
	chat.turn_state.emit({"state": "idle", "reason": "failed"})
	_check(plugin.calls == [["reject", "", "info"]], "A failed turn drops the live preview")

	plugin.calls.clear()
	chat.turn_state.emit({"state": "in_review"})
	_check(plugin.calls.is_empty(), "No stale review opens later")

	plugin._edits.waiting = true
	flow.live_ready("Rename the lamp")
	var media := [{"path": "art/crate.blend", "media": "image", "after": "/x/after.png"}]
	chat.file_preview.emit(
		{"transaction_id": "t1", "summary": "Crate", "media": media, "media_pending": true}
	)
	_check(plugin._file_review.get("transaction_id") == "t1", "The file review is held")
	_check(
		plugin.calls == [["show_file_review", "Crate"], ["panel"]], "The file review opens at once"
	)
	plugin.calls.clear()
	chat.turn_state.emit({"state": "in_review", "transaction_id": "t1"})
	_check(plugin.calls.is_empty(), "With a file review, the live edit rides along with it")

	var rendered := [{"path": "art/crate.blend", "media": "image", "after": "/x/done.png"}]
	chat.review_media.emit({"transaction_id": "other", "media": []})
	_check(plugin._review_tab.panel.media.is_empty(), "Renders of another change are ignored")
	chat.review_media.emit({"transaction_id": "t1", "media": rendered})
	_check(plugin._review_tab.panel.media == rendered, "The panel takes the renders")
	_check(plugin._dock.card.media == rendered, "The card takes its thumbnail")
	_check(plugin._file_review["media"] == rendered, "The held review has them too")

	# A review opens before its renders exist: the real card waits for review_media quietly.
	var card: VBoxContainer = CARD.new("Weather the crate")
	root.add_child(card)
	card.set_ready([{"path": "art/crate.blend", "media": "image", "after": "/x/not_yet.png"}])
	_check(card._thumb.texture == null, "No thumbnail before the render exists")
	_check(not card._ready_row.visible, "No empty picture row while the render runs")
	card.queue_free()

	flow.queue_free()
	chat.queue_free()
	await process_frame
	print("review flow: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)
