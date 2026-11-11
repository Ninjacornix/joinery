"""An adaptive track rendered as synced, loudness-matched, loop-seamless stems.

One recipe renders a whole track in the offline worker under `joinery-surge`:

    each section's .mid (one track per layer)
      -> each layer alone through FluidSynth (General MIDI) or its Surge XT patch
      -> its release tail folded into the start, cut at exactly `bars` (seamless loop)
      -> the section's mix measured (EBU R128); ONE gain for every stem, so the mix
         meets the art bible's loudness and the stems keep their balance
      -> one Ogg Vorbis per stem, every stem of a section the same number of frames
    each stinger's .mid -> FluidSynth -> normalised on its own -> Ogg (no loop)

The recipe carries the track plan on one line, so `edit_track` starts from it,
and everything it writes is deterministic, so `joinery rebuild` replays it byte
for byte.
"""

from __future__ import annotations

import ast
import base64
import json
from pathlib import PurePosixPath
from typing import Any

from pydantic import BaseModel

from joinery.tools import audio_chain, midi
from joinery.tools.compose import TrackPlan

#: The line in a track recipe that carries its plan.
TRACK_LINE = "TRACK_JSON = "


class TrackPaths(BaseModel):
    folder: str
    description: str
    #: Section -> its .mid source.
    sources: dict[str, str]
    #: Section -> layer -> stem.
    stems: dict[str, dict[str, str]]
    #: Event -> (.mid source, .ogg export).
    stingers: dict[str, tuple[str, str]]

    def outputs(self) -> list[str]:
        out = list(self.sources.values())
        for layers in self.stems.values():
            out += list(layers.values())
        for source, export in self.stingers.values():
            out += [source, export]
        return out

    def oggs(self) -> list[str]:
        out = [stem for layers in self.stems.values() for stem in layers.values()]
        return out + [export for _source, export in self.stingers.values()]


def paths(plan: TrackPlan) -> TrackPaths:
    folder = f"audio/music/{plan.name}"
    return TrackPaths(
        folder=folder,
        description=f"music/{plan.name}.tres",
        sources={s: f"{folder}/{s}.mid" for s in plan.sections},
        stems={
            s: {layer.name: f"{folder}/{s}_{layer.name}.ogg" for layer in plan.layers}
            for s in plan.sections
        },
        stingers={
            e: (f"{folder}/stinger_{e}.mid", f"{folder}/stinger_{e}.ogg")
            for e in plan.stingers
        },
    )


def plan_from_script(text: str) -> TrackPlan | None:
    for line in text.splitlines():
        if line.startswith(TRACK_LINE):
            return TrackPlan.model_validate_json(
                ast.literal_eval(line.removeprefix(TRACK_LINE))
            )
    return None


