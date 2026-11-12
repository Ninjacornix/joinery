@tool
extends PanelContainer
## The composer: selected-target chips, the message field, the options picker, and Send. While
## Journeyman works, Stop takes Send's slot (an icon crossfade with a little blur); the
## field stays editable and Enter still submits, so the dock can queue the message. While a
## check runs the field is locked.
## Send is live only when it can act: connected, unlocked and with text in the field; otherwise
## it takes its muted disabled look (so the banner's button, when there is one, is the only
## accent action). The reply-mode picker rests with Send: it is live only while connected and
## idle. The dock also shows Stop while a request is on its way, before Journeyman takes
## it. The draft stays in the field.
## Its border tweens to the accent while the field has focus.
##
## While Journeyman works, "Run alongside" sits beside Stop when the field has text and the
## project allows parallel tasks ([method set_alongside]): it starts the message as its own task
## beside the request (ADR 0036), where Enter would queue it.
##
## Images go with the message as thumbnails above the chips (sidebar_attachments.gd): the eye
## button ("Share my view") adds a screenshot of the editor viewport the user is looking at;
## the paperclip opens a file dialog; pasting an image (Cmd/Ctrl+V) or image files, and dropping
## image files on the composer (from the file manager or the FileSystem dock), add them too.
## Images are reference context for Journeyman, never assets. They ride in the targets list
## ([method target_snapshot]), so a queued or retried message keeps them, and they leave the
## composer once the message is sent or queued.
##
## In the editor, Godot copies files dropped from the file manager into the project. Over the
## composer that would turn a reference into a project file, so while the composer is in the
## editor it takes the window's file drops first ([method _guard_drops]): drops on the composer
## become attachments, and every other drop goes on to the editor as before.
##
## Up and Down bring back the messages sent in this chat (composer_recall.gd, from the
## transcript set with [method recall_from]). While a menu over the composer is open (the `/`
## commands, which register with [method add_menu], or the options) the arrows stay its own.

signal submitted(text: String)
signal stop_pressed
## "Run alongside": start [param text] as a parallel task now instead of queueing it.
signal alongside(text: String)
## "Try 3 ways" in the options menu: make [param text] three ways side by side (ADR 0042).
signal variants(text: String)
## A target chip was pressed: open [param path] where it is made.
signal target_opened(path: String)
## Something to tell the user in the status footer ("Share my view" failed, the limit…).
signal notice(text: String)

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const INPUT_SCRIPT := preload("message_input.gd")
const OPTIONS_SCRIPT := preload("composer_options.gd")
const CHIP_SCRIPT := preload("target_chip.gd")
const ATTACHMENTS := preload("chat_attachments.gd")
const ATTACHMENT_ROW := preload("sidebar_attachments.gd")
const EDITOR_VIEW := preload("editor_view.gd")
const RECALL := preload("composer_recall.gd")

var chips: HFlowContainer
## Holds [member chips] without passing their width up: a long name ends in an ellipsis inside
## the gutter instead of widening the sidebar.
var chip_area: Control
var attachments: ATTACHMENT_ROW
var prompt: TextEdit
var options: MenuButton
var send: Button
var stop: Button
## "Run alongside", beside Stop while Journeyman works.
var alongside_button: Button
## "Share my view": a screenshot of the editor viewport goes with the next message.
var view: Button
## Attach images from disk (a file dialog).
var attach: Button
## Captures the editor viewport (editor_view.gd's result); tests replace it.
var view_provider := Callable(EDITOR_VIEW, "capture")
var _style: StyleBoxFlat
var _focus_tween: Tween
var _focused := false
var _busy := false
var _locked := false
## The project allows parallel tasks, and another one can start now.
var _alongside_ok := false
## "connected", "connecting", "error" or "none", as the dock reports it.
var _connection := "connected"
var _restyling := false
## What the next message is about, as the chips show it.
var _targets: Array[Dictionary] = []
## The dock's placeholder for the current state ("Queue a message…"), "" for the default.
var _waiting := ""
var _dialog: FileDialog
## The editor's own handlers of the window's file drops, while the composer takes them first.
var _editor_drops: Array[Callable] = []
## The row under the field: attach, view, the model picker, Run alongside, Send/Stop.
var _controls: HBoxContainer
## Menus shown over the composer that take the arrow keys while open ([method add_menu]).
var _menus: Array[Control] = []


