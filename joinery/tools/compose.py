"""Rule-based composition: an adaptive track's layers written as MIDI, from rules.

No model writes these notes. A track plan (mood, key, tempo, bars, layers,
sections, seed) picks a mode and chord progression from the mood's table, voices
the chords with the smallest moves, and writes each layer from its role:

* pads: the chords held for each bar, voice-led;
* bass: the chord roots, as whole notes, root-fifth halves or an eighth-note
  pulse with approach notes, by density;
* percussion: General MIDI kit patterns by density, with a fill in the last bar;
* melody: a seeded one-bar motif, repeated and answered (A A' B A''), its
  strong beats on chord tones;
* tension: a tremolo cluster and a low pulse that leans on the flat second.

A seeded `random.Random` makes every choice, so the same plan gives the same
notes on every machine. Each section is one `midi.Song` with one track per
layer; stingers are short songs of their own.
"""

from __future__ import annotations

import math
import random
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from joinery.music_track import STINGERS
from joinery.tools import midi

Mood = Literal[
    "calm",
    "adventurous",
    "heroic",
    "mysterious",
    "tense",
    "dark",
    "playful",
    "sad",
    "epic",
]
MOODS: tuple[str, ...] = (
    "calm",
    "adventurous",
    "heroic",
    "mysterious",
    "tense",
    "dark",
    "playful",
    "sad",
    "epic",
)

MODES: dict[str, tuple[int, ...]] = {
    "major": (0, 2, 4, 5, 7, 9, 11),
    "minor": (0, 2, 3, 5, 7, 8, 10),
    "dorian": (0, 2, 3, 5, 7, 9, 10),
    "lydian": (0, 2, 4, 6, 7, 9, 11),
    "mixolydian": (0, 2, 4, 5, 7, 9, 10),
    "harmonic minor": (0, 2, 3, 5, 7, 8, 11),
}
_NOTES = {"c": 0, "d": 2, "e": 4, "f": 5, "g": 7, "a": 9, "b": 11}


class _MoodRules(BaseModel):
    mode: str
    #: Chord roots as scale degrees (0-based), one chord per bar; the second
    #: progression is the other section's (combat, a bridge).
    progressions: tuple[tuple[int, ...], tuple[int, ...]]
    density: float
    programs: dict[str, int]
    velocity: int


_GM = {"pads": 89, "bass": 33, "melody": 73, "tension": 44}
MOOD_RULES: dict[str, _MoodRules] = {
    "calm": _MoodRules(
        mode="major",
        progressions=((0, 5, 3, 4), (3, 0, 5, 4)),
        density=0.3,
        programs={**_GM, "pads": 89, "melody": 73},
        velocity=70,
    ),
    "adventurous": _MoodRules(
        mode="major",
        progressions=((0, 4, 5, 3), (5, 3, 0, 4)),
        density=0.6,
        programs={**_GM, "pads": 48, "melody": 71},
        velocity=90,
    ),
    "heroic": _MoodRules(
        mode="major",
        progressions=((0, 3, 4, 0), (5, 3, 4, 4)),
        density=0.7,
        programs={**_GM, "pads": 48, "melody": 56, "bass": 32},
        velocity=100,
    ),
    "mysterious": _MoodRules(
        mode="dorian",
        progressions=((0, 6, 3, 6), (0, 2, 3, 4)),
        density=0.4,
        programs={**_GM, "pads": 91, "melody": 11},
        velocity=75,
    ),
    "tense": _MoodRules(
        mode="harmonic minor",
        progressions=((0, 3, 0, 4), (5, 1, 4, 4)),
        density=0.7,
        programs={**_GM, "pads": 49, "melody": 71, "bass": 38},
        velocity=95,
    ),
    "dark": _MoodRules(
        mode="minor",
        progressions=((0, 5, 6, 4), (0, 3, 5, 4)),
        density=0.5,
        programs={**_GM, "pads": 94, "melody": 42, "bass": 38},
        velocity=85,
    ),
    "playful": _MoodRules(
        mode="major",
        progressions=((0, 3, 4, 0), (1, 4, 0, 5)),
        density=0.7,
        programs={**_GM, "pads": 0, "melody": 72, "bass": 32},
        velocity=90,
    ),
    "sad": _MoodRules(
        mode="minor",
        progressions=((0, 5, 3, 4), (5, 2, 3, 4)),
        density=0.3,
        programs={**_GM, "pads": 49, "melody": 0},
        velocity=70,
    ),
    "epic": _MoodRules(
        mode="minor",
        progressions=((0, 5, 2, 6), (0, 3, 6, 4)),
        density=0.8,
        programs={**_GM, "pads": 48, "melody": 60, "bass": 32},
        velocity=105,
    ),
}


