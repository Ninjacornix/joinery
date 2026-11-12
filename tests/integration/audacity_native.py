"""Audacity through Joinery's sound adapter, in the real Audacity worker.

From a recording the user made (a hum with a noise-only lead-in, written here
as a WAV and imported like any recording in the project), one edit builds an
Audacity project: noise reduction from the lead-in's noise profile, a spectral
delete of a whistle band, labels, and a second take moved and panned. It then
checks what Audacity did to the sound, changes the step list (whole list sent
again, as an agent would after inspect_document), and replays every recipe:
the WAV mixdown, the label file and the .aup3 must come back byte for byte.

It also checks the worker has no speech-model package, that every agent is
refused the upstream MCP's transcription tools, and, when the pinned Audacity
MCP is installed, that it starts beside Audacity and answers a read-only call.

    uv run python tests/integration/audacity_native.py
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import random
import struct
import subprocess
import tempfile
import wave
from pathlib import Path

from joinery.authoring import IMAGE, edit_document, inspect_document
from joinery.hook import mcp_violation
from joinery.lockfile import HumanImport, record_imports
from joinery.mcp_runtime import MCPConnection
from joinery.policy import Severity
from joinery.recipe import rebuild_one, recipe_paths
from joinery.runner import SandboxRunner
from joinery.server_install import installation
from joinery.tools.base import AUDACITY_WORKER

RATE = 44100
SOURCE = "audio/sources/hum.aup3"
MIX, LABELS = "audio/sfx/hum.wav", "audio/sfx/hum.txt"
TAKE = "audio/takes/hum.wav"
WHISTLE = 4400.0


def write_take(path: Path) -> None:
    """Half a second of room noise, then a 220 Hz hum with a 4.4 kHz whistle."""
    rng = random.Random(7)
    frames = []
    for i in range(RATE * 3):
        t = i / RATE
        hum = 0.0
        if t >= 0.5:
            hum = 0.4 * math.sin(2 * math.pi * 220 * t)
            hum += 0.2 * math.sin(2 * math.pi * WHISTLE * t)
        value = max(-1.0, min(1.0, hum + rng.uniform(-0.05, 0.05)))
        frames.append(struct.pack("<h", int(value * 32767)))
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(b"".join(frames))


def left(path: Path) -> list[float]:
    with wave.open(str(path)) as audio:
        channels, width = audio.getnchannels(), audio.getsampwidth()
        assert audio.getframerate() == RATE and width == 2, path
        data = audio.readframes(audio.getnframes())
    values = struct.unpack(f"<{len(data) // 2}h", data)
    return [v / 32768 for v in values[::channels]]


def rms(samples: list[float]) -> float:
    return math.sqrt(sum(x * x for x in samples) / len(samples))


def tone(samples: list[float], hertz: float) -> float:
    """Goertzel magnitude of one frequency, normalised by length."""
    k = 2 * math.cos(2 * math.pi * hertz / RATE)
    s1 = s2 = 0.0
    for x in samples:
        s1, s2 = x + k * s1 - s2, s1
    return math.sqrt(max(0.0, s1 * s1 + s2 * s2 - k * s1 * s2)) / len(samples)


def window(samples: list[float], start: float, end: float) -> list[float]:
    return samples[int(start * RATE) : int(end * RATE)]


def steps(reduction_db: float) -> list[dict[str, object]]:
    return [
        {"op": "import", "path": TAKE, "name": "Hum"},
        {
            "op": "noise_reduction",
            "track": 0,
            "profile": [0.0, 0.45],
            "reduction_db": reduction_db,
            "sensitivity": 6,
            "smoothing": 3,
        },
        {
            "op": "spectral_edit",
            "track": 0,
            "time": [1.0, 2.0],
            "low_hz": 4000,
            "high_hz": 4800,
        },
        {"op": "label", "time": [0.5, 1.0], "text": "hum starts"},
        {"op": "label", "at": 1.5, "text": "whistle gone"},
        {"op": "import", "path": TAKE, "name": "Double"},
        {"op": "move", "track": 2, "start": 2.5},
        {"op": "track", "track": 2, "gain_db": -12, "pan": 0.5},
    ]


def digests(stage: Path) -> dict[str, str]:
    return {
        name: hashlib.sha256((stage / name).read_bytes()).hexdigest()
        for name in (SOURCE, MIX, LABELS)
    }


def no_model_packages() -> None:
    got = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--platform",
            "linux/amd64",
            "--network",
            "none",
            "--entrypoint",
            "/opt/audacity-python/bin/python",
            AUDACITY_WORKER,
            "-c",
            (
                "import importlib.util as u; print([m for m in ('faster_whisper',"
                " 'whisper', 'ctranslate2', 'onnxruntime', 'torch', 'transformers',"
                " 'huggingface_hub') if u.find_spec(m)])"
            ),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    assert got.stdout.strip() == "[]", got.stdout


async def upstream_mcp(stage: Path) -> str:
    """The pinned MCP beside Audacity: a read-only call works, transcription doesn't."""
    if not (installation("audacity") / "source/audacity_mcp/main.py").is_file():
        return "not installed"
    from joinery.workers import audacity_worker

    connection = MCPConnection(audacity_worker(stage, readonly=True), stage)
    await connection.connect(timeout=180)
    try:
        info = await connection.call("project_get_info", {}, timeout=120)
        assert not info.is_error, info
        try:
            await connection.call("transcribe_audio", {}, timeout=30)
        except PermissionError:
            pass
        else:
            raise AssertionError("transcribe_audio was not refused")
        return "ok"
    finally:
        await connection.close()