func _init() -> void:
	var body := VBoxContainer.new()
	body.theme_type_variation = "JoineryTight"
	add_child(body)
	attachments = ATTACHMENT_ROW.new()
	attachments.changed.connect(_sync_send)
	body.add_child(attachments)
	chip_area = Control.new()
	chip_area.mouse_filter = Control.MOUSE_FILTER_IGNORE
	chip_area.hide()
	chip_area.resized.connect(_fit_chips)
	body.add_child(chip_area)
	chips = HFlowContainer.new()
	chips.hide()
	chips.minimum_size_changed.connect(_fit_chips)
	chip_area.add_child(chips)
	prompt = INPUT_SCRIPT.new()
	prompt.accessibility_name = "Message to Journeyman"
	prompt.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	prompt.text_submitted.connect(_on_enter)
	prompt.focus_entered.connect(_set_focus.bind(true))
	prompt.focus_exited.connect(_set_focus.bind(false))
	prompt.text_changed.connect(_sync_send)
	prompt.text_set.connect(_sync_send)
	prompt.image_pasted.connect(func(image: Image) -> void: attach_image(image, "Pasted image"))
	prompt.files_pasted.connect(attach_files)
	body.add_child(prompt)
	var controls := HBoxContainer.new()
	controls.theme_type_variation = "JoineryRowTight"
	controls.resized.connect(_fit_controls, CONNECT_DEFERRED)
	body.add_child(controls)
	_controls = controls
	attach = UI.icon_button(
		"attach", "Attach images (PNG, JPEG or WebP). You can also paste or drop them here."
	)
	attach.accessibility_name = "Attach images"
	attach.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	UI_MOTION.press_feedback(attach)
	attach.pressed.connect(open_file_dialog)
	controls.add_child(attach)
	view = UI.icon_button("view", "Share my view: Journeyman sees this viewport with your message")
	view.accessibility_name = "Share my view"
	view.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	UI_MOTION.press_feedback(view)
	view.pressed.connect(share_view)
	controls.add_child(view)
	options = OPTIONS_SCRIPT.new()
	options.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	options.try_ways.connect(func() -> void: variants.emit(prompt.text.strip_edges()))
	controls.add_child(options)
	var push := Control.new()
	push.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	push.mouse_filter = Control.MOUSE_FILTER_IGNORE
	controls.add_child(push)
	alongside_button = UI.quiet("Run alongside")
	alongside_button.tooltip_text = (
		"Start this as its own task now, beside the request Journeyman is working on. "
		+ "Its change is reviewed on its own."
	)
	alongside_button.accessibility_name = "Run this message alongside as a parallel task"
	alongside_button.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	UI_MOTION.press_feedback(alongside_button)
	alongside_button.pressed.connect(_on_alongside)
	alongside_button.hide()
	controls.add_child(alongside_button)
	# Send and Stop share one slot: a MarginContainer stacks its children.
	var slot := MarginContainer.new()
	slot.size_flags_vertical = Control.SIZE_SHRINK_END
	controls.add_child(slot)
	send = Button.new()
	send.theme_type_variation = "JoinerySendButton"
	send.accessibility_name = "Send message"
	send.tooltip_text = "Send (Enter). Shift+Enter adds a line."
	send.focus_mode = Control.FOCUS_ALL
	UI.bind_icon(send, "send")
	UI_MOTION.press_feedback(send)
	send.pressed.connect(func() -> void: _on_enter(prompt.text))
	slot.add_child(send)
	stop = Button.new()
	stop.theme_type_variation = "JoineryStopButton"
	stop.accessibility_name = "Stop the reply"
	stop.tooltip_text = "Stop"
	stop.focus_mode = Control.FOCUS_ALL
	stop.hide()
	UI.bind_icon(stop, "stop")
	UI_MOTION.press_feedback(stop)
	stop.pressed.connect(func() -> void: stop_pressed.emit())
	slot.add_child(stop)
	var recall: RECALL = prompt.get("recall")
	recall.blocked = menu_open
	set_placeholder()
	_sync_send()


func _notification(what: int) -> void:
	if what == NOTIFICATION_THEME_CHANGED:
		_restyle()


func _enter_tree() -> void:
	if Engine.is_editor_hint():
		_guard_drops(true)


func _exit_tree() -> void:
	_guard_drops(false)


