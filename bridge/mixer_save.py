"""The Mixer's Save mix as a reviewed change.

The Mixer tab plays every change live in the editor; nothing is written until
Save mix. Then it sends ``mixer_save``::

    {"kind": "mixer_save",
     "bus_layout": {"path": "res://default_bus_layout.tres",
                    "buses": [{"name": "Music", "volume_db": -3.0, "mute": false,
                               "solo": false, "bypass_fx": false, "send": "Master",
                               "reverb": {"enabled": true, "wet": 0.25},
                               "compressor": {"enabled": true, "threshold": -24.0}}],
                    "ducking": "Voice"},
     "tracks": {"res://music/theme.tres":
                {"layers": {"pads": {"volume_db": -2.0, "muted": false, "solo": false}}}}}

Either part may be left out. The host reads the files as they are now, applies
the values (joinery.mixer, joinery.music_track; whatever the Mixer doesn't
manage stays) and starts a scripted task of the crew that writes them through
Godot's ``write_file``: the change goes through the checks, the reviewer and
the Review tab like any other, and nothing is written before the user accepts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from bridge.crew import Crew, ScriptedCall, Task
from bridge.protocol import Prompt
from joinery import mixer, music_track

#: Folder of the music track descriptions (stems live under audio/music/).
TRACKS = "music"


@dataclass
class SavePlan:
    title: str
    request: str
    calls: list[ScriptedCall] = field(default_factory=list)
    targets: list[str] = field(default_factory=list)
    facts: list[str] = field(default_factory=list)


def _relative(path: str, *, folder: str = "") -> str:
    """A project-relative .tres path; refuses anything outside the project or folder."""
    text = str(path).replace("\\", "/").removeprefix("res://").strip("/")
    parts = PurePosixPath(text).parts
    if not text or ".." in parts or text.startswith("/") or ":" in parts[0]:
        raise ValueError(f"Not a project file: {path}")
    if not text.endswith(".tres"):
        raise ValueError(f"Not a Godot text resource: {path}")
    if folder and (len(parts) < 2 or parts[0] != folder):
        raise ValueError(f"Music tracks live in {folder}/: {path}")
    return text


def _layout(project: Path, data: dict[str, Any]) -> tuple[str, str, list[str]]:
    """(path, new text, words) for the bus layout part of the message."""
    path = _relative(str(data.get("path") or mixer.layout_path(project)))
    file = project / path
    if file.is_file():
        layout = mixer.from_tres(file.read_text(encoding="utf-8"))
    else:
        layout = mixer.BusLayout(buses=[mixer.Bus(name="Master", send="")])
    words = mixer.apply_mix(layout, data)
    return path, mixer.to_tres(layout), words


def _track(project: Path, path: str, data: dict[str, Any]) -> tuple[str, list[str]]:
    """(new text, words) for one track description."""
    file = project / path
    if not file.is_file():
        raise ValueError(f"No music track at {path}.")
    track = music_track.from_tres(file.read_text(encoding="utf-8"))
    edits = data.get("layers") or {}
    if not isinstance(edits, dict):
        raise TypeError("A track's layers are {name: {volume_db, muted, solo}}.")
    known = {layer.name: layer for layer in track.layers}
    unknown = sorted(set(edits) - set(known))
    if unknown:
        raise ValueError(f"{track.name} has no layer {', '.join(unknown)}.")
    words: list[str] = []
    for name, raw in edits.items():
        layer = known[name]
        said: list[str] = []
        if "volume_db" in raw:
            value = round(max(-80.0, min(12.0, float(raw["volume_db"]))), 1)
            if value != layer.volume_db:
                layer.volume_db = value
                said.append(mixer.db_text(value))
        for flag, on, off in (
            ("muted", "muted", "unmuted"),
            ("solo", "solo", "solo off"),
        ):
            if flag in raw and bool(raw[flag]) != getattr(layer, flag):
                setattr(layer, flag, bool(raw[flag]))
                said.append(on if raw[flag] else off)
        if said:
            words.append(f"{name} " + ", ".join(said))
    track = music_track.MusicTrack.model_validate(track.model_dump())
    text = music_track.to_tres(track)
    return text, [f"{track.name}: " + "; ".join(words)] if words else []


def plan(project: Path, data: dict[str, Any]) -> SavePlan:
    """The writes and words of a ``mixer_save`` message. Files that would not
    change are left out; nothing to write is an error the user reads."""
    result = SavePlan("Save the mix", "")
    bus_words: list[str] = []
    track_words: list[str] = []
    writes: list[tuple[str, str, str]] = []
    layout = data.get("bus_layout")
    if isinstance(layout, dict):
        path, text, bus_words = _layout(project, layout)
        writes.append((path, text, "Write the bus layout in Godot"))
    tracks = data.get("tracks") or {}
    if not isinstance(tracks, dict):
        raise TypeError("Tracks are {path: {layers}}.")
    for raw_path, edits in sorted(tracks.items()):
        path = _relative(str(raw_path), folder=TRACKS)
        text, words = _track(project, path, edits if isinstance(edits, dict) else {})
        track_words += words
        writes.append((path, text, f"Write the {Path(path).stem} track's mix in Godot"))
    for path, text, label in writes:
        file = project / path
        if file.is_file() and file.read_text(encoding="utf-8") == text:
            continue
        result.calls.append(
            ScriptedCall(
                "godot",
                "write_file",
                {"projectPath": str(project), "filePath": path, "content": text},
                label,
            )
        )
        result.targets.append(path)
    if not result.calls:
        raise ValueError("Nothing to save: the mix already matches the project.")
    result.facts = (bus_words + track_words)[:4]
    what = []
    if any(not t.startswith(TRACKS + "/") for t in result.targets):
        what.append("the bus levels and effects")
    if any(t.startswith(TRACKS + "/") for t in result.targets):
        what.append("the music layer mix")
    result.request = (
        "Save the Mixer's "
        + " and ".join(what or ["mix"])
        + ", as set and heard in the editor."
    )
    return result


async def start(
    crew: Crew, project: Path, data: dict[str, Any], prompt: Prompt
) -> Task:
    """Start the save as a scripted task of the crew; its change waits in Review."""
    planned = plan(project, data)
    return await crew.start_scripted(
        planned.title,
        planned.request,
        planned.calls,
        prompt=prompt,
        targets=planned.targets,
        facts=planned.facts,
    )