class Key(BaseModel):
    tonic: int = Field(ge=0, le=11)
    mode: str

    @property
    def scale(self) -> tuple[int, ...]:
        return MODES[self.mode]

    def __str__(self) -> str:
        names = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
        return f"{names[self.tonic]} {self.mode}"


def parse_key(text: str, default_mode: str = "major") -> Key:
    """ "D minor", "F# major", "Bb dorian", "c" (the mood's mode)."""
    found = re.fullmatch(r"\s*([A-Ga-g])([#b♯♭]?)\s*(.*?)\s*", text or "")
    if not found:
        raise ValueError(f"{text!r} is not a key such as 'D minor' or 'F# major'")
    tonic = _NOTES[found.group(1).lower()]
    tonic += {"#": 1, "♯": 1, "b": -1, "♭": -1}.get(found.group(2), 0)
    mode = found.group(3).lower() or default_mode
    mode = {
        "m": "minor",
        "min": "minor",
        "maj": "major",
        "aeolian": "minor",
        "ionian": "major",
    }.get(mode, mode)
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}; use one of {', '.join(MODES)}")
    return Key(tonic=tonic % 12, mode=mode)


class LayerPlan(BaseModel):
    """One layer: its role, and how it is played."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=40, pattern=r"^[a-z0-9_]+$")
    role: Literal["pads", "bass", "percussion", "melody", "tension"]
    #: General MIDI program (the mood's choice when left out); ignored for percussion.
    program: int | None = Field(default=None, ge=0, le=127)
    #: A Surge XT source under audio/ instead of General MIDI.
    surge: str | None = None
    #: 0-1; the track's density when left out.
    density: float | None = Field(default=None, ge=0.0, le=1.0)
    volume: int = Field(default=100, ge=0, le=127)


class TrackPlan(BaseModel):
    """Everything compose_track needs; the recipe keeps it, so edits start from it."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=60, pattern=r"^[a-z0-9_]+$")
    mood: Mood = "adventurous"
    key: str = "D minor"
    tempo_bpm: float = Field(default=110.0, ge=40, le=220)
    beats_per_bar: int = Field(default=4, ge=2, le=7)
    #: Bars per section (a multiple of the progression's four chords reads best).
    bars: int = Field(default=8, ge=2, le=64)
    layers: list[LayerPlan] = Field(min_length=1, max_length=8)
    #: One section loops on its own; a second is the horizontal switch (combat).
    sections: list[str] = Field(default=["main"], min_length=1, max_length=4)
    stingers: list[str] = list(STINGERS)
    #: 0-1; the mood decides when left out.
    density: float | None = Field(default=None, ge=0.0, le=1.0)
    #: Same plan and seed, same notes. `variation` changes the melody and fills only.
    seed: int = Field(default=1, ge=0, lt=2**31)
    variation: int = Field(default=0, ge=0, le=999)

    @field_validator("layers", mode="before")
    @classmethod
    def roles_as_layers(cls, value: object) -> object:
        # "layers": ["pads", "bass"] is short for one layer per role.
        if isinstance(value, list):
            return [{"name": v, "role": v} if isinstance(v, str) else v for v in value]
        return value

    @model_validator(mode="after")
    def consistent(self) -> TrackPlan:
        names = [layer.name for layer in self.layers]
        if len(set(names)) != len(names):
            raise ValueError("layer names must be unique")
        if sum(layer.role == "percussion" for layer in self.layers) > 1:
            raise ValueError("use one percussion layer (General MIDI has one drum kit)")
        for section in self.sections:
            if not re.fullmatch(r"[a-z0-9_]+", section):
                raise ValueError(f"section names are lowercase words, not {section!r}")
        if len(set(self.sections)) != len(self.sections):
            raise ValueError("section names must be unique")
        unknown = set(self.stingers) - set(STINGERS)
        if unknown:
            raise ValueError(
                f"stingers are {', '.join(STINGERS)}; not {sorted(unknown)}"
            )
        parse_key(self.key, MOOD_RULES[self.mood].mode)
        return self

    @property
    def parsed_key(self) -> Key:
        return parse_key(self.key, MOOD_RULES[self.mood].mode)

    @property
    def bar_seconds(self) -> float:
        return self.beats_per_bar * 60.0 / self.tempo_bpm


