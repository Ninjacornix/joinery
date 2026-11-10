@tool
extends RefCounted
## Joinery motion: every animated state change goes through these helpers so the twenty motion
## rules hold everywhere and one switch turns them off.
##
## Rules, and the helper that carries each one:
## 1 ease-out entrances ([method enter], [method fade_in], [method stagger]) ·
## 2 menus 150–250 ms (MOTION.menu 0.18) · 3 stagger rows by 30 ms ([method stagger]) ·
## 4 scale 0.97 on press ([method press_feedback]) · 5 stretch a travelling knob 12%
## ([method attach_switch]) · 6 stagger as details settle ([method stagger],
## [method reveal]) · 7 arcs for movement ([method morph]) · 8 pulse to confirm ([method pulse],
## [method flash]) · 9 ease-in-out for moves ([method morph], [method attach_switch]) ·
## 10 popovers start at 0.95 ([method enter] with a trigger) · 11 enter from the trigger ·
## 12 exit to the trigger ([method exit] with a trigger) · 13 no bounce: only cubic and sine
## curves · 14 no tween while held (press scale is instant, release animates) · 15 exits 20%
## shorter (MOTION.exit, menu × 0.8) · 16 overlap old and new ([method crossfade]) · 17 masks
## for reveals ([method reveal]) · 18 crossfade icons with blur ([method crossfade] with blur)
## · 19 rubber band past bounds ([method rubber_band]) · 20 shared elements ([method morph]).
##
## Motion is off when the "Reduce motion" preference is on, and by default when running
## headless, so tests and captures are deterministic; tests can opt in with
## [method set_enabled]. When off, every helper jumps straight to the end state.

const UI_THEME := preload("ui_theme.gd")
## Scale of a pressed button (rule 4).
const PRESS_SCALE := 0.97
## Starting scale of a popover or menu (rule 10).
const POPOVER_SCALE := 0.95
## Knob stretch at mid-travel (rule 5).
const KNOB_STRETCH := 0.12
## Scale overshoot of a success pulse (rule 8).
const PULSE_SCALE := 1.03
## Stagger delays stop growing after this many rows, so long lists still settle quickly.
const STAGGER_CAP := 10
const _ENABLED_META := "joinery_motion_enabled"
const _TWEEN_META := "_joinery_tween_"
const _BLUR := """
shader_type canvas_item;
uniform float amount : hint_range(0.0, 1.0) = 0.0;
void fragment() {
	vec2 px = TEXTURE_PIXEL_SIZE * amount * 2.0;
	vec4 sum = texture(TEXTURE, UV) * 0.36;
	sum += texture(TEXTURE, UV + vec2(px.x, 0.0)) * 0.16;
	sum += texture(TEXTURE, UV - vec2(px.x, 0.0)) * 0.16;
	sum += texture(TEXTURE, UV + vec2(0.0, px.y)) * 0.16;
	sum += texture(TEXTURE, UV - vec2(0.0, px.y)) * 0.16;
	COLOR = sum * COLOR;
}
"""


## Turn motion on or off for this process (tests, captures). Reduce motion still wins.
static func set_enabled(on: bool) -> void:
	Engine.set_meta(_ENABLED_META, on)


## True when helpers animate: not headless (unless enabled explicitly) and Reduce motion off.
static func is_enabled() -> bool:
	if UI_THEME.prefs().get("reduce_motion") == "on":
		return false
	return bool(Engine.get_meta(_ENABLED_META, DisplayServer.get_name() != "headless"))


## A MOTION duration in seconds ("fast", "base", "confirm", "enter", "exit", "menu", "stagger").
static func duration(name: String) -> float:
	return float(UI_THEME.MOTION.get(name, UI_THEME.MOTION["base"]))


