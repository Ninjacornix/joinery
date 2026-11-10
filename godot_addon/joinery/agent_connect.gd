@tool
extends CenterContainer
## "Connect Journeyman to your account": the Project Manager's first screen, also usable
## inside an editor AcceptDialog. Choosing a signed-in account continues; a fresh sign-in
## shows "Connected" on its card, fades out, then emits [signal connected]. In the Project
## Manager the fork switches to the project list on that signal; elsewhere this screen only
## emits it.

## [param agent_id] is the assistant (or API driver); [param method] how it signed in.
signal connected(agent_id: String, method: String)

const COMMAND_SCRIPT := preload("command_job.gd")
const ICON_SCRIPT := preload("agent_icon.gd")
const API_SCRIPT := preload("api_connection_form.gd")
const INDICATOR := preload("activity_indicator.gd")
const UI_THEME := preload("ui_theme.gd")
const UI_COMPONENTS := preload("ui_components.gd")
const UI_MOTION := preload("ui_motion.gd")
const AGENT_NAMES := {
	"claude": "Claude Code",
	"codex": "Codex",
	"gemini": "Gemini",
	"opencode": "opencode",
}
## Width of the content column, in multiples of the body font size.
const COLUMN_EM := 32
const HELPER_MISSING := "Joinery's helper isn't set up. Run the Joinery installer again."
const PROFILE_MISSING := "That saved API connection isn't available. Check its key in API settings."
const UNAVAILABLE := "This connection is unavailable on this platform."
## Seconds a started sign-in keeps the button in its waiting state without a result.
const SIGN_IN_WAIT := 180.0
## Opacity of the icon of an assistant row that can't be chosen yet (not installed, not
## available). The name keeps the normal text colour; the row's flat style and its status
## text say it is unavailable.
const UNAVAILABLE_ALPHA := 0.6
## Under the list while no assistant is chosen: why Continue is not available yet.
const CHOOSE_HINT := "Choose an account to continue."

var engine_home := ""
var initial_agent := ""
var initial_profile := ""
var selected_profile := ""
var _selected := ""
var _agents: Dictionary = {}
var _buttons: Dictionary = {}
## Under the selected card: why it cannot continue (unsupported, key to confirm).
var _detail: Label
## Under the selected card: what the helper said about the last action.
var _message: Label
var _continue: Button
var _login: Button
var _install: Button
var _api: Button
var _content: MarginContainer
var _choices: VBoxContainer
var _list: VBoxContainer
var _inline: VBoxContainer
var _api_form: VBoxContainer
var _page := "choices"
var _job: RefCounted
var _action := ""
var _job_agent := ""
var _accepted_keys: Array[String] = []
var _elapsed := 0.0
var _auto_redirect := true
var _redirecting := false
var _restored := false
var _chosen_by_user := false
var _celebrate := false
var _celebrating := ""
var _waiting_for := ""
var _waiting_since := 0.0
var _group := ButtonGroup.new()


## One provider row: a JoineryCardButton holding the icon tile, the name and a status line.
class Card:
	extends Button
	var row: HBoxContainer
	var title: Label
	var status: Label
	var dot: Control
	var done: TextureRect
	var indicator: Control
	var link: Button
	var tile: Control


