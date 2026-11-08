"""Run a creative tool, in the sandbox or on the host.

Recipes replay by driving real tools, and those tools live in the sandbox image
while Joinery's own Python lives outside it. So the host orchestrates and the
container executes: `joinery rebuild` resolves a recipe here and runs each tool
step through a `Runner`.

Two runners, same interface:

* `SandboxRunner` runs the tool in the image with `--network none`. This is the
  default and the one that actually guarantees Rule Zero, because a worker with
  no network cannot reach a generative service.
* `HostRunner` runs the tool from PATH, for live mode where the user wants to
  watch the work happen, and for a machine with the tools installed natively.

Nothing here knows what a recipe means; it only knows how to run a command
against a project directory.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

SANDBOX_IMAGE = "joinery-creative-worker"
#: Where the project is mounted inside the container.
WORKDIR = "/work"
DEFAULT_TIMEOUT = 900


@dataclass(frozen=True)
class Result:
    """What a tool run produced."""

    ok: bool
    stdout: str
    stderr: str
    #: Wall-clock seconds, so recipes can report honest latency.
    seconds: float

    @property
    def output(self) -> str:
        return (self.stdout + "\n" + self.stderr).strip()


class Runner(Protocol):
    @property
    def name(self) -> str:
        """Short label for reports, e.g. "sandbox"."""
        ...

    def available(self) -> bool:
        """Whether this runner can run anything at all."""
        ...

    def run(
        self, argv: list[str], project: Path, timeout: int = DEFAULT_TIMEOUT
    ) -> Result:
        """Run a tool command with `project` as the working directory."""
        ...


def _timed(cmd: list[str], timeout: int, cwd: Path | None = None) -> Result:
    start = time.monotonic()
    try:
        done = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            cwd=cwd,
        )
    except subprocess.TimeoutExpired:
        return Result(
            False, "", f"timed out after {timeout}s", time.monotonic() - start
        )
    except OSError as exc:
        return Result(False, "", str(exc), time.monotonic() - start)
    return Result(
        done.returncode == 0, done.stdout, done.stderr, time.monotonic() - start
    )


@dataclass(frozen=True)
class SandboxRunner:
    """Run the tool inside the sandbox image, offline."""

    name: str = "sandbox"
    image: str = SANDBOX_IMAGE

    def available(self) -> bool:
        if shutil.which("docker") is None:
            return False
        got = _timed(["docker", "image", "inspect", self.image], timeout=60)
        return got.ok

    def run(
        self, argv: list[str], project: Path, timeout: int = DEFAULT_TIMEOUT
    ) -> Result:
        # --network none is the point of the sandbox, not a detail.
        cmd = [
            "docker",
            "run",
            "--rm",
            "--platform",
            "linux/amd64",
            "--network",
            "none",
            "--label",
            "joinery.scope=" + os.environ.get("JOINERY_WORKER_SCOPE", "recipe-cli"),
            "-v",
            f"{project.resolve()}:{WORKDIR}",
            "-w",
            WORKDIR,
            self.image,
            *argv,
        ]
        return _timed(cmd, timeout)


@dataclass(frozen=True)
class HostRunner:
    """Run the tool from PATH, for live mode on the user's desktop."""

    name: str = "host"

    def available(self) -> bool:
        return True

    def run(
        self, argv: list[str], project: Path, timeout: int = DEFAULT_TIMEOUT
    ) -> Result:
        if shutil.which(argv[0]) is None:
            return Result(False, "", f"{argv[0]} not on PATH", 0.0)
        return _timed(argv, timeout, cwd=project)


def pick(prefer: str = "auto") -> Runner:
    """Choose a runner. `auto` takes the sandbox when it is built, else the host."""
    if prefer == "host":
        return HostRunner()
    sandbox = SandboxRunner()
    if prefer == "sandbox":
        return sandbox
    return sandbox
