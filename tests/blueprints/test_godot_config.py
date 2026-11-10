"""GodotConfig round-trips and input events that stock Godot reads back."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from joinery.catalog import ROOT
from joinery.godot_config import (
    KEYS,
    MOUSE_BUTTONS,
    GodotConfig,
    action_deadzone,
    action_events,
    action_value,
    event_identity,
    key_event,
    mouse_event,
    packed_strings,
    pad_axis_event,
    pad_button_event,
    parse_string,
    to_variant,
)
from tests.blueprints.godot_run import needs_godot, run_godot

TRICKY = """; comment
config_version=5

[application]

config/name="A [tricky] {name}"
config/description="Line one
line \\"two\\" ; not a comment
[not a section]"
config/features=PackedStringArray("4.7")

[input]

jump={
"deadzone": 0.5,
"events": [Object(InputEventKey,"resource_local_to_scene":false,"resource_name":"","device":-1,"window_id":0,"alt_pressed":false,"shift_pressed":false,"ctrl_pressed":false,"meta_pressed":false,"pressed":false,"keycode":0,"physical_keycode":32,"key_label":0,"unicode":0,"location":0,"echo":false,"script":null)
]
}

[rendering]

environment/defaults/default_clear_color=Color(0.1, 0.1, 0.1, 1)
"""


@pytest.mark.parametrize(
    "path",
    ["testbed/project.godot", "templates/project/project.godot"],
)
def test_repo_files_round_trip(path: str) -> None:
    text = (ROOT / path).read_text()
    assert GodotConfig.parse(text).dump() == text


def test_tricky_values_parse_and_round_trip() -> None:
    config = GodotConfig.parse(TRICKY)
    assert config.dump() == TRICKY
    assert config.sections() == ["application", "input", "rendering"]
    assert config.get("", "config_version") == "5"
    description = config.get("application", "config/description") or ""
    assert parse_string(description) == 'Line one\nline "two" ; not a comment\n[not a section]'
    assert parse_string(config.get("application", "config/name") or "") == "A [tricky] {name}"
    jump = config.get("input", "jump") or ""
    assert action_deadzone(jump) == 0.5
    assert action_events(jump) == [key_event("Space")]
    assert config.keys("rendering") == ["environment/defaults/default_clear_color"]


def test_edits_undo_to_identical_bytes() -> None:
    config = GodotConfig.parse(TRICKY)
    config.set("application", "run/main_scene", '"res://main.tscn"')
    config.set("autoload", "Game", '"*res://game.gd"')  # new section, sorted in
    config.set("zeta", "x", "1")  # new last section
    config.set("rendering", "environment/defaults/default_clear_color", "Color(0, 0, 0, 1)")
    text = config.dump()
    assert text.index("[autoload]") < text.index("[input]")
    assert "\n\n[zeta]\n\nx=1\n" in text
    again = GodotConfig.parse(text)
    assert again.dump() == text
    again.erase("application", "run/main_scene")
    again.remove_section("autoload")
    again.remove_section("zeta")
    again.set("rendering", "environment/defaults/default_clear_color", "Color(0.1, 0.1, 0.1, 1)")
    assert again.dump() == TRICKY


def test_presets_rename_and_append_unsorted() -> None:
    config = GodotConfig.parse('[preset.0]\n\nname="Web"\n\n[preset.0.options]\n\na=1\n')
    config.add_section("preset.1", [("name", '"Linux"')], sort=False)
    config.add_section("preset.1.options", [("b", "2")], sort=False)
    assert config.sections() == ["preset.0", "preset.0.options", "preset.1", "preset.1.options"]
    config.remove_section("preset.0")
    config.remove_section("preset.0.options")
    config.rename_section("preset.1", "preset.0")
    config.rename_section("preset.1.options", "preset.0.options")
    assert config.dump() == '[preset.0]\n\nname="Linux"\n\n[preset.0.options]\n\nb=2\n'


def test_variants() -> None:
    assert to_variant(True) == "true"
    assert to_variant(3) == "3"
    assert to_variant(1.0) == "1.0"
    assert to_variant(0.25) == "0.25"
    assert to_variant('say "hi"\\') == '"say \\"hi\\"\\\\"'
    assert to_variant(["a", "b"]) == 'PackedStringArray("a", "b")'
    assert to_variant([]) == "PackedStringArray()"
    assert packed_strings('PackedStringArray("a", "b\\"c")') == ["a", 'b"c']
    assert packed_strings('["x"]') == ["x"]
    with pytest.raises(ValueError):
        packed_strings("42")


def test_action_value_matches_godot_layout() -> None:
    # Copied from a project.godot written by stock Godot 4.7.2.
    assert action_value(0.5, []) == '{\n"deadzone": 0.5,\n"events": []\n}'
    one = action_value(0.25, [key_event("A")])
    assert one == '{\n"deadzone": 0.25,\n"events": [' + key_event("A") + "\n]\n}"
    two = action_value(0.2, [key_event("A"), pad_button_event(0)])
    assert two.endswith("\n, " + pad_button_event(0) + "\n]\n}")
    assert action_events(two) == [key_event("A"), pad_button_event(0)]


def test_event_identity_ignores_device_noise() -> None:
    editor = key_event("A").replace('"device":-1', '"device":16')
    assert event_identity(editor) == event_identity(key_event("A"))
    assert event_identity(key_event("A")) != event_identity(key_event("B"))
    assert event_identity(pad_axis_event("0-")) != event_identity(pad_axis_event("0+"))
    with pytest.raises(ValueError):
        key_event("Hyper")
    with pytest.raises(ValueError):
        pad_axis_event("9+")


CHECK_INPUT = """extends SceneTree

