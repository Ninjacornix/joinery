extends SceneTree
## The header's context ring: hidden until the provider reports usage, then filled with the
## share of the context window in use, with a hover card of this session's tokens and the model.
## A new session starts it over; nothing is polled.

const DOCK_PATH := "res://addons/joinery/journeyman_dock.gd"

var _failed := false


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _texts(node: Node) -> String:
	var parts: Array[String] = []
	for label: Node in node.find_children("", "Label", true, false):
		parts.append((label as Label).text)
	return " | ".join(parts)


func _run() -> void:
	load("res://addons/joinery/ui_motion.gd").call("set_enabled", false)
	var meter_script: GDScript = load("res://addons/joinery/usage_meter.gd")
	_check(meter_script.compact(999) == "999", "Small counts stay exact")
	_check(meter_script.compact(1234) == "1.2k", "Thousands")
	_check(meter_script.compact(31_200) == "31k", "Tens of thousands")
	_check(meter_script.compact(272_000) == "272k", "Context windows")
	_check(meter_script.compact(1_000_000) == "1M", "Millions")

	var chat: Control = load(DOCK_PATH).new()
	root.add_child(chat)
	chat.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	await process_frame
	var backend: Node = load("res://addons/joinery/chat_bridge.gd").new()
	chat.add_child(backend)
	var meter: Control = chat.get("_header").get("usage")
	_check(not meter.visible, "Nothing reported, no ring")

	backend.call("_handle", {"kind": "execution", "model": "sol", "name": "GPT-6.1 Sol"})
	(
		backend
		. call(
			"_handle",
			{
				"kind": "usage",
				"model": "sol",
				"context_used": 31_200,
				"context_window": 272_000,
				"input_tokens": 90_000,
				"output_tokens": 4_000,
				"cached_tokens": 60_000,
			}
		)
	)
	_check(meter.visible, "Reported usage shows the ring")
	_check(is_equal_approx(meter.call("fraction"), 31_200.0 / 272_000.0), "It fills with context")
	_check(meter.tooltip_text == "Context 11% used", "Its tooltip names the share")
	var card: Control = meter.call("_make_custom_tooltip", meter.tooltip_text)
	var text := _texts(card)
	_check(text.contains("31k of 272k"), "The card shows context of window: " + text)
	_check(text.contains("90k (60k cached)"), "Input with its cached part: " + text)
	_check(text.contains("Output this session | 4.0k") or text.contains("4k"), "Output: " + text)
	_check(text.contains("GPT-6.1 Sol"), "The model by name: " + text)
	card.free()

	# A provider that reports tokens but no window: an empty ring and the counts.
	backend.call("_handle", {"kind": "usage", "input_tokens": 500, "output_tokens": 20})
	_check(meter.visible and meter.call("fraction") == 0.0, "No window, no fill")
	_check(meter.tooltip_text == "Tokens this session", "Says what it has")
	# Near the limit the ring warns (danger colour from 90%).
	backend.call("_handle", {"kind": "usage", "context_used": 95, "context_window": 100})
	_check(meter.call("fraction") >= meter.get("NEAR_FULL"), "Near the limit")
	# A report with nothing in it, or a new session, hides it again.
	backend.call("_handle", {"kind": "usage"})
	_check(not meter.visible, "An empty report hides the ring")
	backend.call("_handle", {"kind": "usage", "context_used": 10, "context_window": 100})
	backend.call("start", "", "", "", "")
	_check(not meter.visible, "A new session starts over")

	backend.queue_free()
	chat.queue_free()
	await process_frame
	print("usage meter: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)
