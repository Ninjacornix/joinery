"""Surge XT through Joinery's sound adapter, in the real offline worker.

Creates a source and WAV, edits it, inspects parameters, replays every recipe
exactly, and checks that an unknown parameter is refused by name.
"""

from __future__ import annotations

import asyncio
import json
import math
import struct
import tempfile
import wave
from pathlib import Path

from joinery.authoring import IMAGE, edit_document, inspect_document
from joinery.recipe import rebuild_one, recipe_paths
from joinery.runner import SandboxRunner

PARAMETERS: dict[str, object] = {
    "a_filter_1_type": "LP 24 dB",
    "a_filter_1_cutoff": 900.0,
    "a_osc_1_pitch": -5.0,
}
PATCH: dict[str, object] = {
    "synth": "surge-xt",
    "seconds": 1.2,
    "sample_rate": 48000,
    "parameters": PARAMETERS,
    "notes": [{"pitch": 45, "start": 0.0, "duration": 0.8, "velocity": 110}],
}


def rms(path: Path) -> float:
    with wave.open(str(path)) as audio:
        assert (audio.getframerate(), audio.getnchannels()) == (48000, 2)
        data = audio.readframes(audio.getnframes())
    values = struct.unpack(f"<{len(data) // 2}h", data)
    return math.sqrt(sum(v * v for v in values) / len(values)) / 32768


async def main(stage: Path) -> dict[str, object]:
    (stage.parent / "journal.json").write_text(json.dumps({"state": "staging"}))
    (stage / "joinery.toml").write_text('[tools]\nenabled=["surge-xt"]\n')
    (stage / "project.godot").write_text("config_version=5\n")
    source, export = "audio/sources/creak.json", "audio/sfx/creak.wav"
    await edit_document(stage, "surge-xt", source, json.dumps(PATCH), [export], "creak")
    bright = rms(stage / export)
    darker = {
        **PATCH,
        "parameters": {**PARAMETERS, "a_filter_1_cutoff": 300.0},
    }
    await edit_document(
        stage, "surge-xt", source, json.dumps(darker), [export], "darker"
    )
    dark = rms(stage / export)
    assert 0 < dark < bright, (bright, dark)
    info = await inspect_document(stage, "surge-xt", source)
    assert "a_filter_1_cutoff" in info["parameter_defaults"]
    replays = [
        rebuild_one(path, stage, SandboxRunner(image=IMAGE))
        for path in recipe_paths(stage)
    ]
    assert replays and all(replay.ok for replay in replays), replays
    try:
        bad = {**PATCH, "parameters": {"a_filter_1_cutof": 1}}
        await edit_document(
            stage,
            "surge-xt",
            "audio/sources/bad.json",
            json.dumps(bad),
            ["audio/sfx/bad.wav"],
        )
    except RuntimeError as error:
        assert "Unknown Surge parameters: a_filter_1_cutof" in str(error)
    else:
        raise AssertionError("An unknown Surge parameter was accepted.")
    return {"rms_bright": bright, "rms_darker": dark, "replayed": len(replays)}


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="joinery-surge-") as directory:
        stage = Path(directory) / "stage"
        stage.mkdir()
        print(json.dumps(asyncio.run(main(stage))))
        print("SURGE_NATIVE_OK")
