"""Music as MIDI sources, rendered through FluidSynth (General MIDI) and Surge XT.

A song is a typed score: tempo, metre, length in bars, and tracks of notes, each
played by a General MIDI program (FluidSynth with the FluidR3_GM SoundFont), the
General MIDI drum kit, or a Surge XT patch from the project. Joinery writes the
score as a Standard MIDI File under audio/ (the native source anyone can open
in a sequencer) and renders it in the offline creative worker:

    .mid -> FluidSynth (GM tracks) + Surge XT (one fresh plugin per track)
         -> mix -> loop (the release tail folded into the start) -> loudness
         -> Ogg Vorbis or WAV, with loop points

Everything here is deterministic: FluidSynth and Surge render the same samples
every time (Surge tracks are rendered twice and compared), and the audio chain
encodes bit-exactly, so `joinery rebuild` replays a song byte for byte. The
recipe runs under `joinery-surge`, Surge XT's offline renderer, so a song is a
Surge XT recipe; the SoundFont is checked by hash before every render and named
in the recipe note.

The MIDI writer and reader are plain standard-library code, so the host can
read an imported .mid for editing without any extra package.
"""

from __future__ import annotations

import ast
import base64
import json
import math
import struct
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from joinery.tools import audio_chain

#: Ticks per quarter note in every MIDI file Joinery writes.
TICKS = 480
#: The General MIDI SoundFont in the creative worker (Debian fluid-soundfont-gm).
SOUNDFONT = "/usr/share/sounds/sf2/FluidR3_GM.sf2"
SOUNDFONT_SHA256 = "74594e8f4250680adf590507a306655a299935343583256f3b722c48a1bc1cb0"
SOUNDFONT_CREDIT = (
    "FluidR3_GM.sf2 (Debian fluid-soundfont-gm 3.1-5.3, MIT licence, "
    f"sha256 {SOUNDFONT_SHA256[:16]}…)"
)
#: FluidSynth's master gain. Renders are float, so nothing clips; the mix is
#: brought to the art bible's loudness afterwards.
FLUIDSYNTH_GAIN = 0.4
#: Loudness for music when the art bible sets none (the template's target).
DEFAULT_MUSIC_LUFS = -16.0
#: MIDI channel 10 is General MIDI percussion.
DRUM_CHANNEL = 9
#: Longest render Surge XT's offline renderer accepts.
MAX_SECONDS = 600.0


def _quantise(beats: float) -> float:
    return round(beats * TICKS) / TICKS


class Note(BaseModel):
    """One note; positions and lengths are in quarter notes (beats)."""

    model_config = ConfigDict(extra="forbid")

    pitch: int = Field(ge=0, le=127)
    beat: float = Field(ge=0, le=100_000)
    beats: float = Field(gt=0, le=4096)
    velocity: int = Field(default=100, ge=1, le=127)

    @model_validator(mode="after")
    def on_the_grid(self) -> Note:
        self.beat = _quantise(self.beat)
        self.beats = max(1, round(self.beats * TICKS)) / TICKS
        return self