## Busy: Stop replaces Send; the field stays editable, but Enter waits until the reply ends.
func set_busy(busy: bool) -> void:
	if busy == _busy:
		return
	_busy = busy
	_sync_send()
	stop.disabled = false
	if busy:
		UI_MOTION.crossfade(send, stop, true)
	else:
		UI_MOTION.crossfade(stop, send, true)
	_apply_placeholder()


## Locked while a check runs.
func set_locked(locked: bool) -> void:
	_locked = locked
	prompt.editable = not locked
	_sync_send()
	_apply_placeholder()


## Follow Journeyman's connection: Send is live only while connected.
func set_connection(state: String) -> void:
	_connection = state
	_sync_send()
	_apply_placeholder()


## Placeholder for the current state; [param waiting] overrides it until it is set again (for
## example "Queue a message…" while Journeyman works).
func set_placeholder(waiting := "") -> void:
	_waiting = waiting
	_apply_placeholder()


func _apply_placeholder() -> void:
	if not _waiting.is_empty():
		prompt.placeholder_text = _waiting
	elif not _locked and _connection == "none":
		prompt.placeholder_text = "Connect Journeyman to start"
	elif not _locked:
		prompt.placeholder_text = "Ask Journeyman…"


## Mark Stop as pressed and waiting for the reply to end.
func stopping() -> void:
	stop.disabled = true


func _on_enter(text: String) -> void:
	if _locked or _connection != "connected":
		return
	submitted.emit(text)
	# The dock took the message (sent or queued) when it cleared the field; its images went too.
	if prompt.text.is_empty() and attachments.count() > 0:
		attachments.clear()


## Add a screenshot of the editor viewport the user is looking at to the next message.
func share_view() -> void:
	if attachments.is_full():
		notice.emit("Up to %d images go with one message." % ATTACHMENTS.MAX_IMAGES)
		return
	var result: Dictionary = view_provider.call() if view_provider.is_valid() else {}
	var entry: Dictionary = result.get("entry", {})
	if not result.get("success", false) or entry.is_empty():
		notice.emit(str(result.get("message", "Couldn't capture the view.")))
		return
	attachments.add(entry)
	UI_MOTION.pulse(view)
	prompt.grab_focus()


## Attach [param image] (pasted), named [param label].
func attach_image(image: Image, label: String) -> void:
	if attachments.is_full():
		notice.emit("Up to %d images go with one message." % ATTACHMENTS.MAX_IMAGES)
		return
	var entry := ATTACHMENTS.save(image, label)
	if entry.is_empty():
		notice.emit("Couldn't attach that image.")
		return
	attachments.add(entry)


## Attach the image files at [param paths] (absolute or res://), up to the limit; files that
## are not PNG, JPEG or WebP images are skipped with a note.
func attach_files(paths: PackedStringArray) -> void:
	var problem := ""
	for path: String in paths:
		if attachments.is_full():
			problem = "Up to %d images go with one message." % ATTACHMENTS.MAX_IMAGES
			break
		var entry := ATTACHMENTS.from_file(ProjectSettings.globalize_path(path))
		if entry.has("error"):
			problem = str(entry["error"])
			continue
		attachments.add(entry)
	if not problem.is_empty():
		notice.emit(problem)


## The paperclip: choose image files on disk.
func open_file_dialog() -> void:
	if _dialog == null:
		_dialog = FileDialog.new()
		_dialog.title = "Attach images"
		_dialog.file_mode = FileDialog.FILE_MODE_OPEN_FILES
		_dialog.access = FileDialog.ACCESS_FILESYSTEM
		_dialog.filters = PackedStringArray(["*.png, *.jpg, *.jpeg, *.webp ; Images"])
		_dialog.use_native_dialog = true
		_dialog.files_selected.connect(attach_files)
		_dialog.file_selected.connect(
			func(path: String) -> void: attach_files(PackedStringArray([path]))
		)
		add_child(_dialog)
	_dialog.popup_centered_ratio(0.6)


## Image files dragged from the FileSystem dock land as attachments (the message field takes
## them the same way).
func _can_drop_data(_at: Vector2, data: Variant) -> bool:
	return not INPUT_SCRIPT.dropped_images(data).is_empty()


func _drop_data(_at: Vector2, data: Variant) -> void:
	attach_files(INPUT_SCRIPT.dropped_images(data))


