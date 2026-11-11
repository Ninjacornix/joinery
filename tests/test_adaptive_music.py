"""Adaptive music without a worker: rule-based composition, the stem recipe, the
track description, plan edits and the loudness of a section's mix.

The real renders (FluidSynth, Surge XT, ffmpeg), equal stem lengths after
decoding, loop seams and byte-for-byte replay are checked by
tests/integration/sound_pass.py.
"""

from __future__ import annotations

import ast
import asyncio
import base64
import json
import shlex
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from joinery.bible_rules import check_rules
from joinery.music_track import (
    STATES,
    Layer,
    MusicTrack,
    from_tres,
    mix_measure_script,
    section_mixes,
    to_tres,
)
from joinery.runner import Result
from joinery.tools import audio_chain, compose, midi, stems
from servers.sound import tracks

LAYERS = ["pads", "bass", "percussion", "melody", "tension"]


def plan(**changes: object) -> compose.TrackPlan:
    data: dict[str, object] = {
        "name": "theme",
        "mood": "adventurous",
        "key": "D minor",
        "tempo_bpm": 120,
        "bars": 8,
        "layers": LAYERS,
        "sections": ["main", "combat"],
    }
    data.update(changes)
    return compose.TrackPlan.model_validate(data)


def midi_bytes(songs: dict[str, midi.Song]) -> dict[str, bytes]:
    return {name: midi.write_midi(song) for name, song in songs.items()}


# --- Composition ---------------------------------------------------------------------


def test_the_same_plan_writes_the_same_notes() -> None:
    first = midi_bytes(compose.compose(plan(seed=7)))
    again = midi_bytes(compose.compose(plan(seed=7)))
    assert first == again
    assert midi_bytes(compose.compose(plan(seed=8))) != first


def test_variation_changes_the_melody_and_keeps_the_harmony() -> None:
    base = compose.compose(plan())["main"]
    varied = compose.compose(plan(variation=3))["main"]
    by_name = {t.name: t.notes for t in base.tracks}
    varied_by_name = {t.name: t.notes for t in varied.tracks}
    assert by_name["pads"] == varied_by_name["pads"]
    assert by_name["melody"] != varied_by_name["melody"]


@pytest.mark.parametrize("mood", compose.MOODS)
def test_every_mood_composes_every_layer_and_stinger(mood: str) -> None:
    track = plan(mood=mood, key="F#")
    songs = compose.compose(track)
    assert set(songs) == {"main", "combat"}
    for song in songs.values():
        assert [t.name for t in song.tracks] == LAYERS
        assert all(t.notes for t in song.tracks)
        assert song.bars == 8 and song.tail_beats == song.bar_beats
        # Notes start inside the loop and ring at most into one bar of tail,
        # which the render folds into the start: the loop has no seam.
        for t in song.tracks:
            assert max(n.beat for n in t.notes) < song.length_beats
            assert max(n.beat + n.beats for n in t.notes) <= (
                song.length_beats + song.tail_beats + 1e-9
            )
    for event in ("victory", "checkpoint", "fail"):
        assert compose.stinger(track, event).tracks[0].notes