class Track(BaseModel):
    """One part, played by exactly one instrument."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=60)
    #: General MIDI program number, 0-127 (0 is Acoustic Grand Piano). With
    #: `drums`, it picks the drum kit instead (0 is Standard).
    program: int | None = Field(default=None, ge=0, le=127)
    #: General MIDI percussion (channel 10): each pitch is a drum sound.
    drums: bool = False
    #: A Surge XT source under audio/ (its parameters; its own notes are ignored).
    surge: str | None = None
    #: MIDI volume (CC 7) and pan (CC 10, 64 is centre).
    volume: int = Field(default=100, ge=0, le=127)
    pan: int = Field(default=64, ge=0, le=127)
    notes: list[Note] = Field(min_length=1, max_length=20_000)

    @field_validator("name")
    @classmethod
    def printable(cls, value: str) -> str:
        if not value.isprintable() or not value.strip():
            raise ValueError("track names are printable text")
        return value.strip()

    @model_validator(mode="after")
    def one_instrument(self) -> Track:
        if self.surge is not None:
            if self.program is not None or self.drums:
                raise ValueError(
                    f"{self.name}: a Surge XT track has no General MIDI program or drums"
                )
            path = Path(self.surge)
            if path.is_absolute() or ".." in path.parts or path.parts[:1] != ("audio",):
                raise ValueError(f"{self.name}: the Surge patch must be under audio/")
            if path.suffix != ".json":
                raise ValueError(f"{self.name}: the Surge patch is a .json source")
        elif self.drums and self.program is None:
            self.program = 0  # the Standard kit
        elif self.program is None and not self.drums:
            raise ValueError(
                f"{self.name}: give a General MIDI program (0-127), drums, "
                "or a Surge XT patch"
            )
        return self

    @property
    def kind(self) -> Literal["surge", "drums", "general-midi"]:
        if self.surge is not None:
            return "surge"
        return "drums" if self.drums else "general-midi"


class Song(BaseModel):
    """A score Joinery writes as MIDI and renders offline."""

    model_config = ConfigDict(extra="forbid")

    #: Quarter notes per minute.
    tempo_bpm: float = Field(ge=20, le=300)
    beats_per_bar: int = Field(default=4, ge=1, le=16)
    beat_unit: Literal[2, 4, 8, 16] = 4
    #: Length in bars; a looping song loops exactly this long.
    bars: int = Field(ge=1, le=512)
    #: Quarter notes rendered after the last bar so notes can ring out; folded
    #: back into the start when the music loops.
    tail_beats: float = Field(default=4.0, ge=0, le=32)
    sample_rate: Literal[44100, 48000] = 48000
    tracks: list[Track] = Field(min_length=1, max_length=16)

    @property
    def bar_beats(self) -> float:
        """Quarter notes in one bar."""
        return self.beats_per_bar * 4 / self.beat_unit

    @property
    def length_beats(self) -> float:
        return self.bars * self.bar_beats

    @property
    def seconds_per_beat(self) -> float:
        return 60.0 / self.tempo_bpm

    @property
    def length_seconds(self) -> float:
        return self.length_beats * self.seconds_per_beat

    @property
    def total_seconds(self) -> float:
        return (self.length_beats + self.tail_beats) * self.seconds_per_beat

    @model_validator(mode="after")
    def fits(self) -> Song:
        self.tail_beats = _quantise(self.tail_beats)
        names = [track.name for track in self.tracks]
        if len(set(names)) != len(names):
            raise ValueError("track names must be unique")
        if sum(track.drums for track in self.tracks) > 1:
            raise ValueError("use one drums track (General MIDI has one drum channel)")
        if sum(not track.drums for track in self.tracks) > 15:
            raise ValueError(
                "use at most 15 tracks besides drums (MIDI has 16 channels)"
            )
        if self.total_seconds > MAX_SECONDS - 0.001:
            raise ValueError(
                f"the song lasts {self.total_seconds:.0f} s with its tail; "
                f"keep it under {MAX_SECONDS:.0f} s"
            )
        end = self.length_beats + self.tail_beats
        for track in self.tracks:
            playing: dict[int, float] = {}
            for note in sorted(track.notes, key=lambda n: (n.beat, n.pitch)):
                if note.beat >= self.length_beats:
                    raise ValueError(
                        f"{track.name}: a note starts at beat {note.beat:g}, after the "
                        f"song's {self.bars} bars ({self.length_beats:g} beats)"
                    )
                if note.beat + note.beats > end + 1e-9:
                    raise ValueError(
                        f"{track.name}: the note at beat {note.beat:g} rings past the "
                        f"tail; shorten it or raise tail_beats"
                    )
                if playing.get(note.pitch, -1.0) > note.beat + 1e-9:
                    raise ValueError(
                        f"{track.name}: two notes of pitch {note.pitch} overlap at "
                        f"beat {note.beat:g}"
                    )
                playing[note.pitch] = note.beat + note.beats
        return self

    def channels(self) -> list[int]:
        """The MIDI channel of each track: drums on 10, the rest in order."""
        free = [c for c in range(16) if c != DRUM_CHANNEL]
        out = []
        for track in self.tracks:
            out.append(DRUM_CHANNEL if track.drums else free.pop(0))
        return out


# --- Standard MIDI File writing --------------------------------------------------


def _vlq(value: int) -> bytes:
    out = [value & 0x7F]
    value >>= 7
    while value:
        out.append(0x80 | (value & 0x7F))
        value >>= 7
    return bytes(reversed(out))


def _chunk(kind: bytes, body: bytes) -> bytes:
    return kind + struct.pack(">I", len(body)) + body


def _track_chunk(events: list[tuple[int, int, bytes]], end: int) -> bytes:
    body = bytearray()
    now = 0
    for tick, _order, data in sorted(events, key=lambda e: (e[0], e[1], e[2])):
        body += _vlq(tick - now) + data
        now = tick
    body += _vlq(max(0, end - now)) + b"\xff\x2f\x00"
    return _chunk(b"MTrk", bytes(body))


def _meta(kind: int, data: bytes) -> bytes:
    return bytes([0xFF, kind]) + _vlq(len(data)) + data


def write_midi(song: Song, *, general_midi_only: bool = False) -> bytes:
    """The song as a type-1 Standard MIDI File (deterministic bytes).

    `general_midi_only` leaves out the Surge XT tracks: FluidSynth renders that
    file, and Surge renders its tracks itself.
    """
    end = round((song.length_beats + song.tail_beats) * TICKS)
    tempo = round(60_000_000 / song.tempo_bpm)
    conductor = [
        (0, 0, _meta(0x03, b"Joinery")),
        (
            0,
            1,
            _meta(
                0x58,
                bytes([song.beats_per_bar, int(math.log2(song.beat_unit)), 24, 8]),
            ),
        ),
        (0, 2, _meta(0x51, tempo.to_bytes(3, "big"))),
    ]
    chunks = [_track_chunk(conductor, end)]
    for track, channel in zip(song.tracks, song.channels(), strict=True):
        if general_midi_only and track.kind == "surge":
            continue
        events: list[tuple[int, int, bytes]] = [
            (0, 0, _meta(0x03, track.name.encode("utf-8")[:127])),
            (0, 2, bytes([0xB0 | channel, 7, track.volume])),
            (0, 3, bytes([0xB0 | channel, 10, track.pan])),
        ]
        if track.kind != "surge":
            events.append((0, 1, bytes([0xC0 | channel, track.program or 0])))
        for note in track.notes:
            start = round(note.beat * TICKS)
            stop = start + round(note.beats * TICKS)
            # Note-offs sort before note-ons at the same tick, so a repeated
            # pitch is released before it is struck again.
            events.append(
                (start, 5, bytes([0x90 | channel, note.pitch, note.velocity]))
            )
            events.append((stop, 4, bytes([0x80 | channel, note.pitch, 0])))
        chunks.append(_track_chunk(events, end))
    header = _chunk(b"MThd", struct.pack(">HHH", 1, len(chunks), TICKS))
    return header + b"".join(chunks)


# --- Standard MIDI File reading ---------------------------------------------------

#: Controllers the reader keeps (volume, pan) or can drop without changing the
#: sound of a General MIDI render (bank select, reset, data entry setup).
_KEPT_CONTROLLERS = {7, 10}
_IGNORED_CONTROLLERS = {0, 32, 121}
_IGNORED_META = {
    0x00,
    0x01,
    0x02,
    0x04,
    0x05,
    0x06,
    0x07,
    0x09,
    0x20,
    0x21,
    0x54,
    0x59,
    0x7F,
}


class _Reader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.at = 0

    def take(self, count: int) -> bytes:
        if self.at + count > len(self.data):
            raise ValueError("the MIDI file is truncated")
        out = self.data[self.at : self.at + count]
        self.at += count
        return out

    def byte(self) -> int:
        return self.take(1)[0]

    def vlq(self) -> int:
        value = 0
        for _ in range(4):
            byte = self.byte()
            value = (value << 7) | (byte & 0x7F)
            if not byte & 0x80:
                return value
        raise ValueError("the MIDI file has a malformed length")


def read_midi(data: bytes, *, tail_beats: float = 4.0) -> Song:
    """Read a Standard MIDI File into a Song.

    Notes, programs, volume, pan, one tempo and the time signature are kept.
    Anything that would change the sound but has no place in a Song (pitch
    bends, sustain pedal, tempo changes, other controllers) is refused by name
    rather than silently dropped.
    """
    reader = _Reader(data)
    if reader.take(4) != b"MThd":
        raise ValueError("not a Standard MIDI File")
    length = struct.unpack(">I", reader.take(4))[0]
    header = reader.take(length)
    if len(header) < 6:
        raise ValueError("the MIDI header is too short")
    _format, count, division = struct.unpack(">HHH", header[:6])
    if division & 0x8000:
        raise ValueError("SMPTE-timed MIDI files are not supported; use ticks per beat")
    tempos: set[int] = set()
    signature: tuple[int, int] | None = None
    unsupported: set[str] = set()
    parts: dict[tuple[int, int], dict[str, Any]] = {}
    names: dict[int, str] = {}
    for index in range(count):
        if reader.at >= len(data):
            break
        kind = reader.take(4)
        size = struct.unpack(">I", reader.take(4))[0]
        if kind != b"MTrk":
            reader.take(size)
            continue
        track = _Reader(reader.take(size))
        tick = 0
        status = 0
        open_notes: dict[tuple[int, int], list[tuple[int, int]]] = {}
        while track.at < len(track.data):
            tick += track.vlq()
            first = track.byte()
            if first == 0xFF:
                meta = track.byte()
                body = track.take(track.vlq())
                if meta == 0x2F:
                    break
                if meta == 0x51 and len(body) == 3:
                    tempos.add(int.from_bytes(body, "big"))
                elif meta == 0x58 and len(body) >= 2:
                    signature = signature or (body[0], 2 ** body[1])
                elif meta == 0x03:
                    names.setdefault(index, body.decode("utf-8", "replace").strip())
                elif meta not in _IGNORED_META:
                    unsupported.add(f"meta event 0x{meta:02x}")
                continue
            if first in (0xF0, 0xF7):
                # System exclusive (usually a GM reset): carries no notes.
                track.take(track.vlq())
                continue
            values: list[int] = []
            if first & 0x80:
                status = first
            elif status:
                values.append(first)  # running status: the first data byte
            else:
                raise ValueError("the MIDI file uses running status before any status")
            kind_nibble, channel = status >> 4, status & 0x0F
            while len(values) < (1 if kind_nibble in (0xC, 0xD) else 2):
                values.append(track.byte())
            part = parts.setdefault(
                (index, channel),
                {"notes": [], "program": None, "volume": 100, "pan": 64},
            )
            if kind_nibble == 0x9 and values[1] > 0:
                open_notes.setdefault((channel, values[0]), []).append(
                    (tick, values[1])
                )
            elif kind_nibble in (0x8, 0x9):
                started = open_notes.get((channel, values[0]))
                if started:
                    begin, velocity = started.pop(0)
                    if tick > begin:
                        part["notes"].append((begin, tick - begin, values[0], velocity))
            elif kind_nibble == 0xC:
                part["program"] = values[0]
            elif kind_nibble == 0xB:
                controller, value = values
                if controller == 7:
                    part["volume"] = value
                elif controller == 10:
                    part["pan"] = value
                elif controller not in _IGNORED_CONTROLLERS:
                    unsupported.add(f"controller {controller}")
            elif kind_nibble == 0xE:
                unsupported.add("pitch bend")
            else:
                unsupported.add("aftertouch")
        for (_channel, pitch), left in open_notes.items():
            if left:
                unsupported.add(f"a note ({pitch}) that never ends")
    if len(tempos) > 1:
        unsupported.add("tempo changes")
    if unsupported:
        raise ValueError(
            "This MIDI file uses things a Joinery song cannot keep: "
            + ", ".join(sorted(unsupported))
            + ". Render it as it is with render_music instead of editing it."
        )
    tempo = 60_000_000 / (tempos.pop() if tempos else 500_000)
    beats_per_bar, beat_unit = signature or (4, 4)
    if beat_unit not in (2, 4, 8, 16):
        beats_per_bar, beat_unit = 4, 4
    tracks: list[dict[str, Any]] = []
    last = 0.0
    used: set[str] = set()
    for (index, channel), part in sorted(parts.items()):
        if not part["notes"]:
            continue
        name = names.get(index) or f"Channel {channel + 1}"
        if name in used:
            name = f"{name} ({channel + 1})"
        used.add(name)
        notes = [
            {
                "pitch": pitch,
                "beat": begin / division,
                "beats": length / division,
                "velocity": velocity,
            }
            for begin, length, pitch, velocity in sorted(part["notes"])
        ]
        last = max(last, *(n["beat"] + n["beats"] for n in notes))
        tracks.append(
            {
                "name": name[:60],
                "program": part["program"] or 0,
                "drums": channel == DRUM_CHANNEL,
                "volume": part["volume"],
                "pan": part["pan"],
                "notes": notes,
            }
        )
    if not tracks:
        raise ValueError("the MIDI file has no notes")
    bar = beats_per_bar * 4 / beat_unit
    return Song.model_validate(
        {
            "tempo_bpm": round(tempo, 3),
            "beats_per_bar": beats_per_bar,
            "beat_unit": beat_unit,
            "bars": max(1, math.ceil(last / bar - 1e-9)),
            "tail_beats": tail_beats,
            "tracks": tracks,
        }
    )


# --- Edits -------------------------------------------------------------------------


class TrackChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    program: int | None = Field(default=None, ge=0, le=127)
    surge: str | None = None
    volume: int | None = Field(default=None, ge=0, le=127)
    pan: int | None = Field(default=None, ge=0, le=127)
    transpose: int = Field(default=0, ge=-48, le=48)
    remove: bool = False


class SongChange(BaseModel):
    """Typed edits to an existing song; anything left out stays as it is."""

    model_config = ConfigDict(extra="forbid")

    tempo_bpm: float | None = Field(default=None, ge=20, le=300)
    #: Semitones for every track except drums.
    transpose: int = Field(default=0, ge=-48, le=48)
    #: Multiplies every velocity (clamped to 1-127).
    velocity_scale: float = Field(default=1.0, gt=0, le=8)
    tail_beats: float | None = Field(default=None, ge=0, le=32)
    tracks: dict[str, TrackChange] = {}


def apply_change(song: Song, change: SongChange) -> Song:
    data = song.model_dump()
    missing = sorted(set(change.tracks) - {t.name for t in song.tracks})
    if missing:
        raise ValueError(
            "No track named "
            + ", ".join(missing)
            + "; the song has "
            + ", ".join(t.name for t in song.tracks)
        )
    if change.tempo_bpm is not None:
        data["tempo_bpm"] = change.tempo_bpm
    if change.tail_beats is not None:
        data["tail_beats"] = change.tail_beats
    tracks = []
    for track in data["tracks"]:
        edit = change.tracks.get(track["name"], TrackChange())
        if edit.remove:
            continue
        shift = edit.transpose + (0 if track["drums"] else change.transpose)
        for note in track["notes"]:
            note["pitch"] += shift
            note["velocity"] = max(
                1, min(127, round(note["velocity"] * change.velocity_scale))
            )
            if not 0 <= note["pitch"] <= 127:
                raise ValueError(
                    f"{track['name']}: transposing moves a note outside MIDI 0-127"
                )
        if edit.program is not None:
            track.update(program=edit.program, surge=None)
        if edit.surge is not None:
            track.update(surge=edit.surge, program=None, drums=False)
        if edit.volume is not None:
            track["volume"] = edit.volume
        if edit.pan is not None:
            track["pan"] = edit.pan
        tracks.append(track)
    if not tracks:
        raise ValueError("an edit cannot remove every track")
    data["tracks"] = tracks
    return Song.model_validate(data)


# --- The render recipe ------------------------------------------------------------

#: The line in a song recipe that carries its score, so a later edit starts from it.
SONG_LINE = "SONG_JSON = "


def song_from_script(text: str) -> Song | None:
    for line in text.splitlines():
        if line.startswith(SONG_LINE):
            return Song.model_validate_json(
                ast.literal_eval(line.removeprefix(SONG_LINE))
            )
    return None


def surge_parameters(project: Path, song: Song) -> dict[str, dict[str, Any]]:
    """A snapshot of each Surge track's patch parameters, read from its source.

    The recipe carries the snapshot, so a later edit of the patch does not
    change this song until the song itself is rendered again.
    """
    patches: dict[str, dict[str, Any]] = {}
    for track in song.tracks:
        if track.surge is None:
            continue
        path = project / track.surge
        if not path.is_file() or not path.resolve().is_relative_to(project.resolve()):
            raise ValueError(
                f"{track.name}: {track.surge} is not a Surge source in this project"
            )
        try:
            patch = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            raise ValueError(
                f"{track.name}: {track.surge} is not readable JSON"
            ) from exc
        if not isinstance(patch, dict) or patch.get("synth") != "surge-xt":
            raise ValueError(f"{track.name}: {track.surge} is not a Surge XT source")
        parameters = patch.get("parameters", {})
        if not isinstance(parameters, dict):
            raise ValueError(  # noqa: TRY004 - reported to the agent as a tool error
                f"{track.name}: {track.surge} has no parameter map"
            )
        patches[track.name] = parameters
    return patches


def render_script(
    song: Song,
    source: str,
    export: str,
    patches: dict[str, dict[str, Any]],
    *,
    loop: bool,
    steps: list[audio_chain.Step],
    output: audio_chain.Output | None = None,
) -> str:
    """The recipe that writes the .mid source and renders it to `export`."""
    audio_chain.check_paths(export, export)
    if Path(source).suffix.lower() not in {".mid", ".midi"} or Path(source).parts[
        :1
    ] != ("audio",):
        raise ValueError("A song's source is a .mid file under audio/.")
    output = output or audio_chain.Output()
    surge = []
    for track, channel in zip(song.tracks, song.channels(), strict=True):
        if track.kind != "surge":
            continue
        surge.append(
            {
                "name": track.name,
                "channel": channel,
                "parameters": patches[track.name],
                "volume": track.volume,
                "pan": track.pan,
                "notes": [
                    {
                        "pitch": note.pitch,
                        "start": note.beat * song.seconds_per_beat,
                        "duration": note.beats * song.seconds_per_beat,
                        "velocity": note.velocity,
                    }
                    for note in track.notes
                ],
            }
        )
    general = any(track.kind != "surge" for track in song.tracks)
    payload = {
        "source": source,
        "export": export,
        "source_midi": base64.b64encode(write_midi(song)).decode(),
        "gm_midi": (
            base64.b64encode(write_midi(song, general_midi_only=True)).decode()
            if general
            else ""
        ),
        "soundfont": SOUNDFONT,
        "soundfont_sha256": SOUNDFONT_SHA256,
        "fluidsynth_gain": FLUIDSYNTH_GAIN,
        "sample_rate": song.sample_rate,
        "seconds": song.total_seconds,
        "loop_seconds": song.length_seconds if loop else None,
        "surge": surge,
        "steps": [step.model_dump() for step in steps],
        "output": output.model_dump(),
    }
    return (
        "# Joinery song: a MIDI source rendered through FluidSynth and Surge XT.\n"
        "# Replayed by joinery-surge; edit it with edit_music, not by hand.\n"
        + SONG_LINE
        + repr(song.model_dump_json())
        + "\n"
        + audio_chain.WORKER_LIB
        + MUSIC_LIB
        + "\nif __name__ == '__main__':\n    render_song("
        + repr(json.dumps(payload, sort_keys=True))
        + ")\n"
    )


def file_script(
    midi: str, export: str, steps: list[audio_chain.Step], output: audio_chain.Output
) -> str:
    """A recipe that renders an existing MIDI file as it is through FluidSynth."""
    audio_chain.check_paths(export, export)
    payload = {
        "midi": midi,
        "export": export,
        "soundfont": SOUNDFONT,
        "soundfont_sha256": SOUNDFONT_SHA256,
        "fluidsynth_gain": FLUIDSYNTH_GAIN,
        "sample_rate": output.sample_rate or 48000,
        "steps": [step.model_dump() for step in steps],
        "output": output.model_dump(),
    }
    return (
        "# Joinery: a MIDI file rendered as it is through FluidSynth (General MIDI).\n"
        + audio_chain.WORKER_LIB
        + MUSIC_LIB
        + "\nif __name__ == '__main__':\n    render_file("
        + repr(json.dumps(payload, sort_keys=True))
        + ")\n"
    )


# Runs in the worker after WORKER_LIB, under joinery-surge (Surge's own Python,
# which has numpy and the `render` function whose module renders Surge patches).
MUSIC_LIB = r"""
# --- Joinery music: FluidSynth (General MIDI) and Surge XT, mixed ---
import base64 as _base64
import hashlib as _hashlib
import math as _math