# --- Harmony ----------------------------------------------------------------------


def _pitch(key: Key, degree: int, octave: int) -> int:
    """MIDI pitch of a scale degree (any integer) with C4 = octave 4 = 60."""
    scale = key.scale
    shift, index = divmod(degree, len(scale))
    return 12 * (octave + 1 + shift) + key.tonic + scale[index]


def _chord(key: Key, root: int) -> list[int]:
    """Pitch classes of the diatonic triad on ``root``."""
    return [(_pitch(key, root + step, 4)) % 12 for step in (0, 2, 4)]


def _voice(
    chord: list[int], previous: list[int] | None, low: int, high: int
) -> list[int]:
    """Three pitches of ``chord`` in [low, high], moving least from ``previous``."""
    options: list[list[int]] = []
    for base in range(low, high - 6):
        if base % 12 not in chord:
            continue
        voiced = [base]
        for pc in chord:
            if pc == base % 12:
                continue
            above = voiced[-1] + 1
            while above % 12 != pc:
                above += 1
            voiced.append(above)
        voiced.sort()
        if voiced[-1] <= high:
            options.append(voiced)
    if not options:
        raise ValueError("no voicing fits the pad range")
    if previous is None:
        middle = (low + high) / 2
        return min(options, key=lambda v: (abs(sum(v) / 3 - middle), v))
    return min(
        options,
        key=lambda v: (sum(abs(a - b) for a, b in zip(v, previous, strict=True)), v),
    )


def _progression(plan: TrackPlan, section_index: int) -> list[int]:
    rules = MOOD_RULES[plan.mood]
    chords = rules.progressions[section_index % 2]
    return [chords[bar % len(chords)] for bar in range(plan.bars)]


# --- Layers -----------------------------------------------------------------------

Notes = list[midi.Note]


def _note(pitch: int, beat: float, beats: float, velocity: int) -> midi.Note:
    return midi.Note(
        pitch=pitch, beat=beat, beats=beats, velocity=max(1, min(127, velocity))
    )


def _pads(plan: TrackPlan, roots: list[int], velocity: int) -> Notes:
    key = plan.parsed_key
    notes: Notes = []
    previous: list[int] | None = None
    for bar, root in enumerate(roots):
        voiced = _voice(_chord(key, root), previous, 52, 74)
        previous = voiced
        for pitch in voiced:
            notes.append(
                _note(
                    pitch, bar * plan.beats_per_bar, plan.beats_per_bar, velocity - 20
                )
            )
    return notes