def _layer_payload(
    song: midi.Song, index: int, patches: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """One layer of a section, alone: a General MIDI file or a Surge track."""
    track = song.tracks[index]
    if track.kind == "surge":
        channel = song.channels()[index]
        return {
            "name": track.name,
            "gm_midi": "",
            "surge": {
                "name": track.name,
                "channel": channel,
                "parameters": patches[track.name],
                "volume": track.volume,
                "pan": track.pan,
                "notes": [
                    {
                        "pitch": n.pitch,
                        "start": n.beat * song.seconds_per_beat,
                        "duration": n.beats * song.seconds_per_beat,
                        "velocity": n.velocity,
                    }
                    for n in track.notes
                ],
            },
        }
    alone = song.model_copy(update={"tracks": [track]})
    return {
        "name": track.name,
        "gm_midi": base64.b64encode(midi.write_midi(alone)).decode(),
        "surge": None,
    }


def render_script(
    plan: TrackPlan,
    songs: dict[str, midi.Song],
    stingers: dict[str, midi.Song],
    patches: dict[str, dict[str, Any]],
    *,
    lufs: float,
    true_peak: float,
    output: audio_chain.Output,
) -> str:
    """The recipe that writes the track's .mid sources and renders its stems."""
    where = paths(plan)
    rate = output.sample_rate or 48000
    sections = []
    for name, song in songs.items():
        song = song.model_copy(update={"sample_rate": rate})
        layers = []
        for index, layer in enumerate(plan.layers):
            entry = _layer_payload(song, index, patches)
            entry["export"] = where.stems[name][layer.name]
            layers.append(entry)
        sections.append(
            {
                "name": name,
                "seconds": song.total_seconds,
                "loop_seconds": song.length_seconds,
                "layers": layers,
            }
        )
    stinger_specs = [
        {
            "event": event,
            "gm_midi": base64.b64encode(midi.write_midi(song)).decode(),
            "seconds": song.total_seconds,
            "export": where.stingers[event][1],
        }
        for event, song in stingers.items()
    ]
    sources = {
        where.sources[name]: midi.write_midi(song) for name, song in songs.items()
    }
    for event, song in stingers.items():
        sources[where.stingers[event][0]] = midi.write_midi(song)
    payload = {
        "sources": {
            path: base64.b64encode(data).decode() for path, data in sources.items()
        },
        "sections": sections,
        "stingers": stinger_specs,
        "soundfont": midi.SOUNDFONT,
        "soundfont_sha256": midi.SOUNDFONT_SHA256,
        "fluidsynth_gain": midi.FLUIDSYNTH_GAIN,
        "sample_rate": rate,
        "lufs": lufs,
        "true_peak": true_peak,
        "output": output.model_dump(),
    }
    return (
        "# Joinery adaptive track: layer stems rendered through FluidSynth and Surge XT.\n"
        "# Replayed by joinery-surge; change it with edit_track, not by hand.\n"
        + TRACK_LINE
        + repr(plan.model_dump_json())
        + "\n"
        + audio_chain.WORKER_LIB
        + midi.MUSIC_LIB
        + STEMS_LIB
        + "\nif __name__ == '__main__':\n    render_track("
        + repr(json.dumps(payload, sort_keys=True))
        + ")\n"
    )


def stem_seconds(plan: TrackPlan) -> float:
    return plan.bars * plan.bar_seconds


def is_track_folder(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return len(parts) >= 3 and parts[:2] == ("audio", "music")


# Runs in the worker after WORKER_LIB and MUSIC_LIB, under joinery-surge.
STEMS_LIB = r"""
# --- Joinery adaptive track: synced stems ---


def _layer_frames(layer, spec, seconds, frames):
    import numpy as np

    if layer["surge"] is not None:
        return _surge(layer["surge"], dict(spec, seconds=seconds), frames)
    midi = "/tmp/joinery-layer.mid"
    with open(midi, "wb") as handle:
        handle.write(_base64.b64decode(layer["gm_midi"]))
    rendered = np.frombuffer(_bytes(fluidsynth(midi, spec).samples), dtype="<f4")
    rendered = rendered.reshape(-1, 2).astype(np.float64)
    out = np.zeros((frames, 2))
    count = min(frames, rendered.shape[0])
    out[:count] = rendered[:count]
    return out


def _audio(array, rate):
    return Audio(_floats(array.astype("<f4").tobytes()), rate, 2)


def render_track(payload):
    import numpy as np

    spec = _json.loads(payload)
    for path, data in sorted(spec["sources"].items()):
        folder = _os.path.dirname(path)
        if folder:
            _os.makedirs(folder, exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(_base64.b64decode(data))
    rate = spec["sample_rate"]
    margin = lossy_margin(".ogg")
    ceiling = spec["true_peak"] - margin
    report = {"sections": [], "stingers": []}
    for section in spec["sections"]:
        frames = int(round(section["seconds"] * rate))
        loop = int(round(section["loop_seconds"] * rate))
        stems = []
        for layer in section["layers"]:
            data = _layer_frames(layer, spec, section["seconds"], frames)
            # Fold the release tail into the start: the loop has no seam.
            looped = data[:loop].copy()
            tail = data[loop:]
            looped[: min(len(tail), loop)] += tail[:loop]
            stems.append(looped)
        mix = np.sum(stems, axis=0)
        if not mix.any():
            raise RuntimeError("Section %r renders silence." % section["name"])
        before, peak = measure(_audio(mix, rate))
        gain = spec["lufs"] - before
        limited = peak + gain > ceiling
        if limited:
            # Never squash the stems apart: the whole section is turned down.
            gain = ceiling - peak
        factor = 10.0 ** (gain / 20.0)
        entry = {"name": section["name"], "frames": loop, "gain_db": round(gain, 2),
                 "mix_lufs_before": round(before, 1), "held_by_true_peak": limited,
                 "stems": []}
        for layer, data in zip(section["layers"], stems):
            audio = _audio(data * factor, rate)
            encode(audio, layer["export"], spec["output"], (0, loop))
            stem = {"layer": layer["name"], "export": layer["export"], "frames": audio.frames}
            if data.any():
                stem["lufs"], stem["true_peak"] = [round(v, 1) for v in measure(audio)]
            entry["stems"].append(stem)
        entry["mix_lufs"], entry["mix_true_peak"] = [
            round(v, 1) for v in measure(_audio(mix * factor, rate))
        ]
        report["sections"].append(entry)
    for stinger in spec["stingers"]:
        frames = int(round(stinger["seconds"] * rate))
        layer = {"surge": None, "gm_midi": stinger["gm_midi"]}
        audio = _audio(_layer_frames(layer, spec, stinger["seconds"], frames), rate)
        entry = {"event": stinger["event"], "export": stinger["export"], "steps": []}
        audio = normalise(audio, spec["lufs"], spec["true_peak"], entry, margin)
        audio = fade(audio, 0.0, min(0.25, audio.seconds / 4), "qsin")
        encode(audio, stinger["export"], spec["output"], None)
        entry["seconds"] = round(audio.seconds, 3)
        report["stingers"].append(entry)
    print("JOINERY_AUDIO " + _json.dumps(report, sort_keys=True), flush=True)
# --- end of the Joinery adaptive track ---
"""
