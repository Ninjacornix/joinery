"""Inkscape: self-contained SVG sources exported to PNG in the offline worker.

Persistent SVG edits go through Joinery's `operate_vector`. The pinned upstream
Inkscape MCP is for inspection and analysis only.
"""

from __future__ import annotations

import re

from joinery.tools.base import (
    CREATIVE_WORKER,
    INKSCAPE_WORKER,
    Feature,
    ImageCheck,
    InstallCheck,
    McpProbe,
    ToolModule,
    WorkerExec,
)

#: External files, entities, scripts and remote references.
_EXTERNAL = re.compile(
    r"(?:file:|<!ENTITY|<script|(?:href|url)\s*[=(]\s*['\"]?https?:)", re.IGNORECASE
)


#: Where an upstream Inkscape MCP edit has to go to be kept.
KEEP_EDITS = (
    "Files this server writes have no recipe, so review refuses them; make SVG "
    "changes you want to keep with joinery-paint operate_vector."
)


def check_svg(svg: str) -> None:
    """Refuse an SVG that would load anything from outside itself."""
    if _EXTERNAL.search(svg):
        raise ValueError(
            "Vector documents must be self-contained; external assets and scripts are not accepted."
        )


def vector_code(source: str, svg: str, export: str) -> str:
    """The recipe script: write the SVG, then export the PNG through Inkscape."""
    return (
        "import pathlib, subprocess\n"
        + f"pathlib.Path({source!r}).write_text({svg!r})\n"
        + f"subprocess.run(['inkscape', {source!r}, '--export-filename=' + {export!r}], check=True)\n"
    )


class Inkscape(ToolModule):
    id = "inkscape"
    name = "Inkscape"
    family = "joinery-paint"
    suffix = ".svg"
    authoring = True
    binary = "inkscape"
    desktop = "inkscape"
    ready_summary = "{name} {version} ready (offline worker)"
    needs_image = (
        "Inkscape runs in Joinery's offline worker. Rebuild the worker."
    )
    broken = "Inkscape failed its check"
    features = (
        Feature(
            "analysis",
            "analysis tools",
            needs_image="Inkscape analysis tools not installed; run Joinery setup.",
            needs_install="Inkscape analysis tools not installed; run Joinery setup.",
            broken="Inkscape analysis tools error",
        ),
    )
    checks = (
        ImageCheck(CREATIVE_WORKER),
        WorkerExec(
            CREATIVE_WORKER,
            ("inkscape", "--version"),
            expect=r"Inkscape \d",
            version=r"Inkscape (\d+\.\d+(?:\.\d+)?)",
            on_failure="needs_image",
            failure="Inkscape is missing from the creative worker; rebuild it",
        ),
        McpProbe(
            "joinery-paint",
            "document_capabilities",
            capability="inkscape",
            image=CREATIVE_WORKER,
        ),
        ImageCheck(INKSCAPE_WORKER, feature="analysis"),
        InstallCheck("inkscape", feature="analysis"),
        McpProbe(
            "inkscape",
            "inkscape_system",
            args=(("operation", "diagnostics"),),
            expect=r'"all_passed":\s*true',
            feature="analysis",
            image=INKSCAPE_WORKER,
        ),
    )

    #: The pinned MCP runs headless: its live tools need the desktop app over
    #: D-Bus (policy blocks them), and files it writes get no recipe.
    mcp_notes = (
        (
            "inkscape_extension",
            "Joinery: run_live needs the Inkscape desktop app and is blocked in the "
            "offline worker; use list, describe or run on a file. " + KEEP_EDITS,
        ),
        ("inkscape_vector", "Joinery: " + KEEP_EDITS),
        ("inkscape_file", "Joinery: " + KEEP_EDITS),
    )

    def replay_argv(self, script: str) -> list[str]:
        return ["python3", script]


MODULE = Inkscape()