## Animate any property to [param value] (colour wipes, border colours, sizes) over a MOTION
## duration with ease-out cubic. [param host] owns the tween and must be in the tree; the
## target can be any Object, for example a StyleBoxFlat. Returns null when it jumped.
static func tween_to(
	host: Node, target: Object, property: String, value: Variant, kind := "fast"
) -> Tween:
	# Resource ids are often negative, and "-", ":" or "/" are not valid in a meta key.
	var key := "to_%x_%s" % [absi(target.get_instance_id()), property]
	var tween := _tween(host, key.replace(":", "_").replace("/", "_"))
	if tween == null:
		target.set_indexed(property, value)
		return null
	tween.set_trans(Tween.TRANS_CUBIC).set_ease(Tween.EASE_OUT)
	tween.tween_property(target, property, value, duration(kind))
	return tween


## Fade in: modulate alpha 0 to 1 over MOTION.base, ease-out (rules 1 and 16).
static func fade_in(node: CanvasItem) -> void:
	node.show()
	var tween := _tween(node, "presence")
	if tween == null:
		node.modulate.a = 1.0
		return
	node.modulate.a = 0.0
	tween.set_trans(Tween.TRANS_CUBIC).set_ease(Tween.EASE_OUT)
	tween.tween_property(node, "modulate:a", 1.0, duration("base"))


## Show [param node] with an entrance. Without a trigger it fades in (MOTION.enter). With a
## trigger it is a popover: it grows from 0.95 with its pivot on the edge nearest the trigger
## and fades in over MOTION.menu (rules 2, 10, 11). Position the node before calling.
## [param done] runs at the end (also when motion is off).
static func enter(node: CanvasItem, trigger: Control = null, done := Callable()) -> Tween:
	node.show()
	var tween := _tween(node, "presence")
	var control := node as Control
	if tween == null:
		node.modulate.a = 1.0
		if control != null:
			control.scale = Vector2.ONE
		_finish(done)
		return null
	node.modulate.a = 0.0
	var length := duration("menu" if trigger != null else "enter")
	tween.set_parallel(true).set_trans(Tween.TRANS_CUBIC).set_ease(Tween.EASE_OUT)
	tween.tween_property(node, "modulate:a", 1.0, length)
	if control != null and trigger != null:
		control.scale = Vector2.ONE
		control.pivot_offset = _pivot(control, trigger)
		control.scale = Vector2.ONE * POPOVER_SCALE
		tween.tween_property(control, "scale", Vector2.ONE, length)
	if done.is_valid():
		tween.chain().tween_callback(done)
	return tween


## Hide [param node] with an exit 20% shorter than its entrance, easing in (rules 12, 15).
## With a trigger it shrinks back towards it. At the end the node is hidden (or freed when
## [param free_after] is true), its alpha and scale are reset, and [param done] runs.
static func exit(
	node: CanvasItem, trigger: Control = null, done := Callable(), free_after := false
) -> Tween:
	var control := node as Control
	var finish := func() -> void:
		if not is_instance_valid(node):
			return
		node.hide()
		node.modulate.a = 1.0
		if control != null:
			control.scale = Vector2.ONE
		_finish(done)
		if free_after:
			node.queue_free()
	if not node.visible:
		_kill(node, "presence")
		finish.call()
		return null
	var tween := _tween(node, "presence")
	if tween == null:
		finish.call()
		return null
	var length := duration("menu") * 0.8 if trigger != null else duration("exit")
	tween.set_parallel(true).set_trans(Tween.TRANS_CUBIC).set_ease(Tween.EASE_IN)
	tween.tween_property(node, "modulate:a", 0.0, length)
	if control != null and trigger != null:
		control.pivot_offset = _pivot(control, trigger)
		tween.tween_property(control, "scale", Vector2.ONE * POPOVER_SCALE, length)
	tween.chain().tween_callback(finish)
	return tween


## Press feedback for a button: scale to 0.97 at once while held, animate back on release
## (rules 4 and 14). Safe to call more than once.
static func press_feedback(button: BaseButton) -> void:
	if button.has_meta("_joinery_press"):
		return
	button.set_meta("_joinery_press", true)
	button.button_down.connect(
		func() -> void:
			if not is_enabled():
				return
			_kill(button, "press")
			button.pivot_offset = button.size / 2.0
			button.scale = Vector2.ONE * PRESS_SCALE
	)
	button.button_up.connect(
		func() -> void:
			var tween := _tween(button, "press")
			if tween == null:
				button.scale = Vector2.ONE
				return
			tween.set_trans(Tween.TRANS_CUBIC).set_ease(Tween.EASE_OUT)
			tween.tween_property(button, "scale", Vector2.ONE, duration("fast"))
	)