## Reads InputMap back and re-saves the settings, so Python can compare.


func _init() -> void:
	var expected: Dictionary = JSON.parse_string(FileAccess.get_file_as_string("res://expected.json"))
	var failures: PackedStringArray = PackedStringArray()
	for action: String in expected:
		if not InputMap.has_action(action):
			failures.append("missing action " + action)
			continue
		var events: Array[InputEvent] = InputMap.action_get_events(action)
		var wanted: Array = expected[action]
		if events.size() != wanted.size():
			failures.append("%s has %d events, want %d" % [action, events.size(), wanted.size()])
			continue
		for i: int in range(events.size()):
			var got: String = _describe(events[i])
			if got != str(wanted[i]):
				failures.append("%s[%d] is %s, want %s" % [action, i, got, wanted[i]])
	ProjectSettings.save_custom("res://resaved.godot")
	if failures.is_empty():
		print("input_check: ok")
	for failure: String in failures:
		print("FAIL: ", failure)
	quit(0 if failures.is_empty() else 1)


func _describe(event: InputEvent) -> String:
	if event is InputEventKey:
		return "key:%d" % (event as InputEventKey).physical_keycode
	if event is InputEventMouseButton:
		return "mouse:%d" % (event as InputEventMouseButton).button_index
	if event is InputEventJoypadButton:
		return "pad:%d" % (event as InputEventJoypadButton).button_index
	if event is InputEventJoypadMotion:
		var motion: InputEventJoypadMotion = event as InputEventJoypadMotion
		return "axis:%d:%d" % [motion.axis, int(motion.axis_value)]
	return "other"
"""


@needs_godot
def test_input_events_load_in_stock_godot(tmp_path: Path) -> None:
    project = tmp_path / "input"
    project.mkdir()
    config = GodotConfig.parse(
        'config_version=5\n\n[application]\n\nconfig/name="Input"\n'
        'config/features=PackedStringArray("4.7")\n'
    )
    expected: dict[str, list[str]] = {}
    for name, code in KEYS.items():
        action = f"key_{name.lower()}"
        config.set("input", action, action_value(0.2, [key_event(name)]))
        expected[action] = [f"key:{code}"]
    config.set("input", "mouse_all", action_value(0.2, [mouse_event(m) for m in MOUSE_BUTTONS]))
    expected["mouse_all"] = [f"mouse:{i}" for i in MOUSE_BUTTONS.values()]
    pads = [pad_button_event(b) for b in range(15)]
    axes = [pad_axis_event(f"{a}{s}") for a in range(6) for s in "-+"]
    config.set("input", "pad_all", action_value(0.25, pads + axes))
    expected["pad_all"] = [f"pad:{b}" for b in range(15)] + [
        f"axis:{a}:{-1 if s == '-' else 1}" for a in range(6) for s in "-+"
    ]
    (project / "project.godot").write_text(config.dump())
    (project / "expected.json").write_text(json.dumps(expected))
    (project / "check_input.gd").write_text(CHECK_INPUT)
    run_godot(project, "res://check_input.gd", marker="input_check")
    resaved = GodotConfig.parse((project / "resaved.godot").read_text())
    for action in expected:
        assert resaved.get("input", action) == config.get("input", action), action


@needs_godot
def test_godot_written_files_round_trip(tmp_path: Path) -> None:
    project = tmp_path / "written"
    project.mkdir()
    (project / "project.godot").write_text(
        'config_version=5\n\n[application]\n\nconfig/name="Written"\n'
    )
    (project / "write.gd").write_text(
        "extends SceneTree\n\n\nfunc _init() -> void:\n"
        '\tProjectSettings.set_setting("application/config/description", "a\\nb \\"c\\"")\n'
        '\tProjectSettings.set_setting("display/window/size/viewport_width", 1280)\n'
        '\tvar key: InputEventKey = InputEventKey.new()\n'
        "\tkey.physical_keycode = KEY_A\n"
        '\tProjectSettings.set_setting("input/probe", {"deadzone": 0.2, "events": [key, key]})\n'
        '\tProjectSettings.set_setting("internationalization/locale/translations", '
        'PackedStringArray(["res://a.po", "res://b.po"]))\n'
        "\tProjectSettings.save()\n"
        "\tvar cfg: ConfigFile = ConfigFile.new()\n"
        '\tcfg.set_value("preset.0", "name", "Linux")\n'
        '\tcfg.set_value("preset.0.options", "binary_format/embed_pck", false)\n'
        '\tcfg.save("res://export_presets.cfg")\n'
        '\tprint("write: ok")\n'
        "\tquit(0)\n"
    )
    run_godot(project, "res://write.gd", marker="write")
    for name in ("project.godot", "export_presets.cfg"):
        text = (project / name).read_text()
        assert GodotConfig.parse(text).dump() == text, name
    config = GodotConfig.parse((project / "project.godot").read_text())
    assert packed_strings(
        config.get("internationalization", "locale/translations") or ""
    ) == ["res://a.po", "res://b.po"]
    assert len(action_events(config.get("input", "probe") or "")) == 2