func _ready() -> void:
	name = "JoineryAgentConnect"
	size_flags_vertical = Control.SIZE_EXPAND_FILL
	UI_THEME.watch(self)
	_content = MarginContainer.new()
	add_child(_content)
	_choices = VBoxContainer.new()
	_choices.theme_type_variation = "JoinerySections"
	_choices.size_flags_vertical = Control.SIZE_SHRINK_BEGIN
	_content.add_child(_choices)
	var header := VBoxContainer.new()
	header.theme_type_variation = "JoineryTight"
	_choices.add_child(header)
	header.add_child(
		UI_COMPONENTS.label("Connect Journeyman to your account", "JoineryDisplay", true)
	)
	header.add_child(
		UI_COMPONENTS.label(
			"Sign in with Claude Code, Codex, Gemini or opencode, or use an API key.",
			"JoineryCaption",
			true
		)
	)
	_list = VBoxContainer.new()
	_list.theme_type_variation = "JoineryRow"
	_choices.add_child(_list)
	for id: String in AGENT_NAMES:
		_add_provider(id, AGENT_NAMES[id], id)
	_inline = VBoxContainer.new()
	_inline.theme_type_variation = "JoineryTight"
	_list.add_child(_inline)
	_detail = UI_COMPONENTS.label("", "JoineryError", true)
	_inline.add_child(_detail)
	_message = UI_COMPONENTS.label("", "JoineryError", true)
	_inline.add_child(_message)
	var actions := VBoxContainer.new()
	actions.theme_type_variation = "JoineryTight"
	_choices.add_child(actions)
	_continue = _primary(actions, "Continue", _finish.bind(true))
	_login = _primary(actions, "Sign in", _start.bind("login"))
	_install = _primary(actions, "Get it", _open_install)
	_api = UI_COMPONENTS.link("Use an API key instead")
	_api.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	_api.pressed.connect(_show_api)
	actions.add_child(_api)
	_api_form = API_SCRIPT.new()
	_api_form.size_flags_vertical = Control.SIZE_SHRINK_BEGIN
	_content.add_child(_api_form)
	_api_form.back_requested.connect(_close_api)
	_api_form.save_requested.connect(_configure_api)
	_api_form.environment_requested.connect(_start_environment)
	_api_form.hide()
	_fit_column()
	if initial_profile.is_empty() and _buttons.has(initial_agent):
		_select(initial_agent, false)
	else:
		_update()
	UI_MOTION.stagger(_buttons.values())
	_start("status")


func _notification(what: int) -> void:
	if what == NOTIFICATION_THEME_CHANGED and _content != null:
		_fit_column()


func _on_theme_rebuilt() -> void:
	_fit_column()


func _fit_column() -> void:
	_content.custom_minimum_size.x = UI_THEME.font_size(self, "body") * COLUMN_EM


func _primary(parent: Control, text: String, action: Callable) -> Button:
	var button := UI_COMPONENTS.primary(text)
	# The same height as the assistant rows above it.
	UI_COMPONENTS.bind_height(button, "lg", false)
	button.theme_changed.connect(_style_waiting.bind(button))
	button.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	button.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	button.pressed.connect(action)
	var indicator := INDICATOR.new()
	indicator.name = "Indicator"
	indicator.anchor_left = 1.0
	indicator.anchor_right = 1.0
	indicator.anchor_top = 0.5
	indicator.anchor_bottom = 0.5
	indicator.grow_horizontal = Control.GROW_DIRECTION_BEGIN
	indicator.grow_vertical = Control.GROW_DIRECTION_BOTH
	button.add_child(indicator)
	button.theme_changed.connect(
		func() -> void:
			indicator.offset_right = -UI_THEME.space(button, 3)
			indicator.offset_left = indicator.offset_right
	)
	parent.add_child(button)
	return button


## A disabled primary reads as not available: the neutral raised fill with a hairline
## border and muted text (above 4.5:1 on that fill in both themes). The brass fill appears
## only once a row is chosen. Each override re-sends theme_changed; the meta keeps this
## from re-entering.
func _style_waiting(button: Button) -> void:
	if button.has_meta("_joinery_styling") or not button.is_inside_tree():
		return
	button.set_meta("_joinery_styling", true)
	var normal := button.get_theme_stylebox("normal", "JoineryPrimaryButton")
	if normal is StyleBoxFlat:
		var waiting := (normal as StyleBoxFlat).duplicate() as StyleBoxFlat
		var fill: Color = UI_THEME.color(button, "surface_raised")
		var edge: Color = UI_THEME.color(button, "border_subtle")
		waiting.bg_color = fill
		waiting.border_color = edge
		waiting.set_border_width_all(maxi(1, roundi(waiting.get_border_width(SIDE_LEFT))))
		button.add_theme_stylebox_override("disabled", waiting)
	var muted: Color = UI_THEME.color(button, "text_muted")
	button.add_theme_color_override("font_disabled_color", muted)
	button.add_theme_color_override("icon_disabled_color", muted)
	button.remove_meta("_joinery_styling")


