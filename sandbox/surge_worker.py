#!/opt/surge-python/bin/python
"""Render Surge XT patches offline, through the real plugin.

    joinery-surge <recipe.py>   run a recipe; it calls render(source, patch, exports)
    joinery-surge --list        print every parameter with its current value

A patch source is JSON: Surge parameters by name, in the plugin's own display
units ("LP 24 dB", a cutoff in Hz), and the notes to play. Renders must be
exactly repeatable, because recipes are replayed and compared byte for byte.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import re
import runpy
import sys
from pathlib import Path
from typing import Any

import numpy as np
from mido import Message
from pedalboard import load_plugin
from pedalboard.io import AudioFile

PLUGIN = "/usr/lib/vst3/Surge XT.vst3"
KEYS = {"synth", "sample_rate", "seconds", "parameters", "notes"}
NOTE_KEYS = {"pitch", "start", "duration", "velocity"}
# Oscillators start at a random phase unless retriggered, which would make
# every render different. Patches may still override these.
DEFAULTS = {f"{scene}_osc_{n}_retrigger": 1.0 for scene in "ab" for n in (1, 2, 3)}


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number")
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return float(value)


def parse(text: str) -> dict[str, Any]:
    patch = json.loads(text)
    if not isinstance(patch, dict) or set(patch) - KEYS:
        raise ValueError("A Surge source has only: " + ", ".join(sorted(KEYS)))
    if patch.get("synth") != "surge-xt":
        raise ValueError('A Surge source starts with "synth": "surge-xt"')
    rate = int(_number(patch.get("sample_rate", 48000), "sample_rate"))
    if rate not in (44100, 48000, 96000):
        raise ValueError("sample_rate is 44100, 48000 or 96000")
    seconds = _number(patch.get("seconds"), "seconds")
    if not 0 < seconds <= 600:
        raise ValueError("seconds is between 0 and 600")
    notes = patch.get("notes", [])
    if not isinstance(notes, list) or not notes:
        raise ValueError("Add at least one note")
    for note in notes:
        if not isinstance(note, dict) or set(note) - NOTE_KEYS or "pitch" not in note:
            raise ValueError("Each note has pitch, start, duration and velocity")
        if not 0 <= int(_number(note["pitch"], "pitch")) <= 127:
            raise ValueError("pitch is a MIDI note from 0 to 127")
        if not 1 <= int(_number(note.get("velocity", 100), "velocity")) <= 127:
            raise ValueError("velocity is from 1 to 127")
        start = _number(note.get("start", 0), "start")
        length = _number(note.get("duration", 0.5), "duration")
        if start < 0 or length <= 0 or start + length > seconds:
            raise ValueError("Each note must start and end inside the render")
    if not isinstance(patch.get("parameters", {}), dict):
        raise TypeError("parameters maps Surge parameter names to values")
    return {**patch, "sample_rate": rate, "seconds": seconds, "notes": notes}


def _plugin(parameters: dict[str, Any]) -> Any:
    plugin = load_plugin(PLUGIN)
    for name, value in DEFAULTS.items():
        plugin.parameters[name].raw_value = value
    unknown = sorted(set(parameters) - set(plugin.parameters))
    if unknown:
        raise ValueError("Unknown Surge parameters: " + ", ".join(unknown[:20]))
    for name, value in parameters.items():
        _set(plugin, name, value)
    return plugin


_LEADING_NUMBER = re.compile(r"^\s*([-+]?(?:\d+\.?\d*|\.\d+))")


def _set(plugin: Any, name: str, value: Any) -> None:
    """Set one parameter in display units; a number snaps to the nearest step."""
    choices = [str(choice) for choice in plugin.parameters[name].valid_values]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        steps = [
            (abs(float(match.group(1)) - float(value)), choice)
            for choice in choices
            if (match := _LEADING_NUMBER.match(choice))
        ]
        if steps and not _numeric_setter(plugin, name):
            nearest = min(steps)[1]
            if min(steps)[0] > _step_size(steps):
                raise ValueError(
                    f"{name}: {value} is outside {choices[0]} to {choices[-1]}"
                )
            value = nearest
    try:
        setattr(plugin, name, value)
    except (ValueError, TypeError):
        examples = choices if len(choices) <= 12 else [*choices[:3], "…", *choices[-3:]]
        raise ValueError(
            f"{name}: {value!r} is not accepted; use one of: " + ", ".join(examples)
        ) from None


def _numeric_setter(plugin: Any, name: str) -> bool:
    """True when the plugin takes plain numbers for this parameter (Hz, dB)."""
    return isinstance(getattr(plugin, name), (int, float))


def _step_size(steps: list[tuple[float, str]]) -> float:
    numbers = sorted(
        {float(m.group(1)) for _, c in steps if (m := _LEADING_NUMBER.match(c))}
    )
    gaps = [b - a for a, b in itertools.pairwise(numbers) if b > a]
    return max(gaps) if gaps else 0.0


def _render(patch: dict[str, Any]) -> np.ndarray:
    plugin = _plugin(patch.get("parameters", {}))
    events = []
    for note in patch["notes"]:
        pitch = int(note["pitch"])
        start = float(note.get("start", 0))
        velocity = int(note.get("velocity", 100))
        events.append(Message("note_on", note=pitch, velocity=velocity, time=start))
        end = start + float(note.get("duration", 0.5))
        events.append(Message("note_off", note=pitch, time=end))
    audio = plugin(
        sorted(events, key=lambda message: message.time),
        duration=patch["seconds"],
        sample_rate=patch["sample_rate"],
        num_channels=2,
        buffer_size=512,
        reset=True,
    )
    # Quantise once, so the exported file and the repeatability check agree.
    return np.clip(np.round(audio * 32767), -32768, 32767).astype("<i2")


def render(source: str, text: str, exports: list[str]) -> None:
    patch = parse(text)
    Path(source).parent.mkdir(parents=True, exist_ok=True)
    Path(source).write_text(text)
    first = _render(patch)
    second = _render(patch)
    if (
        hashlib.sha256(first.tobytes()).digest()
        != hashlib.sha256(second.tobytes()).digest()
    ):
        raise RuntimeError(
            "This patch renders differently each time (a random source such as "
            "noise, random LFOs or free-running oscillators), so its recipe could "
            "not be replayed exactly. Remove the randomness or retrigger it."
        )
    if not np.any(first):
        raise RuntimeError("This patch renders silence.")
    for export in exports:
        if Path(export).suffix.lower() != ".wav":
            raise ValueError("Surge renders export WAV files")
        Path(export).parent.mkdir(parents=True, exist_ok=True)
        with AudioFile(export, "w", patch["sample_rate"], 2, bit_depth=16) as output:
            output.write(first.astype(np.float32) / 32768)


def list_parameters() -> None:
    plugin = load_plugin(PLUGIN)
    print(
        json.dumps(
            {name: str(getattr(plugin, name)) for name in plugin.parameters},
            indent=0,
        )
    )


if __name__ == "__main__":
    if sys.argv[1:] == ["--list"]:
        list_parameters()
    elif len(sys.argv) == 2:
        runpy.run_path(
            sys.argv[1], init_globals={"render": render}, run_name="__main__"
        )
    else:
        sys.exit("usage: joinery-surge <recipe.py> | --list")