def _check_soundfont(path, expected):
    with open(path, "rb") as handle:
        digest = _hashlib.file_digest(handle, "sha256").hexdigest()
    if digest != expected:
        raise RuntimeError(
            "The General MIDI SoundFont in the worker is not the one this song was "
            "made with (%s, expected %s); rebuild the creative worker." % (digest[:16], expected[:16])
        )


def fluidsynth(midi, spec):
    _check_soundfont(spec["soundfont"], spec["soundfont_sha256"])
    wav = "/tmp/joinery-fluidsynth.wav"
    _run(
        ["fluidsynth", "-ni", "-q", "-F", wav, "-T", "wav", "-O", "float",
         "-r", str(spec["sample_rate"]), "-g", "%.3f" % spec["fluidsynth_gain"],
         spec["soundfont"], midi]
    )
    return load(wav, spec["sample_rate"], 2)


def _surge(track, spec, frames):
    import numpy as np

    module = render.__globals__  # noqa: F821 - joinery-surge provides render
    # A hair longer than the song, so a note that ends with it is never
    # refused for floating-point rounding.
    patch = {
        "synth": "surge-xt", "sample_rate": spec["sample_rate"],
        "seconds": spec["seconds"] + 1e-6,
        "parameters": track["parameters"], "notes": track["notes"],
    }
    parsed = module["parse"](_json.dumps(patch))
    first = module["_render"](parsed)
    second = module["_render"](parsed)
    if first.tobytes() != second.tobytes():
        raise RuntimeError(
            "Surge track %r renders differently each time (noise or a free-running "
            "source), so the song could not be replayed exactly." % track["name"]
        )
    if not first.any():
        raise RuntimeError("Surge track %r renders silence." % track["name"])
    data = first.T.astype(np.float64) / 32768.0
    out = np.zeros((frames, 2))
    count = min(frames, data.shape[0])
    out[:count] = data[:count]
    level = (track["volume"] / 127.0) ** 2
    angle = track["pan"] / 127.0 * _math.pi / 2
    out[:, 0] *= level * _math.cos(angle) * _math.sqrt(2)
    out[:, 1] *= level * _math.sin(angle) * _math.sqrt(2)
    return out


