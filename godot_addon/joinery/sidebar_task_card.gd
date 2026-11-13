@tool
extends PanelContainer
## One agent working beside Journeyman (a parallel task, ADR 0036, or a debugging task, ADR
## 0037): an outlined card with the task's title and its own Stop, and under it one caption
## line: the apps at work, the current step and a quiet timer while it works; "Ready for review"
## with a Review link once its change passed Joinery's checks; then the outcome. The change
## itself is decided in the Review tab, like every other change. A debugging task's card is
## titled "Debugging: <error>" (the error in full on hover); its debugger reads, then a
## worker makes the fix, and the cause it found is on the caption's hover.

signal stop_pressed
signal review_pressed

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const STYLE := preload("sidebar_style.gd")
const INDICATOR := preload("activity_indicator.gd")
const REVIEW_TAB := preload("review_tab.gd")
## Final state -> [status icon kind, words].
const OUTCOMES := {
	"accepted": ["done", "Change accepted"],
	"discarded": ["stopped", "Discarded. Your files were not touched."],
	"done": ["done", "Done"],
	"failed": ["failed", "Didn't finish"],
	"stopped": ["stopped", "Stopped"],
}

var task_id := ""
var title := ""
## working, in_review, accepted, discarded, done, failed or stopped.
var state := "working"
var transaction_id := ""
## The agent role at work (joinery.roles): worker, or debugger while a debug task reads.
var role := "worker"
var title_label: Label
var stop: Button
var _dots: Control
var _status: TextureRect
var _apps: HBoxContainer
var _shown_apps: Array[String] = []
var _step: Label
var _timer: Label
var _review: Button
var _started_msec := 0
var _last_second := -1


func _init(identifier := "", name_text := "") -> void:
	task_id = identifier
	title = name_text
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	accessibility_name = "Task: " + name_text
	# The queued card's outline: a task is quieter than the user's own request.
	STYLE.on_theme_overrides(
		self,
		func() -> void:
			var source := get_theme_stylebox("panel", "JoineryChangeCard")
			if source == null:
				return
			var box := source.duplicate() as StyleBox
			box.set_content_margin_all(float(UI_THEME.space(self, 2)))
			add_theme_stylebox_override("panel", box)
	)
	var column := VBoxContainer.new()
	column.theme_type_variation = "JoineryTight"
	add_child(column)
	var head := HBoxContainer.new()
	head.theme_type_variation = "JoineryRowTight"
	column.add_child(head)
	title_label = UI.label(name_text)
	title_label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	title_label.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	title_label.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	title_label.tooltip_text = name_text
	head.add_child(title_label)
	# Framed like the composer's Stop, so it reads as a button, not a grey square.
	stop = UI.icon_button("stop", "Stop this task")
	stop.theme_type_variation = "JoineryStopButton"
	stop.accessibility_name = "Stop task: " + name_text
	stop.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	UI_MOTION.press_feedback(stop)
	STYLE.on_theme_overrides(
		stop,
		func() -> void:
			var side := float(UI_THEME.control_h(stop, "sm"))
			stop.custom_minimum_size = Vector2(side, side)
	)
	stop.pressed.connect(_on_stop)
	head.add_child(stop)
	var line := HBoxContainer.new()
	line.theme_type_variation = "JoineryRowTight"
	UI.bind_height(line, "xs", false)
	column.add_child(line)
	_dots = INDICATOR.new()
	_dots.set("running", true)
	line.add_child(_dots)
	_status = UI.status_icon("done")
	_status.hide()
	line.add_child(_status)
	_apps = HBoxContainer.new()
	_apps.theme_type_variation = "JoineryRowTight"
	_apps.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_apps.hide()
	line.add_child(_apps)
	_step = UI.label("Starting…", "JoineryCaption")
	_step.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_step.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_step.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	line.add_child(_step)
	_review = UI.link("Review")
	_review.accessibility_name = "Review this task's change"
	UI.bind_height(_review, "xs")
	_review.pressed.connect(func() -> void: review_pressed.emit())
	_review.hide()
	line.add_child(_review)
	_timer = UI.label("", "JoineryNumeric")
	_timer.text_overrun_behavior = TextServer.OVERRUN_NO_TRIMMING
	_timer.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_timer.accessibility_name = "Time this task has worked"
	_timer.hide()
	line.add_child(_timer)
	_started_msec = Time.get_ticks_msec()
	set_process(true)


func _process(_delta: float) -> void:
	if state != "working":
		set_process(false)
		return
	var seconds := int((Time.get_ticks_msec() - _started_msec) / 1000.0)
	if seconds == _last_second:
		return
	_last_second = seconds
	set_elapsed(seconds)