## Confirm success (rule 8): a short scale pulse plus the accent [method flash].
static func pulse(node: Control) -> void:
	flash(node)
	var tween := _tween(node, "pulse")
	if tween == null:
		return
	node.pivot_offset = node.size / 2.0
	tween.set_trans(Tween.TRANS_CUBIC).set_ease(Tween.EASE_OUT)
	tween.tween_property(node, "scale", Vector2.ONE * PULSE_SCALE, duration("fast"))
	tween.set_trans(Tween.TRANS_SINE).set_ease(Tween.EASE_IN_OUT)
	tween.tween_property(node, "scale", Vector2.ONE, duration("base"))


## An accent tint behind the node's children that fades out over MOTION.confirm. It is drawn
## through the node's draw signal, so it never changes layout.
static func flash(node: Control) -> void:
	if not node.has_meta("_joinery_flash"):
		node.draw.connect(
			func() -> void:
				var amount := float(node.get_meta("_joinery_flash", 0.0))
				if amount <= 0.0:
					return
				var tint := UI_THEME.color(node, "accent")
				tint.a = 0.14 * amount
				var box := StyleBoxFlat.new()
				box.bg_color = tint
				box.set_corner_radius_all(UI_THEME.radius(node, "md"))
				node.draw_style_box(box, Rect2(Vector2.ZERO, node.size))
		)
	node.set_meta("_joinery_flash", 0.0)
	var tween := _tween(node, "flash")
	if tween == null:
		node.queue_redraw()
		return
	var step := func(value: float) -> void:
		node.set_meta("_joinery_flash", value)
		node.queue_redraw()
	tween.set_trans(Tween.TRANS_CUBIC).set_ease(Tween.EASE_OUT)
	tween.tween_method(step, 1.0, 0.0, duration("confirm"))


## Bring rows in one after another, [param step] seconds apart (MOTION.stagger by default),
## each fading in with ease-out (rules 3 and 6). Hidden rows are skipped.
static func stagger(nodes: Array, step := -1.0) -> void:
	var gap := duration("stagger") if step < 0.0 else step
	var index := 0
	for item: Variant in nodes:
		var node := item as CanvasItem
		if node == null or not node.visible:
			continue
		var tween := _tween(node, "presence")
		if tween == null:
			node.modulate.a = 1.0
			continue
		node.modulate.a = 0.0
		tween.set_trans(Tween.TRANS_CUBIC).set_ease(Tween.EASE_OUT)
		var delay := gap * mini(index, STAGGER_CAP)
		tween.tween_property(node, "modulate:a", 1.0, duration("enter")).set_delay(delay)
		index += 1


## Replace [param old] with [param new] by overlapping them: old fades out while new fades in,
## never a blank frame (rule 16). With [param blur], both also blur through the change (rule 18,
## for icon swaps such as Send and Stop). Put both in a container that stacks its children
## (MarginContainer, PanelContainer) when they share one slot. At the end old is hidden.
static func crossfade(old: CanvasItem, new: CanvasItem, blur := false) -> void:
	new.show()
	if not old.visible:
		_kill(new, "presence")
		new.modulate.a = 1.0
		return
	var tween := _tween(new, "presence")
	if tween == null:
		old.hide()
		new.modulate.a = 1.0
		return
	var old_tween := _tween(old, "presence")
	new.modulate.a = 0.0
	tween.set_parallel(true).set_trans(Tween.TRANS_CUBIC).set_ease(Tween.EASE_OUT)
	old_tween.set_parallel(true).set_trans(Tween.TRANS_CUBIC).set_ease(Tween.EASE_IN)
	tween.tween_property(new, "modulate:a", 1.0, duration("enter"))
	old_tween.tween_property(old, "modulate:a", 0.0, duration("exit"))
	if blur:
		var shader := Shader.new()
		shader.code = _BLUR
		for pair: Array in [[old, old_tween, 0.0, 1.0], [new, tween, 1.0, 0.0]]:
			var item: CanvasItem = pair[0]
			var material := ShaderMaterial.new()
			material.shader = shader
			material.set_shader_parameter("amount", pair[2])
			item.material = material
			(pair[1] as Tween).tween_property(
				material, "shader_parameter/amount", pair[3], duration("enter")
			)
	var restore := func() -> void:
		if is_instance_valid(old):
			old.hide()
			old.modulate.a = 1.0
			if blur:
				old.material = null
		if is_instance_valid(new) and blur:
			new.material = null
	tween.chain().tween_callback(restore)