def _bass(
    plan: TrackPlan, roots: list[int], density: float, velocity: int, rng: random.Random
) -> Notes:
    key = plan.parsed_key
    bar_beats = plan.beats_per_bar
    notes: Notes = []
    for bar, root in enumerate(roots):
        start = bar * bar_beats
        low = _pitch(key, root, 2)
        while low > 47:
            low -= 12
        while low < 33:
            low += 12
        fifth = low + 7
        if density < 0.35:
            notes.append(_note(low, start, bar_beats, velocity))
        elif density < 0.7:
            half = bar_beats / 2
            notes.append(_note(low, start, half, velocity))
            notes.append(
                _note(
                    fifth if rng.random() < 0.7 else low + 12,
                    start + half,
                    half,
                    velocity - 8,
                )
            )
        else:
            steps = int(bar_beats * 2)
            for step in range(steps):
                pitch = low + (12 if step % 4 == 3 and rng.random() < 0.5 else 0)
                accent = 10 if step % 2 == 0 else 0
                notes.append(
                    _note(pitch, start + step * 0.5, 0.45, velocity - 12 + accent)
                )
        if bar + 1 < len(roots) and density >= 0.5 and rng.random() < 0.5:
            # An approach note into the next bar's root, on the last half beat.
            target = _pitch(key, roots[bar + 1], 2)
            while target > 47:
                target -= 12
            while target < 33:
                target += 12
            approach = target + (1 if rng.random() < 0.5 else -1)
            last = start + bar_beats - 0.5
            notes = [
                n
                for n in notes
                if not (n.beat >= last - 1e-9 and n.beat < start + bar_beats)
            ]
            notes = [
                n.model_copy(update={"beats": min(n.beats, last - n.beat)})
                if n.beat < last < n.beat + n.beats
                else n
                for n in notes
            ]
            notes.append(_note(approach, last, 0.45, velocity - 15))
    return notes


_KICK, _SNARE, _HAT, _OPEN_HAT, _CRASH, _LOW_TOM, _MID_TOM, _HIGH_TOM, _SHAKER = (
    36,
    38,
    42,
    46,
    49,
    45,
    47,
    50,
    70,
)


def _percussion(
    plan: TrackPlan, density: float, velocity: int, rng: random.Random
) -> Notes:
    bar_beats = plan.beats_per_bar
    notes: Notes = []
    for bar in range(plan.bars):
        start = bar * bar_beats
        last = bar == plan.bars - 1
        hits: list[tuple[int, float, int]] = []
        if density < 0.35:
            hits.append((_KICK, 0.0, velocity - 10))
            hits += [(_SHAKER, float(b), velocity - 35) for b in range(bar_beats)]
        else:
            hits.append((_KICK, 0.0, velocity))
            if bar_beats >= 4:
                hits.append((_KICK, 2.0, velocity - 8))
            hits += [(_SNARE, float(b), velocity - 5) for b in range(1, bar_beats, 2)]
            step = 0.25 if density >= 0.8 else 0.5
            beat = 0.0
            while beat < bar_beats - 1e-9:
                accent = 0 if abs(beat - round(beat)) < 1e-9 else -12
                hits.append((_HAT, beat, velocity - 25 + accent))
                beat += step
            if density >= 0.6 and rng.random() < 0.6:
                hits.append((_KICK, bar_beats - 0.5, velocity - 15))
        if bar == 0 and density >= 0.5:
            hits.append((_CRASH, 0.0, velocity - 10))
        if last and density >= 0.4:
            # A fill: drop the hats in the last two beats and walk down the toms.
            fill = bar_beats - 2
            hits = [h for h in hits if h[1] < fill or h[0] == _KICK]
            toms = [_HIGH_TOM, _MID_TOM, _LOW_TOM, _LOW_TOM]
            for index in range(4):
                hits.append((toms[index], fill + index * 0.5, velocity - 5 + index * 3))
        seen: set[tuple[int, float]] = set()
        for pitch, beat, vel in sorted(hits, key=lambda h: (h[1], h[0])):
            if (pitch, beat) in seen:
                continue
            seen.add((pitch, beat))
            notes.append(_note(pitch, start + beat, 0.25, vel))
    return notes


