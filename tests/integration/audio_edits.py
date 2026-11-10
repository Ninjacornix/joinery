"""ffmpeg audio edits through the joinery-sound MCP server, in the real offline worker.

A SuperCollider render with a silent lead-in is trimmed, faded and brought to
the art bible's loudness, then converted to a looping stereo Ogg at another
sample rate. Every call goes through the MCP server an agent talks to, so the
typed-step validation, the errors and the replayable recipe are what an agent
gets. Every recipe replays byte for byte; refused calls change nothing.

Technical fixtures in a temporary project; nothing here approves art direction
or touches a user's game. Run: `uv run python tests/integration/audio_edits.py`.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import struct
import sys
import tempfile
import wave
from pathlib import Path
from typing import Any

from mcp import Client

from joinery.authoring import IMAGE, edit_document, run_offline
from joinery.recipe import load_recipe, rebuild_one, recipe_paths
from joinery.runner import SandboxRunner
from joinery.server_install import configured
from servers.authoring import make_server
from servers.sound import ops

BIBLE = """# Art bible (test fixture)

## Checked rules

```toml
schema = 2

[audio]
true_peak_max_dbtp = -1.0
lufs_tolerance = 1.0

[audio.lufs]
"audio/sfx" = -18.0
```
"""

SOURCE, RENDER, OGG = (
    "audio/sources/tone.scd",
    "audio/sfx/tone.wav",
    "audio/sfx/tone.ogg",
)

# 0.2 s of silence, a 440 Hz tone of about 0.56 s, then silence to 1.0 s.
TONE = (
    "(\nvar definition=SynthDef(\\tone,{ Out.ar(0,SinOsc.ar(440,0,0.4)"
    "*EnvGen.kr(Env.linen(0.01,0.5,0.05),doneAction:2)) });\n"
    "var score=Score([[0.0,[\\d_recv,definition.asBytes]],"
    "[0.2,[\\s_new,\\tone,1000,0,0]],[1.0,[\\c_set,0,0]]]);\n"
    f'score.recordNRT(outputFilePath:"{RENDER}",sampleRate:48000,'
    'headerFormat:"WAV",sampleFormat:"int16",options:ServerOptions.new.'
    "numOutputBusChannels_(1),duration:1.0,action:{0.exit});\n)\n"
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def wav_facts(path: Path) -> tuple[float, int, int]:
    """Duration in seconds, the first sample and the peak, of a 16-bit WAV."""
    with wave.open(str(path)) as audio:
        assert audio.getsampwidth() == 2, audio.getsampwidth()
        frames = audio.getnframes()
        data = audio.readframes(frames)
        rate, channels = audio.getframerate(), audio.getnchannels()
    values = struct.unpack(f"<{len(data) // 2}h", data)
    return (
        frames / rate,
        values[0] if channels == 1 else max(values[:channels]),
        max(abs(v) for v in values),
    )


async def probe(stage: Path, path: str) -> dict[str, Any]:
    got = await run_offline(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_name,sample_rate,channels,duration_ts:stream_tags",
            "-of",
            "json",
            path,
        ],
        stage,
        readonly=True,
    )
    assert got.ok, got.output
    stream: dict[str, Any] = json.loads(got.stdout)["streams"][0]
    return stream


async def call(
    client: Client, tool: str, arguments: dict[str, Any]
) -> tuple[bool, str]:
    got = await client.call_tool(tool, arguments)
    text = "\n".join(getattr(part, "text", "") for part in got.content)
    return bool(got.is_error), text


async def main(stage: Path) -> dict[str, Any]:
    (stage.parent / "journal.json").write_text(json.dumps({"state": "staging"}))
    (stage / "joinery.toml").write_text('[tools]\nenabled=["supercollider"]\n')
    (stage / "project.godot").write_text("config_version=5\n")
    (stage / "joinery.lock").write_text("version=1\n")
    (stage / "art").mkdir()
    (stage / "art/bible.md").write_text(BIBLE)
    facts: dict[str, Any] = {}

    # SuperCollider alone starts the sound server that carries the audio edits.
    servers = {spec.name for spec in configured(stage)[0]}
    assert "joinery-sound" in servers, servers

    await edit_document(stage, "supercollider", SOURCE, TONE, [RENDER], "tone")
    rendered = sha(stage / RENDER)
    length, _, _ = wav_facts(stage / RENDER)
    assert abs(length - 1.0) < 0.01, length

    async with Client(make_server(stage, "joinery-sound")) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
        assert "process_audio" in names, names

        # 1. In place: cut the silence, fade both ends, normalise to the bible.
        error, text = await call(
            client,
            "process_audio",
            {
                "source": RENDER,
                "steps": [
                    {"op": "trim_silence", "threshold_db": -60, "keep_seconds": 0.05},
                    {"op": "fade", "in_seconds": 0.02, "out_seconds": 0.1},
                    {"op": "normalise"},
                ],
            },
        )
        assert not error, text
        edited = json.loads(text)
        assert edited["status"] == "staged" and edited["requires_review"], edited
        lufs = edited["measured"]["steps"][-1]["lufs"]
        assert abs(lufs + 18.0) <= 0.5, edited["measured"]
        length, first, peak = wav_facts(stage / RENDER)
        # 1.0 s minus about 0.2 s of lead-in and the quiet tail past 0.05 s.
        assert 0.55 <= length <= 0.75, length
        assert abs(first) <= 1, first  # the fade-in starts from silence
        assert peak < 32767 * 10 ** (-1.0 / 20), peak  # under the -1 dBTP ceiling
        assert sha(stage / RENDER) != rendered
        facts["trimmed_seconds"] = round(length, 3)
        facts["lufs"] = lufs

        # 2. A second edit on the same render: a looping stereo Ogg at 44.1 kHz.
        error, text = await call(
            client,
            "process_audio",
            {
                "source": RENDER,
                "steps": [
                    {"op": "loop", "start": 0.0, "end": 0.4, "crossfade": 0.02},
                    {"op": "normalise"},
                ],
                "export": OGG,
                "output": {"channels": 2, "sample_rate": 44100},
            },
        )
        assert not error, text
        stream = await probe(stage, OGG)
        assert stream["codec_name"] == "vorbis", stream
        assert (stream["channels"], stream["sample_rate"]) == (2, "44100"), stream
        tags = {k.upper(): v for k, v in stream.get("tags", {}).items()}
        assert tags.get("LOOPSTART") == "0", tags
        assert tags.get("LOOPLENGTH") == str(round(0.4 * 44100)), tags
        imported = (stage / f"{OGG}.import").read_text()
        assert "loop=true" in imported, imported
        facts["ogg_loop"] = [tags["LOOPSTART"], tags["LOOPLENGTH"]]

        (recipe_path,) = recipe_paths(stage)
        recipe = load_recipe(recipe_path)
        assert set(recipe.outputs) == {SOURCE, RENDER, OGG}, recipe.outputs
        chain = ops.recipe_edits((stage / recipe.script).read_text())
        assert len(chain["edits"]) == 2, chain

        # 3. Refusals reach the agent as errors and change nothing.
        before = {
            p: sha(p) for p in sorted((stage / "audio").rglob("*")) if p.is_file()
        }
        recipes = sorted(p.name for p in (stage / "recipes").glob("*.toml"))
        imported_wav = stage / "audio/sfx/door.wav"
        imported_wav.write_bytes((stage / RENDER).read_bytes())
        with (stage / "joinery.lock").open("a") as lock:
            lock.write(
                "\n[[asset]]\n"
                'path = "audio/sfx/door.wav"\n'
                f'sha256 = "{sha(imported_wav)}"\n'
                'origin = "human-import"\n'
                'imported_by = "test fixture"\n'
                'licence = "CC0-1.0"\n'
            )
        before[imported_wav] = sha(imported_wav)
        refusals = {
            "imported file": (
                {"source": "audio/sfx/door.wav", "steps": [{"op": "gain", "db": -3}]},
                "has no recipe",
            ),
            "empty fade": (
                {"source": RENDER, "steps": [{"op": "fade"}]},
                "a fade needs",
            ),
            "outside audio/": (
                {"source": "art/bible.md", "steps": [{"op": "gain", "db": 1}]},
                "audio/",
            ),
            "escaping path": (
                {"source": "../outside.wav", "steps": [{"op": "gain", "db": 1}]},
                "",
            ),
            "step after loop": (
                {
                    "source": RENDER,
                    "steps": [
                        {"op": "loop", "start": 0.0, "end": 0.3},
                        {"op": "fade", "out_seconds": 0.1},
                    ],
                },
                "loop",
            ),
        }
        for label, (arguments, needle) in refusals.items():
            error, text = await call(client, "process_audio", arguments)
            assert error, (label, text)
            assert needle in text, (label, text)
        after = {p: sha(p) for p in sorted((stage / "audio").rglob("*")) if p.is_file()}
        assert after == before, "a refused edit changed a file"
        assert sorted(p.name for p in (stage / "recipes").glob("*.toml")) == recipes
        facts["refused"] = sorted(refusals)

    # 4. The edited render and the Ogg replay byte for byte from the recipe.
    replays = [
        rebuild_one(path, stage, SandboxRunner(image=IMAGE))
        for path in recipe_paths(stage)
    ]
    assert replays and all(replay.ok for replay in replays), replays
    facts["replayed"] = len(replays)
    return facts


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="joinery-audio-") as directory:
        stage = Path(directory) / "stage"
        stage.mkdir()
        print(json.dumps(asyncio.run(main(stage)), indent=1))
        print("AUDIO_EDITS_OK")
        sys.exit(0)