## Shared-element transition (rules 7, 9, 20): a ghost with [param from]'s surface travels on
## a gentle arc, easing in and out, from its rect to [param to]'s rect while from fades out
## and to fades in at the end. Afterwards from is hidden and to is visible. [param to] should
## already be laid out (shown at least one frame) for an exact landing.
static func morph(from: Control, to: Control, done := Callable()) -> void:
	if not is_enabled() or not from.is_inside_tree() or not to.is_inside_tree():
		from.hide()
		to.show()
		to.modulate.a = 1.0
		_finish(done)
		return
	var start := from.get_global_rect()
	to.show()
	to.modulate.a = 0.0
	var ghost := Panel.new()
	ghost.top_level = true
	ghost.mouse_filter = Control.MOUSE_FILTER_IGNORE
	var surface := from.get_theme_stylebox("panel") if from is PanelContainer else null
	if surface == null:
		surface = UI_THEME.panel(
			UI_THEME.color(from, "surface_raised"),
			Color.TRANSPARENT,
			0,
			UI_THEME.radius(from, "md"),
			0
		)
	ghost.add_theme_stylebox_override("panel", surface)
	to.get_parent().add_child(ghost)
	ghost.global_position = start.position
	ghost.size = start.size
	from.modulate.a = 0.0
	var end := to.get_global_rect()
	var bend := (end.get_center() - start.get_center()).orthogonal() * 0.2
	var control := (start.get_center() + end.get_center()) / 2.0 + bend
	var travel := func(t: float) -> void:
		var a := start.get_center().lerp(control, t)
		var b := control.lerp(end.get_center(), t)
		var size := start.size.lerp(end.size, t)
		ghost.size = size
		ghost.global_position = a.lerp(b, t) - size / 2.0
	var tween := ghost.create_tween()
	tween.set_trans(Tween.TRANS_CUBIC).set_ease(Tween.EASE_IN_OUT)
	tween.tween_method(travel, 0.0, 1.0, duration("base") * 1.5)
	# The ghost hands over to [param to] only once it has landed.
	tween.tween_property(ghost, "modulate:a", 0.0, duration("fast"))
	tween.parallel().tween_property(to, "modulate:a", 1.0, duration("fast"))
	tween.tween_callback(
		func() -> void:
			if is_instance_valid(from):
				from.hide()
				from.modulate.a = 1.0
			ghost.queue_free()
			_finish(done)
	)


## Reveal [param node] behind a moving mask edge (rules 6 and 17): its children appear from the
## top ("down") or from the bottom ("up") over MOTION.enter. Use it on a container that draws
## nothing itself (VBox, Margin); a node that draws its own panel falls back to a fade.
static func reveal(node: Control, direction := "down") -> void:
	node.show()
	if node is PanelContainer or node is Panel:
		fade_in(node)
		return
	# A reveal interrupted by another one must not leave its mask behind.
	_end_reveal(node)
	var tween := _tween(node, "reveal")
	if tween == null:
		return
	var prior := node.clip_children
	node.clip_children = CanvasItem.CLIP_CHILDREN_ONLY
	node.set_meta("_joinery_reveal", 0.0)
	var mask := func() -> void:
		var shown := float(node.get_meta("_joinery_reveal", 1.0)) * node.size.y
		var top := 0.0 if direction == "down" else node.size.y - shown
		node.draw_rect(Rect2(0.0, top, node.size.x, shown), Color.WHITE)
	node.draw.connect(mask)
	node.set_meta("_joinery_reveal_state", [mask, prior])
	var step := func(value: float) -> void:
		node.set_meta("_joinery_reveal", value)
		node.queue_redraw()
	tween.set_trans(Tween.TRANS_CUBIC).set_ease(Tween.EASE_OUT)
	tween.tween_method(step, 0.0, 1.0, duration("enter"))
	tween.tween_callback(_end_reveal.bind(node))