async def main(stage: Path) -> dict[str, object]:
    (stage.parent / "journal.json").write_text(json.dumps({"state": "staging"}))
    (stage / "joinery.toml").write_text('[tools]\nenabled=["godot","audacity"]\n')
    (stage / "project.godot").write_text("config_version=5\n")
    write_take(stage / TAKE)
    # The user's own recording, imported by them: its origin is a human import.
    record_imports(
        stage,
        [
            HumanImport(
                path=TAKE,
                sha256=hashlib.sha256((stage / TAKE).read_bytes()).hexdigest(),
                origin="human-import",
                imported_by="test fixture (the user's recording)",
                licence="CC0-1.0",
            )
        ],
    )
    take = left(stage / TAKE)

    await edit_document(
        stage,
        "audacity",
        SOURCE,
        json.dumps({"steps": steps(12)}),
        [MIX, LABELS],
        "clean",
    )
    mix = left(stage / MIX)
    noise_before, noise_after = rms(window(take, 0, 0.45)), rms(window(mix, 0, 0.45))
    # 12 dB of reduction on the profiled noise (allow for Audacity's smoothing).
    assert noise_after < noise_before / 2.5, (noise_before, noise_after)

    # Compare the edited window (1.1-1.9 s) with an untouched one (0.6-0.95 s)
    # of the same mix, so the mixdown's overall level (pan law, noise
    # reduction) cancels out and only the spectral edit shows.
    def ratio(hertz: float, start: float, end: float) -> float:
        return tone(window(mix, start, end), hertz) / tone(
            window(take, start, end), hertz
        )

    level = ratio(220, 0.6, 0.95)
    hum = ratio(220, 1.1, 1.9) / level
    whistle_kept = ratio(WHISTLE, 0.6, 0.95) / level
    whistle_edited = ratio(WHISTLE, 1.1, 1.9) / level
    # The whistle band is gone where it was selected; the hum below it and the
    # whistle outside the selection stay.
    assert whistle_edited < 0.1, whistle_edited
    assert 0.85 < hum < 1.15, hum
    assert whistle_kept > 0.5, whistle_kept
    labels = (stage / LABELS).read_text()
    assert labels == (
        "0.500000\t1.000000\thum starts\n1.500000\t1.500000\twhistle gone\n"
    ), labels

    # Change the amount the way an agent would: read the list, send it all back.
    info = await inspect_document(stage, "audacity", SOURCE)
    assert info["steps"] == steps(12), info
    first = digests(stage)
    changed = info["steps"]
    changed[1] = {**changed[1], "reduction_db": 24}
    await edit_document(
        stage,
        "audacity",
        SOURCE,
        json.dumps({"steps": changed}),
        [MIX, LABELS],
        "cleaner",
    )
    stronger = rms(window(left(stage / MIX), 0, 0.45))
    assert stronger < noise_after, (noise_after, stronger)
    second = digests(stage)
    assert second[MIX] != first[MIX]

    paths = recipe_paths(stage)
    assert len(paths) == 1, paths  # the second edit replaced the first recipe
    replays = [rebuild_one(path, stage, SandboxRunner(image=IMAGE)) for path in paths]
    assert all(replay.ok for replay in replays), replays
    assert digests(stage) == second, "a replay changed the outputs"

    no_model_packages()
    project = stage
    for tool in ("transcribe_audio", "transcribe_to_labels", "transcription_set_model"):
        verdict = mcp_violation("audacity", tool, project)
        assert verdict is not None and verdict.severity is Severity.DENY, tool
    mcp = await upstream_mcp(stage)
    return {
        "lead_in_rms": [
            round(noise_before, 4),
            round(noise_after, 4),
            round(stronger, 4),
        ],
        "mix_level": round(level, 3),
        "hum_kept": round(hum, 3),
        "whistle_kept_outside": round(whistle_kept, 3),
        "whistle_left_inside": round(whistle_edited, 4),
        "replayed": len(replays),
        "sha256": second,
        "upstream_mcp": mcp,
    }


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="joinery-audacity-") as directory:
        stage = Path(directory) / "stage"
        stage.mkdir()
        print(json.dumps(asyncio.run(main(stage)), indent=1))
        print("AUDACITY_NATIVE_OK")
