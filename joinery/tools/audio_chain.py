"""Typed audio edits (trim, fade, gain, loudness, loop points, format) run through ffmpeg.

The steps are validated here, on the host, and then written into a recipe as a
small stdlib-only Python program (`WORKER_LIB` plus one call). The program runs
offline in the creative worker, so a replay applies the same ffmpeg filters to
the same samples and produces the same bytes:

* every encode uses ffmpeg's bit-exact flags and drops metadata, so neither a
  version string nor a random Ogg stream serial gets into the file;
* every number written into a filter is formatted with fixed decimals;
* loop points are written into the file itself (a WAV `smpl` chunk, which
  Godot reads as "Detect From WAV", and `LOOPSTART`/`LOOPLENGTH` comments in an
  Ogg Vorbis file), and Godot's Ogg import settings are written beside it.

Loudness targets come from the project's art bible (`[audio] lufs` and
`true_peak_max_dbtp`); `resolve_targets` fills them in before the program is
written, so the recipe records the numbers it used.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

#: Formats an audio edit can write.
AUDIO_SUFFIXES = frozenset({".wav", ".ogg"})
#: True-peak ceiling when the art bible sets none (the template's value).
DEFAULT_TRUE_PEAK = -1.0
#: Fade curves ffmpeg's afade accepts that make sense for game audio.
CURVES = ("tri", "qsin", "hsin", "esin", "log", "ipar", "qua", "cub", "squ", "exp")
#: Printed by the worker program with a JSON report of what it measured.
REPORT_MARKER = "JOINERY_AUDIO "


class _Step(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Trim(_Step):
    """Keep `start` to `end` seconds (end defaults to the end of the file)."""

    op: Literal["trim"]
    start: float = Field(default=0.0, ge=0, le=3600)
    end: float | None = Field(default=None, gt=0, le=3600)

    @model_validator(mode="after")
    def ordered(self) -> Trim:
        if self.end is not None and self.end <= self.start:
            raise ValueError("trim end must come after its start")
        return self


class TrimSilence(_Step):
    """Remove leading and trailing audio quieter than `threshold_db` (peak)."""

    op: Literal["trim_silence"]
    threshold_db: float = Field(default=-60.0, ge=-120, le=-20)
    #: Seconds of the quiet tail kept after the last sound, so a decay is not cut.
    keep_seconds: float = Field(default=0.0, ge=0, le=10)


class Fade(_Step):
    op: Literal["fade"]
    in_seconds: float = Field(default=0.0, ge=0, le=600)
    out_seconds: float = Field(default=0.0, ge=0, le=600)
    curve: Literal[
        "tri", "qsin", "hsin", "esin", "log", "ipar", "qua", "cub", "squ", "exp"
    ] = "tri"

    @model_validator(mode="after")
    def something(self) -> Fade:
        if self.in_seconds == 0 and self.out_seconds == 0:
            raise ValueError("a fade needs in_seconds or out_seconds")
        return self


class Gain(_Step):
    op: Literal["gain"]
    db: float = Field(ge=-60, le=40)


class Normalise(_Step):
    """Integrated loudness (EBU R128) to `lufs`, true peak kept under `true_peak`.

    Both default to the art bible's values for the export's folder.
    """

    op: Literal["normalise"]
    lufs: float | None = Field(default=None, ge=-60, le=-5)
    true_peak: float | None = Field(default=None, ge=-20, le=0)


class Loop(_Step):
    """Loop from `start` to `end` seconds; the file is cut at `end`.

    `crossfade` blends the audio after `end` into the start of the loop, and
    `fold_tail` adds everything after `end` (a release tail) into the start, so
    the jump from end to start is seamless. Both need audio after `end`.
    """

    op: Literal["loop"]
    start: float = Field(default=0.0, ge=0, le=3600)
    end: float | None = Field(default=None, gt=0, le=3600)
    crossfade: float = Field(default=0.0, ge=0, le=10)
    fold_tail: bool = False

    @model_validator(mode="after")
    def ordered(self) -> Loop:
        if self.end is not None and self.end <= self.start:
            raise ValueError("loop end must come after its start")
        if self.crossfade and self.fold_tail:
            raise ValueError("use crossfade or fold_tail, not both")
        if (self.crossfade or self.fold_tail) and self.end is None:
            raise ValueError(
                "crossfade and fold_tail need a loop end before the end of the file"
            )
        return self


Step = Annotated[
    Trim | TrimSilence | Fade | Gain | Normalise | Loop, Field(discriminator="op")
]
_STEPS: TypeAdapter[list[Step]] = TypeAdapter(list[Step])


class Output(BaseModel):
    """How the result is written; the format comes from the export's suffix."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sample_rate: Literal[22050, 32000, 44100, 48000] | None = None
    channels: Literal[1, 2] | None = None
    #: Ogg Vorbis quality, -1 to 10 (ffmpeg -q:a).
    quality: float = Field(default=6.0, ge=-1, le=10)
    #: WAV bit depth.
    bit_depth: Literal[16, 24] = 16


