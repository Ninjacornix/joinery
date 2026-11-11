"""Debugging a Godot error: what the debugger agent reads, runs and reports.

The sidebar sends the error the user wants debugged (``/debug``, or Debug on the
offer that follows a new script error in Godot's Output): the error line, the
Output text around it and the tail of the game's log. The debugger agent (role
``debugger``, read-only) reads those, inspects scripts and scenes, may run the
project headless in the offline worker (no network, a throwaway copy of the
project) to reproduce it, and reports a diagnosis. Its fix is then made by a
worker as a normal staged change, checked and reviewed like any other.
"""

from __future__ import annotations

import re
import shlex
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from joinery.authoring import run_offline
from joinery.transactions import clone_tree

#: Output lines that name an error worth debugging.
ERROR_LINE = re.compile(
    r"^(?:SCRIPT ERROR|ERROR|Parse Error|USER ERROR|Invalid|E \d+:\d+:\d+)",
    re.IGNORECASE,
)
#: "res://player.gd:12" in a stack trace or an error.
LOCATION = re.compile(r"res://[\w./ -]+?\.(?:gd|tscn|tres|gdshader|cs):\d+")
MAX_TEXT = 12_000
MAX_LINE = 300
REPRODUCE_SECONDS = 120
#: Frames the headless run lasts before it quits.
REPRODUCE_FRAMES = 300


@dataclass(frozen=True)
class DebugRequest:
    """The error to debug, as the sidebar sent it."""

    error: str
    output: str = ""
    log: str = ""
    #: The scene the game was run with ("" for the main scene).
    scene: str = ""

    @property
    def title(self) -> str:
        # The card says what went wrong, not the log's prefix.
        words = re.sub(r"^(?:SCRIPT ERROR|USER ERROR|ERROR|Parse Error):\s*", "", self.error)
        return "Debugging: " + words[:80]

    def locations(self) -> list[str]:
        found = LOCATION.findall(f"{self.error}\n{self.output}\n{self.log}")
        return list(dict.fromkeys(found))[:12]


def debug_request(data: dict[str, Any]) -> DebugRequest:
    """The sidebar's ``debug_start``, checked: {error, output?, log?, scene?}."""
    error = " ".join(str(data.get("error", "")).split())
    output = str(data.get("output", ""))
    log = str(data.get("log", ""))
    if not error:
        error = next(
            (
                line.strip()
                for line in reversed(output.splitlines())
                if ERROR_LINE.match(line.strip())
            ),
            "",
        )
    if not error:
        raise ValueError(
            "There's no error to debug. Run the game, or say what goes wrong after /debug."
        )
    scene = str(data.get("scene", "")).strip()
    if scene and not (scene.startswith("res://") and scene.endswith((".tscn", ".scn"))):
        scene = ""
    return DebugRequest(
        error=error[:MAX_LINE],
        output=output[-MAX_TEXT:],
        log=log[-MAX_TEXT:],
        scene=scene,
    )


@dataclass(frozen=True)
class Diagnosis:
    """What the debugger found, and the fix a worker should make ("" = none)."""

    cause: str
    fix: str = ""
    targets: list[str] = field(default_factory=list)
    reproduced: bool = False


def diagnosis_from(payload: Any) -> Diagnosis:
    """The debugger's report, checked; raises in words it can act on."""
    if not isinstance(payload, dict):
        raise TypeError("Report {cause, fix, targets?, reproduced?}.")
    cause = " ".join(str(payload.get("cause", "")).split())
    fix = str(payload.get("fix", "") or "").strip()
    targets = payload.get("targets") or []
    if not 1 <= len(cause) <= 1000:
        raise ValueError("cause is one to 1000 characters: what goes wrong and why.")
    if len(fix) > 4000:
        raise ValueError(
            "fix is up to 4000 characters: the change a worker should make."
        )
    if (
        not isinstance(targets, list)
        or len(targets) > 20
        or any(not isinstance(t, str) or not 1 <= len(t) <= 400 for t in targets)
    ):
        raise ValueError("targets is a list of up to 20 project paths.")
    return Diagnosis(
        cause=cause,
        fix=fix,
        targets=[str(t) for t in targets],
        reproduced=bool(payload.get("reproduced", False)),
    )


def problem_lines(output: str, limit: int = 40) -> list[str]:
    """The error lines and stack frames in a run's output."""
    lines = [
        line.strip()
        for line in output.splitlines()
        if ERROR_LINE.match(line.strip()) or LOCATION.search(line)
    ]
    return lines[:limit]


async def reproduce(project: Path, scene: str = "") -> dict[str, Any]:
    """Run the project headless in the offline worker and report what it printed.

    The run uses a throwaway copy of the project, so nothing it writes (imports,
    user files) reaches the project, and the worker has no network.
    """
    if scene and not (scene.startswith("res://") and scene.endswith((".tscn", ".scn"))):
        raise ValueError("scene is a res:// scene path, or empty for the main scene.")
    scratch = Path(tempfile.mkdtemp(prefix="joinery-debug-"))
    try:
        copy = scratch / "project"
        clone_tree(project, copy)
        run = (
            "godot --headless --path /work --editor --import --quit >/dev/null 2>&1; "
            f"godot --headless --path /work --quit-after {REPRODUCE_FRAMES}"
            + (" " + shlex.quote(scene) if scene else "")
        )
        got = await run_offline(["sh", "-c", run], copy, REPRODUCE_SECONDS)
        output = got.output
        return {
            "ok": got.ok,
            "errors": problem_lines(output),
            "output": output[-6000:],
            "seconds": round(got.seconds, 1),
        }
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
