"""A small adaptive music track for the Mixer's widget test and the visual suite.

Writes res://music/<name>.tres (joinery.music_track) and one short WAV stem per
layer under audio/music/<name>/, synthesised here (soft tones and a pulse), so
no audio file is kept in the repository for it.
"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

from joinery import music_track

RATE = 22050
#: Layer name, role, tone (Hz; 0 = a noise-free pulse for percussion).
LAYERS = (
    ("pads", "pads", 220.0),
    ("bass", "bass", 55.0),
    ("drums", "percussion", 0.0),
    ("lead", "melody", 440.0),
)


def _stem(path: Path, tone: float, seconds: float, bpm: float) -> None:
    beat = 60.0 / bpm
    frames = bytearray()
    for index in range(int(RATE * seconds)):
        t = index / RATE
        if tone:
            swell = 0.55 + 0.45 * math.sin(2 * math.pi * t / (beat * 4))
            value = 0.35 * swell * math.sin(2 * math.pi * tone * t)
        else:
            since = t % beat
            value = 0.6 * math.exp(-since * 30) * math.sin(2 * math.pi * 90 * since)
        frames += struct.pack("<h", int(max(-1.0, min(1.0, value)) * 32767))
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(bytes(frames))


def plant(project: Path, name: str = "harbour") -> str:
    """Write the track and its stems into ``project``; returns its res:// path."""
    bpm, bars = 120.0, 2
    seconds = bars * 4 * 60.0 / bpm
    layers = [music_track.Layer(name=n, role=role) for n, role, _tone in LAYERS]  # type: ignore[arg-type]
    stems: dict[str, str] = {}
    for layer, _role, tone in LAYERS:
        relative = f"audio/music/{name}/main_{layer}.wav"
        _stem(project / relative, tone, seconds, bpm)
        stems[layer] = "res://" + relative
    track = music_track.MusicTrack(
        name=name,
        mood="calm harbour at dusk",
        key="D minor",
        tempo_bpm=bpm,
        layers=layers,
        sections=[music_track.Section(name="main", bars=bars, stems=stems)],
        states=music_track.default_states(layers, ["main"]),
    )
    target = project / "music" / f"{name}.tres"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(music_track.to_tres(track), encoding="utf-8")
    return f"res://music/{name}.tres"