_CELLS: tuple[tuple[float, ...], ...] = (
    (1.0, 1.0, 2.0),
    (0.5, 0.5, 1.0, 2.0),
    (1.5, 0.5, 2.0),
    (1.0, 0.5, 0.5, 1.0, 1.0),
    (2.0, 1.0, 1.0),
    (0.5, 0.5, 0.5, 0.5, 2.0),
)


def _melody(
    plan: TrackPlan, roots: list[int], density: float, velocity: int, rng: random.Random
) -> Notes:
    key = plan.parsed_key
    bar_beats = plan.beats_per_bar
    # A motif: rhythm cells filling one bar, and scale steps between its notes.
    rhythm: list[float] = []
    budget = float(bar_beats)
    while budget > 1e-9:
        cells = [c for c in _CELLS if sum(c) <= budget + 1e-9]
        if density < 0.4:
            cells = [c for c in cells if len(c) <= 3] or cells
        cell = cells[rng.randrange(len(cells))] if cells else (budget,)
        rhythm += cell
        budget -= sum(cell)
    contour = [rng.choice((-2, -1, -1, 1, 1, 2)) for _ in rhythm]
    answer = [-step for step in contour]
    notes: Notes = []
    degree = 7  # the tonic an octave up, as a scale degree from octave 4
    for bar, root in enumerate(roots):
        phrase = bar % 4
        if phrase == 3 and density < 0.5:
            continue  # breathe at the end of a phrase
        steps = answer if phrase == 2 else contour
        chord_degrees = {(root + d) % 7 for d in (0, 2, 4)}
        beat = float(bar * bar_beats)
        for index, length in enumerate(rhythm):
            strong = (
                abs((beat % bar_beats) - round(beat % bar_beats)) < 1e-9
                and round(beat % bar_beats) % 2 == 0
            )
            if index == 0:
                # Start each bar on the nearest chord tone.
                degree = min(
                    (d for d in range(3, 12) if d % 7 in chord_degrees),
                    key=lambda d: (abs(d - degree), d),
                )
            else:
                degree += steps[index]
                if strong and degree % 7 not in chord_degrees:
                    degree += 1 if (degree + 1) % 7 in chord_degrees else -1
                degree = max(3, min(13, degree))
            if phrase == 1 and index == len(rhythm) - 1:
                degree = min(
                    (d for d in range(3, 12) if d % 7 in chord_degrees),
                    key=lambda d: abs(d - degree),
                )
            pitch = _pitch(key, degree, 4)
            accent = 10 if strong else 0
            notes.append(_note(pitch, beat, length * 0.9, velocity - 10 + accent))
            beat += length
    return notes


def _tension(plan: TrackPlan, roots: list[int], density: float, velocity: int) -> Notes:
    key = plan.parsed_key
    bar_beats = plan.beats_per_bar
    notes: Notes = []
    for bar, root in enumerate(roots):
        start = bar * bar_beats
        if bar % 2 == 0:
            top = _pitch(key, root + 4, 5)
            span = min(2, plan.bars - bar) * bar_beats
            # A cluster of the fifth and a semitone above it: unresolved on purpose.
            notes.append(_note(top, start, span, velocity - 25))
            notes.append(_note(top + 1, start, span, velocity - 30))
        pulse = _pitch(key, 0, 3)
        step = 0.5 if density >= 0.5 else 1.0
        beat = 0.0
        index = 0
        while beat < bar_beats - 1e-9:
            pitch = pulse + (1 if index % 4 == 3 else 0)
            notes.append(
                _note(
                    pitch,
                    start + beat,
                    step * 0.8,
                    velocity - 30 + int(10 * beat / bar_beats),
                )
            )
            beat += step
            index += 1
    return notes