## Seconds the task has worked; the timer appears after three, like the request card's.
func set_elapsed(seconds: int) -> void:
	if seconds < 3 or state != "working":
		return
	_timer.text = UI_MOTION.format_clock(seconds)
	if not _timer.visible:
		UI_MOTION.fade_in(_timer)


## The task's state from the host: {state, step, message, apps, transaction_id, elapsed_s}.
func apply(event: Dictionary) -> void:
	var next := str(event.get("state", state))
	transaction_id = str(event.get("transaction_id", transaction_id))
	_set_role(event)
	if event.has("elapsed_s") and next == "working" and _last_second < 0:
		_started_msec = Time.get_ticks_msec() - int(event["elapsed_s"]) * 1000
	_set_apps(event.get("apps", []))
	if next == state and next == "working":
		if not str(event.get("step", "")).is_empty():
			set_step(str(event["step"]))
		return
	state = next
	match state:
		"working":
			pass
		"in_review":
			_settle("done", "Ready for review")
			_review.show()
			UI_MOTION.pulse(self)
		_:
			var spec: Array = OUTCOMES.get(state, OUTCOMES["failed"])
			var words := str(spec[1])
			var message := str(event.get("message", ""))
			if state == "failed" and not message.is_empty():
				words = message
			elif state == "done" and not message.is_empty():
				words = message
			_settle(str(spec[0]), words)
			_review.hide()


## A debugging task: the error on the title's hover, then the cause its debugger found.
func _set_role(event: Dictionary) -> void:
	role = str(event.get("role", role))
	var error := str(event.get("error", ""))
	if not error.is_empty():
		title_label.tooltip_text = error
		accessibility_name = "Debugging task: " + error
	var cause := str(event.get("cause", ""))
	if not cause.is_empty():
		_step.tooltip_text = "Cause: " + cause


## The step the task's agent is on ("Working in Blender…").
func set_step(text: String) -> void:
	if state != "working" or text.is_empty():
		return
	_step.text = text
	_step.tooltip_text = text


## Still working: its Stop is live.
func working() -> bool:
	return state == "working"


func step_text() -> String:
	return _step.text


func _settle(kind: String, words: String) -> void:
	_dots.set("running", false)
	_timer.hide()
	stop.hide()
	UI.set_status_icon(_status, kind)
	_status.show()
	_step.text = words
	_step.tooltip_text = words


func _on_stop() -> void:
	if mark_stopping():
		stop_pressed.emit()


## Optimistic: the card says "Stopping…" at once; the host confirms with "stopped". False when
## the task no longer works.
func mark_stopping() -> bool:
	if state != "working" or stop.disabled:
		return false
	stop.disabled = true
	_step.text = "Stopping…"
	return true


## App logos at text size, once each, in the order the task first used them.
func _set_apps(apps: Variant) -> void:
	if not apps is Array:
		return
	for app: Variant in apps:
		var name := str(REVIEW_TAB.APP_NAMES.get(str(app), str(app).capitalize()))
		if _shown_apps.has(name):
			continue
		var logo := _logo(name)
		if logo == null:
			continue
		_shown_apps.append(name)
		logo.tooltip_text = name
		_apps.add_child(logo)
		_apps.show()


func _logo(app: String) -> TextureRect:
	var slug := app.to_lower().replace(" ", "_")
	var base := (get_script() as Script).resource_path.get_base_dir()
	for name: String in [slug, slug.replace("_", "-"), slug.replace("_", "")]:
		if UI_THEME.has_app_logo(name):
			var logo := STYLE.glyph("")
			logo.mouse_filter = Control.MOUSE_FILTER_PASS
			STYLE.on_theme(logo, func() -> void: logo.texture = UI_THEME.app_logo(logo, name))
			return logo
		var path := base.path_join("icons/tools/%s.svg" % name)
		if FileAccess.file_exists(path):
			var picture := STYLE.glyph("text_muted")
			picture.mouse_filter = Control.MOUSE_FILTER_PASS
			STYLE.on_theme(
				picture,
				func() -> void:
					var image := Image.new()
					var svg := FileAccess.get_file_as_string(path)
					if image.load_svg_from_string(svg, UI_THEME.unit(picture)) == OK:
						picture.texture = ImageTexture.create_from_image(image)
			)
			return picture
	return null


## Controls whose text follows the transcript text size, as [control, TYPE step] pairs.
func scalables() -> Array:
	return [[title_label, "body"], [_step, "caption"]]