func _add_provider(id: String, label: String, icon: String) -> void:
	var card := Card.new()
	card.theme_type_variation = "JoineryCardButton"
	card.toggle_mode = true
	card.button_group = _group
	card.focus_mode = Control.FOCUS_ALL
	card.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	card.accessibility_name = label
	UI_COMPONENTS.bind_height(card, "lg", false)
	UI_MOTION.press_feedback(card)
	card.row = HBoxContainer.new()
	card.row.theme_type_variation = "JoineryRow"
	card.row.mouse_filter = Control.MOUSE_FILTER_IGNORE
	card.row.set_anchors_preset(Control.PRESET_FULL_RECT)
	card.add_child(card.row)
	card.theme_changed.connect(
		func() -> void:
			var inset := UI_THEME.space(card, 3)
			card.row.offset_left = inset
			card.row.offset_right = -inset
	)
	card.theme_changed.connect(_style_unavailable.bind(card))
	card.tile = ICON_SCRIPT.tile(icon, label)
	card.row.add_child(card.tile)
	card.title = UI_COMPONENTS.label(label)
	card.title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	card.title.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	card.row.add_child(card.title)
	card.indicator = INDICATOR.new()
	card.row.add_child(card.indicator)
	card.dot = UI_COMPONENTS.status_dot("ready")
	card.row.add_child(card.dot)
	card.done = TextureRect.new()
	card.done.stretch_mode = TextureRect.STRETCH_KEEP_CENTERED
	card.done.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	UI_COMPONENTS.bind_icon(card.done, "success", "texture")
	card.done.theme_changed.connect(
		func() -> void: card.done.self_modulate = UI_THEME.color(card.done, "success")
	)
	card.row.add_child(card.done)
	card.status = UI_COMPONENTS.label("", "JoineryCaption")
	card.status.text_overrun_behavior = TextServer.OVERRUN_NO_TRIMMING
	card.status.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	card.row.add_child(card.status)
	card.link = UI_COMPONENTS.link("Get it")
	UI_COMPONENTS.bind_height(card.link, "xs")
	card.link.pressed.connect(_open_install_for.bind(id))
	card.row.add_child(card.link)
	for child: Node in card.row.get_children():
		if child is Control and child != card.link:
			(child as Control).mouse_filter = Control.MOUSE_FILTER_IGNORE
	card.pressed.connect(_select.bind(id))
	_list.add_child(card)
	if _inline != null:
		_list.move_child(_inline, -1)
	_buttons[id] = card
	_refresh_card(id)


## The caption, dot state ("" for none) and whether to show "Checking…" for a card.
func _card_state(id: String, data: Dictionary) -> Dictionary:
	var confirmed: bool = not data.get("requires_key_confirmation", false) or _accepted_keys.has(id)
	var signed_in := bool(data.get("connected", false))
	var state := {"text": "Sign in with your account"}
	if _celebrating == id:
		state = {"text": "Connected", "done": true}
	elif data.is_empty():
		state = {"text": "Checking…", "checking": true}
	elif not data.get("supported", true):
		state = {"text": "Not available here", "dot": "off", "blocked": true}
	elif data.get("profile", false):
		state = (
			{"text": "Key saved", "dot": "ready"} if signed_in else {"text": "Check API settings"}
		)
	elif not data.get("installed", false):
		state = {"text": "Not installed", "link": data.has("install_url"), "blocked": true}
	elif signed_in and confirmed:
		state = {"text": "Signed in", "dot": "ready"}
	return state


func _refresh_card(id: String) -> void:
	var card: Card = _buttons.get(id)
	if card == null:
		return
	var state := _card_state(id, _agents.get(id, {}))
	card.status.text = str(state["text"])
	card.indicator.set("running", state.get("checking", false))
	var dot := str(state.get("dot", ""))
	card.dot.visible = not dot.is_empty()
	if not dot.is_empty():
		card.dot.set("state", dot)
	card.done.visible = state.get("done", false)
	card.link.visible = state.get("link", false)
	# A row that can't be chosen is dimmed and takes no hover or press; only its
	# "Get it" link stays live. The selected row stays enabled so it can show why.
	var blocked: bool = state.get("blocked", false) and _selected != id
	card.disabled = blocked
	card.focus_mode = Control.FOCUS_NONE if blocked else Control.FOCUS_ALL
	card.tile.modulate.a = UNAVAILABLE_ALPHA if state.get("blocked", false) else 1.0
	card.accessibility_name = "%s, %s" % [card.title.text, card.status.text]
	card.tooltip_text = str(_agents.get(id, {}).get("reason", ""))


