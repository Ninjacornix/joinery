"""SuperCollider in the real offline workers: NRT fails fast, live audition stays out of the project.

Technical fixtures only; nothing here approves art direction or touches a
user's game. Run: `uv run python tests/integration/supercollider_native.py [nrt|live|all]`.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import tempfile
import time
from pathlib import Path

from joinery.authoring import run_offline
from joinery.mcp_runtime import MCPConnection
from joinery.tools import get
from joinery.tools.supercollider import NO_EXIT, PARSE_ERROR, RUN_ERROR
from joinery.workers import AUDITION_DIR, supercollider_worker

#: A failing patch must end well inside this; before the fix it hung until the
#: worker's 600 s limit.
FAST = 60


def score(frequency: int, action: str = "0.exit;") -> str:
    return (
        "(\nvar definition=SynthDef(\\fixture,{ Out.ar(0,SinOsc.ar("
        + str(frequency)
        + ",0,0.2)*EnvGen.kr(Env.perc(0.001,0.04),doneAction:2)) });\n"
        "var score=Score([[0.0,[\\d_recv,definition.asBytes]],"
        "[0.001,[\\s_new,\\fixture,1000,0,0]],[0.08,[\\c_set,0,0]]]);\n"
        'score.recordNRT(outputFilePath:"audio/fixture.wav",sampleRate:48000,'
        'headerFormat:"WAV",sampleFormat:"int16",options:ServerOptions.new.'
        "numOutputBusChannels_(1),duration:0.08,action:{" + action + "});\n)\n"
    )


def project_at(directory: str) -> Path:
    project = Path(directory)
    (project / "project.godot").write_text("config_version=5\n")
    (project / "joinery.toml").write_text(
        '[tools]\nenabled=["godot","supercollider"]\n'
    )
    (project / "joinery.lock").write_text("version=1\n")
    (project / "audio").mkdir()
    (project / "recipes").mkdir()
    return project


async def render(project: Path, name: str, code: str) -> tuple[bool, str, float]:
    module = get("supercollider")
    script = f"recipes/{name}.scd"
    (project / script).write_text(
        module.script(f"audio/{name}.scd", "", code, ["audio/fixture.wav"])
    )
    start = time.monotonic()
    result = await run_offline(module.replay_argv(script), project, timeout=600)
    return result.ok, result.output, time.monotonic() - start


async def nrt() -> None:
    with tempfile.TemporaryDirectory(prefix="joinery-sclang-") as directory:
        project = project_at(directory)
        ok, output, _ = await render(project, "good", score(440))
        assert ok, output[-2000:]
        first = hashlib.sha256((project / "audio/fixture.wav").read_bytes()).digest()
        (project / "audio/fixture.wav").unlink()
        ok, output, _ = await render(project, "good", score(440))
        assert ok, output[-2000:]
        again = hashlib.sha256((project / "audio/fixture.wav").read_bytes()).digest()
        assert first == again, "NRT replay must be byte-repeatable"
        failing = {
            "runtime": ("(\nnil.fooBar;\n0.exit;\n)\n", RUN_ERROR),
            "parse": ("(\nSinOsc.ar(\n0.exit;\n", PARSE_ERROR),
            "noexit": ("(\n1 + 2;\n)\n", NO_EXIT),
            "action": (score(440, "nil.fooBar; 0.exit;"), RUN_ERROR),
        }
        for name, (code, message) in failing.items():
            ok, output, seconds = await render(project, name, code)
            assert not ok, name
            assert message in output, (name, output[-1500:])
            assert seconds < FAST, (name, seconds)
        print("SuperCollider NRT render/repeatable replay/fail-fast on error: PASS")


def text(result: object) -> str:
    return " ".join(
        getattr(part, "text", "") for part in getattr(result, "content", [])
    )


async def live() -> None:
    with tempfile.TemporaryDirectory(prefix="joinery-collider-") as directory:
        project = project_at(directory)
        connection = MCPConnection(supercollider_worker(project), project)
        await connection.connect(timeout=90)
        try:
            status = await connection.call("cc_status", {}, timeout=90)
            assert "Server: running" in text(status), text(status)
            # The pinned checkout's samples and example songs are not visible.
            hidden = await connection.call(
                "cc_execute",
                {
                    "code": '["/opt/collider/samples", "/opt/collider/tapes"]'
                    ".collect { |p| PathName(p).entries.size }.sum"
                },
                timeout=60,
            )
            assert text(hidden).strip().endswith("0"), text(hidden)
            visible = await connection.call(
                "cc_execute",
                {"code": 'File.exists("/opt/collider/dist/index.js")'},
                timeout=60,
            )
            assert "true" in text(visible), text(visible)
            started = await connection.call(
                "cc_recording", {"action": "start", "filename": "probe.wav"}, 60
            )
            assert not started.is_error, text(started)
            await connection.call(
                "cc_execute", {"code": "{ SinOsc.ar(330, 0, 0.2) ! 2 }.play; nil"}, 60
            )
            await asyncio.sleep(1)
            await connection.call("cc_control", {"action": "stop"}, 60)
            stopped = await connection.call("cc_recording", {"action": "stop"}, 60)
            assert not stopped.is_error, text(stopped)
            kept = await connection.call(
                "cc_execute",
                {"code": f'File.exists("{AUDITION_DIR}/probe.wav")'},
                timeout=60,
            )
            assert "true" in text(kept), text(kept)
        finally:
            await connection.close()
        written = [p for p in (project / "audio").rglob("*") if p.is_file()]
        assert not written, f"live audition wrote into the project: {written}"
        print(
            "SuperCollider live audition/recording outside the project/no bundled samples: PASS"
        )


async def main(which: str) -> None:
    if which in {"all", "nrt"}:
        await nrt()
    if which in {"all", "live"}:
        await live()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "which", choices=["all", "nrt", "live"], default="all", nargs="?"
    )
    asyncio.run(main(parser.parse_args().which))
