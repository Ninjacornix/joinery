"""Sound tools without a worker: songs and MIDI files, audio-edit steps and their
worker program, Surge XT parameter merges, chained recipes and the sound server.

The real renders (FluidSynth, Surge XT, SuperCollider, ffmpeg) and byte-for-byte
replay are checked by tests/integration/midi_render.py and surge_native.py.
"""

from __future__ import annotations

import array
import asyncio
import json
import re
import struct
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from mcp import Client
from pydantic import ValidationError

from joinery import authoring, tools
from joinery.readiness import status
from joinery.recipe import Recipe
from joinery.runner import Result
from joinery.server_install import configured
from joinery.tools import audio_chain, midi
from joinery.tools.surge_xt import AUDIO_EDIT_CHECK, MUSIC_CHECK
from joinery.tools.surge_xt import MODULE as SURGE
from servers import authoring as server_authoring
from servers.sound import ops

PIANO = {
    "name": "Piano",
    "program": 0,
    "notes": [
        {"pitch": 60 + i, "beat": i, "beats": 1, "velocity": 90} for i in range(4)
    ],
}
DRUMS = {
    "name": "Drums",
    "drums": True,
    "notes": [{"pitch": 36, "beat": b, "beats": 0.5} for b in range(4)],
}


def song(**changes: Any) -> midi.Song:
    data: dict[str, Any] = {
        "tempo_bpm": 120,
        "bars": 1,
        "tail_beats": 2,
        "tracks": [PIANO, DRUMS],
    }
    data.update(changes)
    return midi.Song.model_validate(data)


# --- Songs and MIDI files -------------------------------------------------------------


def test_song_timing_and_channels() -> None:
    piece = song(beats_per_bar=6, beat_unit=8, bars=2)
    assert piece.bar_beats == 3.0
    assert piece.length_seconds == pytest.approx(3.0)
    assert piece.total_seconds == pytest.approx(4.0)
    assert piece.channels() == [0, midi.DRUM_CHANNEL]
    assert piece.tracks[1].program == 0  # the Standard kit


