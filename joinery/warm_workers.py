"""Reuse only a supervised MCP's project-bound offline container, never its app."""

from __future__ import annotations

import asyncio
import atexit
import contextlib
import os
import subprocess
import time
import uuid
from pathlib import Path

from joinery.runner import Result
from joinery.worker_identity import docker_owner_args

# Each command gets a new process group, which is reaped even if an application
# leaves helper processes behind. Source documents are reloaded on every call.
JOB = """export DISPLAY="${DISPLAY:-:99}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/tmp/runtime-joiner}"
deadline=$((SECONDS+10))
while [[ ! -S /tmp/.X11-unix/X${DISPLAY##*:} ]]; do
 if (( SECONDS >= deadline )); then echo 'Worker display did not become ready' >&2; exit 1; fi
 sleep .05
done
job=0
trap 'if (( job > 0 )); then kill -KILL -- "-$job" 2>/dev/null || true; fi' EXIT
setsid -- "$@" &
job=$!
wait "$job"
exit "$?"
"""
_containers: dict[tuple[str, str, bool, str], str] = {}
_locks: dict[tuple[str, str, bool, str], asyncio.Lock] = {}


def _cleanup() -> None:
    for name in tuple(_containers.values()):
        with contextlib.suppress(OSError, subprocess.TimeoutExpired):
            subprocess.run(
                ["docker", "rm", "--force", name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
    _containers.clear()


atexit.register(_cleanup)


async def _command(argv: list[str], timeout: int) -> Result:
    start = time.monotonic()
    process = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        output, error = await asyncio.wait_for(process.communicate(), timeout)
        return Result(
            process.returncode == 0,
            output.decode(errors="replace"),
            error.decode(errors="replace"),
            time.monotonic() - start,
        )
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


async def execute(
    argv: list[str], project: Path, timeout: int, readonly: bool, image: str
) -> Result:
    started = time.monotonic()
    scope = os.environ["JOINERY_WORKER_SCOPE"]
    if len(scope) != 32 or any(c not in "0123456789abcdef" for c in scope):
        raise ValueError("Invalid supervised native worker scope.")
    key = (scope, str(project.resolve()), readonly, image)
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        name = _containers.get(key)
        if name is None:
            name = "joinery-warm-" + uuid.uuid4().hex
            # Register before startup: cancellation must also reap a partially
            # started container. The supervisor independently owns this label.
            _containers[key] = name
            try:
                ready = await _command(
                    [
                        "docker",
                        "run",
                        "-d",
                        *docker_owner_args(),
                        "--platform",
                        "linux/amd64",
                        "--network",
                        "none",
                        "--name",
                        name,
                        "--label",
                        "joinery.scope=" + scope,
                        "--pids-limit",
                        "512",
                        "--memory",
                        "4g",
                        "-v",
                        f"{project.resolve()}:/work" + (":ro" if readonly else ""),
                        "-w",
                        "/work",
                        image,
                        "sleep",
                        "infinity",
                    ],
                    30,
                )
                if not ready.ok:
                    raise RuntimeError(
                        "Could not start the offline worker: " + ready.output[-1000:]
                    )
            except BaseException:
                _containers.pop(key, None)
                await _command(["docker", "rm", "--force", name], 10)
                raise
        try:
            got = await _command(
                [
                    "docker",
                    "exec",
                    "-w",
                    "/work",
                    name,
                    "bash",
                    "--noprofile",
                    "--norc",
                    "-c",
                    JOB,
                    "--",
                    *argv,
                ],
                timeout,
            )
            if not got.ok:
                _containers.pop(key, None)
                await _command(["docker", "rm", "--force", name], 10)
            return Result(got.ok, got.stdout, got.stderr, time.monotonic() - started)
        except BaseException:
            _containers.pop(key, None)
            await _command(["docker", "rm", "--force", name], 10)
            raise
