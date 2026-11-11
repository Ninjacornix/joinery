"""The audio bus layout the Mixer reads and writes (joinery/mixer.py)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from joinery import mixer

#: What Godot 4.7 itself saves for a Music bus with a ducking compressor and a
#: reverb, a Voice bus with an effect the Mixer doesn't manage, and a header uid.
GODOT_SAVED = """[gd_resource type="AudioBusLayout" format=3 uid="uid://b2mix"]

[sub_resource type="AudioEffectCompressor" id="AudioEffectCompressor_qymsb"]
threshold = -24.0
attack_us = 20000.0
sidechain = &"Voice"

[sub_resource type="AudioEffectReverb" id="AudioEffectReverb_6j2fm"]
room_size = 0.6
wet = 0.25

[sub_resource type="AudioEffectChorus" id="AudioEffectChorus_snu11"]
voice_count = 3
voice/1/delay_ms = 12.0

[sub_resource type="AudioEffectEQ6" id="unused_eq"]
band_db/32_hz = -3.0

[resource]
bus/1/name = &"Music"
bus/1/solo = false
bus/1/mute = false
bus/1/bypass_fx = false
bus/1/volume_db = -3.5
bus/1/send = &"Master"
bus/1/effect/0/effect = SubResource("AudioEffectCompressor_qymsb")
bus/1/effect/0/enabled = true
bus/1/effect/1/effect = SubResource("AudioEffectReverb_6j2fm")
bus/1/effect/1/enabled = false
bus/2/name = &"Voice"
bus/2/solo = false
bus/2/mute = true
bus/2/bypass_fx = false
bus/2/volume_db = 0.0
bus/2/send = &"Master"
bus/2/effect/0/effect = SubResource("AudioEffectChorus_snu11")
bus/2/effect/0/enabled = true
"""


def test_reads_what_godot_saves() -> None:
    layout = mixer.from_tres(GODOT_SAVED)
    assert [b.name for b in layout.buses] == ["Master", "Music", "Voice"]
    music = layout.bus("Music")
    assert music is not None and music.volume_db == -3.5
    squeeze = music.first(mixer.COMPRESSOR)
    assert squeeze is not None and squeeze.sidechain == "Voice"
    assert squeeze.number("threshold") == -24.0 and squeeze.number("ratio") == 4.0
    room = music.first(mixer.REVERB)
    assert room is not None and not room.enabled and room.number("wet") == 0.25
    voice = layout.bus("Voice")
    assert voice is not None and voice.mute
    assert mixer.ducking(layout) == "Voice"


def test_round_trip_is_stable_and_keeps_what_the_mixer_does_not_manage() -> None:
    layout = mixer.from_tres(GODOT_SAVED)
    text = mixer.to_tres(layout)
    assert mixer.to_tres(mixer.from_tres(text)) == text
    again = mixer.from_tres(text)
    assert again.model_dump() == layout.model_dump()
    # The chorus keeps its settings, the unused EQ stays, the uid stays.
    assert "voice_count = 3\nvoice/1/delay_ms = 12.0" in text
    assert (
        '[sub_resource type="AudioEffectEQ6" id="unused_eq"]\nband_db/32_hz = -3.0'
        in text
    )
    assert text.startswith(
        '[gd_resource type="AudioBusLayout" format=3 uid="uid://b2mix"]'
    )
    assert 'bus/2/effect/0/effect = SubResource("AudioEffectChorus_snu11")' in text


def test_standard_layout_matches_the_game_core() -> None:
    layout = mixer.standard_layout()
    assert [b.name for b in layout.buses] == list(mixer.STANDARD_BUSES)
    assert all(b.send == "Master" for b in layout.buses[1:])
    assert mixer.ducking(layout) == "Voice"
    squeeze = layout.buses[1].first(mixer.COMPRESSOR)
    assert squeeze is not None
    assert (squeeze.number("threshold"), squeeze.number("attack_us")) == (
        -24.0,
        20000.0,
    )
    room = layout.buses[3].first(mixer.REVERB)
    assert room is not None and room.number("wet") == 0.25
    assert room.number("room_size") == 0.6
    text = mixer.to_tres(layout)
    assert 'id="AudioEffectCompressor_music"' in text
    assert 'id="AudioEffectReverb_ambience"' in text


def test_add_missing_buses_keeps_the_existing_ones_first() -> None:
    layout = mixer.from_tres(GODOT_SAVED)
    added = mixer.add_missing_buses(layout)
    assert added == ["SFX", "Ambience", "UI"]
    assert [b.name for b in layout.buses] == [
        "Master",
        "Music",
        "Voice",
        "SFX",
        "Ambience",
        "UI",
    ]
    assert mixer.add_missing_buses(layout) == []


def test_ducking_sets_and_clears_the_music_sidechain() -> None:
    layout = mixer.from_tres(GODOT_SAVED)
    mixer.set_ducking(layout, "SFX")
    assert mixer.ducking(layout) == "SFX"
    assert 'sidechain = &"SFX"' in mixer.to_tres(layout)
    mixer.set_ducking(layout, "")
    assert mixer.ducking(layout) == ""
    music = layout.bus("Music")
    assert music is not None
    squeeze = music.first(mixer.COMPRESSOR)
    assert squeeze is not None and not squeeze.enabled
    # A Music bus without a compressor gets one when ducking is turned on.
    bare = mixer.BusLayout(buses=[mixer.Bus(name="Master"), mixer.Bus(name="Music")])
    mixer.set_ducking(bare, "Voice")
    assert mixer.ducking(bare) == "Voice"
    # A compressor that only squeezes the music itself is not ducking: Off leaves it on.
    plain = bare.buses[1].effects[0]
    plain.sidechain = ""
    mixer.set_ducking(bare, "")
    assert plain.enabled
    with pytest.raises(ValueError, match="Voice or SFX"):
        mixer.set_ducking(bare, "Ambience")


def test_apply_mix_changes_levels_effects_and_ducking_in_plain_words() -> None:
    layout = mixer.from_tres(GODOT_SAVED)
    words = mixer.apply_mix(
        layout,
        {
            "buses": [
                {
                    "name": "Music",
                    "volume_db": -6.04,
                    "reverb": {"enabled": True, "wet": 0.4},
                },
                {
                    "name": "Voice",
                    "mute": False,
                    "compressor": {"enabled": True, "threshold": -18},
                },
                {"name": "UI", "volume_db": -2.0},
            ],
            "ducking": "SFX",
        },
    )
    assert words == [
        "Music −6.0 dB",
        "Voice unmuted",
        "Added the UI bus",
        "Music ducks under SFX",
    ]
    music = layout.bus("Music")
    assert music is not None and music.volume_db == -6.0
    room = music.first(mixer.REVERB)
    assert room is not None and room.enabled and room.number("wet") == 0.4
    voice = layout.bus("Voice")
    assert voice is not None
    assert [e.type for e in voice.effects] == ["AudioEffectChorus", mixer.COMPRESSOR]
    ui = layout.bus("UI")
    assert ui is not None and ui.volume_db == -2.0


def test_layout_path_follows_the_project_setting(tmp_path: Path) -> None:
    assert mixer.layout_path(tmp_path) == "default_bus_layout.tres"
    (tmp_path / "project.godot").write_text(
        'config_version=5\n[audio]\nbuses/default_bus_layout="res://audio/buses.tres"\n'
    )
    assert mixer.layout_path(tmp_path) == "audio/buses.tres"


def _godot() -> str:
    found = os.environ.get("JOINERY_TEST_GODOT", "")
    if not found:
        default = (
            Path(__file__).resolve().parents[1]
            / ".tools/Godot.app/Contents/MacOS/Godot"
        )
        found = str(default) if default.is_file() else ""
    return found


@pytest.mark.skipif(not _godot(), reason="needs a Godot binary (JOINERY_TEST_GODOT)")
def test_stock_godot_loads_the_layout_it_writes(tmp_path: Path) -> None:
    layout = mixer.from_tres(GODOT_SAVED)
    mixer.add_missing_buses(layout)
    mixer.apply_mix(
        layout, {"buses": [{"name": "Music", "volume_db": -4.5}], "ducking": "SFX"}
    )
    (tmp_path / "project.godot").write_text("config_version=5\n")
    (tmp_path / "layout.tres").write_text(mixer.to_tres(layout))
    (tmp_path / "check.gd").write_text(
        "extends SceneTree\n"
        "func _initialize() -> void:\n"
        '\tvar layout: AudioBusLayout = load("res://layout.tres")\n'
        "\tAudioServer.set_bus_layout(layout)\n"
        '\tvar music := AudioServer.get_bus_index(&"Music")\n'
        "\tvar squeeze: AudioEffectCompressor = AudioServer.get_bus_effect(music, 0)\n"
        "\tvar chorus: AudioEffectChorus = AudioServer.get_bus_effect(\n"
        '\t\tAudioServer.get_bus_index(&"Voice"), 0)\n'
        '\tprint("BUSES ", AudioServer.bus_count, " ", AudioServer.get_bus_volume_db(music),\n'
        '\t\t" ", squeeze.sidechain, " ", chorus.voice_count,\n'
        '\t\t" ", AudioServer.get_bus_send(AudioServer.get_bus_index(&"UI")))\n'
        "\tquit()\n"
    )
    result = subprocess.run(
        [_godot(), "--headless", "--path", str(tmp_path), "--script", "check.gd"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    output = result.stdout + result.stderr
    assert "BUSES 6 -4.5 SFX 3 Master" in output, output
    assert "ERROR" not in output, output
