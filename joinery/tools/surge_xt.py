"""Surge XT: JSON patch sources rendered offline through the real VST3 plugin.

Surge's offline renderer (`joinery-surge`, Surge's own Python with pedalboard,
mido and numpy) also runs Joinery's music recipes, which render MIDI songs
through FluidSynth and Surge XT, and the typed audio edits on Surge's output.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from joinery.tools.base import (
    CREATIVE_WORKER,
    Feature,
    ImageCheck,
    McpProbe,
    Run,
    ToolModule,
    WorkerExec,
)
from joinery.tools.midi import SOUNDFONT, SOUNDFONT_SHA256

#: ffmpeg in the creative worker has every filter and encoder the audio edits use.
AUDIO_EDIT_CHECK = WorkerExec(
    CREATIVE_WORKER,
    (
        "sh",
        "-c",
        (
            "ffmpeg -hide_banner -filters 2>/dev/null | awk '{print $2}'"
            " | grep -cxE 'afade|alimiter|ebur128|volume'"
            " && ffmpeg -hide_banner -encoders 2>/dev/null | awk '{print $2}'"
            " | grep -cx libvorbis"
            " && command -v ffprobe python3 >/dev/null"
            " && echo JOINERY_AUDIO_EDIT_OK"
        ),
    ),
    expect=r"^4\s+1\s+JOINERY_AUDIO_EDIT_OK",
    feature="audio-edit",
    on_failure="needs_image",
    failure="ffmpeg in the creative worker is missing a filter or encoder",
)

#: FluidSynth with the recorded General MIDI SoundFont, and Surge's MIDI reader.
MUSIC_CHECK = WorkerExec(
    CREATIVE_WORKER,
    (
        "sh",
        "-c",
        (
            "fluidsynth --version | head -n 1"
            f" && sha256sum {SOUNDFONT}"
            " && /opt/surge-python/bin/python -c"
            " 'import mido, numpy; print(\"JOINERY_MIDI_OK\")'"
        ),
    ),
    expect=SOUNDFONT_SHA256 + r"[\s\S]*JOINERY_MIDI_OK",
    feature="music",
    on_failure="needs_image",
    failure="FluidSynth or its General MIDI SoundFont is missing from the creative worker",
)


class SurgeXT(ToolModule):
    id = "surge-xt"
    name = "Surge XT"
    family = "joinery-sound"
    suffix = ".json"
    script_ext = ".py"
    authoring = True
    text_source = True
    binary = "joinery-surge"
    desktop = ""
    ready_summary = "{name} {version} ready: renders offline in the creative worker"
    needs_image = "Surge XT: the creative worker isn't built yet. Rebuild the worker."
    broken = "Surge XT failed its check"
    features = (
        Feature(
            "music",
            "MIDI music (General MIDI and Surge XT)",
            needs_image=(
                "MIDI music unavailable: FluidSynth or its SoundFont is missing "
                "from the creative worker. Rebuild the worker."
            ),
            broken="MIDI music failed its check",
        ),
        Feature(
            "audio-edit",
            "audio edits (trim, fade, loudness, loops)",
            needs_image=(
                "Audio edits unavailable: ffmpeg in the creative worker is "
                "incomplete. Rebuild the worker."
            ),
            broken="Audio edits failed their check",
        ),
    )
    checks = (
        ImageCheck(CREATIVE_WORKER),
        WorkerExec(
            CREATIVE_WORKER,
            (
                "sh",
                "-c",
                (
                    "test -x /usr/local/bin/joinery-surge"
                    ' && test -d "/usr/lib/vst3/Surge XT.vst3"'
                    " && dpkg-query -W -f='${Version}\\n' surge-xt"
                ),
            ),
            expect=r"\d+\.\d+\.\d+",
            version=r"(\d+\.\d+\.\d+)",
            on_failure="needs_image",
            failure="Surge XT: the plugin is missing from the worker; rebuild the creative worker",
        ),
        McpProbe(
            "joinery-sound",
            "document_capabilities",
            capability="surge-xt",
            image=CREATIVE_WORKER,
            failure="Not connected: the sound server didn't start",
        ),
        WorkerExec(
            CREATIVE_WORKER,
            ("joinery-surge", "--list"),
            expect=r"\{",
            tier="deep",
            timeout=120,
        ),
        MUSIC_CHECK,
        AUDIO_EDIT_CHECK,
    )

    def script(self, source: str, base: str, code: str, exports: list[str]) -> str:
        # Like SuperCollider, the log carries the whole patch, so a replay never
        # depends on the source file that the same recipe writes.
        return f"render({source!r}, {code!r}, {exports!r})\n"

    def replay_argv(self, script: str) -> list[str]:
        return ["joinery-surge", script]

    def chain_script(self, previous: str, post: str, audio_input: str) -> str:
        """Replay `previous` (a Surge or music recipe), then edit its output.

        The rendered file is removed first, so the edit only ever processes a
        fresh render; a failing render raises before the edit runs.
        """
        return (
            "# Joinery audio edit: replay the previous step, then edit its output.\n"
            "import os\n"
            f"if os.path.exists({audio_input!r}):\n"
            f"    os.remove({audio_input!r})\n"
            f"exec(compile({previous!r}, 'previous-step', 'exec'),"
            " {'render': render, '__name__': '__main__'})\n"
            f"exec(compile({post!r}, 'audio-edit', 'exec'), {{'__name__': '__main__'}})\n"
        )

    async def inspect(
        self, project: Path, path: Path, source: str, run: Run
    ) -> dict[str, Any] | None:
        got = await run(["joinery-surge", "--list"], project, readonly=True)
        if not got.ok:
            raise ValueError(
                "Surge XT could not list its parameters: " + got.output[-800:]
            )
        return {
            "source": source,
            "content": path.read_text(),
            # The plugin's defaults; this source's own values are in "content".
            "parameter_defaults": json.loads(got.stdout),
        }


MODULE = SurgeXT()