## Set [param label] to [param value] like an odometer: changed digits roll vertically (up when
## the value grows), unchanged characters stay put. [param format] is a format string such as
## "%d files" or a Callable(value) -> String; use [method format_clock] for m:ss timers. Give
## the label the JoineryNumeric variation (tabular digits) so widths do not jitter. The first
## call, or any call with motion off, sets the text directly.
static func roll_number(label: Label, value: float, format: Variant = "%d") -> void:
	var text := _format(value, format)
	var previous := label.text
	var last := float(label.get_meta("_joinery_value", value))
	label.set_meta("_joinery_value", value)
	if previous == text:
		return
	label.text = text
	if previous.is_empty() or not label.is_inside_tree():
		_kill(label, "roll")
		_end_roll(label)
		return
	var tween := _tween(label, "roll")
	if tween == null:
		_end_roll(label)
		return
	label.set_meta("_joinery_roll", [previous, text, 0.0, 1.0 if value >= last else -1.0])
	if not label.has_meta("_joinery_roll_drawn"):
		label.set_meta("_joinery_roll_drawn", true)
		label.draw.connect(_draw_roll.bind(label))
	label.visible_characters = 0
	var step := func(progress: float) -> void:
		var state: Array = label.get_meta("_joinery_roll", [])
		if state.size() == 4:
			state[2] = progress
		label.queue_redraw()
	tween.set_trans(Tween.TRANS_CUBIC).set_ease(Tween.EASE_OUT)
	tween.tween_method(step, 0.0, 1.0, duration("base"))
	tween.tween_callback(_end_roll.bind(label))


## Seconds as m:ss ("0:07", "1:15", "12:03").
static func format_clock(seconds: float) -> String:
	var total := maxi(0, floori(seconds))
	return "%d:%02d" % [total / 60, total % 60]


## Elastic overscroll (rule 19): wheel past the top or bottom of [param scroll] nudges its
## content a little, with resistance, and it settles back with ease-out.
static func rubber_band(scroll: ScrollContainer) -> void:
	if scroll.has_meta("_joinery_rubber"):
		return
	scroll.set_meta("_joinery_rubber", 0.0)
	scroll.gui_input.connect(
		func(event: InputEvent) -> void:
			var wheel := event as InputEventMouseButton
			if wheel == null or not wheel.pressed or not is_enabled():
				return
			var direction := 0.0
			var bar := scroll.get_v_scroll_bar()
			if wheel.button_index == MOUSE_BUTTON_WHEEL_UP and scroll.scroll_vertical <= 0:
				direction = 1.0
			elif (
				wheel.button_index == MOUSE_BUTTON_WHEEL_DOWN
				and scroll.scroll_vertical >= int(bar.max_value - bar.page) - 1
			):
				direction = -1.0
			if direction != 0.0:
				_nudge(scroll, direction)
	)


## Keep the true bottom of [param scroll] in view after content was added (a status line
## under the last message): once layout has grown the scroll range, scroll to its end, with
## a short ease when motion is on. Safe to call repeatedly; the newest call wins.
static func scroll_to_end(scroll: ScrollContainer) -> void:
	if not is_instance_valid(scroll):
		return
	var bar := scroll.get_v_scroll_bar()
	var settle := func() -> void:
		if not is_instance_valid(scroll):
			return
		var end := int(maxf(0.0, bar.max_value - bar.page))
		var tween := _tween(scroll, "scroll")
		if tween == null or absi(end - scroll.scroll_vertical) < 2:
			scroll.scroll_vertical = end
			return
		tween.set_trans(Tween.TRANS_QUART).set_ease(Tween.EASE_OUT)
		tween.tween_property(scroll, "scroll_vertical", end, duration("fast"))
	if not bar.changed.is_connected(settle):
		bar.changed.connect(settle, CONNECT_ONE_SHOT | CONNECT_DEFERRED)
	settle.call_deferred()


