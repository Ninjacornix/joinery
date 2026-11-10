extends VBoxContainer
## The "Play online" page: your name; host a game, join one by address or
## from the list of games on your network; then the players, your ready
## toggle and, for the host, Start. Works with the ui_* actions alone.

## Wired to the page's Back button by `Lobby.open_page`.
var back_action: Callable

var _status: Label
var _name_edit: LineEdit
var _offline: VBoxContainer
var _address: LineEdit
var _lan_list: VBoxContainer
var _lan_hint: Label
var _extra: VBoxContainer
var _online: VBoxContainer
var _player_list: VBoxContainer
var _ready_toggle: CheckButton
var _start: Button
var _browser: LanDiscovery


func _init() -> void:
	name = "Lobby"
	back_action = _on_back
	_status = UiStyle.hint("")
	_status.name = "Status"
	_status.auto_translate_mode = Node.AUTO_TRANSLATE_MODE_DISABLED
	add_child(_status)

	_name_edit = LineEdit.new()
	_name_edit.name = "PlayerName"
	_name_edit.max_length = 24
	_name_edit.placeholder_text = "Your name"
	_name_edit.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_name_edit.text_changed.connect(_on_name_changed)
	add_child(UiStyle.row("Name", _name_edit))

	_offline = VBoxContainer.new()
	_offline.name = "Offline"
	add_child(_offline)
	var host_button: Button = UiStyle.button("Host game")
	host_button.name = "Host"
	host_button.pressed.connect(_on_host)
	_offline.add_child(host_button)
	_address = LineEdit.new()
	_address.name = "Address"
	_address.placeholder_text = "Address, such as 192.168.1.20"
	_address.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_address.text_submitted.connect(_on_join_text)
	_offline.add_child(UiStyle.row("Address", _address))
	var join_button: Button = UiStyle.button("Join")
	join_button.name = "Join"
	join_button.pressed.connect(_on_join)
	_offline.add_child(join_button)
	_lan_hint = UiStyle.hint("Looking for games on your network…")
	_lan_hint.name = "LanHint"
	_offline.add_child(_lan_hint)
	_lan_list = VBoxContainer.new()
	_lan_list.name = "LanList"
	_offline.add_child(_lan_list)
	_extra = VBoxContainer.new()
	_extra.name = "Extra"
	_offline.add_child(_extra)

	_online = VBoxContainer.new()
	_online.name = "Online"
	add_child(_online)
	_player_list = VBoxContainer.new()
	_player_list.name = "Players"
	_online.add_child(_player_list)
	_ready_toggle = UiStyle.toggle(false)
	_ready_toggle.name = "Ready"
	_ready_toggle.toggled.connect(_on_ready_toggled)
	_online.add_child(UiStyle.row("Ready", _ready_toggle))
	_start = UiStyle.button("Start")
	_start.name = "Start"
	_start.pressed.connect(_on_start)
	_online.add_child(_start)
	var leave_button: Button = UiStyle.button("Leave")
	leave_button.name = "Leave"
	leave_button.pressed.connect(_on_leave)
	_online.add_child(leave_button)

	_browser = LanDiscovery.new()
	_browser.name = "Browser"
	add_child(_browser)


func _ready() -> void:
	_name_edit.text = Net.player_name
	Net.hosted.connect(_refresh)
	Net.joined.connect(_refresh)
	Net.join_failed.connect(_on_failed)
	Net.session_ended.connect(_refresh.unbind(1))
	Lobby.players_changed.connect(_refresh)
	_browser.hosts_changed.connect(_refresh_lan)
	_refresh()


func _exit_tree() -> void:
	_browser.stop()


func _refresh() -> void:
	var online: bool = Net.is_online()
	_offline.visible = not online
	_online.visible = online
	_name_edit.editable = not online
	_status.text = _status_text()
	if online:
		_browser.stop()
	elif not _browser.is_active():
		if _browser.start_listening(Net.lan_port) != OK:
			_lan_hint.text = "Cannot look for games on this network right now."
	_rebuild_players()
	_rebuild_extra()
	_refresh_lan()
	_ready_toggle.set_pressed_no_signal(Lobby.is_ready(multiplayer.get_unique_id()))
	_ready_toggle.disabled = not Net.is_connected_to_game() or Net.dedicated
	_start.visible = Net.is_server()
	_start.disabled = not Lobby.can_start()
	_start.focus_mode = Control.FOCUS_ALL if not _start.disabled else Control.FOCUS_NONE
	_keep_focus()


