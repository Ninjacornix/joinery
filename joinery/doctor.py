"""Check that the programs a project's tools need are actually installed.

`joinery doctor` answers one question honestly: can this project do the work it
claims to? A project enables tools in its `joinery.toml`, and each of those
drives a real program. If the program is missing, the MCP will fail at the first
op, so it is better to say so up front than to discover it mid-transaction.

Runs wherever Joinery itself runs: on the host, which is also where live mode
watches a tool work. It only looks for programs and reads their versions; it
never installs anything, since installing is the sandbox image's job, and when
something is missing it points at that image. The image deliberately carries
the creative tools rather than Joinery's Python environment, so running the
MCP servers inside it is a separate step (see ADR 0008).
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from joinery.catalog import (
    CONFIG_PATH,
    Catalog,
    load_catalog,
    load_project_tools,
    selection_ids,
)


@dataclass(frozen=True)
class Probe:
    """How to find one program and ask its version."""

    #: Executable name, looked up on PATH.
    command: str
    #: Argument that makes it print a version. Empty means do not ask.
    version_flag: str = "--version"
    #: Set when the program is not expected in the sandbox image.
    byo: bool = False
    note: str = ""


# Keyed by catalog tool id. This is about the local machine, not policy, so it
# lives here rather than in policy/preferred_tools.toml.
PROBES: dict[str, Probe] = {
    "godot": Probe("godot"),
    "blender": Probe("blender"),
    "krita": Probe("krita"),
    "supercollider": Probe("sclang", version_flag="-v"),
    "surge-xt": Probe(
        "surge-xt-cli",
        version_flag="",
        byo=True,
        note="not packaged for Debian; the sandbox synthesises with SuperCollider",
    ),
    "material-maker": Probe("material-maker", version_flag=""),
    "gimp": Probe("gimp", version_flag="--version"),
    "inkscape": Probe("inkscape"),
    "audacity": Probe("audacity"),
    "musescore": Probe("mscore"),
    "reaper": Probe("reaper", version_flag="", byo=True, note="your own licence"),
    "aseprite": Probe("aseprite", version_flag="", byo=True, note="your own licence"),
}

# Programs every project needs, whatever tools it enabled.
CORE: dict[str, Probe] = {
    "ffmpeg": Probe("ffmpeg"),
    # Xvfb has no version flag, so finding it on PATH is the whole check.
    "xvfb": Probe("Xvfb", version_flag=""),
}


@dataclass(frozen=True)
class Finding:
    """What we learned about one program."""

    name: str
    ok: bool
    detail: str
    required: bool

    def line(self) -> str:
        mark = "ok  " if self.ok else ("MISS" if self.required else "--  ")
        return f"  [{mark}] {self.name:<16} {self.detail}"


def _version(probe: Probe) -> str:
    path = shutil.which(probe.command)
    if path is None:
        return ""
    if not probe.version_flag:
        return path
    try:
        done = subprocess.run(
            [path, probe.version_flag],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (subprocess.SubprocessError, OSError):
        return path
    out = (done.stdout or done.stderr).strip().splitlines()
    return out[0].strip() if out else path


def _check(name: str, probe: Probe, required: bool) -> Finding:
    found = _version(probe)
    if found:
        return Finding(name, True, found, required)
    missing = f"{probe.command} not on PATH"
    if probe.note:
        missing += f" ({probe.note})"
    # A bring-your-own tool is never "missing" in the image's sense.
    return Finding(name, False, missing, required and not probe.byo)


def findings(catalog: Catalog, config: Path = CONFIG_PATH) -> list[Finding]:
    """Check the core programs plus every tool this project enabled."""
    results = [_check(name, probe, True) for name, probe in CORE.items()]
    enabled = selection_ids(catalog, load_project_tools(config))
    for tool_id in enabled:
        probe = PROBES.get(tool_id)
        if probe is None:
            results.append(
                Finding(tool_id, False, "no probe defined for this tool", False)
            )
            continue
        results.append(_check(tool_id, probe, True))
    return results


def report(config: Path = CONFIG_PATH) -> tuple[str, int]:
    """Render the findings and an exit code: non-zero when something required is absent."""
    catalog = load_catalog()
    results = findings(catalog, config)
    lines = ["Joinery doctor: programs this project's tools need"]
    lines += [f.line() for f in results]
    missing = [f.name for f in results if f.required and not f.ok]
    byo = [f.name for f in results if not f.ok and not f.required]
    if byo:
        lines.append(
            "\nNot found, and not expected in the sandbox image (supply your own): "
            + ", ".join(byo)
        )
    if missing:
        lines.append(
            "\nMissing: "
            + ", ".join(missing)
            + "\nBuild or enter the sandbox image, which installs them:"
            "\n  docker build -t joinery-sandbox sandbox/"
            "\n  docker run --rm -it --network none -v \"$PWD\":/work joinery-sandbox"
        )
    else:
        lines.append("\nEverything this project needs is present.")
    return "\n".join(lines), (1 if missing else 0)
