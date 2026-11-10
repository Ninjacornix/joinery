"""MIDI music, Surge XT parameter edits and audio edits, in the real offline worker.

Writes a song (General MIDI piano and drums through FluidSynth, a bass line
through a Surge XT patch), loops it, edits it, edits rendered audio from Surge
XT and SuperCollider (trim, fade, loudness, loop points, format), changes one
Surge parameter without losing those edits, renders an imported MIDI file as it
is, checks every result against the art bible's audio rules, and replays every
recipe byte for byte.

Technical fixtures only; nothing here approves art direction or touches a
user's game. Run: `uv run python tests/integration/midi_render.py`.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import struct
import tempfile
from pathlib import Path
from typing import Any

from joinery.authoring import IMAGE, edit_document, run_offline
from joinery.bible_rules import check_rules
from joinery.recipe import load_recipe, rebuild_one, recipe_paths
from joinery.runner import Result, SandboxRunner
from joinery.tools import midi
from servers.sound import ops

BIBLE = """# Art bible (test fixture)

## Checked rules

```toml
schema = 2

[audio]
true_peak_max_dbtp = -1.0
lufs_tolerance = 1.0

[audio.lufs]
"audio/music" = -16.0
"audio/sfx" = -18.0

[audio.format."audio/music"]
container = "ogg"
sample_rates = [48000]
channels = [2]
```
"""

BASS: dict[str, Any] = {
    "synth": "surge-xt",
    "seconds": 1.2,
    "sample_rate": 48000,
    "parameters": {
        "a_filter_1_type": "LP 24 dB",
        "a_filter_1_cutoff": 900.0,
        "a_osc_1_pitch": -5.0,
    },
    "notes": [{"pitch": 45, "start": 0.0, "duration": 0.8, "velocity": 110}],
}

CHORDS = [(60, 64, 67), (57, 60, 64), (53, 57, 60), (55, 59, 62)]
SONG: dict[str, Any] = {
    "tempo_bpm": 120,
    "bars": 2,
    "tail_beats": 2,
    "tracks": [
        {
            "name": "Piano",
            "program": 0,
            "notes": [
                {"pitch": pitch, "beat": 2 * index, "beats": 2, "velocity": 80}
                for index, chord in enumerate(CHORDS)
                for pitch in chord
            ],
        },
        {
            "name": "Drums",
            "drums": True,
            "notes": [
                {"pitch": 36 if beat % 2 == 0 else 38, "beat": beat, "beats": 0.5}
                for beat in range(8)
            ],
        },
        {
            "name": "Bass",
            "surge": "audio/sources/bass.json",
            "volume": 110,
            "notes": [
                {
                    "pitch": chord[0] - 24,
                    "beat": 2 * index,
                    "beats": 1.5,
                    "velocity": 100,
                }
                for index, chord in enumerate(CHORDS)
            ],
        },
    ],
}

BLIP = (
    "(\nvar definition=SynthDef(\\blip,{ Out.ar(0,SinOsc.ar(660,0,0.3)"
    "*EnvGen.kr(Env.perc(0.005,0.5),doneAction:2)) });\n"
    "var score=Score([[0.0,[\\d_recv,definition.asBytes]],"
    "[0.1,[\\s_new,\\blip,1000,0,0]],[0.9,[\\c_set,0,0]]]);\n"
    'score.recordNRT(outputFilePath:"audio/sfx/blip.wav",sampleRate:48000,'
    'headerFormat:"WAV",sampleFormat:"int16",options:ServerOptions.new.'
    "numOutputBusChannels_(1),duration:0.9,action:{0.exit});\n)\n"
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def wav_chunks(path: Path) -> dict[bytes, bytes]:
    data = path.read_bytes()
    assert data[:4] == b"RIFF" and data[8:12] == b"WAVE"
    assert struct.unpack("<I", data[4:8])[0] == len(data) - 8, "RIFF size"
    chunks: dict[bytes, bytes] = {}
    at = 12
    while at + 8 <= len(data):
        kind, size = data[at : at + 4], struct.unpack("<I", data[at + 4 : at + 8])[0]
        chunks[kind] = data[at + 8 : at + 8 + size]
        at += 8 + size + (size & 1)
    return chunks


def smpl_loop(path: Path) -> tuple[int, int]:
    body = wav_chunks(path)[b"smpl"]
    values = struct.unpack("<9I6I", body)
    assert values[7] == 1, "one loop"
    return values[11], values[12]


async def probe(stage: Path, path: str) -> dict[str, Any]:
    got = await run_offline(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=sample_rate,channels,duration_ts:stream_tags",
            "-of",
            "json",
            path,
        ],
        stage,
        readonly=True,
    )
    assert got.ok, got.output
    stream: dict[str, Any] = json.loads(got.stdout)["streams"][0]
    return stream


async def measure(argv: list[str], root: Path) -> Result:
    return await run_offline(argv, root, readonly=True)


async def bible_clean(stage: Path, paths: list[str]) -> None:
    problems = await check_rules(stage, paths, measure)
    assert not problems, problems


def current_recipe(stage: Path, output: str) -> tuple[Path, Any]:
    for path in recipe_paths(stage):
        recipe = load_recipe(path)
        if output in recipe.outputs:
            return path, recipe
    raise AssertionError(f"no recipe claims {output}")


async def main(stage: Path) -> dict[str, Any]:
    (stage.parent / "journal.json").write_text(json.dumps({"state": "staging"}))
    (stage / "joinery.toml").write_text(
        '[tools]\nenabled=["surge-xt","supercollider"]\n'
    )
    (stage / "project.godot").write_text("config_version=5\n")
    (stage / "art").mkdir()
    (stage / "art/bible.md").write_text(BIBLE)
    facts: dict[str, Any] = {}

    # A Surge XT patch, which the song's bass track plays.
    await edit_document(
        stage,
        "surge-xt",
        "audio/sources/bass.json",
        json.dumps(BASS),
        ["audio/sfx/bass.wav"],
        "bass",
    )

    # 1. A looping song: FluidSynth piano and drums, Surge XT bass.
    song = await ops.compose_music(
        stage, "audio/music/theme.mid", SONG, "audio/music/theme.ogg"
    )
    assert song["status"] == "staged" and song["requires_review"], song
    stream = await probe(stage, "audio/music/theme.ogg")
    assert stream["duration_ts"] == 4 * 48000, stream  # exactly 2 bars at 120 BPM
    tags = {k.upper(): v for k, v in stream.get("tags", {}).items()}
    assert tags.get("LOOPSTART") == "0" and tags.get("LOOPLENGTH") == "192000", tags
    measured = song["measured"]["steps"][0]
    assert abs(measured["lufs"] + 16.0) <= 0.5, measured
    assert {t["instrument"] for t in song["measured"]["tracks"]} == {
        "FluidR3_GM",
        "Surge XT",
    }
    imported = (stage / "audio/music/theme.ogg.import").read_text()
    for line in ("loop=true", "loop_offset=0.0", "bpm=120.0", "beat_count=8"):
        assert line in imported, imported
    written = midi.read_midi(
        (stage / "audio/music/theme.mid").read_bytes(), tail_beats=2
    )
    assert [t.name for t in written.tracks] == ["Piano", "Drums", "Bass"]
    _, recipe = current_recipe(stage, "audio/music/theme.ogg")
    assert recipe.tool == "surge-xt" and "FluidR3_GM" in recipe.note, recipe
    facts["song_lufs"] = measured["lufs"]
    first_theme = sha(stage / "audio/music/theme.ogg")

    # 2. Edit the song: up a tone, a different piano; drums stay put.
    await ops.edit_music(
        stage,
        "audio/music/theme.mid",
        {"transpose": 2, "tracks": {"Piano": {"program": 4}}},
    )
    score = ops.inspect_music(stage, "audio/music/theme.mid")["song"]
    tracks = {t["name"]: t for t in score["tracks"]}
    assert tracks["Piano"]["program"] == 4
    assert tracks["Piano"]["notes"][0]["pitch"] == 62
    assert tracks["Drums"]["notes"][0]["pitch"] == 36
    assert sha(stage / "audio/music/theme.ogg") != first_theme

    # 3. Audio edits on Surge's WAV, in place: trim, fade, loudness, loop points.
    edited = await ops.process_audio(
        stage,
        "audio/sfx/bass.wav",
        [
            {"op": "trim_silence", "threshold_db": -70},
            {"op": "fade", "out_seconds": 0.1},
            {"op": "loop", "start": 0.1, "end": 0.6, "crossfade": 0.05},
            {"op": "normalise"},
        ],
    )
    loop = smpl_loop(stage / "audio/sfx/bass.wav")
    assert loop == (4800, 28800), loop
    assert abs(edited["measured"]["steps"][-1]["lufs"] + 18.0) <= 0.5, edited

    # 4. Convert to a mono Ogg beside it: a second edit on the same recipe.
    await ops.process_audio(
        stage,
        "audio/sfx/bass.wav",
        [{"op": "gain", "db": -3}, {"op": "normalise"}],
        export="audio/sfx/bass.ogg",
        output={"channels": 1, "sample_rate": 44100},
    )
    stream = await probe(stage, "audio/sfx/bass.ogg")
    assert (stream["channels"], stream["sample_rate"]) == (1, "44100"), stream
    _, recipe = current_recipe(stage, "audio/sfx/bass.ogg")
    marker = ops.recipe_edits((stage / recipe.script).read_text())
    assert len(marker["edits"]) == 2, marker
    assert set(recipe.outputs) == {
        "audio/sources/bass.json",
        "audio/sfx/bass.wav",
        "audio/sfx/bass.ogg",
    }

    # 5. Change one Surge parameter: the rest of the patch and both edits stay.
    before = sha(stage / "audio/sfx/bass.ogg")
    changed = await ops.set_surge_parameters(
        stage, "audio/sources/bass.json", {"a_filter_1_cutoff": 400.0}
    )
    patch = json.loads((stage / "audio/sources/bass.json").read_text())
    assert patch["parameters"] == {**BASS["parameters"], "a_filter_1_cutoff": 400.0}
    assert patch["notes"] == BASS["notes"]
    assert changed["parameters"]["a_filter_1_type"] == "LP 24 dB"
    assert smpl_loop(stage / "audio/sfx/bass.wav") == loop
    assert sha(stage / "audio/sfx/bass.ogg") != before
    _, recipe = current_recipe(stage, "audio/sfx/bass.ogg")
    assert len(ops.recipe_edits((stage / recipe.script).read_text())["edits"]) == 2

    # 6. SuperCollider's render, edited the same way.
    await edit_document(
        stage, "supercollider", "audio/sources/blip.scd", BLIP, ["audio/sfx/blip.wav"]
    )
    blip = await ops.process_audio(
        stage,
        "audio/sfx/blip.wav",
        [
            {"op": "trim", "start": 0.05},
            {"op": "fade", "in_seconds": 0.01, "out_seconds": 0.2},
            {"op": "normalise"},
        ],
    )
    assert abs(blip["measured"]["steps"][-1]["lufs"] + 18.0) <= 0.5, blip

    # 7. An imported MIDI file (declared by the user), rendered as it is.
    imported_song = midi.Song.model_validate(
        {
            "tempo_bpm": 90,
            "bars": 1,
            "tail_beats": 2,
            "tracks": [
                {
                    "name": "Strings",
                    "program": 48,
                    "notes": [{"pitch": 55, "beat": 0, "beats": 4, "velocity": 90}],
                }
            ],
        }
    )
    imported_mid = stage / "audio/music/imported.mid"
    imported_mid.write_bytes(midi.write_midi(imported_song))
    with (stage / "joinery.lock").open("a") as lock:
        lock.write(
            "\n[[asset]]\n"
            'path = "audio/music/imported.mid"\n'
            f'sha256 = "{sha(imported_mid)}"\n'
            'origin = "human-import"\n'
            'imported_by = "test fixture"\n'
            'licence = "CC0-1.0"\n'
        )
    rendered = await ops.render_music(
        stage, "audio/music/imported.mid", "audio/music/imported.ogg"
    )
    assert sha(imported_mid) == sha(stage / "audio/music/imported.mid")
    assert abs(rendered["measured"]["steps"][-1]["lufs"] + 16.0) <= 0.5, rendered

    # 8. Refusals.
    try:
        await ops.process_audio(
            stage,
            "audio/music/imported.ogg",
            [{"op": "gain", "db": 1}],
            export="audio/music/imported.wav",
        )
    except ValueError as error:
        assert "OGG" in str(error), error
    else:
        raise AssertionError("A WAV in a folder the bible keeps for Ogg was accepted.")

    # 9. Every result meets the art bible's audio rules ...
    outputs = sorted(
        {o for p in recipe_paths(stage) for o in load_recipe(p).outputs}
        - {"audio/sources/bass.json", "audio/sources/blip.scd"}
    )
    await bible_clean(stage, [o for o in outputs if not o.endswith(".mid")])

    # ... and every recipe replays byte for byte.
    replays = [
        rebuild_one(path, stage, SandboxRunner(image=IMAGE))
        for path in recipe_paths(stage)
    ]
    assert replays and all(replay.ok for replay in replays), replays
    facts["replayed"] = len(replays)
    facts["outputs"] = outputs
    return facts


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="joinery-midi-") as directory:
        stage = Path(directory) / "stage"
        stage.mkdir()
        print(json.dumps(asyncio.run(main(stage)), indent=1))
        print("MIDI_RENDER_OK")