def render_song(payload):
    import numpy as np

    spec = _json.loads(payload)
    folder = _os.path.dirname(spec["source"])
    if folder:
        _os.makedirs(folder, exist_ok=True)
    with open(spec["source"], "wb") as handle:
        handle.write(_base64.b64decode(spec["source_midi"]))
    rate = spec["sample_rate"]
    frames = int(round(spec["seconds"] * rate))
    mix = np.zeros((frames, 2))
    report = {"source": spec["source"], "steps": [], "tracks": []}
    if spec["gm_midi"]:
        midi = "/tmp/joinery-general-midi.mid"
        with open(midi, "wb") as handle:
            handle.write(_base64.b64decode(spec["gm_midi"]))
        general = np.frombuffer(_bytes(fluidsynth(midi, spec).samples), dtype="<f4")
        general = general.reshape(-1, 2).astype(np.float64)
        count = min(frames, general.shape[0])
        mix[:count] += general[:count]
        report["tracks"].append({"instrument": "FluidR3_GM", "seconds": round(count / rate, 3)})
    for track in spec["surge"]:
        mix += _surge(track, spec, frames)
        report["tracks"].append({"instrument": "Surge XT", "name": track["name"]})
    if not mix.any():
        raise RuntimeError("The song renders silence.")
    audio = Audio(_floats(mix.astype("<f4").tobytes()), rate, 2)
    loop = None
    if spec["loop_seconds"]:
        audio, loop = loop_points(audio, 0.0, spec["loop_seconds"], 0.0, True)
    audio, _ = apply_steps(audio, spec["steps"], report, lossy_margin(spec["export"]))
    finish(audio, spec["export"], spec["output"], loop, report)


def render_file(payload):
    spec = _json.loads(payload)
    audio = fluidsynth(spec["midi"], spec)
    report = {"source": spec["midi"], "steps": []}
    audio, loop = apply_steps(
        audio, spec["steps"], report, lossy_margin(spec["export"])
    )
    finish(audio, spec["export"], spec["output"], loop, report)
# --- end of Joinery music ---
"""
