"""The description of an adaptive music track: layers, sections, intensity states.

A track is a set of stems that play in sync (one per layer and section), and a
small table that says how loud each layer is in each intensity state. Joinery
writes it as a plain Godot resource, so the game reads it without any Joinery
code and stock Godot opens it:

    [gd_resource type="Resource" format=3]

    [resource]
    resource_name = "theme"
    metadata/joinery_music = {...}

`compose_track` writes it next to the stems; the Mixer changes layer levels,
mutes, solos and state levels in it as a reviewed change; the game core's
`Music` autoload plays it (core/audio/music.gd).
"""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: Intensity states, quietest first; the game core and the Mixer use these names.
STATES = ("calm", "explore", "danger", "combat", "victory")
#: Layer roles the composer knows.
ROLES = ("pads", "bass", "percussion", "melody", "tension")
#: Events a track may have a stinger for.
STINGERS = ("victory", "checkpoint", "fail")
META_KEY = "joinery_music"
_META_LINE = re.compile(r"^metadata/" + META_KEY + r"\s*=\s*", re.MULTILINE)

#: How loud each role is in each state (0-1, linear), before the layer's own fader.
DEFAULT_LEVELS: dict[str, dict[str, float]] = {
    "calm": {
        "pads": 1.0,
        "bass": 0.0,
        "percussion": 0.0,
        "melody": 0.5,
        "tension": 0.0,
    },
    "explore": {
        "pads": 1.0,
        "bass": 1.0,
        "percussion": 0.5,
        "melody": 1.0,
        "tension": 0.0,
    },
    "danger": {
        "pads": 0.8,
        "bass": 1.0,
        "percussion": 0.8,
        "melody": 0.4,
        "tension": 1.0,
    },
    "combat": {
        "pads": 0.7,
        "bass": 1.0,
        "percussion": 1.0,
        "melody": 0.8,
        "tension": 1.0,
    },
    "victory": {
        "pads": 1.0,
        "bass": 1.0,
        "percussion": 0.6,
        "melody": 1.0,
        "tension": 0.0,
    },
}


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Layer(_Model):
    name: str = Field(min_length=1, max_length=40, pattern=r"^[a-z0-9_]+$")
    role: Literal["pads", "bass", "percussion", "melody", "tension"]
    volume_db: float = Field(default=0.0, ge=-80.0, le=12.0)
    muted: bool = False
    solo: bool = False


class Section(_Model):
    name: str = Field(min_length=1, max_length=40, pattern=r"^[a-z0-9_]+$")
    bars: int = Field(ge=1, le=256)
    #: Layer name -> res:// path of its stem.
    stems: dict[str, str]


class State(_Model):
    section: str
    #: Layer name -> level 0-1 (linear). A layer left out is silent.
    layers: dict[str, float]

    @model_validator(mode="after")
    def in_range(self) -> State:
        for name, level in self.layers.items():
            if not 0.0 <= level <= 1.0:
                raise ValueError(f"{name}: a state's layer level is 0-1, not {level}")
        return self


class MusicTrack(_Model):
    version: Literal[1] = 1
    name: str = Field(min_length=1, max_length=60, pattern=r"^[a-z0-9_]+$")
    mood: str = ""
    key: str = ""
    tempo_bpm: float = Field(ge=20, le=300)
    beats_per_bar: int = Field(default=4, ge=1, le=16)
    layers: list[Layer] = Field(min_length=1, max_length=8)
    sections: list[Section] = Field(min_length=1, max_length=8)
    states: dict[str, State]
    default_state: str = "explore"
    change_on: Literal["bar", "beat"] = "bar"
    fade_beats: float = Field(default=2.0, ge=0.0, le=32.0)
    stingers: dict[str, str] = {}
    bus: str = "Music"

    @model_validator(mode="after")
    def consistent(self) -> MusicTrack:
        layers = [layer.name for layer in self.layers]
        if len(set(layers)) != len(layers):
            raise ValueError("layer names must be unique")
        sections = [section.name for section in self.sections]
        if len(set(sections)) != len(sections):
            raise ValueError("section names must be unique")
        for section in self.sections:
            if set(section.stems) != set(layers):
                raise ValueError(
                    f"section {section.name} needs exactly one stem per layer "
                    f"({', '.join(layers)})"
                )
        unknown = set(self.states) - set(STATES)
        if unknown:
            raise ValueError("unknown states: " + ", ".join(sorted(unknown)))
        for name, state in self.states.items():
            if state.section not in sections:
                raise ValueError(f"state {name} plays a section that doesn't exist")
            extra = set(state.layers) - set(layers)
            if extra:
                raise ValueError(f"state {name} names unknown layers: {sorted(extra)}")
        if self.default_state not in self.states:
            raise ValueError("default_state must be one of the track's states")
        unknown = set(self.stingers) - set(STINGERS)
        if unknown:
            raise ValueError("unknown stingers: " + ", ".join(sorted(unknown)))
        return self

    @property
    def bar_seconds(self) -> float:
        return self.beats_per_bar * 60.0 / self.tempo_bpm

    def section(self, name: str) -> Section:
        for section in self.sections:
            if section.name == name:
                return section
        raise ValueError(f"no section {name}")

    def gains(self, state: str) -> dict[str, float]:
        """Each layer's linear gain in ``state``, as the game plays it."""
        chosen = self.states[state]
        soloed = any(layer.solo for layer in self.layers)
        out: dict[str, float] = {}
        for layer in self.layers:
            level = chosen.layers.get(layer.name, 0.0)
            if layer.muted or (soloed and not layer.solo):
                level = 0.0
            out[layer.name] = level * 10.0 ** (layer.volume_db / 20.0)
        return out


