"""Run blueprint projects in stock Godot, headless, for tests.

Every helper fails loudly: a non-zero exit, a missing `<name>: ok` marker, or
any `SCRIPT ERROR` / `ERROR:` line in the output is a test failure.
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest

from joinery.catalog import ROOT, load_catalog
from joinery.installer import Selection
from joinery.project import create_project

STOCK_GODOT = ROOT / ".tools/Godot.app/Contents/MacOS/Godot"
#: `{settings}` becomes a settings file of the project's own, outside it and
#: outside `user://`, so no two tests ever share one.
SETTINGS_ARG = "--settings-path={settings}"
TEST_ARGS = ("--autostart", SETTINGS_ARG)
_ERROR = re.compile(r"^(SCRIPT ERROR|ERROR:|USER SCRIPT ERROR|USER ERROR)", re.MULTILINE)
_OK = re.compile(r"^\S.*: ok\s*$", re.MULTILINE)


def godot_binary() -> Path | None:
    """`JOINERY_TEST_GODOT`, then the stock binary on disk, else None."""
    configured = os.environ.get("JOINERY_TEST_GODOT")
    for candidate in (configured, str(STOCK_GODOT)):
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return Path(candidate)
    return None


needs_godot = pytest.mark.skipif(
    godot_binary() is None, reason="needs a Godot 4.7 binary (JOINERY_TEST_GODOT)"
)


@dataclass(frozen=True)
class Completed:
    args: list[str]
    returncode: int
    output: str


def make_project(tmp_path: Path, blueprint: str, features: Sequence[str] = ()) -> Path:
    """A fresh Joinery project made from a blueprint and features."""
    project = tmp_path / ("game-" + "-".join((blueprint, *features)))[:80]
    create_project(
        project,
        "Blueprint Test",
        Selection(["godot"], []),
        load_catalog(),
        blueprint=blueprint,
        features=features,
    )
    return project


def _godot() -> Path:
    binary = godot_binary()
    if binary is None:
        pytest.skip("needs a Godot 4.7 binary (JOINERY_TEST_GODOT)")
    return binary


def godot_env(project: Path) -> dict[str, str]:
    """The environment for a Godot run on `project`, with a HOME of its own.

    Every test project is called "Blueprint Test", so with the real HOME they
    would all share one `user://` folder (settings, saves, logs) and one set
    of editor settings, in the user's own Application Support. Sharing those
    with other Godot processes also makes the stock editor crash now and then
    while it shuts down after `--import`. A HOME beside the project keeps
    `user://` per project (it persists across that project's runs), leaves
    the user's folders alone, and lets projects run in parallel.
    """
    home = project.parent / f".home-{project.name}"
    home.mkdir(parents=True, exist_ok=True)
    return {
        **os.environ,
        "GODOT_SILENCE_ROOT_WARNING": "1",
        "HOME": str(home),
        "XDG_DATA_HOME": str(home / "share"),
        "XDG_CONFIG_HOME": str(home / "config"),
        "XDG_CACHE_HOME": str(home / "cache"),
    }


def settings_file(project: Path) -> Path:
    """The absolute settings file `{settings}` stands for in user args."""
    return project.parent / f".settings-{project.name}.cfg"


def _user_args(project: Path, user_args: Sequence[str]) -> list[str]:
    return [arg.replace("{settings}", str(settings_file(project))) for arg in user_args]


def _run(args: list[str], timeout: float, project: Path) -> Completed:
    env = godot_env(project)
    process = subprocess.run(
        args, capture_output=True, text=True, timeout=timeout, env=env, check=False
    )
    return Completed(args, process.returncode, process.stdout + process.stderr)


def _check(result: Completed, marker: str | None) -> Completed:
    problems: list[str] = []
    if result.returncode != 0:
        problems.append(f"exit code {result.returncode}")
    errors = [line for line in result.output.splitlines() if _ERROR.match(line)]
    if errors:
        problems.append("errors: " + "; ".join(errors[:5]))
    if marker is not None:
        found = (
            f"{marker}: ok" in result.output
            if marker
            else _OK.search(result.output) is not None
        )
        if not found:
            problems.append(f"missing marker {marker + ': ok' if marker else '<name>: ok'}")
    if problems:
        tail = "\n".join(result.output.splitlines()[-60:])
        raise AssertionError(
            f"{' '.join(result.args)}\n{', '.join(problems)}\n--- output ---\n{tail}"
        )
    return result


def import_project(project: Path, timeout: float = 180) -> Completed:
    """`--headless --import` once per project (later calls are skipped)."""
    stamp = project / ".godot" / ".joinery-imported"
    if stamp.is_file():
        return Completed([], 0, "")
    result = _run(
        [str(_godot()), "--headless", "--path", str(project), "--import"], timeout, project
    )
    _check(result, None)
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text("")
    return result


def run_godot(
    project: Path,
    script: str,
    user_args: Sequence[str] = (),
    timeout: float = 120,
    *,
    marker: str | None = "",
) -> Completed:
    """Run a `SceneTree` script; it must print `<marker>: ok` (any name when "")."""
    import_project(project)
    args = [
        str(_godot()), "--headless", "--path", str(project), "--fixed-fps", "60",
        "--script", script, "--", *_user_args(project, user_args),
    ]
    return _check(_run(args, timeout, project), marker)


def boot(
    project: Path,
    frames: int = 120,
    user_args: Sequence[str] = TEST_ARGS,
    timeout: float = 120,
    *,
    marker: str | None = None,
) -> Completed:
    """Run the main scene for `frames` frames; no errors allowed."""
    import_project(project)
    args = [
        str(_godot()), "--headless", "--path", str(project), "--fixed-fps", "60",
        "--quit-after", str(frames), "--", *_user_args(project, user_args),
    ]
    return _check(_run(args, timeout, project), marker)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def run_pair(
    project: Path,
    host_args: Sequence[str],
    client_args: Sequence[str],
    timeout: float = 90,
    *,
    script: str | None = None,
    host_marker: str | None = "",
    client_marker: str | None = "",
) -> tuple[Completed, Completed]:
    """Host and client in two processes on a free localhost port.

    `{port}` in any argument is replaced by the port. With `script`, both run
    that SceneTree script; otherwise both run the main scene.
    """
    import_project(project)
    port = str(free_port())
    base = [str(_godot()), "--headless", "--path", str(project), "--fixed-fps", "60"]
    if script:
        base += ["--script", script]

    def command(extra: Sequence[str]) -> list[str]:
        return [*base, "--", *(arg.replace("{port}", port) for arg in _user_args(project, extra))]

    env = godot_env(project)
    host_cmd, client_cmd = command(host_args), command(client_args)
    host = subprocess.Popen(
        host_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env
    )
    try:
        client = subprocess.run(
            client_cmd, capture_output=True, text=True, timeout=timeout, env=env, check=False
        )
        host_out, _ = host.communicate(timeout=timeout)
    finally:
        if host.poll() is None:
            host.kill()
            host.communicate()
    host_result = Completed(host_cmd, host.returncode, host_out)
    client_result = Completed(client_cmd, client.returncode, client.stdout + client.stderr)
    return _check(host_result, host_marker), _check(client_result, client_marker)
