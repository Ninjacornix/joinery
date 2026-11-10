"""SuperCollider: complete NRT patches rendered offline; live audition via its MCP.

sclang does not exit on its own. A patch that fails to parse, raises, or never
calls `exit` would otherwise sit idle until the worker's own timeout, so the
recipe script exits with a clear `JOINERY_ERROR` / `JOINERY_TIMEOUT` line, and
replay runs under `timeout` as a last resort.
"""

from __future__ import annotations

import json
import re

from joinery.tools.base import (
    CREATIVE_WORKER,
    MCP_WORKER,
    Feature,
    ImageCheck,
    InstallCheck,
    McpProbe,
    ToolModule,
    WorkerExec,
)

#: Seconds a patch may run before the recipe script stops it. An NRT render is
#: much faster than real time, so this only trips on a patch that never exits.
RENDER_LIMIT = 240
#: Hard stop for replay, above RENDER_LIMIT so the script's own message wins.
REPLAY_TIMEOUT = 300
#: A patch ends sclang itself (usually `0.exit` in recordNRT's action).
_EXITS = re.compile(r"\bexit\b")

NO_EXIT = (
    "JOINERY_ERROR: the patch never exits. End the render with 0.exit, for "
    "example in Score.recordNRT's action: { 0.exit }."
)
PARSE_ERROR = (
    "JOINERY_ERROR: the patch does not parse; see the SuperCollider error above."
)
RUN_ERROR = (
    "JOINERY_ERROR: the patch raised an error; see the SuperCollider error above."
)
TIMEOUT = (
    f"JOINERY_TIMEOUT: the patch did not exit within {RENDER_LIMIT} s. End the "
    "render with 0.exit in Score.recordNRT's action."
)


def _sc(text: str) -> str:
    """A SuperCollider string literal."""
    return json.dumps(text, ensure_ascii=False)


class SuperCollider(ToolModule):
    id = "supercollider"
    name = "SuperCollider"
    family = "joinery-sound"
    suffix = ".scd"
    script_ext = ".scd"
    authoring = True
    text_source = True
    binary = "sclang"
    desktop = "supercollider"
    ready_summary = "{name} {version} ready (offline renders)"
    needs_image = "SuperCollider: worker image missing. Run Joinery setup."
    broken = "SuperCollider failed its check"
    features = (
        Feature(
            "live-audition",
            "live audition",
            needs_image="Offline renders ready; live audition not installed. Run Joinery setup.",
            needs_install="Offline renders ready; live audition not installed. Run Joinery setup.",
            broken="Live audition error",
        ),
    )
    checks = (
        ImageCheck(CREATIVE_WORKER),
        WorkerExec(
            CREATIVE_WORKER,
            ("sclang", "-v"),
            expect=r"sclang \d",
            version=r"sclang (\d+\.\d+(?:\.\d+)?)",
            on_failure="needs_image",
            failure="SuperCollider is missing from the creative worker; rebuild it",
        ),
        McpProbe(
            "joinery-sound",
            "document_capabilities",
            capability="supercollider",
            image=CREATIVE_WORKER,
        ),
        ImageCheck(MCP_WORKER, feature="live-audition"),
        InstallCheck("supercollider", feature="live-audition"),
        McpProbe(
            "claude-collider",
            "cc_status",
            expect=r"Server: running",
            feature="live-audition",
            image=MCP_WORKER,
        ),
    )

    #: Live audio is real time and cannot be replayed, so it never becomes an
    #: asset (joinery.workers keeps recordings inside the worker).
    mcp_notes = (
        (
            "cc_recording",
            (
                "Joinery: audition only. Recordings stay inside the worker and are "
                "discarded with it. Render a sound you want to keep with "
                "joinery-sound operate_document (an NRT patch), which records a "
                "replayable recipe."
            ),
        ),
    )

    def script(self, source: str, base: str, code: str, exports: list[str]) -> str:
        # The log recreates the exact native patch before running it, so a
        # clean replay never depends on the previous source output remaining.
        # Rendering is NRT with a completion action; no sound device is needed.
        # - OnError catches an error raised later, such as in recordNRT's action;
        # - the SystemClock limit ends a patch that never calls exit;
        # - compile returns nil on a syntax error (interpret would carry on);
        # - try catches an error while the patch runs.
        header = (
            "(\nvar patch;\n"
            + f"OnError.add({{ {_sc(RUN_ERROR)}.postln; 1.exit }});\n"
            + f"SystemClock.sched({RENDER_LIMIT}, {{ {_sc(TIMEOUT)}.postln; 2.exit; nil }});\n"
            + f'File.use({_sc(source)}, "w", {{ |file| file.write({_sc(code)}); }});\n'
        )
        if not _EXITS.search(code):
            # Without an exit sclang idles after the render until the limit;
            # say so at once instead of running the patch.
            return header + f"{_sc(NO_EXIT)}.postln;\n1.exit;\n)\n"
        return (
            header
            + f"patch = {_sc(code)}.compile;\n"
            + f"if(patch.isNil) {{ {_sc(PARSE_ERROR)}.postln; 1.exit }} {{\n"
            + f"  {{ patch.value }}.try {{ |error| error.reportError; {_sc(RUN_ERROR)}.postln; 1.exit }};\n"
            + "};\n)\n"
        )

    def replay_argv(self, script: str) -> list[str]:
        return ["timeout", str(REPLAY_TIMEOUT), "sclang", script]


MODULE = SuperCollider()