def _instrument(plan: TrackPlan, layer: LayerPlan) -> dict[str, object]:
    if layer.role == "percussion":
        return {"drums": True}
    if layer.surge is not None:
        return {"surge": layer.surge}
    program = layer.program
    if program is None:
        program = MOOD_RULES[plan.mood].programs[layer.role]
    return {"program": program}


def _rng(plan: TrackPlan, section: int, layer: str) -> random.Random:
    # Python's string hash is salted per process; mix the name in by its bytes.
    salt = sum((index + 1) * byte for index, byte in enumerate(layer.encode()))
    return random.Random(
        plan.seed * 1_000_003 + plan.variation * 7919 + section * 104_729 + salt
    )


def compose(plan: TrackPlan) -> dict[str, midi.Song]:
    """Each section's score: one track per layer, all exactly ``plan.bars`` long."""
    rules = MOOD_RULES[plan.mood]
    songs: dict[str, midi.Song] = {}
    for index, section in enumerate(plan.sections):
        roots = _progression(plan, index)
        tracks = []
        for layer in plan.layers:
            base = plan.density if plan.density is not None else rules.density
            density = layer.density if layer.density is not None else base
            if index > 0:
                density = min(1.0, density + 0.2)  # the second section pushes harder
            velocity = rules.velocity
            rng = _rng(plan, index, layer.name)
            notes: Notes
            if layer.role == "pads":
                notes = _pads(plan, roots, velocity)
            elif layer.role == "bass":
                notes = _bass(plan, roots, density, velocity, rng)
            elif layer.role == "percussion":
                notes = _percussion(plan, density, velocity, rng)
            elif layer.role == "melody":
                notes = _melody(plan, roots, density, velocity, rng)
            else:
                notes = _tension(plan, roots, density, velocity)
            tracks.append(
                {
                    "name": layer.name,
                    "volume": layer.volume,
                    "notes": [n.model_dump() for n in notes],
                    **_instrument(plan, layer),
                }
            )
        songs[section] = midi.Song.model_validate(
            {
                "tempo_bpm": plan.tempo_bpm,
                "beats_per_bar": plan.beats_per_bar,
                "bars": plan.bars,
                "tail_beats": float(plan.beats_per_bar),
                "tracks": tracks,
            }
        )
    return songs


def stinger(plan: TrackPlan, event: str) -> midi.Song:
    """A short phrase for ``event``: victory rises, checkpoint chimes, fail falls."""
    key = plan.parsed_key
    tonic = _pitch(key, 0, 4)
    if event == "victory":
        program, steps = 61, [0, 2, 4, 7]
        notes = [
            _note(_pitch(key, d, 4), i * 0.5, 0.5, 100) for i, d in enumerate(steps)
        ]
        notes += [_note(_pitch(key, d, 4), 2.0, 4.0, 105) for d in (0, 2, 4, 7)]
    elif event == "checkpoint":
        program = 14
        notes = [
            _note(_pitch(key, 4, 5), 0.0, 1.0, 95),
            _note(_pitch(key, 7, 5), 1.0, 3.0, 100),
        ]
    elif event == "fail":
        program = 57
        notes = [
            _note(_pitch(key, 4, 4), 0.0, 1.0, 95),
            _note(_pitch(key, 2, 4) - (1 if key.scale[2] == 4 else 0), 1.0, 1.0, 90),
            _note(tonic - 1, 2.0, 2.0, 85),
        ]
    else:
        raise ValueError(f"no stinger for {event!r}")
    end = max(n.beat + n.beats for n in notes)
    return midi.Song.model_validate(
        {
            "tempo_bpm": plan.tempo_bpm,
            "beats_per_bar": plan.beats_per_bar,
            "bars": max(1, math.ceil(end / plan.beats_per_bar - 1e-9)),
            "tail_beats": 2.0,
            "tracks": [
                {
                    "name": f"stinger {event}",
                    "program": program,
                    "notes": [n.model_dump() for n in notes],
                }
            ],
        }
    )