## A row that can't be chosen drops its fill and border: a flat line with the same insets,
## so it reads as "not here yet" without greying its name. Each override re-sends
## theme_changed; the meta keeps this from re-entering.
func _style_unavailable(card: Card) -> void:
	if card.has_meta("_joinery_styling") or not card.is_inside_tree():
		return
	card.set_meta("_joinery_styling", true)
	var box := card.get_theme_stylebox("disabled", "JoineryCardButton")
	if box is StyleBoxFlat:
		var flat := (box as StyleBoxFlat).duplicate() as StyleBoxFlat
		flat.draw_center = false
		flat.set_border_width_all(0)
		card.add_theme_stylebox_override("disabled", flat)
	card.remove_meta("_joinery_styling")


func _select(id: String, user_choice: bool = true) -> void:
	_selected = id
	_chosen_by_user = _chosen_by_user or user_choice
	_auto_redirect = true
	_redirecting = false
	if _waiting_for != id:
		_waiting_for = ""
	_buttons[id].set_pressed_no_signal(true)
	if _message != null:
		_message.text = ""
	_update()


func _display_name(id: String) -> String:
	if AGENT_NAMES.has(id):
		return AGENT_NAMES[id]
	return str(_agents.get(id, {}).get("model", id))


func _update() -> void:
	var data: Dictionary = _agents.get(_selected, {})
	var installed := bool(data.get("installed", false))
	var supported := bool(data.get("supported", true))
	var ready := _can_continue(data)
	_detail.text = ""
	_detail.theme_type_variation = "JoineryCaption"
	if _selected.is_empty():
		_detail.text = CHOOSE_HINT if _page == "choices" else ""
	elif not supported:
		_detail.text = str(data.get("reason", UNAVAILABLE))
		_detail.theme_type_variation = "JoineryError"
	elif data.get("requires_key_confirmation", false) and not _accepted_keys.has(_selected):
		_detail.text = "An API key is already set on this computer. Open API key settings to use it."
	elif data.get("profile", false) and not ready:
		_detail.text = "Check this connection's key in API settings."
	_detail.visible = not _detail.text.is_empty()
	_message.visible = not _message.text.is_empty()
	_place_inline()
	for id: String in _buttons:
		_refresh_card(id)
	_update_actions(data, supported, installed, ready)
	if ready and _job == null and _auto_redirect and _page == "choices" and not _redirecting:
		_message.text = ""
		_message.hide()
		_redirecting = true
		_finish.call_deferred()


func _update_actions(data: Dictionary, supported: bool, installed: bool, ready: bool) -> void:
	var name_text := _display_name(_selected)
	var busy := _job != null and _action != "status"
	if _waiting_for == _selected and Time.get_ticks_msec() / 1000.0 - _waiting_since > SIGN_IN_WAIT:
		_waiting_for = ""
	var waiting := not ready and not _selected.is_empty() and _waiting_for == _selected
	_login.visible = supported and installed and not ready and AGENT_NAMES.has(_selected)
	_login.text = "Waiting for sign-in…" if waiting else "Sign in to " + name_text
	_login.disabled = busy or waiting
	_login.get_node("Indicator").set("running", waiting)
	_install.visible = (
		supported and not installed and not data.is_empty() and data.has("install_url")
	)
	_install.text = "Get " + name_text
	_continue.visible = not _login.visible and not _install.visible
	_continue.text = "Continue with " + name_text if ready else "Continue"
	_continue.disabled = not ready or busy or not _celebrating.is_empty()
	_continue.tooltip_text = _continue_reason(data, ready)
	_api.disabled = busy


func _continue_reason(data: Dictionary, ready: bool) -> String:
	if ready:
		return ""
	if _selected.is_empty():
		return "Choose an account first."
	if data.is_empty():
		return "Checking this account…"
	if not data.get("supported", true):
		return str(data.get("reason", UNAVAILABLE))
	return "Check this connection in API settings."


## Keep the detail and message lines directly under the selected card.
func _place_inline() -> void:
	var card: Control = _buttons.get(_selected)
	var index := _list.get_child_count() - 1 if card == null else card.get_index() + 1
	if card != null and _inline.get_index() < card.get_index():
		index -= 1
	_list.move_child(_inline, clampi(index, 0, _list.get_child_count() - 1))
	_inline.visible = _detail.visible or _message.visible


