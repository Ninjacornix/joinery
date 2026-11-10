extends SceneTree
## Save-load with online play, in two real processes on localhost: the host
## saves while a client is connected (every player's state goes in the slot);
## the client may neither save nor load, never autosaves, and sees the pause
## menu's Save and Load disabled.
##
## Host:   --host --port=N --autostart --settings-path=<file>
## Client: --join=127.0.0.1:N --autostart --settings-path=<file>
##
## Only meaningful with the online-play feature applied. This file is compiled
## before the autoloads exist, so the checks live in online_checks.gd.

const CHECKS := "res://tests/features/save-load/online_checks.gd"


func _initialize() -> void:
	Engine.max_fps = 120
	_run.call_deferred()


func _run() -> void:
	var net: Node = root.get_node_or_null(^"Net")
	var hosting: bool = net != null and bool(net.call(&"has_arg", "host"))
	var h: PlayHarness = PlayHarness.new(self, "save-load host" if hosting else "save-load client")
	h.expect(net != null, "online play is applied")
	var script: GDScript = load(CHECKS) as GDScript
	h.expect(script != null, "the online save-load checks load")
	if net != null and script != null:
		var checks: Variant = script.new(self, h, net)
		if hosting:
			await checks.host()
		else:
			await checks.client()
	h.finish()
