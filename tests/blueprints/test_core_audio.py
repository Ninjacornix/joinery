"""The game core's audio in stock Godot: the bus layout, adaptive music, and
the sound building blocks (SoundCue, Footsteps, AmbienceZone, UiSounds).

`res://tests/support/audio_test.gd` makes every sound it plays in code; the
core ships none. Headless Godot mixes through its Dummy audio driver in real
time, so the test paces its frames to real time and reads the music clock from
the players, as a game does.
"""

from __future__ import annotations

import re
from pathlib import Path

from joinery import blueprints
from tests.blueprints.godot_run import make_project, needs_godot, run_godot

OVERLAY = blueprints.BLUEPRINTS_DIR / blueprints.CORE_DIR_NAME / "overlay"
AUDIO_TEST = "res://tests/support/audio_test.gd"
LAYOUT = OVERLAY / "default_bus_layout.tres"
DRIFT = re.compile(r"max layer drift ([0-9.]+) ms over (\d+) samples")


def test_bus_layout_has_six_buses_and_their_effects() -> None:
    text = LAYOUT.read_text(encoding="utf-8")
    names = re.findall(r'^bus/(\d+)/name = &"(\w+)"$', text, re.MULTILINE)
    assert names == [("1", "Music"), ("2", "SFX"), ("3", "Ambience"), ("4", "UI"), ("5", "Voice")]
    assert all(f'bus/{i}/send = &"Master"' in text for i in range(1, 6))
    assert 'sidechain = &"Voice"' in text
    assert 'bus/1/effect/0/effect = SubResource("AudioEffectCompressor_ducking")' in text
    assert 'bus/3/effect/0/effect = SubResource("AudioEffectReverb_room")' in text


def test_audio_scripts_ship_with_the_core() -> None:
    for name in ("music", "ui_sounds", "sound_cue", "footsteps", "ambience_zone"):
        script = OVERLAY / "core" / "audio" / f"{name}.gd"
        assert script.is_file(), script
        assert script.with_name(script.name + ".uid").is_file(), script


@needs_godot
def test_core_audio_in_godot(tmp_path: Path) -> None:
    project = make_project(tmp_path, "blank")
    settings = tmp_path / "settings" / "audio.cfg"
    result = run_godot(
        project,
        AUDIO_TEST,
        ("--autostart", f"--settings-path={settings}"),
        timeout=240,
        marker="audio",
    )
    assert "audio: ok" in result.output
    drift = DRIFT.search(result.output)
    assert drift is not None
    assert float(drift.group(1)) <= 3.0
    assert int(drift.group(2)) >= 20