## Take the window's file drops before the editor does (see the class notes), or hand them back.
func _guard_drops(on: bool) -> void:
	var window := get_tree().root if is_inside_tree() else null
	if window == null:
		return
	if on and not window.files_dropped.is_connected(_on_files_dropped):
		for connection: Dictionary in window.files_dropped.get_connections():
			var handler: Callable = connection["callable"]
			_editor_drops.append(handler)
			window.files_dropped.disconnect(handler)
		window.files_dropped.connect(_on_files_dropped)
	elif not on and window.files_dropped.is_connected(_on_files_dropped):
		window.files_dropped.disconnect(_on_files_dropped)
		for handler: Callable in _editor_drops:
			if handler.is_valid() and not window.files_dropped.is_connected(handler):
				window.files_dropped.connect(handler)
		_editor_drops.clear()


func _on_files_dropped(files: PackedStringArray) -> void:
	var over := is_visible_in_tree() and get_global_rect().has_point(get_global_mouse_position())
	route_drop(files, over)


## Files dropped from the file manager: on the composer they are attached (never copied into the
## project); anywhere else they go on to the editor.
func route_drop(files: PackedStringArray, over_composer: bool) -> void:
	if over_composer:
		attach_files(files)
		return
	for handler: Callable in _editor_drops:
		if handler.is_valid():
			handler.call(files)


## Offer "Run alongside" while Journeyman works ([param allowed]: the project allows another
## parallel task now).
func set_alongside(allowed: bool) -> void:
	_alongside_ok = allowed
	_sync_send()


func _on_alongside() -> void:
	var text := prompt.text.strip_edges()
	if text.is_empty() or not alongside_button.visible:
		return
	alongside.emit(text)


func _sync_send() -> void:
	var offer := (
		_busy
		and _alongside_ok
		and not _locked
		and _connection == "connected"
		and not prompt.text.strip_edges().is_empty()
	)
	if offer != alongside_button.visible:
		if offer:
			UI_MOTION.enter(alongside_button)
		else:
			alongside_button.hide()
		_fit_controls()
	send.disabled = _locked or _connection != "connected" or prompt.text.strip_edges().is_empty()
	options.disabled = _busy or _connection != "connected"
	options.focus_mode = Control.FOCUS_NONE if options.disabled else Control.FOCUS_ALL


## In a narrow sidebar Run alongside takes the model picker's place while it is offered; the
## picker is disabled while Journeyman works, and it comes back with the room or the offer's
## end. Strictly less than the row's width: a row that pushed the composer wider measures
## exactly its own width, so it gives the picker up rather than keeping the overflow.
func _fit_controls() -> void:
	if _controls == null:
		return
	var keep := true
	if alongside_button.visible and _controls.size.x > 0.0:
		var need := 0.0
		var count := 0
		for child: Node in _controls.get_children():
			var part := child as Control
			if part != null and (part.visible or part == options):
				need += part.get_combined_minimum_size().x
				count += 1
		need += float(_controls.get_theme_constant("separation")) * maxf(0.0, count - 1.0)
		keep = need + 0.5 < _controls.size.x
	if options.visible != keep:
		options.visible = keep


func _set_focus(on: bool) -> void:
	_focused = on
	if _style == null:
		return
	var ink := get_theme_color("accent" if on else "border_strong", "Joinery")
	if _focus_tween != null and _focus_tween.is_valid():
		_focus_tween.kill()
	if not UI_MOTION.is_enabled() or not is_inside_tree():
		_style.border_color = ink
		return
	_focus_tween = create_tween().set_trans(Tween.TRANS_CUBIC).set_ease(Tween.EASE_OUT)
	_focus_tween.tween_property(_style, "border_color", ink, UI_MOTION.duration("fast"))


func _restyle() -> void:
	if _restyling:
		return
	_restyling = true
	var source := get_theme_stylebox("panel", "JoineryComposer")
	if source is StyleBoxFlat:
		_style = source.duplicate() as StyleBoxFlat
		var role := "accent" if _focused else "border_strong"
		_style.border_color = get_theme_color(role, "Joinery")
		add_theme_stylebox_override("panel", _style)
	var gap := UI_THEME.space(self, 1)
	chips.add_theme_constant_override("h_separation", gap)
	chips.add_theme_constant_override("v_separation", gap)
	_restyling = false
	_match_heights.call_deferred()


