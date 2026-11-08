"""Native Audacity + actual pinned MCP tools inside a disposable test image."""

import array
import asyncio
import json
import math
import subprocess
import time
import wave
from pathlib import Path

configuration = Path.home() / ".audacity-data/audacity.cfg"
configuration.parent.mkdir(parents=True, exist_ok=True)
configuration.write_text("WantWelcomeScreen=0\n[Module]\nmod-script-pipe=1\n")
display_socket = Path("/tmp/.X11-unix/X99")
display_deadline = time.monotonic() + 10
while not display_socket.exists():
    if time.monotonic() > display_deadline:
        raise RuntimeError("Private X display never became ready")
    time.sleep(0.1)


async def main(process):
    deadline = time.monotonic() + 30
    while not list(Path("/tmp").glob("audacity_script_pipe.to.*")):
        if process.poll() is not None or time.monotonic() > deadline:
            raise RuntimeError(
                "Audacity script pipe did not become ready: "
                + Path("/work/audacity.log").read_text()[-3000:]
            )
        await asyncio.sleep(0.1)
    import audacity_mcp.main as upstream

    calls = []

    async def call(tool, /, **arguments):
        result = await upstream.mcp.call_tool(tool, arguments)
        blocks = result[0] if isinstance(result, tuple) else result
        payload = json.loads(blocks[0].text)
        assert payload.get("success"), (tool, payload)
        calls.append(tool)
        return payload

    await call("track_add_mono")
    await call("generate_tone", frequency=440, amplitude=0.2, duration=0.1)
    await call("track_add_mono")
    await call("track_set_properties", track=0, name="ToneFixture")
    tracks = json.loads((await call("track_get_info"))["message"])
    assert len(tracks) == 2, tracks
    assert tracks[0]["name"] == "ToneFixture" and tracks[1]["name"] != "ToneFixture", (
        tracks
    )
    await call("select_all")
    await call("project_save_as", path="/work/fixture.aup3")
    await call("project_export_audio", path="/work/fixture.wav", num_channels=1)
    assert Path("/work/fixture.aup3").stat().st_size > 0
    with wave.open("/work/fixture.wav") as audio:
        duration = audio.getnframes() / audio.getframerate()
        assert 0.09 < duration < 0.11, duration
    converted = await asyncio.to_thread(
        subprocess.run,
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            "/work/fixture.wav",
            "-f",
            "s16le",
            "-ac",
            "1",
            "-ar",
            "48000",
            "-",
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    pcm = converted.stdout
    samples = array.array("h", pcm)
    rms = math.sqrt(sum((sample / 32768) ** 2 for sample in samples) / len(samples))
    assert rms > 0.01, rms
    await upstream.client.close()
    Path("/work/results.json").write_text(
        json.dumps(
            {
                "native_calls": calls,
                "tracks": len(tracks),
                "seconds": duration,
                "rms": rms,
            }
        )
    )
    print("AUDACITY_NATIVE_MCP_OK", flush=True)


with Path("/work/audacity.log").open("w") as application_log:
    process = subprocess.Popen(
        ["audacity"],
        stdin=subprocess.DEVNULL,
        stdout=application_log,
        stderr=subprocess.STDOUT,
    )
    try:
        asyncio.run(main(process))
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
