@tool
extends CenterContainer
## Project Manager connection screen; successful authentication advances automatically.

signal connected(agent_id: String, method: String)

const COMMAND_SCRIPT := preload("command_job.gd")
const ICON_SCRIPT := preload("agent_icon.gd")
const API_SCRIPT := preload("api_connection_form.gd")
const UI_THEME := preload("ui_theme.gd")
const AGENT_NAMES := {
	"claude": "Claude Code",
	"codex": "Codex",
	"gemini": "Gemini",
	"opencode": "opencode",
}
var engine_home := ""
var initial_agent := ""
var initial_profile := ""
var selected_profile := ""
var _selected := ""
var _agents: Dictionary = {}
var _buttons: Dictionary = {}
var _detail: Label
var _message: Label
var _login: Button
var _api: Button
var _install: Button
var _choices: VBoxContainer
var _profiles: VBoxContainer
var _api_form: VBoxContainer
var _job: RefCounted
var _action := ""
var _job_agent := ""
var _accepted_keys: Array[String] = []
var _elapsed := 0.0
var _auto_redirect := true
var _redirecting := false
var _restored := false
var _chosen_by_user := false
var _group := ButtonGroup.new()


func _ready() -> void:
	theme = UI_THEME.build(self)
	name = "JoineryAgentConnect"
	size_flags_vertical = Control.SIZE_EXPAND_FILL
	var content := VBoxContainer.new()
	content.custom_minimum_size.x = get_theme_default_font_size() * 30
	content.add_theme_constant_override("separation", get_theme_default_font_size())
	add_child(content)
	_choices = VBoxContainer.new()
	_choices.add_theme_constant_override("separation", get_theme_default_font_size())
	content.add_child(_choices)
	var heading := Label.new()
	heading.text = "Connect your agent"
	heading.set_theme_type_variation("HeaderLarge")
	_choices.add_child(heading)
	var subtitle := Label.new()
	subtitle.text = "Sign in with your subscription, or connect a model through its API."
	subtitle.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_choices.add_child(subtitle)
	var providers := VBoxContainer.new()
	_choices.add_child(providers)
	for id: String in AGENT_NAMES:
		_add_provider(providers, id, AGENT_NAMES[id], id)
	_profiles = VBoxContainer.new()
	_choices.add_child(_profiles)
	_detail = Label.new()
	_detail.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_choices.add_child(_detail)
	var actions := HBoxContainer.new()
	_choices.add_child(actions)
	_login = _button(actions, "Sign in", _start.bind("login"))
	_api = _button(actions, "Use an API key instead…", _show_api)
	_install = _button(actions, "Install agent", _open_install)
	_api_form = API_SCRIPT.new()
	content.add_child(_api_form)
	_api_form.back_requested.connect(_close_api)
	_api_form.save_requested.connect(_configure_api)
	_api_form.environment_requested.connect(_start.bind("use-key"))
	_api_form.hide()
	_message = Label.new()
	_message.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	content.add_child(_message)
	if initial_profile.is_empty() and _buttons.has(initial_agent):
		_select(initial_agent, false)
	else:
		_update()
	_start("status")


func _button(parent: Control, text: String, action: Callable) -> Button:
	var button := Button.new()
	button.text = text
	button.pressed.connect(action)
	parent.add_child(button)
	return button


func _add_provider(parent: Control, id: String, label: String, icon: String) -> void:
	var button := Button.new()
	button.text = label
	button.clip_text = true
	button.tooltip_text = label
	button.icon = ICON_SCRIPT.texture(
		icon, int(get_theme_default_font_size() * 1.5), get_script().resource_path.get_base_dir()
	)
	button.expand_icon = true
	button.add_theme_constant_override("icon_max_width", int(get_theme_default_font_size() * 1.5))
	for state: String in ["normal", "hover", "pressed", "hover_pressed", "focus"]:
		button.add_theme_color_override("icon_" + state + "_color", Color.WHITE)
	button.alignment = HORIZONTAL_ALIGNMENT_LEFT
	button.add_theme_constant_override("h_separation", get_theme_default_font_size())
	button.toggle_mode = true
	button.button_group = _group
	button.custom_minimum_size.y = get_theme_default_font_size() * 3
	button.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	button.pressed.connect(_select.bind(id))
	parent.add_child(button)
	_buttons[id] = button


func _select(id: String, user_choice: bool = true) -> void:
	_selected = id
	_chosen_by_user = _chosen_by_user or user_choice
	_auto_redirect = true
	_redirecting = false
	_buttons[id].set_pressed_no_signal(true)
	if _message != null:
		_message.text = ""
	_update()