def parse_steps(raw: list[dict[str, Any]]) -> list[Step]:
    """Validate a list of steps; loop points must survive what follows them."""
    if not isinstance(raw, list) or not raw:
        raise ValueError("Give at least one audio step.")
    steps = _STEPS.validate_python(raw)
    if len(steps) > 32:
        raise ValueError("Use at most 32 steps in one edit.")
    looped = False
    for step in steps:
        if looped and not isinstance(step, (Gain, Normalise)):
            raise ValueError(
                "Only gain and normalise may follow a loop step; "
                "anything else would move the loop points."
            )
        if isinstance(step, Loop):
            if looped:
                raise ValueError("Set loop points once.")
            looped = True
    return steps


def folder_value[T](values: dict[str, T], path: str) -> T | None:
    """The value for the most specific folder containing `path`."""
    best: tuple[int, T] | None = None
    for folder, value in values.items():
        prefix = folder.strip("/")
        if (path == prefix or path.startswith(prefix + "/")) and (
            best is None or len(prefix) > best[0]
        ):
            best = (len(prefix), value)
    return best[1] if best else None


def resolve_targets(
    steps: list[Step],
    export: str,
    lufs: dict[str, float],
    true_peak: float | None,
    *,
    default_lufs: float | None = None,
) -> list[Step]:
    """Fill each normalise step's loudness and ceiling from the art bible."""
    resolved: list[Step] = []
    for step in steps:
        if isinstance(step, Normalise):
            target = step.lufs
            if target is None:
                target = folder_value(lufs, export)
            if target is None:
                target = default_lufs
            if target is None:
                raise ValueError(
                    f"The art bible sets no loudness for {Path(export).parent.as_posix()}; "
                    "give normalise a lufs target (music is usually -16, ambience -22)."
                )
            ceiling = step.true_peak
            if ceiling is None:
                ceiling = true_peak if true_peak is not None else DEFAULT_TRUE_PEAK
            step = step.model_copy(update={"lufs": target, "true_peak": ceiling})
        resolved.append(step)
    return resolved


def check_paths(source: str, export: str) -> None:
    for path in (source, export):
        parts = Path(path).parts
        if (
            not parts
            or parts[0] != "audio"
            or Path(path).suffix.lower() not in AUDIO_SUFFIXES
        ):
            raise ValueError(
                "Audio edits read and write .wav or .ogg files under audio/."
            )


def program(
    source: str, export: str, steps: list[Step], output: Output | None = None
) -> str:
    """A self-contained program that applies `steps` to `source` and writes `export`."""
    check_paths(source, export)
    output = output or Output()
    payload = {
        "source": source,
        "export": export,
        "steps": [step.model_dump() for step in steps],
        "output": output.model_dump(),
    }
    return (
        WORKER_LIB
        + "\nif __name__ == '__main__':\n    run_chain("
        + repr(json.dumps(payload, sort_keys=True))
        + ")\n"
    )


def parse_report(stdout: str) -> dict[str, Any]:
    """The worker's last JSON report, or an empty dict."""
    report: dict[str, Any] = {}
    for line in stdout.splitlines():
        if line.startswith(REPORT_MARKER):
            try:
                found = json.loads(line.removeprefix(REPORT_MARKER))
            except json.JSONDecodeError:
                continue
            if isinstance(found, dict):
                report = found
    return report