def default_states(layers: list[Layer], sections: list[str]) -> dict[str, State]:
    """Every state with the default level of each layer's role.

    With a second section, combat plays it (a horizontal switch); everything
    else plays the first.
    """
    out: dict[str, State] = {}
    for state in STATES:
        section = (
            sections[1] if state == "combat" and len(sections) > 1 else sections[0]
        )
        out[state] = State(
            section=section,
            layers={layer.name: DEFAULT_LEVELS[state][layer.role] for layer in layers},
        )
    return out


def to_tres(track: MusicTrack) -> str:
    """The track as a Godot text resource (deterministic text)."""
    data = json.dumps(track.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
    return (
        '[gd_resource type="Resource" format=3]\n\n'
        "[resource]\n"
        f"resource_name = {json.dumps(track.name)}\n"
        f"metadata/{META_KEY} = {data}\n"
    )


def _value_text(text: str, start: int) -> str:
    """The balanced {...} value starting at ``start`` (Godot may spread it over lines)."""
    depth = 0
    quoted = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == '"':
            quoted = True
        elif char in "{[":
            depth += 1
        elif char in "}]":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    raise ValueError("the track description is cut short")


def from_tres(text: str) -> MusicTrack:
    """Read a track description, also after Godot saved it again."""
    found = _META_LINE.search(text)
    if found is None:
        raise ValueError(f"not a music track description (no metadata/{META_KEY})")
    raw = _value_text(text, found.end())
    # Godot writes StringNames as &"name"; JSON has no such thing.
    raw = re.sub(r'&(")', r"\1", raw)
    return MusicTrack.model_validate(json.loads(raw))


# --- Stems in the project --------------------------------------------------------


def section_mixes(root: Path) -> dict[str, list[str]]:
    """Every section of every track in ``root``: "music/<name>.tres (<section> mix)" -> its stems.

    A stem is quieter than the music target on its own; what the player hears
    is the section's mix, so loudness is judged on the mix, not the stem.
    """
    out: dict[str, list[str]] = {}
    for path in sorted((root / "music").glob("*.tres")):
        try:
            track = from_tres(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for section in track.sections:
            stems = [
                stem.removeprefix("res://") for _, stem in sorted(section.stems.items())
            ]
            where = path.relative_to(root).as_posix()
            out[f"{where} ({section.name} mix)"] = stems
    return out


def mix_measure_script(mixes: dict[str, list[str]]) -> str:
    """A shell script printing the EBU R128 loudness of each mix, like the file check."""
    lines = []
    for label, stems in mixes.items():
        inputs = " ".join("-i " + shlex.quote(stem) for stem in stems)
        graph = (
            f"amix=inputs={len(stems)}:normalize=0:duration=longest,ebur128=peak=true"
        )
        lines.append(
            f"echo {shlex.quote('JOINERY_FILE:' + label)}; "
            f"ffmpeg -nostats -hide_banner {inputs} -filter_complex {shlex.quote(graph)} "
            "-f null - 2>&1 | sed -n '/Summary:/,$p'"
        )
    return "\n".join(lines) + "\n"