func _update() -> void:
	var data: Dictionary = _agents.get(_selected, {})
	var installed := bool(data.get("installed", false))
	var ready := _can_continue(data)
	_detail.text = (
		"Connected: " + str(data.get("method", ""))
		if ready
		else (
			"Sign in with this agent to continue."
			if installed
			else (
				"Checking your local agents…"
				if data.is_empty()
				else "Install this agent's CLI to connect."
			)
		)
	)
	if _selected.is_empty():
		_detail.text = ""
	elif data.get("requires_key_confirmation", false) and not _accepted_keys.has(_selected):
		_detail.text = "An API key is available in API settings."
	elif data.get("profile", false) and not ready:
		_detail.text = "Check this model's API settings to connect."
	var supported := bool(data.get("supported", true))
	if not supported:
		_detail.text = str(data.get("reason", "This connection is unavailable on this platform."))
	_detail.visible = not _detail.text.is_empty()
	_login.visible = supported and installed and not ready and AGENT_NAMES.has(_selected)
	_login.disabled = _job != null
	_api.disabled = _job != null
	_install.visible = supported and not installed and not data.is_empty()
	if ready and _job == null and _auto_redirect and not _api_form.visible and not _redirecting:
		_message.text = ""
		_redirecting = true
		_finish.call_deferred()


func _can_continue(data: Dictionary) -> bool:
	return (
		not _selected.is_empty()
		and data.get("supported", true)
		and data.get("installed", false)
		and data.get("connected", false)
		and (not data.get("requires_key_confirmation", false) or _accepted_keys.has(_selected))
	)


func _start(action: String, payload: Dictionary = {}) -> void:
	if _job != null:
		return
	_action = action
	_job_agent = _selected
	var args := PackedStringArray(["run", "--project", engine_home, "joinery", "connect", action])
	if action in ["login", "use-key"]:
		args.append_array(PackedStringArray(["--agent", _selected]))
	_job = COMMAND_SCRIPT.new()
	if _job.start("uv", args) != OK:
		_job = null
		_message.text = "Could not start the connection helper. Check that uv is installed."
	elif not payload.is_empty():
		_job.send_line(JSON.stringify(payload))
	_update()


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
	var payload: Variant = JSON.parse_string(str(result["output"]))
	if not payload is Dictionary:
		_message.text = "Could not check the connection. Try signing in with the agent's CLI."
	elif _action == "status":
		_apply_status(payload)
	elif payload.has("profile"):
		var profile: Dictionary = payload["profile"]
		_add_profile(profile)
		_api_form.clear_key()
		if _api_form.visible:
			_close_api()
			_select(str(profile["id"]))
	else:
		_message.text = str(payload.get("error", payload.get("message", "")))
		if _action == "use-key" and int(result["code"]) == 0 and payload.get("started", false):
			if not _accepted_keys.has(_job_agent):
				_accepted_keys.append(_job_agent)
			_agents[_job_agent]["connected"] = false
			_close_api()
			_auto_redirect = true
		_elapsed = 3.0
	_update()


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
	if not _restored:
		_restored = true
		if (
			not _chosen_by_user
			and not initial_profile.is_empty()
			and _agents.has(initial_profile)
			and not _api_form.visible
		):
			_select(initial_profile, false)
		elif not initial_profile.is_empty() and not _agents.has(initial_profile):
			_message.text = "The saved API connection is unavailable. Choose a connection."


func _add_profile(profile: Dictionary) -> void:
	var id := str(profile["id"])
	_agents[id] = profile
	if not _buttons.has(id):
		_add_provider(_profiles, id, str(profile["model"]), str(profile["driver"]))


func _show_api() -> void:
	_auto_redirect = false
	_message.text = ""
	_api_form.configure(_agents, _selected)
	_choices.hide()
	_api_form.show()


func _close_api() -> void:
	_api_form.clear_key()
	_api_form.hide()
	_choices.show()


func _configure_api(settings: Dictionary) -> void:
	_message.text = "Checking the API key and model…"
	_start("configure", settings)


func open_settings() -> void:
	_selected = ""
	_chosen_by_user = true
	_auto_redirect = false
	_redirecting = false
	for button: Button in _buttons.values():
		button.set_pressed_no_signal(false)
	_close_api()
	_message.text = ""
	_update()
	show()


func _open_install() -> void:
	var data: Dictionary = _agents.get(_selected, {})
	var agent_id := str(data.get("driver", _selected))
	var agent: Dictionary = _agents.get(agent_id, data)
	if agent.has("install_url"):
		OS.shell_open(str(agent["install_url"]))


func _finish() -> void:
	if not is_visible_in_tree():
		return
	var data: Dictionary = _agents.get(_selected, {})
	if _can_continue(data) and _job == null and not _api_form.visible:
		selected_profile = _selected if data.get("profile", false) else ""
		connected.emit(str(data.get("driver", _selected)), str(data["method"]))
	else:
		_redirecting = false


func _exit_tree() -> void:
	if _job != null:
		_job.cancel()
		_job = null
