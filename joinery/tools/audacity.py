"""Audacity: recorded audio edited in Audacity itself, in its own offline worker.

A source is an Audacity project (.aup3) under audio/. It is never edited in
place: its recipe lists typed steps from the user's own recordings (import,
noise reduction from a noise profile in the recording, spectral edits, labels,
track gain/pan/mute, clip moves), and `joinery-audacity` replays every step in
a fresh Audacity through mod-script-pipe, then writes the project, the WAV
mixdowns and the label file. To change a project, send its whole step list
again with the change; `inspect_document` returns the current list.

The one fallback is a `macro` step: raw Audacity scripting commands, with a
reason, refused for file, preference, playback and Nyquist commands, and
listed in the replay report as raw.

The pinned upstream Audacity MCP runs in the same worker for inspection only;
a call to its transcription tools stops the turn (Rule Zero), and no speech
model is installed.
"""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Any

from joinery.tools.base import (
    AUDACITY_WORKER,
    Feature,
    ImageCheck,
    InstallCheck,
    Run,
    ToolModule,
    WorkerExec,
)

#: Must match sandbox/audacity_worker.py.
RECIPE_FORMAT = 1
#: Typed steps the worker replays; "macro" is the logged raw fallback.
OPERATIONS = frozenset(
    {"import", "noise_reduction", "spectral_edit", "label", "track", "move", "macro"}
)

#: Kept edits go through operate_document; the upstream MCP's writes have no
#: recipe, so review refuses them.
KEEP_EDITS = (
    "Files this server writes have no recipe, so review refuses them; make "
    "Audacity edits you want to keep with joinery-sound operate_document."
)


def steps_of(code: str) -> list[dict[str, Any]]:
    """The step list in operate_document's code: {"steps": [...]} or a list."""
    try:
        data = json.loads(code)
    except json.JSONDecodeError as error:
        raise ValueError(f"Audacity code is a JSON step list: {error}") from None
    steps = data.get("steps") if isinstance(data, dict) else data
    if not isinstance(steps, list) or not steps:
        raise ValueError('Audacity code is {"steps": [...]} with at least one step.')
    for step in steps:
        if not isinstance(step, dict) or step.get("op") not in OPERATIONS:
            raise ValueError(
                f"Unknown Audacity step {step!r}; use one of {sorted(OPERATIONS)}."
            )
        if step["op"] == "import":
            path = step.get("path")
            if not isinstance(path, str) or not path.startswith("audio/"):
                raise ValueError(
                    "Audacity imports the project's own audio under audio/."
                )
    return steps


class Audacity(ToolModule):
    id = "audacity"
    name = "Audacity"
    family = "joinery-sound"
    suffix = ".aup3"
    script_ext = ".json"
    authoring = True
    worker_image = AUDACITY_WORKER
    replay_image = AUDACITY_WORKER
    # Every replay rebuilds the project from the user's recordings.
    uses_preimage = False
    binary = "joinery-audacity"
    desktop = "audacity"
    ready_summary = "{name} {version} ready (runs offline)"
    broken = "Audacity failed its check"
    mcp_notes = (
        ("project_save", KEEP_EDITS),
        ("project_export_audio", KEEP_EDITS),
    )
    features = (
        Feature(
            "mcp",
            "the Audacity MCP (inspection)",
            broken="The Audacity MCP failed its check",
        ),
    )
    checks = (
        ImageCheck(AUDACITY_WORKER),
        WorkerExec(
            AUDACITY_WORKER,
            (
                "sh",
                "-c",
                (
                    "test -x /usr/local/bin/joinery-audacity"
                    " && test -s /opt/joinery/audacity-plugins/pluginregistry.cfg"
                    " && dpkg-query -W -f='${Version}\\n' audacity"
                ),
            ),
            expect=r"\d+\.\d+\.\d+",
            version=r"(\d+\.\d+\.\d+)",
            on_failure="needs_image",
            failure="Audacity is missing from its worker; rebuild the Audacity worker",
        ),
        # Starts Audacity and answers one scripting command.
        WorkerExec(
            AUDACITY_WORKER,
            ("joinery-audacity", "--check"),
            expect=r"JOINERY_AUDACITY_READY",
            version=r"JOINERY_AUDACITY_READY (\d+\.\d+\.\d+)",
            timeout=240,
            tier=2,
            failure="Audacity started but did not answer its script pipe",
        ),
        InstallCheck("audacity", feature="mcp"),
    )

    def script(self, source: str, base: str, code: str, exports: list[str]) -> str:
        # The recipe is the whole step list from the user's recordings, so a
        # replay never depends on the project file the same recipe writes.
        recipe: dict[str, Any] = {
            "joinery_audacity": RECIPE_FORMAT,
            "steps": steps_of(code),
            "audio": [
                {"path": path, "channels": 2}
                for path in exports
                if path.endswith(".wav")
            ],
            "project": source,
        }
        labels = [path for path in exports if path.endswith(".txt")]
        if labels:
            recipe["labels"] = labels[0]
        return json.dumps(recipe, indent=1, sort_keys=True) + "\n"

    def replay_argv(self, script: str) -> list[str]:
        return ["joinery-audacity", script]

    def script_inputs(self, code: str) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                str(step["path"]) for step in steps_of(code) if step["op"] == "import"
            )
        )

    def check_destinations(self, source: str, exports: list[str]) -> None:
        if PurePosixPath(source).parts[0] != "audio":
            raise ValueError("Audacity projects belong under audio/.")
        labels = [path for path in exports if path.endswith(".txt")]
        if len(labels) > 1:
            raise ValueError("An Audacity project exports at most one label file.")
        for path in exports:
            if not path.endswith((".wav", ".txt")):
                raise ValueError(
                    "Audacity exports WAV mixdowns and a label .txt; make Ogg "
                    "files from the WAV with the sound tools."
                )

    def capability_programs(self) -> dict[str, str]:
        # document_capabilities looks in the creative worker; Audacity has its
        # own, which readiness checks instead.
        return {}

    def capability_requirements(self) -> dict[str, tuple[str, ...]]:
        return {}

    async def inspect(
        self, project: Path, path: Path, source: str, run: Run
    ) -> dict[str, Any] | None:
        """The step list that made the project, for an edit to start from."""
        newest_first = sorted(
            (project / "recipes").glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for recipe in newest_first:
            try:
                data = json.loads(recipe.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            if (
                data.get("joinery_audacity") == RECIPE_FORMAT
                and data.get("project") == source
            ):
                return {
                    "source": source,
                    "recipe": recipe.relative_to(project).as_posix(),
                    "steps": data["steps"],
                    "audio": data.get("audio", []),
                    "labels": data.get("labels", ""),
                    "note": "Send the whole step list again with your change.",
                }
        return {"source": source, "steps": [], "note": "No recipe made this project."}


MODULE = Audacity()