## Play the knob travel of a JoinerySwitch when it toggles: the in-between theme frames, with
## the knob stretched 12% mid-way, easing in and out (rules 5 and 9).
static func attach_switch(check: CheckButton) -> void:
	if check.has_meta("_joinery_switch"):
		return
	check.set_meta("_joinery_switch", true)
	check.toggled.connect(
		func(on: bool) -> void:
			var last := UI_THEME.SWITCH_FRAMES
			if not check.has_theme_icon("frame_%d" % last, "JoinerySwitch"):
				return
			var tween := _tween(check, "switch")
			if tween == null:
				return
			var show_frame := func(position: float) -> void:
				var frame := check.get_theme_icon("frame_%d" % roundi(position), "JoinerySwitch")
				for name: String in [
					"checked", "unchecked", "checked_mirrored", "unchecked_mirrored"
				]:
					check.add_theme_icon_override(name, frame)
			tween.set_trans(Tween.TRANS_SINE).set_ease(Tween.EASE_IN_OUT)
			var span := [0.0, float(last)] if on else [float(last), 0.0]
			tween.tween_method(show_frame, span[0], span[1], duration("base"))
			tween.tween_callback(
				func() -> void:
					for name: String in [
						"checked", "unchecked", "checked_mirrored", "unchecked_mirrored"
					]:
						check.remove_theme_icon_override(name)
			)
	)


## Loading pulse: alpha between 0.6 and 1 over one second, until [method stop] or the node
## leaves the tree. Returns null when motion is off (the node then stays fully visible).
static func breathe(node: CanvasItem) -> Tween:
	var tween := _tween(node, "presence")
	if tween == null:
		node.modulate.a = 1.0
		return null
	tween.set_loops()
	tween.set_trans(Tween.TRANS_SINE).set_ease(Tween.EASE_IN_OUT)
	tween.tween_property(node, "modulate:a", 0.6, 0.5)
	tween.tween_property(node, "modulate:a", 1.0, 0.5)
	return tween


## Stop every Joinery animation on [param node] and restore its resting alpha and scale.
static func stop(node: CanvasItem) -> void:
	for key: StringName in node.get_meta_list():
		if str(key).begins_with(_TWEEN_META):
			var tween: Tween = node.get_meta(key)
			if tween != null and tween.is_valid():
				tween.kill()
			node.remove_meta(key)
	node.modulate.a = 1.0
	if node is Control:
		(node as Control).scale = Vector2.ONE


static func _tween(node: Node, channel: String) -> Tween:
	var key := _TWEEN_META + channel
	_kill(node, channel)
	if not is_enabled() or not node.is_inside_tree():
		return null
	var tween := node.create_tween()
	node.set_meta(key, tween)
	return tween


## Kill a running Joinery tween on [param node] without starting a new one.
static func _kill(node: Node, channel: String) -> void:
	var key := _TWEEN_META + channel
	if not node.has_meta(key):
		return
	var running: Tween = node.get_meta(key)
	if running != null and running.is_valid():
		running.kill()
	node.remove_meta(key)


## Undo a reveal mask (finished or interrupted): disconnect it and restore clip_children.
static func _end_reveal(node: Control) -> void:
	if not is_instance_valid(node) or not node.has_meta("_joinery_reveal_state"):
		return
	var state: Array = node.get_meta("_joinery_reveal_state")
	node.remove_meta("_joinery_reveal_state")
	var mask: Callable = state[0]
	if node.draw.is_connected(mask):
		node.draw.disconnect(mask)
	node.clip_children = state[1]
	if node.has_meta("_joinery_reveal"):
		node.remove_meta("_joinery_reveal")
	node.queue_redraw()


