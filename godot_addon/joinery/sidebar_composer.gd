@tool
extends PanelContainer
## The composer: selected-target chips, the message field, the options picker, and Send. While
## the assistant works, Stop takes Send's slot (an icon crossfade with a little blur); the
## field stays editable but Enter waits. While a change waits for review the field is locked.
## Send is live only when it can act: connected, unlocked and with text in the field; otherwise
## it takes its muted disabled look (so the banner's button, when there is one, is the only
## accent action). The reply-mode picker rests with Send: it is live only while connected and
## idle. The dock also shows Stop while a request is on its way, before the
## assistant takes it. The draft stays in the field.
## Its border tweens to the accent while the field has focus.

signal submitted(text: String)
signal stop_pressed

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const INPUT_SCRIPT := preload("message_input.gd")
const OPTIONS_SCRIPT := preload("composer_options.gd")

var chips: HFlowContainer
var prompt: TextEdit
var options: MenuButton
var send: Button
var stop: Button
var agent_name := "Codex"
var _style: StyleBoxFlat
var _focus_tween: Tween
var _focused := false
var _busy := false
var _locked := false
## "connected", "connecting", "error" or "none", as the dock reports it.
var _connection := "connected"
var _restyling := false


func _init() -> void:
	var body := VBoxContainer.new()
	body.theme_type_variation = "JoineryTight"
	add_child(body)
	chips = HFlowContainer.new()
	chips.hide()
	body.add_child(chips)
	prompt = INPUT_SCRIPT.new()
	prompt.accessibility_name = "Message to your assistant"
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
	set_placeholder()


## Locked while a change waits for review or a check runs.
func set_locked(locked: bool) -> void:
	_locked = locked
	prompt.editable = not locked
	_sync_send()
	set_placeholder()


## Follow the assistant connection: Send is live only while connected.
func set_connection(state: String) -> void:
	_connection = state
	_sync_send()
	set_placeholder()


## Placeholder for the current state; [param waiting] overrides it (for example during a review).
func set_placeholder(waiting := "") -> void:
	if not waiting.is_empty():
		prompt.placeholder_text = waiting
	elif not _locked and _connection == "none":
		prompt.placeholder_text = "Connect an assistant to start"
	elif not _locked:
		var name := agent_name if not agent_name.is_empty() else "your assistant"
		prompt.placeholder_text = "Message " + name


## Mark Stop as pressed and waiting for the reply to end.
func stopping() -> void:
	stop.disabled = true


func _on_enter(text: String) -> void:
	if _busy or _locked or _connection != "connected":
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
	var square := send.get_combined_minimum_size()
	options.custom_minimum_size.y = square.y
	stop.custom_minimum_size = square