def godot_import(
    text: str | None,
    resource: str,
    *,
    loop: bool,
    loop_offset: float = 0.0,
    bpm: float | None = None,
    beat_count: int | None = None,
    bar_beats: int | None = None,
) -> str:
    """Godot's Ogg Vorbis import settings with the loop written in.

    An existing `.import` file keeps everything Godot wrote (uid, remap,
    destination files); only the loop parameters, and the tempo ones when
    given, are set. A new one holds just enough for Godot to import the file
    with these settings.
    """
    values = {
        "loop": "true" if loop else "false",
        "loop_offset": _godot_float(loop_offset),
    }
    if bpm is not None:
        values["bpm"] = _godot_float(bpm)
    if beat_count is not None:
        values["beat_count"] = str(int(beat_count))
    if bar_beats is not None:
        values["bar_beats"] = str(max(2, int(bar_beats)))
    if not text:
        text = (
            '[remap]\n\nimporter="oggvorbisstr"\ntype="AudioStreamOggVorbis"\n\n'
            f'[deps]\n\nsource_file="res://{resource}"\n\n[params]\n\n'
        )
    lines = text.splitlines()
    try:
        start = lines.index("[params]")
    except ValueError:
        lines += ["", "[params]", ""]
        start = len(lines) - 2
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith("[")),
        len(lines),
    )
    seen: set[str] = set()
    for index in range(start + 1, end):
        key = lines[index].split("=", 1)[0].strip()
        if key in values:
            lines[index] = f"{key}={values[key]}"
            seen.add(key)
    insert = [f"{key}={value}" for key, value in values.items() if key not in seen]
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    if end == start + 1:
        insert = ["", *insert]
    lines[end:end] = insert
    return "\n".join(lines).rstrip("\n") + "\n"


#: Where a SuperCollider chain writes the edit program inside the worker.
SC_CHAIN_PROGRAM = "/tmp/joinery-audio-chain.py"


def sc_string(text: str) -> str:
    """A SuperCollider string literal: only backslash and quote are escaped.

    sclang reads newlines and tabs in a literal as themselves, but not JSON's
    \\uXXXX escapes, so the text is kept raw otherwise.
    """
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


#: sclang's lexer cuts a string literal at 8 KB, so long text is split.
SC_CHUNK = 2000


def sc_text(text: str) -> str:
    """A SuperCollider expression for any length of text."""
    if len(text) <= SC_CHUNK:
        return sc_string(text)
    pieces = [text[i : i + SC_CHUNK] for i in range(0, len(text), SC_CHUNK)]
    return "[\n" + ",\n".join(sc_string(piece) for piece in pieces) + "\n].join"


def sc_unquote(expression: str) -> str:
    """The text of an expression `sc_text` wrote (its literals, joined)."""
    out: list[str] = []
    inside = escaped = False
    for char in expression:
        if not inside:
            if char == '"':
                inside = True
            continue
        if escaped:
            out.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            inside = False
        else:
            out.append(char)
    if inside:
        raise ValueError("unterminated SuperCollider string")
    return "".join(out)


def supercollider_chain(previous: str, post: str, audio_input: str) -> str:
    """Replay a SuperCollider recipe script, then run the edit program on its output.

    An NRT patch exits from its completion action, so the edit runs as a
    ShutDown action: after the render and before sclang exits. The rendered
    file is removed first, so the edit only ever processes a fresh render. The
    edit cannot change sclang's exit status, so it prints JOINERY_POST_OK or
    JOINERY_POST_FAILED, and on failure its output file is simply not written.

    Edits nest: a second edit wraps the script of the first. Each level writes
    its program to its own file (named by its content), and puts its action at
    the front of ShutDown's list. The inner level registers later, while the
    outer one interprets it, so it ends up first and its edit runs first: the
    outer edit always reads the inner edit's result.
    """
    digest = hashlib.sha256(post.encode()).hexdigest()[:16]
    program = SC_CHAIN_PROGRAM.removesuffix(".py") + f"-{digest}.py"
    # systemCmd does not pass the child's output on, so read it and post it.
    command = f"python3 {program} 2>&1; echo JOINERY_EXIT=$?"
    return (
        "(\n"
        "var joineryEdit;\n"
        f"File.delete({sc_string(audio_input)});\n"
        f'File.use({sc_string(program)}, "w", {{ |file| file.write({sc_text(post)}) }});\n'
        "joineryEdit = {\n"
        f"    var out = {sc_string(command)}.unixCmdGetStdOut;\n"
        "    out.post;\n"
        '    if(out.contains("JOINERY_EXIT=0")) {\n'
        '        "JOINERY_POST_OK".postln;\n'
        "    } {\n"
        '        "JOINERY_POST_FAILED".postln;\n'
        "    };\n"
        "};\n"
        "ShutDown.add(joineryEdit);\n"
        "ShutDown.objects.remove(joineryEdit);\n"
        "ShutDown.objects.addFirst(joineryEdit);\n"
        f"thisProcess.interpreter.interpret({sc_text(previous)});\n"
        ")\n"
    )


