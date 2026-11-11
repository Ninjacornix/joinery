extends RefCounted
## The visual suite's sound screens: a music track in review and the Mixer tab. Copied beside
## visual_editor_test.gd by run_visual_suite.py; [param suite] is that test, whose `_shot`,
## `_check`, `_frames` and `_plugin` these use.


## A new music track in review: its layers with their stems and levels, loudness from the facts.
static func review_music(suite: Variant, dock: Control, panel: Control) -> void:
	var text := FileAccess.get_file_as_string("res://music/harbour.tres")
	var diff := "--- music/harbour.tres\n+++ music/harbour.tres\n@@ -0,0 +1,4 @@\n"
	for line: String in text.strip_edges().split("\n"):
		diff += "+" + line + "\n"
	var lead := ProjectSettings.globalize_path("res://audio/music/harbour/main_lead.wav")
	var stem := {"path": "audio/music/harbour/main_lead.wav", "media": "audio", "after": lead}
	var changes := [{"path": "music/harbour.tres", "change": "created"}]
	changes.append({"path": stem["path"], "change": "created"})
	dock.show_file_review("Compose a calm harbour theme", diff, [stem], changes)
	panel.call("show_glance", {"facts": ["4 layers", "Lead −15.8 LUFS", "Mix −16.1 LUFS"]})
	await dock.get_tree().create_timer(0.4).timeout
	await suite._shot("review-music", [panel, dock])


## The Mixer tab: a clean project with no music yet, then a mix in progress with a track.
static func mixer(suite: Variant) -> void:
	var tab: Control
	for node: Node in EditorInterface.get_base_control().find_children("Mixer", "", true, false):
		if node.get_script() == load("res://addons/joinery/mixer_tab.gd"):
			tab = node as Control
	suite._check(tab != null, "The Mixer tab exists")
	if tab == null:
		return
	var plugin: EditorPlugin = suite._plugin
	plugin.make_bottom_panel_item_visible(tab)
	var split: SplitContainer
	var walker: Node = tab
	while walker != null and split == null:
		walker = walker.get_parent()
		split = walker as SplitContainer
	var offset := split.split_offset if split != null else 0
	if split != null:
		split.split_offset = offset - 560
	tab.call("refresh")
	var music: Control = tab.get("music")
	music.call("select", "")
	await tab.get_tree().create_timer(0.3).timeout
	await suite._shot("mixer-empty", [tab])
	music.call("refresh")
	var strip: Control = tab.call("strip", "Music")
	(strip.get("fader") as Range).value = -4.5
	(tab.call("strip", "Ambience").get("reverb_amount") as Range).value = 0.4
	(tab.get("duck_buttons")["SFX"] as Button).button_pressed = true
	music.call("set_intensity", "danger")
	music.call("set_layer", "lead", "volume_db", -3.0)
	music.call("set_layer", "pads", "muted", true)
	await tab.get_tree().create_timer(0.3).timeout
	await suite._shot("mixer", [tab])
	tab.call("revert")
	if split != null:
		split.split_offset = offset
	plugin.hide_bottom_panel()
	await suite._frames()
