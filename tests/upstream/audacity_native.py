"""Native Audacity + actual pinned MCP tools inside a disposable test image."""

import array
import asyncio
import json
import math
import os
import subprocess
import sys
import time
import traceback
import wave
from datetime import datetime
from pathlib import Path

configuration = Path.home() / ".audacity-data/audacity.cfg"
configuration.parent.mkdir(parents=True, exist_ok=True)
# Without a [Version] matching the installed release, Audacity treats this as a
# fresh install: it resets these settings and opens a modal "What's new" dialog
# that blocks every script command. It also resets a module to "new" (never
# loaded) unless its recorded path and file date match the module on disk.
script_pipe = Path("/usr/lib/audacity/modules/mod-script-pipe.so")
module_date = datetime.fromtimestamp(script_pipe.stat().st_mtime).strftime(
    "%Y-%m-%dT%H:%M:%S"
)
version = subprocess.run(
    ["dpkg-query", "-W", "-f=${Version}", "audacity"],
    check=True,
    capture_output=True,
    text=True,
).stdout
major, minor, micro = version.split("+")[0].split("-")[0].split(".")[:3]
configuration.write_text(
    "PrefsVersion=1.1.1r1\n"
    "[GUI]\nShowSplashScreen=0\n"
    f"[Version]\nMajor={major}\nMinor={minor}\nMicro={micro}\n"
    "[Module]\nmod-script-pipe=1\n"
    f"[ModulePath]\nmod-script-pipe={script_pipe}\n"
    f"[ModuleDateTime]\nmod-script-pipe={module_date}\n"
)
display_socket = Path("/tmp/.X11-unix/X99")
display_deadline = time.monotonic() + 10
while not display_socket.exists():
    if time.monotonic() > display_deadline:
        raise RuntimeError("Private X display never became ready")
    time.sleep(0.1)


def _cpu_ticks(pid: int) -> int:
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return int(fields[11]) + int(fields[12])  # utime + stime


async def _settled(process, deadline: float) -> None:
    """Wait until Audacity has used under 20% CPU for three seconds in a row."""
    hertz = os.sysconf("SC_CLK_TCK")
    quiet = 0
    await asyncio.sleep(3)
    while quiet < 3:
        if process.poll() is not None:
            raise RuntimeError("Audacity exited while starting")
        if time.monotonic() > deadline:
            raise RuntimeError("Audacity never finished starting")
        before = _cpu_ticks(process.pid)
        await asyncio.sleep(1)
        busy = (_cpu_ticks(process.pid) - before) / hertz
        quiet = quiet + 1 if busy < 0.2 else 0


async def main(process):
    deadline = time.monotonic() + 30
    while not list(Path("/tmp").glob("audacity_script_pipe.to.*")):
        if process.poll() is not None or time.monotonic() > deadline:
            raise RuntimeError(
                "Audacity script pipe did not become ready: "
                + Path("/work/audacity.log").read_text()[-3000:]
            )
        await asyncio.sleep(0.1)

    # The pipe opens long before Audacity can run commands. A command sent too
    # early gets an empty reply, and dropping that connection mid-reply crashes
    # Audacity. So send nothing until it has finished starting: its CPU use
    # settles once the main window is up.
    started = time.monotonic()
    await _settled(process, started + 180)
    # Importing the MCP creates its client, which opens the pipe at once.
    import audacity_mcp.main as upstream

    try:
        await asyncio.wait_for(upstream.mcp.call_tool("track_get_info", {}), timeout=60)
    except Exception as error:
        raise RuntimeError(
            f"Audacity did not answer its first command: {error}\n"
            + Path("/work/audacity.log").read_text()[-3000:]
        ) from error
    ready_after = time.monotonic() - started

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
                "seconds_to_ready": round(ready_after, 1),
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
    status = 1
    try:
        asyncio.run(main(process))
        status = 0
    except BaseException:  # noqa: BLE001 - report any failure, then exit hard below.
        traceback.print_exc()
        # Keep what the screen showed, so the next diagnosis starts from evidence.
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-f",
                "x11grab",
                "-i",
                ":99",
                "-frames:v",
                "1",
                "/work/failure.png",
            ],
            check=False,
            timeout=30,
        )
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    # The MCP client reads in worker threads that can wait minutes for a reply
    # Audacity will never send. Exit now rather than wait for them to unwind.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(status)