def _godot_float(value: float) -> str:
    text = repr(float(value))
    return text if "." in text or "e" in text else text + ".0"


# The program below runs in the worker with the system Python or Surge's
# Python. It uses only the standard library and the ffmpeg and ffprobe programs,
# so the same text works under SuperCollider's chain and Surge's recipes.
WORKER_LIB = r'''
# --- Joinery audio chain: stdlib and ffmpeg only, bit-exact output ---
import array as _array
import json as _json
import os as _os
import re as _re
import struct as _struct
import subprocess as _subprocess
import sys as _sys

_FFMPEG = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]
_EXACT = ["-fflags", "+bitexact", "-flags:a", "+bitexact", "-map_metadata", "-1"]


class Audio:
    """Interleaved 32-bit float samples."""

    def __init__(self, samples, rate, channels):
        self.samples = samples
        self.rate = int(rate)
        self.channels = int(channels)

    @property
    def frames(self):
        return len(self.samples) // self.channels

    @property
    def seconds(self):
        return self.frames / self.rate


def _run(argv, data=None):
    got = _subprocess.run(argv, input=data, capture_output=True)
    if got.returncode != 0:
        detail = got.stderr.decode(errors="replace").strip()[-600:]
        raise RuntimeError(argv[0] + " failed: " + (detail or "exit %d" % got.returncode))
    return got.stdout, got.stderr


def _raw(audio):
    return ["-f", "f32le", "-ar", str(audio.rate), "-ac", str(audio.channels)]


def _floats(data):
    samples = _array.array("f")
    samples.frombytes(data)
    if _sys.byteorder != "little":
        samples.byteswap()
    return samples


def _bytes(samples):
    if _sys.byteorder == "little":
        return samples.tobytes()
    copy = _array.array("f", samples)
    copy.byteswap()
    return copy.tobytes()


def load(path, rate=None, channels=None):
    """Decode any audio file to float samples (optionally resampled)."""
    if not _os.path.isfile(path):
        raise RuntimeError(path + " was not produced, so there is nothing to edit")
    out, _ = _run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
         "stream=sample_rate,channels", "-of", "json", path]
    )
    streams = _json.loads(out or b"{}").get("streams") or []
    if not streams:
        raise RuntimeError(path + " has no audio stream")
    rate = int(rate or streams[0]["sample_rate"])
    channels = int(channels or streams[0]["channels"])
    out, _ = _run(
        _FFMPEG + ["-i", path, "-f", "f32le", "-acodec", "pcm_f32le", "-ar", str(rate),
                   "-ac", str(channels), "pipe:1"]
    )
    return Audio(_floats(out), rate, channels)


def apply_filter(audio, graph):
    out, _ = _run(
        _FFMPEG + _raw(audio) + ["-i", "pipe:0", "-af", graph, "-f", "f32le",
                                 "-acodec", "pcm_f32le", "-ar", str(audio.rate),
                                 "-ac", str(audio.channels), "pipe:1"],
        _bytes(audio.samples),
    )
    return Audio(_floats(out), audio.rate, audio.channels)


def measure(audio):
    """Integrated loudness (LUFS) and true peak (dBTP), EBU R128."""
    got = _subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-nostats"] + _raw(audio)
        + ["-i", "pipe:0", "-af", "ebur128=peak=true", "-f", "null", "-"],
        input=_bytes(audio.samples), capture_output=True,
    )
    text = got.stderr.decode(errors="replace")
    summary = text[text.rfind("Summary:"):] if "Summary:" in text else ""
    lufs = _re.search(r"I:\s+(-?inf|-?[\d.]+)\s+LUFS", summary)
    peak = _re.search(r"True peak:\s+Peak:\s+(-?inf|-?[\d.]+)\s+dBFS", summary)
    if got.returncode != 0 or not lufs or not peak:
        raise RuntimeError("ffmpeg could not measure loudness: " + text.strip()[-400:])
    return float(lufs.group(1)), float(peak.group(1))


def _db(value):
    return "%.4f" % value


def trim(audio, start, end):
    first = int(round(start * audio.rate))
    last = audio.frames if end is None else min(audio.frames, int(round(end * audio.rate)))
    if first >= last:
        raise RuntimeError("the trim keeps no audio (the file is %.3f s long)" % audio.seconds)
    c = audio.channels
    return Audio(audio.samples[first * c:last * c], audio.rate, c)


def trim_silence(audio, threshold_db, keep_seconds):
    limit = 10.0 ** (threshold_db / 20.0)
    samples = audio.samples
    first = next((i for i in range(len(samples)) if abs(samples[i]) > limit), None)
    if first is None:
        raise RuntimeError("the whole file is quieter than %.1f dB" % threshold_db)
    last = next(i for i in range(len(samples) - 1, -1, -1) if abs(samples[i]) > limit)
    c = audio.channels
    start = first // c
    end = min(audio.frames, last // c + 1 + int(round(keep_seconds * audio.rate)))
    return Audio(samples[start * c:end * c], audio.rate, c)


def fade(audio, in_seconds, out_seconds, curve):
    parts = []
    length = audio.seconds
    if in_seconds > length or out_seconds > length:
        raise RuntimeError("a fade is longer than the audio (%.3f s)" % length)
    if in_seconds:
        parts.append("afade=t=in:st=0:d=%s:curve=%s" % (_db(in_seconds), curve))
    if out_seconds:
        parts.append(
            "afade=t=out:st=%s:d=%s:curve=%s"
            % (_db(length - out_seconds), _db(out_seconds), curve)
        )
    return apply_filter(audio, ",".join(parts))


def gain(audio, db):
    return apply_filter(audio, "volume=%sdB:precision=float" % _db(db))


def normalise(audio, lufs, true_peak, report, margin=0.0):
    # A lossy encode can overshoot between samples; aim under the ceiling by
    # `margin` dB so the encoded file still meets it.
    ceiling = true_peak - margin
    if audio.seconds < 0.4:
        raise RuntimeError("loudness needs at least 0.4 s of audio to measure")
    before, peak = measure(audio)
    if before <= -69.9:
        raise RuntimeError("the audio is silent, so it cannot be normalised")

    def level(change):
        graph = "volume=%sdB:precision=float" % _db(change)
        if peak + change <= ceiling:
            return apply_filter(audio, graph), False
        # A lookahead limiter holds sample peaks 1 dB under the ceiling, which
        # keeps the true peak (between samples) under it as well.
        limit = max(0.0625, min(1.0, 10.0 ** ((ceiling - 1.0) / 20.0)))
        graph += (
            ",alimiter=limit=%.6f:attack=5:release=50:level=disabled:latency=enabled"
            % limit
        )
        return apply_filter(audio, graph), True

    applied = lufs - before
    result, limited = level(applied)
    after, after_peak = measure(result)
    if limited and abs(after - lufs) > 0.3:
        # Limiting lowered the loudness; make up the difference once.
        applied += lufs - after
        result, limited = level(applied)
        after, after_peak = measure(result)
    if after_peak > ceiling + 0.05:
        raise RuntimeError(
            "could not keep the true peak under %.1f dBTP (it is %.1f)" % (true_peak, after_peak)
        )
    report.update(
        lufs_before=round(before, 1), true_peak_before=round(peak, 1),
        lufs=round(after, 1), true_peak=round(after_peak, 1),
        gain_db=round(applied, 2), limited=limited, target_lufs=lufs,
        ceiling_dbtp=true_peak,
    )
    return result


def loop_points(audio, start, end, crossfade, fold_tail):
    """Cut at the loop end; blend or fold what followed it into the loop start."""
    c = audio.channels
    first = int(round(start * audio.rate))
    last = audio.frames if end is None else int(round(end * audio.rate))
    if not 0 <= first < last <= audio.frames:
        raise RuntimeError(
            "loop points must lie inside the audio (%.3f s long)" % audio.seconds
        )
    samples = _array.array("f", audio.samples)
    if fold_tail:
        tail = audio.frames - last
        for i in range(min(tail, last - first) * c):
            samples[first * c + i] += samples[last * c + i]
    elif crossfade:
        width = int(round(crossfade * audio.rate))
        if last + width > audio.frames or first + width > last:
            raise RuntimeError(
                "the crossfade needs %.3f s of audio after the loop end and inside the loop"
                % crossfade
            )
        for frame in range(width):
            g = frame / width
            for ch in range(c):
                a = (first + frame) * c + ch
                b = (last + frame) * c + ch
                samples[a] = samples[b] * (1.0 - g) + samples[a] * g
    return Audio(samples[: last * c], audio.rate, c), (first, last)


def smpl_chunk(rate, first, last):
    """A RIFF smpl chunk with one forward loop. Godot treats the end as exclusive."""
    body = _struct.pack(
        "<9I6I", 0, 0, int(round(1e9 / rate)), 60, 0, 0, 0, 1, 0,
        0, 0, first, last, 0, 0,
    )
    return b"smpl" + _struct.pack("<I", len(body)) + body


def add_smpl(path, rate, first, last):
    with open(path, "rb") as handle:
        data = handle.read()
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise RuntimeError(path + " is not a RIFF WAVE file")
    data += smpl_chunk(rate, first, last)
    data = data[:4] + _struct.pack("<I", len(data) - 8) + data[8:]
    with open(path, "wb") as handle:
        handle.write(data)


def encode(audio, path, output, loop=None):
    folder = _os.path.dirname(path)
    if folder:
        _os.makedirs(folder, exist_ok=True)
    suffix = _os.path.splitext(path)[1].lower()
    temporary = path + ".joinery-tmp" + suffix
    argv = _FFMPEG + _raw(audio) + ["-i", "pipe:0"]
    if suffix == ".wav":
        argv += ["-c:a", "pcm_s24le" if output.get("bit_depth") == 24 else "pcm_s16le"]
    elif suffix == ".ogg":
        argv += ["-c:a", "libvorbis", "-q:a", "%.2f" % float(output.get("quality", 6))]
        if loop:
            argv += ["-metadata", "LOOPSTART=%d" % loop[0],
                     "-metadata", "LOOPLENGTH=%d" % (loop[1] - loop[0])]
    else:
        raise RuntimeError("export WAV or Ogg Vorbis, not " + suffix)
    try:
        _run(argv + _EXACT + [temporary], _bytes(audio.samples))
        if suffix == ".wav" and loop:
            add_smpl(temporary, audio.rate, loop[0], loop[1])
        _os.replace(temporary, path)
    finally:
        if _os.path.exists(temporary):
            _os.remove(temporary)


def apply_steps(audio, steps, report, margin=0.0):
    loop = None
    for step in steps:
        op = step["op"]
        entry = {"op": op}
        if op == "trim":
            audio = trim(audio, step["start"], step.get("end"))
        elif op == "trim_silence":
            audio = trim_silence(audio, step["threshold_db"], step["keep_seconds"])
        elif op == "fade":
            audio = fade(audio, step["in_seconds"], step["out_seconds"], step["curve"])
        elif op == "gain":
            audio = gain(audio, step["db"])
        elif op == "normalise":
            audio = normalise(audio, step["lufs"], step["true_peak"], entry, margin)
        elif op == "loop":
            audio, loop = loop_points(
                audio, step["start"], step.get("end"), step["crossfade"], step["fold_tail"]
            )
            entry.update(loop_start_frame=loop[0], loop_end_frame=loop[1])
        else:
            raise RuntimeError("unknown audio step " + repr(op))
        entry["seconds"] = round(audio.seconds, 4)
        report["steps"].append(entry)
    return audio, loop


def finish(audio, export, output, loop, report):
    encode(audio, export, output, loop)
    report.update(
        export=export, seconds=round(audio.seconds, 4), sample_rate=audio.rate,
        channels=audio.channels,
    )
    if loop:
        report["loop"] = {
            "start_frame": loop[0], "end_frame": loop[1],
            "start_seconds": round(loop[0] / audio.rate, 6),
        }
    print("JOINERY_AUDIO " + _json.dumps(report, sort_keys=True), flush=True)


def lossy_margin(path):
    """Headroom (dB) under the true-peak ceiling for a lossy export."""
    return 0.5 if path.lower().endswith(".ogg") else 0.0


def run_chain(payload):
    spec = _json.loads(payload)
    output = spec["output"]
    audio = load(spec["source"], output.get("sample_rate"), output.get("channels"))
    report = {"source": spec["source"], "steps": []}
    audio, loop = apply_steps(audio, spec["steps"], report, lossy_margin(spec["export"]))
    finish(audio, spec["export"], output, loop, report)
# --- end of the Joinery audio chain ---
'''
