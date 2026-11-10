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

signal submitted(text: String)
signal stop_pressed
## A target chip was pressed: open [param path] where it is made.
signal target_opened(path: String)

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const INPUT_SCRIPT := preload("message_input.gd")
const OPTIONS_SCRIPT := preload("composer_options.gd")
const CHIP_SCRIPT := preload("target_chip.gd")

var chips: HFlowContainer
var prompt: TextEdit
var options: MenuButton
var send: Button
var stop: Button
var _style: StyleBoxFlat
var _focus_tween: Tween
var _focused := false
var _busy := false
var _locked := false
## "connected", "connecting", "error" or "none", as the dock reports it.
var _connection := "connected"
var _restyling := false
## What the next message is about, as the chips show it.
var _targets: Array[Dictionary] = []
## The dock's placeholder for the current state ("Queue a message…"), "" for the default.
var _waiting := ""


func _init() -> void:
	var body := VBoxContainer.new()
	body.theme_type_variation = "JoineryTight"
	add_child(body)
	chips = HFlowContainer.new()
	chips.hide()
	body.add_child(chips)
	prompt = INPUT_SCRIPT.new()
	prompt.accessibility_name = "Message to Journeyman"
	prompt.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	prompt.text_submitted.connect(_on_enter)
	prompt.focus_entered.connect(_set_focus.bind(true))
	prompt.focus_exited.connect(_set_focus.bind(false))
	prompt.text_changed.connect(_sync_send)
	prompt.text_set.connect(_sync_send)
	body.add_child(prompt)
	var controls := HBoxContainer.new()
	controls.theme_type_variation = "JoineryRowTight"
	body.add_child(controls)
	options = OPTIONS_SCRIPT.new()
	options.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	controls.add_child(options)
	var push := Control.new()
	push.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	push.mouse_filter = Control.MOUSE_FILTER_IGNORE
	controls.add_child(push)
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
	set_placeholder()
	_sync_send()


func _notification(what: int) -> void:
	if what == NOTIFICATION_THEME_CHANGED:
		_restyle()


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


func _sync_send() -> void:
	send.disabled = _locked or _connection != "connected" or prompt.text.strip_edges().is_empty()
	options.disabled = _busy or _connection != "connected"
	options.focus_mode = Control.FOCUS_NONE if options.disabled else Control.FOCUS_ALL


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


# --- Targets -----------------------------------------------------------------------------------


func target_snapshot() -> Array[Dictionary]:
	return _targets.duplicate(true)


func set_targets(targets: Array[Dictionary]) -> void:
	_targets = targets.duplicate(true)
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