func _status_text() -> String:
	match Net.state:
		Net.State.HOSTING:
			return tr("Hosting on port %d. Players can join now.") % Net.port
		Net.State.CONNECTING:
			return tr("Connecting…")
		Net.State.CONNECTED:
			return tr("Connected. Waiting for the host to start.")
	if not Net.last_reason.is_empty():
		return Net.last_reason
	return tr("Host a game, or join one on your network or by address.")


func _rebuild_players() -> void:
	for child: Node in _player_list.get_children():
		_player_list.remove_child(child)
		child.queue_free()
	var ids: Array = Lobby.players.keys()
	ids.sort()
	for id: Variant in ids:
		var info: Dictionary = Lobby.players[id]
		var line: Label = Label.new()
		line.auto_translate_mode = Node.AUTO_TRANSLATE_MODE_DISABLED
		var template: String = tr("%s: ready") if bool(info.get("ready", false)) else tr("%s: not ready")
		line.text = template % str(info.get("name", ""))
		_player_list.add_child(line)


func _rebuild_extra() -> void:
	for child: Node in _extra.get_children():
		_extra.remove_child(child)
		child.queue_free()
	for entry: Dictionary in Lobby.actions():
		var item: Button = UiStyle.button(str(entry["label"]))
		item.name = str(entry["id"])
		item.pressed.connect(entry["action"])
		_extra.add_child(item)


func _refresh_lan() -> void:
	for child: Node in _lan_list.get_children():
		_lan_list.remove_child(child)
		child.queue_free()
	var found: Array[Dictionary] = []
	if _browser.is_active():
		found = _browser.hosts()
	for entry: Dictionary in found:
		var item: Button = UiStyle.button("")
		item.auto_translate_mode = Node.AUTO_TRANSLATE_MODE_DISABLED
		item.text = tr("Join %s (%d of %d)") % [
			str(entry["name"]), int(entry["players"]), int(entry["max"])
		]
		item.disabled = not bool(entry["compatible"])
		item.pressed.connect(_join_found.bind(str(entry["address"]), int(entry["port"])))
		_lan_list.add_child(item)
	if _browser.is_active():
		_lan_hint.text = (
			"Looking for games on your network…" if found.is_empty() else "Games on your network"
		)


func _keep_focus() -> void:
	var focused: Control = get_viewport().gui_get_focus_owner() if is_inside_tree() else null
	if focused != null and focused.is_visible_in_tree() and is_ancestor_of(focused):
		return
	if not is_inside_tree():
		return
	for candidate: Control in [_offline.get_node(^"Host") as Control, _ready_toggle, _start]:
		if candidate.is_visible_in_tree() and candidate.focus_mode == Control.FOCUS_ALL:
			if not (candidate is BaseButton and (candidate as BaseButton).disabled):
				candidate.grab_focus.call_deferred()
				return


func _on_name_changed(text: String) -> void:
	Lobby.set_local_name(text)


func _on_host() -> void:
	if Net.host(Net.port, Net.max_clients) != OK:
		Menus.toast(Net.last_reason, 4.0)
	_refresh()


func _on_join() -> void:
	_on_join_text(_address.text)


func _on_join_text(text: String) -> void:
	if text.strip_edges().is_empty():
		Menus.toast("Type the host's address first.")
		return
	var target: Array = Net.split_address(text, Net.port)
	_join_found(str(target[0]), int(target[1]))


func _join_found(address: String, port: int) -> void:
	Net.join(address, port)
	_refresh()


func _on_failed(_reason: String) -> void:
	_refresh()


func _on_ready_toggled(on: bool) -> void:
	Lobby.mark_ready(on)


func _on_start() -> void:
	if Lobby.start() != OK:
		Menus.toast("Everyone needs to be ready first.")


func _on_leave() -> void:
	Net.leave()
	_refresh()


func _on_back() -> void:
	if Net.is_online() and Game.current_level() == null:
		Net.leave()
	Menus.back()