def test_melody_and_chords_stay_in_the_key() -> None:
    song = compose.compose(plan(mood="dark", key="A minor"))["main"]
    scale = {(9 + step) % 12 for step in compose.MODES["minor"]}
    for name in ("pads", "melody", "bass"):
        track = next(t for t in song.tracks if t.name == name)
        off = [n.pitch for n in track.notes if n.pitch % 12 not in scale]
        # Bass approach notes may lean a semitone into the next root.
        assert len(off) <= (len(track.notes) // 4 if name == "bass" else 0), (name, off)


def test_density_thins_and_fills_the_parts() -> None:
    sparse = compose.compose(plan(density=0.1))["main"]
    busy = compose.compose(plan(density=0.95))["main"]

    def count(song: midi.Song, name: str) -> int:
        return len(next(t for t in song.tracks if t.name == name).notes)

    assert count(sparse, "bass") < count(busy, "bass")
    assert count(sparse, "percussion") < count(busy, "percussion")


@pytest.mark.parametrize(
    ("text", "tonic", "mode"),
    [
        ("D minor", 2, "minor"),
        ("F# major", 6, "major"),
        ("Bb dorian", 10, "dorian"),
        ("c", 0, "lydian"),
        ("Am", 9, "minor"),
    ],
)
def test_keys_are_read_in_plain_words(text: str, tonic: int, mode: str) -> None:
    key = compose.parse_key(text, "lydian")
    assert (key.tonic, key.mode) == (tonic, mode)


def test_plans_refuse_what_cannot_play() -> None:
    with pytest.raises(ValidationError, match="key"):
        plan(key="H minor")
    with pytest.raises(ValidationError, match="percussion"):
        plan(layers=["percussion", {"name": "drums2", "role": "percussion"}])
    with pytest.raises(ValidationError, match="unique"):
        plan(layers=["pads", "pads"])
    with pytest.raises(ValidationError, match="stingers"):
        plan(stingers=["boss"])


# --- The stem recipe -----------------------------------------------------------------


def payload(script: str) -> dict[str, Any]:
    line = script.strip().splitlines()[-1]
    assert line.startswith("    render_track(")
    literal = line.strip()[len("render_track(") : -1]
    data: dict[str, Any] = json.loads(ast.literal_eval(literal))
    return data


def test_every_stem_of_a_section_is_rendered_the_same_length() -> None:
    track = plan()
    songs = compose.compose(track)
    script = stems.render_script(
        track,
        songs,
        {e: compose.stinger(track, e) for e in track.stingers},
        {},
        lufs=-16.0,
        true_peak=-1.0,
        output=audio_chain.Output(channels=2, sample_rate=48000),
    )
    data = payload(script)
    sections = data["sections"]
    assert isinstance(sections, list)
    for section in sections:
        assert section["loop_seconds"] == pytest.approx(stems.stem_seconds(track))
        assert section["loop_seconds"] == pytest.approx(8 * 4 * 0.5)
        exports = [layer["export"] for layer in section["layers"]]
        assert exports == [
            f"audio/music/theme/{section['name']}_{name}.ogg" for name in LAYERS
        ]
        # Each layer is its own General MIDI file with only that part.
        for layer in section["layers"]:
            alone = midi.read_midi(base64.b64decode(layer["gm_midi"]))
            assert [t.name for t in alone.tracks] == [layer["name"]]
    assert stems.plan_from_script(script) == track
    where = stems.paths(track)
    assert where.description == "music/theme.tres"
    assert set(data["sources"]) == {
        *where.sources.values(),
        *(source for source, _ in where.stingers.values()),
    }
    assert sorted(where.outputs()) == sorted(set(where.outputs()))


# --- The track description -----------------------------------------------------------


def description() -> MusicTrack:
    return tracks.describe(plan(), None)


def test_a_description_round_trips_and_survives_godot_saving_it() -> None:
    track = description()
    text = to_tres(track)
    assert text.startswith('[gd_resource type="Resource" format=3]')
    assert from_tres(text) == track
    # Godot spreads a dictionary over lines and writes StringNames as &"…".
    resaved = text.replace(", ", ",\n").replace('"bus": "Music"', '"bus": &"Music"')
    assert from_tres(resaved) == track
    with pytest.raises(ValueError, match="joinery_music"):
        from_tres('[gd_resource type="Resource" format=3]\n\n[resource]\n')


def test_default_states_raise_the_layers_with_intensity() -> None:
    track = description()
    assert list(track.states) == list(STATES)
    calm, combat = track.gains("calm"), track.gains("combat")
    assert calm["tension"] == 0.0 and combat["tension"] == 1.0
    assert (
        sum(calm.values()) < sum(track.gains("explore").values()) < sum(combat.values())
    )
    # The second section is the horizontal switch: combat plays it.
    assert track.states["combat"].section == "combat"
    assert track.states["explore"].section == "main"


def test_faders_mutes_and_solos_shape_the_gains() -> None:
    track = description()
    track.layers[0].volume_db = -6.0
    track.layers[1].muted = True
    gains = track.gains("explore")
    assert gains["pads"] == pytest.approx(10 ** (-6 / 20))
    assert gains["bass"] == 0.0
    track.layers[3].solo = True
    assert [k for k, v in track.gains("combat").items() if v] == ["melody"]


def test_a_description_refuses_what_the_game_cannot_play() -> None:
    data = description().model_dump()
    data["sections"][0]["stems"].pop("bass")
    with pytest.raises(ValidationError, match="one stem per layer"):
        MusicTrack.model_validate(data)
    data = description().model_dump()
    data["states"]["boss"] = data["states"]["combat"]
    with pytest.raises(ValidationError, match="unknown states"):
        MusicTrack.model_validate(data)
    with pytest.raises(ValidationError):
        Layer(name="Pads", role="pads")


def test_an_edit_keeps_the_mix_the_user_set() -> None:
    before = description()
    before.layers[1].volume_db = -4.5
    before.states["calm"].layers["melody"] = 0.2
    before.default_state = "calm"
    edited = tracks.apply_change(
        plan(),
        tracks.TrackChange(
            tempo_bpm=128,
            add_layers=[compose.LayerPlan(name="arp", role="melody", program=8)],
            remove_layers=["percussion"],
        ),
    )
    after = tracks.describe(edited, before)
    assert [layer.name for layer in after.layers] == [
        "pads",
        "bass",
        "melody",
        "tension",
        "arp",
    ]
    assert after.layers[1].volume_db == -4.5
    assert after.states["calm"].layers["melody"] == 0.2
    assert "percussion" not in after.states["calm"].layers
    assert after.default_state == "calm" and after.tempo_bpm == 128


def test_track_changes() -> None:
    track = plan()
    revoiced = tracks.apply_change(
        track,
        tracks.TrackChange(
            revoice={"pads": {"program": 52}}, layer_density={"bass": 0.9}, key="E"
        ),
    )
    assert revoiced.layers[0].program == 52 and revoiced.layers[1].density == 0.9
    assert revoiced.key == "E" and revoiced.parsed_key.mode == "major"
    assert not tracks.TrackChange(mute={"pads": True}).renders()
    assert tracks.TrackChange(variation=2).renders()
    with pytest.raises(ValueError, match="No layer named lead"):
        tracks.apply_change(track, tracks.TrackChange(remove_layers=["lead"]))
    with pytest.raises(ValueError, match="every layer"):
        tracks.apply_change(track, tracks.TrackChange(remove_layers=LAYERS))


# --- Loudness of the mix -------------------------------------------------------------

BIBLE = """# Art bible

```toml
[audio]
true_peak_max_dbtp = -1.0
lufs_tolerance = 1.0
[audio.lufs]
"audio/music" = -16.0
```
"""


def summary(label: str, lufs: float, peak: float) -> str:
    return (
        f"JOINERY_FILE:{label}\n[Parsed_ebur128_0 @ 0x1] Summary:\n\n"
        f"  Integrated loudness:\n    I:         {lufs} LUFS\n\n"
        f"  True peak:\n    Peak:      {peak} dBFS\n"
    )


def project(tmp_path: Path) -> tuple[Path, list[str]]:
    (tmp_path / "art").mkdir()
    (tmp_path / "art/bible.md").write_text(BIBLE)
    track = tracks.describe(plan(layers=["pads", "bass"], sections=["main"]), None)
    (tmp_path / "music").mkdir()
    (tmp_path / "music/theme.tres").write_text(to_tres(track))
    files = [s.removeprefix("res://") for s in track.sections[0].stems.values()]
    for path in files:
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_bytes(b"OggS")
    return tmp_path, sorted(files)


def test_stems_are_judged_by_their_section_mix(tmp_path: Path) -> None:
    root, files = project(tmp_path)
    mixes = section_mixes(root)
    assert mixes == {"music/theme.tres (main mix)": files}
    script = mix_measure_script(mixes)
    assert "amix=inputs=2:normalize=0" in script
    assert all(shlex.quote(f) in script for f in files)

    def measured(mix_lufs: float):  # type: ignore[no-untyped-def]
        async def measure(argv: list[str], _root: Path) -> Result:
            if "amix" in argv[2]:
                return Result(True, summary(next(iter(mixes)), mix_lufs, -3.0), "", 0.1)
            # Each stem alone is far quieter than the target: never a problem.
            text = "".join(summary(f, -25.0, -9.0) for f in files)
            return Result(True, text, "", 0.1)

        return measure

    assert asyncio.run(check_rules(root, files, measured(-16.2))) == []
    problems = asyncio.run(check_rules(root, files, measured(-12.0)))
    assert problems == [
        "music/theme.tres (main mix): -12.0 LUFS, the art bible asks for -16.0 ± 1.0"
    ]