static func _finish(done: Callable) -> void:
	if done.is_valid():
		done.call()


## The point of [param control] (local) closest to the trigger's centre.
static func _pivot(control: Control, trigger: Control) -> Vector2:
	var local := (
		control.get_global_transform().affine_inverse() * trigger.get_global_rect().get_center()
	)
	return Vector2(clampf(local.x, 0.0, control.size.x), clampf(local.y, 0.0, control.size.y))


static func _format(value: float, format: Variant) -> String:
	if format is Callable:
		return str((format as Callable).call(value))
	var pattern := str(format)
	if pattern.contains("%d") or pattern.contains("%i"):
		return pattern % roundi(value)
	return pattern % value if pattern.contains("%") else str(value)


static func _end_roll(label: Label) -> void:
	if not is_instance_valid(label):
		return
	label.remove_meta("_joinery_roll")
	label.visible_characters = -1
	label.queue_redraw()


static func _draw_roll(label: Label) -> void:
	var state: Array = label.get_meta("_joinery_roll", [])
	if state.size() != 4:
		return
	var font := label.get_theme_font("font")
	var size := label.get_theme_font_size("font_size")
	var ink := label.get_theme_color("font_color")
	var box := label.get_theme_stylebox("normal")
	var new_text := str(state[1])
	var width := font.get_string_size(new_text, HORIZONTAL_ALIGNMENT_LEFT, -1, size).x
	var left := box.get_margin(SIDE_LEFT)
	if label.horizontal_alignment == HORIZONTAL_ALIGNMENT_CENTER:
		left = (label.size.x - width) / 2.0
	elif label.horizontal_alignment == HORIZONTAL_ALIGNMENT_RIGHT:
		left = label.size.x - box.get_margin(SIDE_RIGHT) - width
	var line := font.get_height(size)
	var baseline := (label.size.y - line) / 2.0 + font.get_ascent(size)
	var count := maxi(str(state[0]).length(), new_text.length())
	var before := str(state[0]).lpad(count)
	var after := new_text.lpad(count)
	var progress := float(state[2])
	var shift := float(state[3]) * line * 0.7
	var x := left + width
	for index: int in range(count - 1, -1, -1):
		var old_char := before.unicode_at(index)
		var new_char := after.unicode_at(index)
		x -= font.get_char_size(new_char if new_char != 32 else old_char, size).x
		var canvas := label.get_canvas_item()
		if old_char == new_char:
			font.draw_char(canvas, Vector2(x, baseline), new_char, size, ink)
			continue
		var fading := Color(ink, ink.a * (1.0 - progress))
		var arriving := Color(ink, ink.a * progress)
		font.draw_char(canvas, Vector2(x, baseline - shift * progress), old_char, size, fading)
		var y := baseline + shift * (1.0 - progress)
		font.draw_char(canvas, Vector2(x, y), new_char, size, arriving)


static func _nudge(scroll: ScrollContainer, direction: float) -> void:
	var content: Control = null
	for child: Node in scroll.get_children():
		if child is Control:
			content = child
			break
	if content == null:
		return
	var limit := float(UI_THEME.space(scroll, 5))
	var offset := float(scroll.get_meta("_joinery_rubber", 0.0))
	var base := content.position.y - offset
	offset = clampf(offset + direction * limit * 0.35 * (1.0 - absf(offset) / limit), -limit, limit)
	scroll.set_meta("_joinery_rubber", offset)
	content.position.y = base + offset
	var tween := _tween(scroll, "rubber")
	if tween == null:
		content.position.y = base
		scroll.set_meta("_joinery_rubber", 0.0)
		return
	var settle := func(value: float) -> void:
		scroll.set_meta("_joinery_rubber", value)
		if is_instance_valid(content):
			content.position.y = base + value
	tween.set_trans(Tween.TRANS_CUBIC).set_ease(Tween.EASE_OUT)
	tween.tween_method(settle, offset, 0.0, duration("enter"))
