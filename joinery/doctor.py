"""Check that a project's tools can actually do their work.

`joinery doctor` answers one question honestly: can this project do the work it
claims to? It renders the shared readiness core (`joinery.readiness`), the same
answer `joinery status`, the addon and every agent's `discover_tools` see.

Creative tools run in offline Docker workers, so doctor checks them there, never
on the host PATH: a Mac without Blender, Xvfb or ffmpeg installed is not missing
anything. The only host program a project needs is the Joinery editor itself,
which the Godot module checks. Desktop apps are only for "Open in …" and are
reported as information, never as a failure.

Doctor only looks; it never installs anything. When something is missing, the
advice is Joinery setup, which builds the workers and installs the pinned tools.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from joinery import readiness
from joinery.catalog import CONFIG_PATH

#: States that mean the project cannot do its work until someone acts.
PROBLEMS = frozenset({"needs_docker", "needs_image", "needs_licence", "broken"})


def _fill(
    project: Path, data: dict[str, Any], host: readiness.Host | None
) -> dict[str, Any]:
    """Run the checks the cache could not answer, one tool at a time."""
    tools = []
    for entry in data["tools"]:
        if entry.get("checking") and not entry.get("custom"):
            fresh = readiness.status(project, tool=entry["id"], refresh=True, host=host)
            entry = {**fresh["tools"][0], "selected": entry.get("selected", True)}
        tools.append(entry)
    return {**data, "tools": tools, "checking": any(t.get("checking") for t in tools)}


def readiness_report(
    config: Path = CONFIG_PATH,
    *,
    workers: bool = True,
    refresh: bool = False,
    host: readiness.Host | None = None,
) -> dict[str, Any]:
    """The readiness report doctor renders, with the checks the cache lacked run now.

    `problems` counts the tools that can't work until someone acts; the
    sidebar's /doctor shows each tool's line from it, problems included.
    """
    project = config.parent
    data = readiness.status(project, refresh=refresh, host=host)
    if workers and not refresh and data.get("checking"):
        data = _fill(project, data, host)
    tools = [t for t in data["tools"] if not t.get("custom")]
    data["problems"] = sum(1 for t in tools if t.get("state") in PROBLEMS)
    return data


def report(
    config: Path = CONFIG_PATH,
    *,
    workers: bool = True,
    refresh: bool = False,
    host: readiness.Host | None = None,
) -> tuple[str, int]:
    """Render readiness and an exit code: non-zero when a tool cannot work.

    Cached checks are reused; checks the cache cannot answer run now. With
    `workers=False` the cache alone answers (unknown checks show as checking).
    With `refresh`, every check runs again.
    """
    project = config.parent
    data = readiness_report(config, workers=workers, refresh=refresh, host=host)
    lines = ["Joinery doctor: can this project's tools do their work?"]
    tools = [t for t in data["tools"] if not t.get("custom")]
    if not data["tools"]:
        lines.append("  No tools are selected for this project.")
    lines += readiness.lines(data)
    problems = [t for t in tools if t.get("state") in PROBLEMS]
    states = {t.get("state") for t in problems}
    if "needs_docker" in states:
        lines.append(
            "\nStart Docker, then run joinery doctor again. Joinery's creative tools "
            "run offline in Docker workers, so nothing needs installing on this "
            "computer."
        )
    if states - {"needs_docker"}:
        lines.append(
            "\nTo fix this, run Joinery setup (or joinery bootstrap --project "
            f"{project}). It builds the offline workers and installs the pinned tools."
        )
    if not problems:
        if data.get("checking"):
            lines.append("\nSome checks have not run yet. Run joinery doctor again.")
        elif any(t.get("state") == "downloading" for t in tools):
            lines.append("\nJoinery setup is still preparing a worker.")
        else:
            lines.append("\nEverything this project needs is ready.")
    return "\n".join(lines), (1 if problems else 0)
