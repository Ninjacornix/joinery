"""The sound kit: typed specs become deterministic native patches; make_sound is typed."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from joinery import gateway, sound_kit
from joinery.tools import get
from servers.sound import make


def test_every_kind_has_a_patch_in_its_tool() -> None:
    for kind in sound_kit.KINDS:
        surface = "stone" if kind == "footstep" else None
        spec = sound_kit.SoundSpec(kind=kind, name=f"k_{kind}", surface=surface)  # type: ignore[arg-type]
        export = spec.export_path()
        render = spec.render_path(export)
        text = spec.source_text(render, 48000, spec.channels)
        if spec.tool == "surge-xt":
            patch = json.loads(text)
            assert patch["synth"] == "surge-xt"
            assert patch["parameters"]["a_osc_1_type"] == "Sine", kind
            assert patch["notes"]
            assert spec.source_path().endswith(".json")
        else:
            # Seeded noise and an NRT render that exits: a replay is byte for byte.
            assert f"RandSeed.ir(1, {spec.effective_seed});" in text, kind
            assert f'outputFilePath: "{render}"' in text
            assert "0.exit" in text
            assert spec.source_path().endswith(".scd")
            # The wrapped recipe compiles the patch rather than refusing it.
            script = get("supercollider").script(spec.source_path(), "", text, [render])
            assert "never exits" not in script
        assert text == spec.source_text(render, 48000, spec.channels), kind


def test_where_sounds_go() -> None:
    step = sound_kit.SoundSpec(kind="footstep", name="step_grass", surface="grass")
    assert step.export_path() == "audio/sfx/step_grass.wav"
    assert step.render_path(step.export_path()) == step.export_path()
    assert step.source_path() == "audio/sources/sfx/step_grass.scd"
    assert step.channels == 1 and step.tool == "supercollider"
    wind = sound_kit.SoundSpec(kind="wind", name="level_wind")
    assert wind.export_path() == "audio/ambience/level_wind.ogg"
    assert (
        wind.render_path(wind.export_path()) == "audio/sources/ambience/level_wind.wav"
    )
    assert wind.channels == 2
    assert [s["op"] for s in wind.steps()] == ["loop", "normalise"]
    assert sound_kit.SoundSpec(kind="pickup", name="pickup_coin").tool == "surge-xt"


def test_variants_differ_and_seeds_are_stable() -> None:
    one = sound_kit.SoundSpec(kind="hit", name="hit_enemy")
    two = sound_kit.SoundSpec(kind="hit", name="hit_enemy", variant=1)
    assert (
        one.effective_seed
        == sound_kit.SoundSpec(kind="hit", name="hit_enemy").effective_seed
    )
    assert one.effective_seed != two.effective_seed
    assert one.source_text("a.wav", 48000, 1) != two.source_text("a.wav", 48000, 1)
    fixed = sound_kit.SoundSpec(kind="hit", name="hit_enemy", seed=7)
    assert "RandSeed.ir(1, 7);" in fixed.source_text("a.wav", 48000, 1)


def test_specs_are_refused_in_plain_words() -> None:
    with pytest.raises(ValidationError, match="needs its surface"):
        sound_kit.SoundSpec(kind="footstep", name="step")
    with pytest.raises(ValidationError, match="only footsteps"):
        sound_kit.SoundSpec(kind="jump", name="jump", surface="stone")
    with pytest.raises(ValidationError, match="lower_snake_case"):
        sound_kit.SoundSpec(kind="jump", name="Big Jump")
    with pytest.raises(ValidationError):
        sound_kit.SoundSpec(kind="explosion", name="boom")  # type: ignore[arg-type]


def test_make_sound_runs_the_app_of_its_kind() -> None:
    assert "make_sound" in gateway.NATIVE_OPS
    assert gateway.op_apps("make_sound", {"kind": "footstep"}) == ("supercollider",)
    assert gateway.op_apps("make_sound", {"kind": "ui_click"}) == ("surge-xt",)
    assert gateway.op_apps("make_sound", {"kind": "cave"}) == ("supercollider",)
    assert gateway.op_apps("make_sound", {"kind": "nope"}) == ("supercollider",)


def test_make_sound_needs_a_transaction_and_its_tool(tmp_path: Path) -> None:
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "joinery.toml").write_text('[tools]\nenabled = ["godot"]\n')
    with pytest.raises(PermissionError, match="project transaction"):
        asyncio.run(make.make_sound(stage, "jump", "player_jump"))
    (tmp_path / "journal.json").write_text(json.dumps({"state": "staging"}))
    with pytest.raises(PermissionError, match="supercollider is not selected"):
        asyncio.run(make.make_sound(stage, "jump", "player_jump"))
    (stage / "joinery.toml").write_text(
        '[tools]\nenabled = ["godot", "supercollider"]\n'
    )
    with pytest.raises(ValueError, match="exports to .wav"):
        asyncio.run(
            make.make_sound(stage, "jump", "player_jump", export="audio/sfx/jump.ogg")
        )
    # A file the kit did not make is never written over.
    (stage / "audio/sfx").mkdir(parents=True)
    (stage / "audio/sfx/player_jump.wav").write_bytes(b"RIFF")
    with pytest.raises(ValueError, match="did not make it"):
        asyncio.run(make.make_sound(stage, "jump", "player_jump"))