func _can_continue(data: Dictionary) -> bool:
	return (
		not _selected.is_empty()
		and data.get("supported", true)
		and data.get("installed", false)
		and data.get("connected", false)
		and (not data.get("requires_key_confirmation", false) or _accepted_keys.has(_selected))
	)


func _home() -> String:
	if not engine_home.is_empty():
		return engine_home
	var from_env := OS.get_environment("JOINERY_HOME")
	if not from_env.is_empty():
		return from_env
	return ProjectSettings.globalize_path("res://").path_join("..").simplify_path()


func _start(action: String, payload: Dictionary = {}) -> void:
	if _job != null:
		return
	_action = action
	_job_agent = _selected
	var args := PackedStringArray(["run", "--project", _home(), "joinery", "connect", action])
	if action in ["login", "use-key"]:
		args.append_array(PackedStringArray(["--agent", _selected]))
		_celebrate = true
	if action == "login":
		_waiting_for = _selected
		_waiting_since = Time.get_ticks_msec() / 1000.0
	_job = COMMAND_SCRIPT.new()
	if _job.start("uv", args) != OK:
		_job = null
		_waiting_for = ""
		_show_message(HELPER_MISSING, true)
		if action == "configure":
			_api_form.call("show_result", HELPER_MISSING)
	elif not payload.is_empty():
		_job.send_line(JSON.stringify(payload))
	_update()


func _start_environment() -> void:
	_start("use-key")


func _show_message(text: String, error: bool) -> void:
	_message.text = text
	_message.theme_type_variation = "JoineryError" if error else "JoineryCaption"
	_message.visible = not text.is_empty()
	_place_inline()


func _process(delta: float) -> void:
	if not is_visible_in_tree():
		return
	_elapsed += delta
	if _job == null:
		if _elapsed >= 3.0:
			_start("status")
		return
	var result: Dictionary = _job.poll()
	if result.is_empty():
		return
	_job = null
	_elapsed = 0
	var payload_json := JSON.new()
	var payload: Variant = (
		payload_json.data if payload_json.parse(str(result["output"])) == OK else null
	)
	if not payload is Dictionary:
		_waiting_for = ""
		_show_message(HELPER_MISSING, true)
		if _action == "configure":
			_api_form.call("show_result", HELPER_MISSING)
	elif _action == "status":
		_apply_status(payload)
	elif payload.has("profile"):
		_apply_profile(payload["profile"])
	else:
		_apply_reply(payload, int(result["code"]))
	_update()


func _apply_profile(profile: Dictionary) -> void:
	_add_profile(profile)
	_api_form.call("clear_key")
	_api_form.call("show_result", "")
	_celebrate = true
	if _page == "api":
		_close_api()
		_select(str(profile["id"]))


func _apply_reply(payload: Dictionary, code: int) -> void:
	var error := str(payload.get("error", ""))
	var text := error if not error.is_empty() else str(payload.get("message", ""))
	if not error.is_empty():
		_waiting_for = ""
	if _action == "configure":
		_api_form.call("show_result", text, error.is_empty())
	else:
		_show_message(text, not error.is_empty())
	if _action == "use-key" and code == 0 and payload.get("started", false):
		if not _accepted_keys.has(_job_agent):
			_accepted_keys.append(_job_agent)
		_agents[_job_agent]["connected"] = false
		_close_api()
		_auto_redirect = true
	_elapsed = 3.0


func _apply_status(payload: Dictionary) -> void:
	for agent: Dictionary in payload.get("agents", []):
		_agents[str(agent["id"])] = agent
	var present: Array[String] = []
	for profile: Dictionary in payload.get("profiles", []):
		present.append(str(profile["id"]))
		_add_profile(profile)
	for id: String in _agents.keys():
		if id.begins_with("api_") and not present.has(id):
			_agents.erase(id)
			var button: Button = _buttons[id]
			button.get_parent().remove_child(button)
			button.queue_free()
			_buttons.erase(id)
			if _selected == id:
				_selected = ""
	if _waiting_for != "" and _can_continue(_agents.get(_waiting_for, {})):
		_waiting_for = ""
	if not _restored:
		_restored = true
		_sort_cards()
		if (
			not _chosen_by_user
			and not initial_profile.is_empty()
			and _agents.has(initial_profile)
			and _page == "choices"
		):
			_select(initial_profile, false)
		elif not initial_profile.is_empty() and not _agents.has(initial_profile):
			_show_message(PROFILE_MISSING, true)