@pytest.mark.parametrize(
    "change,message",
    [
        ({"tracks": [PIANO, {**PIANO}]}, "unique"),
        (
            {"tracks": [{**PIANO, "notes": [{"pitch": 60, "beat": 4, "beats": 1}]}]},
            "after the song",
        ),
        (
            {"tracks": [{**PIANO, "notes": [{"pitch": 60, "beat": 3, "beats": 4}]}]},
            "rings past",
        ),
        (
            {
                "tracks": [
                    {
                        **PIANO,
                        "notes": [
                            {"pitch": 60, "beat": 0, "beats": 2},
                            {"pitch": 60, "beat": 1, "beats": 1},
                        ],
                    }
                ]
            },
            "overlap",
        ),
        ({"tracks": [DRUMS, {**DRUMS, "name": "More drums"}]}, "one drums"),
        ({"tracks": [{"name": "X", "notes": PIANO["notes"]}]}, "General MIDI program"),
        (
            {"tracks": [{**PIANO, "program": 1, "surge": "audio/sources/a.json"}]},
            "Surge XT track",
        ),
        (
            {"tracks": [{**PIANO, "program": None, "surge": "../a.json"}]},
            "under audio/",
        ),
        ({"bars": 400, "tempo_bpm": 40}, "under 600"),
    ],
)
def test_song_refuses_what_cannot_render(change: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        song(**change)


def test_notes_snap_to_the_midi_grid() -> None:
    piece = song(
        tracks=[{**PIANO, "notes": [{"pitch": 60, "beat": 0.33333, "beats": 0.0001}]}]
    )
    note = piece.tracks[0].notes[0]
    assert note.beat * midi.TICKS == round(note.beat * midi.TICKS)
    assert note.beats == 1 / midi.TICKS


def test_midi_round_trip_is_exact_and_deterministic() -> None:
    piece = song(tempo_bpm=97.5, beats_per_bar=3, bars=2)
    data = midi.write_midi(piece)
    assert data == midi.write_midi(piece)
    assert data[:4] == b"MThd" and struct.unpack(">HHH", data[8:14]) == (1, 3, 480)
    assert midi.read_midi(data, tail_beats=2).model_dump() == piece.model_dump()


def test_general_midi_file_leaves_out_surge_tracks() -> None:
    piece = song(
        tracks=[
            PIANO,
            {
                "name": "Bass",
                "surge": "audio/sources/bass.json",
                "notes": PIANO["notes"],
            },
        ]
    )
    assert struct.unpack(">H", midi.write_midi(piece)[10:12])[0] == 3
    assert (
        struct.unpack(">H", midi.write_midi(piece, general_midi_only=True)[10:12])[0]
        == 2
    )


def _smf(*events: bytes, division: int = 96) -> bytes:
    body = b"".join(events) + b"\x00\xff\x2f\x00"
    return (
        b"MThd"
        + struct.pack(">IHHH", 6, 0, 1, division)
        + b"MTrk"
        + struct.pack(">I", len(body))
        + body
    )


def test_reader_handles_running_status_and_note_on_zero() -> None:
    data = _smf(
        b"\x00\xc0\x05",  # program 5
        b"\x00\x90\x3c\x64",  # note on
        b"\x30\x3c\x00",  # running status: note on velocity 0 = off, 48 ticks later
        b"\x00\x3e\x50",  # running status: another note on
        b"\x60\x80\x3e\x00",
    )
    piece = midi.read_midi(data)
    (track,) = piece.tracks
    assert track.program == 5
    assert [(n.pitch, n.beat, n.beats) for n in track.notes] == [
        (60, 0.0, 0.5),
        (62, 0.5, 1.0),
    ]


@pytest.mark.parametrize(
    "event,name",
    [
        (b"\x00\xe0\x00\x50", "pitch bend"),
        (b"\x00\xb0\x40\x7f", "controller 64"),
        (b"\x00\xff\x51\x03\x07\xa1\x20\x60\xff\x51\x03\x06\x1a\x80", "tempo changes"),
    ],
)
def test_reader_refuses_what_a_song_would_drop(event: bytes, name: str) -> None:
    data = _smf(event, b"\x00\x90\x3c\x64", b"\x60\x80\x3c\x00")
    with pytest.raises(ValueError, match=name) as raised:
        midi.read_midi(data)
    assert "render_music" in str(raised.value)


def test_song_edits_keep_what_they_do_not_name() -> None:
    piece = song()
    changed = midi.apply_change(
        piece,
        midi.SongChange(
            transpose=3,
            velocity_scale=2,
            tracks={"Piano": midi.TrackChange(program=40, volume=90)},
        ),
    )
    piano, drums = changed.tracks
    assert [n.pitch for n in piano.notes] == [63, 64, 65, 66]
    assert piano.program == 40 and piano.volume == 90
    assert all(n.velocity == 127 for n in piano.notes)
    assert [n.pitch for n in drums.notes] == [36] * 4  # drums are never transposed
    assert changed.tempo_bpm == piece.tempo_bpm
    removed = midi.apply_change(
        piece, midi.SongChange(tracks={"Drums": midi.TrackChange(remove=True)})
    )
    assert [t.name for t in removed.tracks] == ["Piano"]
    with pytest.raises(ValueError, match="No track named Bass"):
        midi.apply_change(piece, midi.SongChange(tracks={"Bass": midi.TrackChange()}))
    with pytest.raises(ValueError, match="outside MIDI"):
        midi.apply_change(
            piece,
            midi.SongChange(
                transpose=48, tracks={"Piano": midi.TrackChange(transpose=48)}
            ),
        )


def test_render_script_carries_its_score_and_soundfont() -> None:
    piece = song()
    steps = audio_chain.resolve_targets(
        [audio_chain.Normalise(op="normalise")],
        "audio/music/a.ogg",
        {},
        None,
        default_lufs=midi.DEFAULT_MUSIC_LUFS,
    )
    script = midi.render_script(
        piece, "audio/music/a.mid", "audio/music/a.ogg", {}, loop=True, steps=steps
    )
    compile(script, "song", "exec")
    assert midi.song_from_script(script) == piece
    assert midi.SOUNDFONT_SHA256 in script
    assert script == midi.render_script(
        piece, "audio/music/a.mid", "audio/music/a.ogg", {}, loop=True, steps=steps
    )
    with pytest.raises(ValueError, match=".mid file under audio"):
        midi.render_script(piece, "art/a.mid", "audio/a.ogg", {}, loop=True, steps=[])


def test_surge_track_snapshots_its_patch(tmp_path: Path) -> None:
    (tmp_path / "audio/sources").mkdir(parents=True)
    (tmp_path / "audio/sources/bass.json").write_text(
        json.dumps({"synth": "surge-xt", "parameters": {"a_volume": 0.5}})
    )
    piece = song(
        tracks=[
            {
                "name": "Bass",
                "surge": "audio/sources/bass.json",
                "notes": PIANO["notes"],
            }
        ]
    )
    assert midi.surge_parameters(tmp_path, piece) == {"Bass": {"a_volume": 0.5}}
    (tmp_path / "audio/sources/bass.json").write_text('{"synth": "other"}')
    with pytest.raises(ValueError, match="not a Surge XT source"):
        midi.surge_parameters(tmp_path, piece)


# --- Audio-edit steps -------------------------------------------------------------------


def test_steps_are_typed_and_loop_points_must_survive() -> None:
    steps = audio_chain.parse_steps(
        [
            {"op": "trim", "start": 0.1},
            {"op": "loop", "end": 2, "crossfade": 0.1},
            {"op": "normalise"},
        ]
    )
    assert [s.op for s in steps] == ["trim", "loop", "normalise"]
    for bad, message in [
        ([], "at least one"),
        ([{"op": "loop"}, {"op": "fade", "out_seconds": 1}], "Only gain and normalise"),
        ([{"op": "loop", "crossfade": 0.1}], "loop end"),
        ([{"op": "loop", "end": 2, "crossfade": 0.1, "fold_tail": True}], "not both"),
        ([{"op": "fade"}], "in_seconds or out_seconds"),
        ([{"op": "trim", "start": 2, "end": 1}], "after its start"),
        ([{"op": "reverse"}], "reverse"),
    ]:
        with pytest.raises(ValueError, match=message):
            audio_chain.parse_steps(bad)


def test_loudness_targets_come_from_the_art_bible() -> None:
    steps = audio_chain.parse_steps([{"op": "normalise"}])
    lufs = {"audio": -20.0, "audio/music": -16.0}
    (music,) = audio_chain.resolve_targets(steps, "audio/music/a.ogg", lufs, -2.0)
    (sfx,) = audio_chain.resolve_targets(steps, "audio/sfx/a.wav", lufs, None)
    assert (music.lufs, music.true_peak) == (-16.0, -2.0)  # type: ignore[union-attr]
    assert (sfx.lufs, sfx.true_peak) == (-20.0, audio_chain.DEFAULT_TRUE_PEAK)  # type: ignore[union-attr]
    with pytest.raises(ValueError, match="sets no loudness for audio/sfx"):
        audio_chain.resolve_targets(steps, "audio/sfx/a.wav", {}, None)
    given = audio_chain.parse_steps([{"op": "normalise", "lufs": -23}])
    (step,) = audio_chain.resolve_targets(given, "audio/sfx/a.wav", lufs, None)
    assert step.lufs == -23  # type: ignore[union-attr]


def test_audio_paths_stay_in_audio() -> None:
    for source, export in [
        ("art/a.wav", "audio/a.wav"),
        ("audio/a.wav", "audio/a.mp3"),
    ]:
        with pytest.raises(ValueError, match="under audio/"):
            audio_chain.check_paths(source, export)


def test_program_is_deterministic_python() -> None:
    steps = audio_chain.parse_steps([{"op": "gain", "db": -3}])
    program = audio_chain.program("audio/a.wav", "audio/b.ogg", steps)
    compile(program, "chain", "exec")
    assert program == audio_chain.program("audio/a.wav", "audio/b.ogg", steps)
    assert "+bitexact" in program and "-map_metadata" in program


@pytest.fixture(scope="module")
def worker() -> dict[str, Any]:
    """The worker library, run here on its pure-Python parts."""
    namespace: dict[str, Any] = {"__name__": "joinery_test"}
    exec(compile(audio_chain.WORKER_LIB, "worker", "exec"), namespace)  # noqa: S102 - our own program text
    return namespace


def _audio(worker: dict[str, Any], values: list[float], channels: int = 1) -> Any:
    return worker["Audio"](array.array("f", values), 10, channels)


def test_trim_and_silence_trim(worker: dict[str, Any]) -> None:
    audio = _audio(worker, [0.0, 0.0, 0.5, 0.25, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0], 1)
    trimmed = worker["trim"](audio, 0.2, 0.4)
    assert list(trimmed.samples) == [0.5, 0.25]
    quiet = worker["trim_silence"](audio, -60.0, 0.2)
    assert list(quiet.samples) == [0.5, 0.25, 0.0, 0.0]
    with pytest.raises(RuntimeError, match="keeps no audio"):
        worker["trim"](audio, 0.5, 0.5)
    with pytest.raises(RuntimeError, match="quieter"):
        worker["trim_silence"](_audio(worker, [0.0] * 4), -60.0, 0.0)


def test_loop_crossfade_and_fold(worker: dict[str, Any]) -> None:
    values = [float(i) for i in range(10)]
    looped, points = worker["loop_points"](_audio(worker, values), 0.0, 0.6, 0.2, False)
    assert points == (0, 6)
    # At the jump from frame 5 to 0, playback continues with what followed 5.
    assert list(looped.samples) == [6.0, 4.0, 2.0, 3.0, 4.0, 5.0]
    folded, points = worker["loop_points"](_audio(worker, values), 0.0, 0.6, 0.0, True)
    assert points == (0, 6)
    assert list(folded.samples) == [6.0, 8.0, 10.0, 12.0, 4.0, 5.0]
    stereo = _audio(worker, [1.0, -1.0] * 6, 2)
    looped, points = worker["loop_points"](stereo, 0.1, 0.4, 0.0, False)
    assert points == (1, 4) and len(looped.samples) == 8
    with pytest.raises(RuntimeError, match="after the loop end"):
        worker["loop_points"](_audio(worker, values), 0.0, 0.9, 0.2, False)


def test_wav_loop_points_are_a_smpl_chunk_godot_reads(
    worker: dict[str, Any], tmp_path: Path
) -> None:
    wav = tmp_path / "a.wav"
    data = struct.pack("<4sI4s", b"RIFF", 36 + 4, b"WAVE")
    data += struct.pack("<4sIHHIIHH", b"fmt ", 16, 1, 1, 48000, 96000, 2, 16)
    data += struct.pack("<4sI", b"data", 4) + b"\x00\x00\x01\x00"
    wav.write_bytes(data)
    worker["add_smpl"](str(wav), 48000, 100, 2000)
    written = wav.read_bytes()
    assert struct.unpack("<I", written[4:8])[0] == len(written) - 8
    chunk = written[len(data) :]
    assert chunk[:4] == b"smpl" and struct.unpack("<I", chunk[4:8])[0] == 60
    # Godot skips ten words (nine header fields and the cue id), then reads
    # the loop type, start and (exclusive) end.
    words = struct.unpack("<15I", chunk[8:])
    assert words[7] == 1 and words[10:13] == (0, 100, 2000)


def test_report_parsing() -> None:
    out = 'noise\nJOINERY_AUDIO {"seconds": 1}\nJOINERY_AUDIO {"seconds": 2}\n'
    assert audio_chain.parse_report(out) == {"seconds": 2}
    assert audio_chain.parse_report("JOINERY_AUDIO not json") == {}


def test_godot_import_keeps_what_godot_wrote() -> None:
    fresh = audio_chain.godot_import(
        None, "audio/m.ogg", loop=True, loop_offset=1.5, bpm=96
    )
    assert 'importer="oggvorbisstr"' in fresh and "loop=true" in fresh
    assert "loop_offset=1.5" in fresh and "bpm=96.0" in fresh
    existing = (
        '[remap]\n\nimporter="oggvorbisstr"\ntype="AudioStreamOggVorbis"\n'
        'uid="uid://abc"\npath="res://.godot/imported/m.ogg-1.oggvorbisstr"\n\n'
        '[deps]\n\nsource_file="res://audio/m.ogg"\n\n'
        "[params]\n\nloop=false\nloop_offset=0\nbpm=0\nbeat_count=0\nbar_beats=4\n"
    )
    updated = audio_chain.godot_import(
        existing, "audio/m.ogg", loop=True, loop_offset=0.25
    )
    assert 'uid="uid://abc"' in updated
    assert "loop=true\nloop_offset=0.25\nbpm=0\n" in updated
    assert updated.count("loop=") == 1


def test_supercollider_text_survives_the_lexer_limit() -> None:
    text = 'line "one"\n\\path\n' + "x" * 9000
    expression = audio_chain.sc_text(text)
    assert expression.endswith(".join")
    assert max(len(part) for part in expression.split(",\n")) < 8000
    assert audio_chain.sc_unquote(expression) == text
    chain = audio_chain.supercollider_chain(
        "(\n0.exit;\n)\n", "print(1)\n", "audio/a.wav"
    )
    assert "ShutDown.add" in chain and 'File.delete("audio/a.wav")' in chain


def test_nested_supercollider_edits_run_inner_first_from_their_own_files() -> None:
    # Two edits on one render: each level keeps its own program file, and each
    # puts its action first, so the inner edit (registered later) runs first.
    inner = audio_chain.supercollider_chain(
        "(\n0.exit;\n)\n", "print('inner')\n", "audio/a.wav"
    )
    outer = audio_chain.supercollider_chain(inner, "print('outer')\n", "audio/a.wav")
    programs = re.findall(r"/tmp/joinery-audio-chain-[0-9a-f]{16}\.py", outer)
    assert len(set(programs)) == 2, programs
    assert outer.count("ShutDown.objects.addFirst(joineryEdit)") == 2
    # The outer level's text still ends by interpreting the level below it.
    assert outer.rstrip().endswith(")\n)") or outer.endswith(");\n)\n")


# --- Chained recipes ---------------------------------------------------------------------


def _payload(source: str, export: str) -> dict[str, Any]:
    return {
        "source": source,
        "export": export,
        "steps": [{"op": "gain", "db": -1.0}],
        "output": audio_chain.Output().model_dump(),
    }


@pytest.mark.parametrize("tool", ["surge-xt", "supercollider"])
def test_edits_wrap_the_render_and_unwrap_back_to_it(tool: str) -> None:
    base = (
        SURGE.script("audio/s.json", "", "{}", ["audio/a.wav"])
        if tool == "surge-xt"
        else tools.get(tool).script(
            "audio/s.scd", "", "(\n0.exit;\n)\n", ["audio/a.wav"]
        )
    )
    edits = [
        _payload("audio/a.wav", "audio/a.wav"),
        _payload("audio/a.wav", "audio/a.ogg"),
    ]
    script, outputs = ops._with_edits(tool, base, ["audio/s", "audio/a.wav"], edits)
    assert outputs == ["audio/s", "audio/a.wav", "audio/a.ogg"]
    marker = ops.recipe_edits(script)
    assert marker == {"base_outputs": ["audio/s", "audio/a.wav"], "edits": edits}
    assert ops._base_script(script, tool) == base
    if tool == "surge-xt":
        compile(script, "chain", "exec")


# --- The sound server's operations, with the worker replaced --------------------------------


@pytest.fixture
def stage(tmp_path: Path) -> Path:
    root = tmp_path / "stage"
    root.mkdir()
    (tmp_path / "journal.json").write_text(json.dumps({"state": "staging"}))
    (root / "joinery.toml").write_text(
        '[tools]\nenabled=["surge-xt","supercollider","krita"]\n'
    )
    (root / "project.godot").write_text("config_version=5\n")
    return root


def _recipe(
    stage: Path, tool: str, script: str, outputs: list[str], name: str = "r"
) -> Path:
    (stage / "recipes").mkdir(exist_ok=True)
    suffix = ".py" if tool != "supercollider" else ".scd"
    (stage / f"recipes/{name}{suffix}").write_text(script)
    path = stage / f"recipes/{name}.toml"
    path.write_text(
        f'version = 1\ntool = "{tool}"\nscript = "recipes/{name}{suffix}"\n'
        f"outputs = {json.dumps(outputs)}\n"
    )
    for output in outputs:
        (stage / output).parent.mkdir(parents=True, exist_ok=True)
        (stage / output).write_bytes(b"x")
    return path


class Worker:
    """Stands in for the offline worker: writes the outputs, prints a report."""

    def __init__(self, stage: Path, report: dict[str, Any] | None = None) -> None:
        self.stage = stage
        self.report = report or {"steps": []}
        self.calls: list[list[str]] = []
        self.outputs: list[str] = []

    async def __call__(
        self, argv: list[str], project: Path, *args: Any, **kw: Any
    ) -> Result:
        self.calls.append(argv)
        script = (project / argv[-1]).read_text()
        for output in self.outputs:
            (project / output).parent.mkdir(parents=True, exist_ok=True)
            (project / output).write_bytes(script.encode()[:64])
        return Result(True, "JOINERY_AUDIO " + json.dumps(self.report), "", 0.1)


@pytest.fixture
def record(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    recorded = AsyncMock(
        side_effect=lambda project, recipe, path: {
            "outputs": list(recipe.outputs),
            "status": "staged",
            "requires_review": True,
            "recipe_object": recipe,
        }
    )
    monkeypatch.setattr(authoring, "_record", recorded)
    return recorded


def test_audio_edit_chains_onto_the_render_that_made_the_file(
    stage: Path, monkeypatch: pytest.MonkeyPatch, record: AsyncMock
) -> None:
    base = SURGE.script("audio/sources/a.json", "", "{}", ["audio/sfx/a.wav"])
    _recipe(stage, "surge-xt", base, ["audio/sources/a.json", "audio/sfx/a.wav"])
    worker = Worker(stage, {"steps": [], "loop": {"start_seconds": 0.5}})
    worker.outputs = ["audio/sources/a.json", "audio/sfx/a.wav", "audio/sfx/a.ogg"]
    monkeypatch.setattr(authoring, "run_offline", worker)
    result = asyncio.run(
        ops.process_audio(
            stage,
            "audio/sfx/a.wav",
            [{"op": "normalise", "lufs": -18}, {"op": "gain", "db": -1}],
            export="audio/sfx/a.ogg",
        )
    )
    recipe: Recipe = result["recipe_object"]
    assert recipe.tool == "surge-xt"
    assert recipe.outputs == (
        "audio/sources/a.json",
        "audio/sfx/a.wav",
        "audio/sfx/a.ogg",
    )
    assert worker.calls == [["joinery-surge", recipe.script]]
    script = (stage / recipe.script).read_text()
    assert ops._base_script(script, "surge-xt") == base
    assert "normalise, gain, written to audio/sfx/a.ogg" in recipe.note
    imported = (stage / "audio/sfx/a.ogg.import").read_text()
    assert "loop=true" in imported and "loop_offset=0.5" in imported
    assert result["godot_import"] == "audio/sfx/a.ogg.import"


def test_supercollider_edit_fails_without_the_post_marker(
    stage: Path, monkeypatch: pytest.MonkeyPatch, record: AsyncMock
) -> None:
    _recipe(stage, "supercollider", "(\n0.exit;\n)\n", ["audio/sfx/b.wav"])
    worker = Worker(stage)
    worker.outputs = ["audio/sfx/b.wav"]
    monkeypatch.setattr(authoring, "run_offline", worker)
    with pytest.raises(RuntimeError, match="did not complete"):
        asyncio.run(
            ops.process_audio(stage, "audio/sfx/b.wav", [{"op": "gain", "db": 1}])
        )
    record.assert_not_awaited()
    assert worker.calls[0][-2:] == ["sclang", worker.calls[0][-1]]


@pytest.mark.parametrize(
    "setup,source,message",
    [
        (None, "audio/sfx/none.wav", "has no recipe"),
        ("krita", "audio/sfx/k.wav", "need Surge XT or SuperCollider"),
        ("surge-xt", "art/a.wav", "under audio/"),
    ],
)
def test_audio_edits_refuse_what_they_cannot_rebuild(
    stage: Path,
    monkeypatch: pytest.MonkeyPatch,
    setup: str | None,
    source: str,
    message: str,
) -> None:
    if setup:
        _recipe(stage, setup, "pass\n", [source])
    worker = AsyncMock()
    monkeypatch.setattr(authoring, "run_offline", worker)
    with pytest.raises(ValueError, match=message):
        asyncio.run(ops.process_audio(stage, source, [{"op": "gain", "db": 1}]))
    worker.assert_not_awaited()


def test_audio_edits_follow_the_art_bible_format(
    stage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (stage / "art").mkdir()
    (stage / "art/bible.md").write_text(
        '## Checked rules\n\n```toml\n[audio.format."audio/music"]\ncontainer = "ogg"\n```\n'
    )
    _recipe(stage, "surge-xt", "pass\n", ["audio/music/a.ogg"])
    monkeypatch.setattr(authoring, "run_offline", AsyncMock())
    with pytest.raises(ValueError, match="asks for OGG"):
        asyncio.run(
            ops.process_audio(
                stage,
                "audio/music/a.ogg",
                [{"op": "gain", "db": 1}],
                "audio/music/a.wav",
            )
        )


def test_surge_parameters_merge_and_keep_edits(
    stage: Path, monkeypatch: pytest.MonkeyPatch, record: AsyncMock
) -> None:
    patch = {
        "synth": "surge-xt",
        "seconds": 1,
        "parameters": {"a_filter_1_cutoff": 900, "a_osc_1_pitch": -5, "a_volume": 0.5},
        "notes": [{"pitch": 45}],
    }
    base = SURGE.script(
        "audio/sources/a.json", "", json.dumps(patch), ["audio/sfx/a.wav"]
    )
    script, outputs = ops._with_edits(
        "surge-xt",
        base,
        ["audio/sources/a.json", "audio/sfx/a.wav"],
        [_payload("audio/sfx/a.wav", "audio/sfx/a.ogg")],
    )
    _recipe(stage, "surge-xt", script, outputs)
    (stage / "audio/sources/a.json").write_text(json.dumps(patch))
    worker = Worker(stage)
    worker.outputs = outputs
    monkeypatch.setattr(authoring, "run_offline", worker)
    result = asyncio.run(
        ops.set_surge_parameters(
            stage, "audio/sources/a.json", {"a_filter_1_cutoff": 300, "a_volume": None}
        )
    )
    assert result["parameters"] == {"a_filter_1_cutoff": 300, "a_osc_1_pitch": -5}
    recipe: Recipe = result["recipe_object"]
    assert recipe.outputs == tuple(outputs)
    new = (stage / recipe.script).read_text()
    assert len(ops.recipe_edits(new)["edits"]) == 1
    rendered = ops._base_script(new, "surge-xt")
    assert '"a_filter_1_cutoff": 300' in rendered and "a_volume" not in rendered
    assert recipe.sources and recipe.sources[0].startswith("recipes/inputs/")


def test_compose_refuses_to_overwrite_a_midi_file_it_did_not_write(
    stage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (stage / "audio/music").mkdir(parents=True)
    (stage / "audio/music/a.mid").write_bytes(midi.write_midi(song()))
    monkeypatch.setattr(authoring, "run_offline", AsyncMock())
    with pytest.raises(ValueError, match="edit_music"):
        asyncio.run(
            ops.compose_music(
                stage, "audio/music/a.mid", song().model_dump(), "audio/music/a.ogg"
            )
        )


def test_song_round_trip_through_compose_and_edit(
    stage: Path, monkeypatch: pytest.MonkeyPatch, record: AsyncMock
) -> None:
    worker = Worker(stage, {"steps": [], "loop": {"start_seconds": 0.0}})
    worker.outputs = ["audio/music/a.mid", "audio/music/a.ogg"]
    monkeypatch.setattr(authoring, "run_offline", worker)
    first = asyncio.run(
        ops.compose_music(
            stage, "audio/music/a.mid", song().model_dump(), "audio/music/a.ogg"
        )
    )
    recipe: Recipe = first["recipe_object"]
    assert recipe.tool == "surge-xt" and "FluidR3_GM" in recipe.note
    imported = (stage / "audio/music/a.ogg.import").read_text()
    assert "bpm=120.0" in imported and "beat_count=4" in imported
    # Record it as the current recipe, then edit the song it carries.
    (stage / "recipes/current.toml").write_text(
        f'version = 1\ntool = "surge-xt"\nscript = "{recipe.script}"\n'
        'outputs = ["audio/music/a.mid", "audio/music/a.ogg"]\n'
    )
    info = ops.inspect_music(stage, "audio/music/a.mid")
    assert (
        info["written_by_joinery"] and info["render"]["export"] == "audio/music/a.ogg"
    )
    second = asyncio.run(ops.edit_music(stage, "audio/music/a.mid", {"tempo_bpm": 90}))
    edited = midi.song_from_script((stage / second["recipe_object"].script).read_text())
    assert edited is not None and edited.tempo_bpm == 90
    assert second["recipe_object"].outputs == ("audio/music/a.mid", "audio/music/a.ogg")


def test_imported_midi_renders_from_an_immutable_copy(
    stage: Path, monkeypatch: pytest.MonkeyPatch, record: AsyncMock
) -> None:
    (stage / "audio/music").mkdir(parents=True)
    (stage / "audio/music/in.mid").write_bytes(midi.write_midi(song()))
    worker = Worker(stage)
    worker.outputs = ["audio/music/in.ogg"]
    monkeypatch.setattr(authoring, "run_offline", worker)
    result = asyncio.run(
        ops.render_music(stage, "audio/music/in.mid", "audio/music/in.ogg")
    )
    recipe: Recipe = result["recipe_object"]
    assert recipe.outputs == ("audio/music/in.ogg",)
    assert recipe.sources[0].startswith("recipes/inputs/")
    assert recipe.sources[0] in (stage / recipe.script).read_text()


# --- Server, startup and readiness -------------------------------------------------------------


def test_surge_alone_starts_the_sound_server(tmp_path: Path) -> None:
    (tmp_path / "joinery.toml").write_text('[tools]\nenabled=["surge-xt"]\n')
    names = [spec.name for spec in configured(tmp_path)[0]]
    assert "joinery-sound" in names


def test_sound_server_lists_the_typed_operations_and_surge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "joinery.toml").write_text('[tools]\nenabled=["surge-xt"]\n')
    monkeypatch.setattr(
        server_authoring,
        "run_offline",
        AsyncMock(return_value=Result(True, '{"surge-xt": true}', "", 0.1)),
    )

    async def run() -> tuple[set[str], dict[str, Any]]:
        async with Client(
            server_authoring.make_server(tmp_path, "joinery-sound")
        ) as client:
            listed = (await client.list_tools()).tools
            got = await client.call_tool("document_capabilities", {})
            return {tool.name for tool in listed}, json.loads(got.content[0].text)

    names, capabilities = asyncio.run(run())
    assert {
        "compose_music",
        "edit_music",
        "inspect_music",
        "render_music",
        "process_audio",
        "set_surge_parameters",
    } <= names
    assert capabilities["tools"] == [{"id": "surge-xt", "application_installed": True}]


def test_surge_features_are_measured_in_the_worker() -> None:
    assert MUSIC_CHECK.feature == "music" and AUDIO_EDIT_CHECK.feature == "audio-edit"
    assert midi.SOUNDFONT in " ".join(MUSIC_CHECK.argv)

    class Host:
        """Healthy worker except for the SoundFont."""

        def docker(self) -> str:
            return ""

        def image(self, name: str) -> Any:
            from joinery.readiness import Image

            return Image("sha256:" + name)

        def run(
            self, image: str, argv: Any, entrypoint: str, timeout: int
        ) -> tuple[int, str]:
            joined = " ".join(argv)
            if "FluidR3_GM" in joined:
                return (
                    1,
                    "sha256sum: /usr/share/sounds/sf2/FluidR3_GM.sf2: No such file",
                )
            if "JOINERY_AUDIO_EDIT_OK" in joined:
                return 0, "4\n1\nJOINERY_AUDIO_EDIT_OK\n"
            return 0, "1.3.4\n"

        def installed(self, tool: str) -> str:
            return ""

        def tree(self, tool: str) -> str:
            return ""

        def setup(self) -> None:
            return None

        def expected_revision(self) -> str:
            return ""

        def desktop(self, project: Path | None, key: str) -> tuple[bool, str]:
            return False, ""

    report = status(tool="surge-xt", refresh=True, host=Host())  # type: ignore[arg-type]
    (surge,) = report["tools"]
    features = {f["id"]: f for f in surge["features"]}
    assert surge["state"] == "ready"
    assert features["audio-edit"]["state"] == "ready"
    assert features["music"]["state"] == "needs_image"
    # Missing from a worker that is there: the one refresh wording, not the worker's name.
    assert features["music"]["detail"] == (
        "MIDI music (General MIDI and Surge XT) needs a refresh after the Joinery update."
    )
    assert "Audio edits" in surge["summary"] and "MIDI music" not in surge["summary"]


@pytest.fixture(autouse=True)
def readiness_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOINERY_READINESS_CACHE", str(tmp_path / "readiness-cache"))