## The picker matches Send's height so the row's controls share top and bottom lines, and Stop
## takes exactly Send's square so the swap never shifts the row.
func _match_heights() -> void:
	send.custom_minimum_size = Vector2.ONE * UI_THEME.send_px(send)
	var square := send.get_combined_minimum_size()
	options.custom_minimum_size.y = square.y
	stop.custom_minimum_size = square
	view.custom_minimum_size = square
	attach.custom_minimum_size = square


## Put a queued message back: [param text] in the field and its [param targets] as chips. A
## draft in the field stays, and the call returns false.
func restore(text: String, targets: Array) -> bool:
	if not prompt.text.strip_edges().is_empty():
		return false
	var typed: Array[Dictionary] = []
	for target: Variant in targets:
		if target is Dictionary:
			typed.append(target)
	set_targets(typed)
	prompt.text = text
	prompt.set_caret_column(text.length())
	prompt.grab_focus()
	return true


# --- History -----------------------------------------------------------------------------------


## Up and Down recall the requests in [param transcript] (chat_history.gd): the chat on screen,
## a saved one opened again included.
func recall_from(transcript: Control) -> void:
	var recall: RECALL = prompt.get("recall")
	recall.source = Callable(RECALL, "sent_in").bind(transcript)


## [param menu] opens over the composer and takes the arrow keys while it shows.
func add_menu(menu: Control) -> void:
	if not _menus.has(menu):
		_menus.append(menu)


## A menu over the composer is open: the arrows are its own, not the history's.
func menu_open() -> bool:
	if options.get_popup().visible:
		return true
	for menu: Control in _menus:
		if is_instance_valid(menu) and menu.visible:
			return true
	return false


# --- Targets -----------------------------------------------------------------------------------


## The message's targets, then its attached images (chat_attachments.gd entries).
func target_snapshot() -> Array[Dictionary]:
	var snapshot := _targets.duplicate(true)
	snapshot.append_array(attachments.entries())
	return snapshot


## Show [param targets] as chips. Images among them replace the attached ones (a queued message
## put back); a list without images (a new selection) leaves the attached images alone.
func set_targets(targets: Array[Dictionary]) -> void:
	var scene_targets: Array[Dictionary] = []
	var images: Array[Dictionary] = []
	for target: Dictionary in targets:
		if ATTACHMENTS.is_attachment(target):
			images.append(target)
		else:
			scene_targets.append(target)
	if not images.is_empty():
		attachments.set_entries(images)
	_targets = scene_targets.duplicate(true)
	for child: Node in chips.get_children():
		chips.remove_child(child)
		child.queue_free()
	# Many targets (a whole selection) show the first two and one "+N more" chip.
	var shown := _targets if _targets.size() <= 3 else _targets.slice(0, 2)
	for target: Dictionary in shown:
		var id := str(target["id"])
		var chip := CHIP_SCRIPT.build(self, target, target_opened.emit, _remove_target.bind(id))
		chip.connect("removed", func() -> void: _sync_chips.call_deferred())
		chips.add_child(chip)
	if shown.size() < _targets.size():
		var rest := _targets.slice(shown.size())
		var names: Array[String] = []
		for target: Dictionary in rest:
			names.append(str(target.get("label", target["id"])))
		var more := UI.chip("+%d more" % rest.size(), "", true)
		more.tooltip_text = ", ".join(names)
		more.remove.tooltip_text = "Remove these from this message"
		more.remove.pressed.connect(
			func() -> void:
				for target: Dictionary in rest:
					_remove_target(str(target["id"]))
		)
		more.connect("removed", func() -> void: _sync_chips.call_deferred())
		chips.add_child(more)
	_sync_chips()


## Optimistic: the target leaves the request at once; the chip row hides after it animates out.
func _remove_target(id: String) -> void:
	var remaining: Array[Dictionary] = []
	for target: Dictionary in _targets:
		if str(target["id"]) != id:
			remaining.append(target)
	_targets = remaining


func _sync_chips() -> void:
	chips.visible = not _targets.is_empty()
	chip_area.visible = chips.visible
	_fit_chips()


## Every chip fits the composer's width; the area takes the wrapped rows' height.
func _fit_chips() -> void:
	var width := chip_area.size.x
	if width <= 0.0:
		return
	for child: Node in chips.get_children():
		var chip := child as UI.Chip
		if chip != null and not is_equal_approx(chip.max_width, width):
			chip.max_width = width
	chips.position = Vector2.ZERO
	chips.size = Vector2(width, 0.0)
	chip_area.custom_minimum_size.y = chips.get_combined_minimum_size().y