## Ready assistants first, then installed ones, then the rest; the first status only, so
## rows never move under the pointer later.
func _sort_cards() -> void:
	var ids: Array = AGENT_NAMES.keys()
	var rank := func(id: String) -> int:
		var data: Dictionary = _agents.get(id, {})
		var usable: bool = data.get("installed", false) and data.get("supported", true)
		if usable and data.get("connected", false):
			return 0
		return 1 if usable else 2
	var order: Array = ids.duplicate()
	order.sort_custom(
		func(a: String, b: String) -> bool:
			var ra: int = rank.call(a)
			var rb: int = rank.call(b)
			return ra < rb or (ra == rb and ids.find(a) < ids.find(b))
	)
	for index: int in order.size():
		_list.move_child(_buttons[order[index]], index)


func _add_profile(profile: Dictionary) -> void:
	var id := str(profile["id"])
	_agents[id] = profile
	if not _buttons.has(id):
		_add_provider(id, str(profile["model"]), str(profile["driver"]))


func _show_api() -> void:
	_auto_redirect = false
	_show_message("", false)
	_api_form.call("configure", _agents, _selected)
	_page = "api"
	UI_MOTION.crossfade(_choices, _api_form)


func _close_api() -> void:
	_api_form.call("clear_key")
	if _page == "api":
		_page = "choices"
		UI_MOTION.crossfade(_api_form, _choices)
	else:
		_api_form.hide()
		_choices.show()


func _configure_api(settings: Dictionary) -> void:
	_start("configure", settings)


## Reopen the screen to change the assistant (the fork's "Change" button calls this).
func open_settings() -> void:
	_selected = ""
	_chosen_by_user = true
	_auto_redirect = false
	_redirecting = false
	_celebrating = ""
	_waiting_for = ""
	for button: Button in _buttons.values():
		button.set_pressed_no_signal(false)
	_close_api()
	_content.show()
	_show_message("", false)
	_update()
	show()


func _open_install() -> void:
	_open_install_for(_selected)


func _open_install_for(id: String) -> void:
	var data: Dictionary = _agents.get(id, {})
	var agent_id := str(data.get("driver", id))
	var agent: Dictionary = _agents.get(agent_id, data)
	if agent.has("install_url"):
		OS.shell_open(str(agent["install_url"]))


## Continue with the selected assistant. After a sign-in made on this screen the card first
## shows "Connected" (MOTION.confirm), then the screen fades out and emits [signal connected].
func _finish(user_choice := false) -> void:
	if not is_visible_in_tree():
		return
	var data: Dictionary = _agents.get(_selected, {})
	var busy := _job != null and _action != "status"
	if not _can_continue(data) or busy or _page != "choices":
		_redirecting = false
		return
	if not user_choice and not _auto_redirect:
		_redirecting = false
		return
	var agent := str(data.get("driver", _selected))
	var method := str(data.get("method", ""))
	var profile := _selected if data.get("profile", false) else ""
	if not _celebrate or not UI_MOTION.is_enabled():
		_celebrate = false
		_emit(agent, method, profile)
		return
	_celebrate = false
	_celebrating = _selected
	selected_profile = profile
	_refresh_card(_selected)
	_update_actions(data, true, true, true)
	UI_MOTION.pulse(_buttons[_selected])
	await get_tree().create_timer(UI_MOTION.duration("confirm")).timeout
	UI_MOTION.exit(_content, null, _emit.bind(agent, method, profile))


func _emit(agent: String, method: String, profile: String) -> void:
	_celebrating = ""
	_redirecting = false
	# Only a new choice or sign-in continues again; never re-emit on the next refresh.
	_auto_redirect = false
	selected_profile = profile
	connected.emit(agent, method)
	# The host hides this screen on the signal; reset it for the next time it is shown.
	_content.show()
	_content.modulate.a = 1.0
	_update()


func _exit_tree() -> void:
	if _job != null:
		_job.cancel()
		_job = null
