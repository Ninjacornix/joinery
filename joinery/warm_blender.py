"""One Blender kept running per warm worker container, for typed operations.

Starting Blender is most of a small typed operation's time. Inside a warm
worker container (``joinery.warm_workers``) one Blender process runs a small
request loop on its stdin: each request is a recipe script (``blender
--background --factory-startup --python recipes/x.py``) or a document read
(``blender --background art/x.blend --python-expr …``, the recipe fingerprint)
and gets back the exit code and everything the script wrote to stdout and
stderr, as the cold command would have printed it.

It must give what a fresh Blender gives:

* every request starts from Blender's factory state
  (``read_factory_settings``), as ``--factory-startup`` does, or from the opened
  document, and runs with fresh globals, ``__name__ == "__main__"`` and the
  script's own ``__file__``;
* an error that reaches the top of a recipe exits 1 when the recipe installed
  its fail-on-error hook (``joinery.tools.blender.FAIL_ON_ERROR``), and 0 when
  it did not, as Blender itself does;
* the file descriptors 1 and 2 are captured, so Blender's own C-level messages
  are part of the output too.

On any doubt Joinery uses a fresh process instead: the process is restarted
after ``MAX_OPS`` requests, after ``IDLE_SECONDS`` without one, after a request
that left a handler or a registered class behind, after a failed request, and
whenever it dies, stops answering or answers out of protocol. A request the
warm process could not answer is run again cold (``None`` from :meth:`run`).
``JOINERY_WARM_BLENDER=0`` turns it off.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from typing import Any

from joinery.runner import Result

#: Requests one process serves before a fresh one starts.
MAX_OPS = 25
#: Seconds without a request before the process is stopped.
IDLE_SECONDS = 300.0
#: Seconds a new process has to say it is ready.
START_SECONDS = 90.0
#: Each reply is one line starting with this.
MARK = "JOINERY_WARM "
#: Characters of a request's stdout or stderr kept (the tail), as large as a
#: cold run's output ever needs to be parsed.
OUTPUT_CHARS = 4_000_000
#: A reply line may be this long.
LINE_LIMIT = 2 * OUTPUT_CHARS + 65_536

# The request loop, run as ``--python-expr`` (one argv element: no file in the
# project). It speaks only on a duplicate of the original stdout.
SERVER = r"""
import builtins, json, os, sys, tempfile, traceback
import bpy

_MARK = "JOINERY_WARM "
_LIMIT = 4000000
_proto = os.fdopen(os.dup(1), "w", buffering=1)
_fd1, _fd2 = os.dup(1), os.dup(2)
_hook = sys.excepthook
_argv = list(sys.argv)

def _handlers():
    found = {}
    for name in dir(bpy.app.handlers):
        value = getattr(bpy.app.handlers, name)
        if isinstance(value, list):
            # Which functions, not how many times: Blender's own pose library
            # adds its load handlers again on every factory reset.
            found[name] = sorted({
                getattr(f, "__module__", "?") + "." + getattr(f, "__qualname__", "?")
                for f in value
            })
    return found

_base_handlers = _handlers()
_base_types = set(dir(bpy.types))

def _reply(data):
    _proto.write(_MARK + json.dumps(data) + "\n")
    _proto.flush()

def _read(handle):
    handle.seek(0)
    text = handle.read().decode("utf-8", "replace")
    handle.close()
    return text[-_LIMIT:]

def _run(request):
    os.chdir(os.environ.get("JOINERY_WARM_WORKDIR", "/work"))
    out, err = tempfile.TemporaryFile(), tempfile.TemporaryFile()
    sys.stdout.flush(); sys.stderr.flush()
    os.dup2(out.fileno(), 1)
    os.dup2(err.fileno(), 2)
    code = 0
    try:
        sys.argv = list(request.get("argv") or _argv)
        try:
            if request.get("open"):
                bpy.ops.wm.open_mainfile(filepath=request["open"])
            else:
                bpy.ops.wm.read_factory_settings(use_empty=False)
            name = request.get("script") or "<string>"
            text = open(name).read() if request.get("script") else request["expr"]
            scope = {"__name__": "__main__", "__builtins__": builtins}
            if request.get("script"):
                scope["__file__"] = name
            exec(compile(text, name, "exec"), scope)
        except SystemExit as stop:
            if stop.code is None:
                code = 0
            elif isinstance(stop.code, int):
                code = stop.code
            else:
                print(stop.code, file=sys.stderr)
                code = 1
        except BaseException:
            traceback.print_exc()
            # A recipe's fail-on-error hook exits 1; Blender alone exits 0.
            code = 1 if sys.excepthook is not _hook else 0
    finally:
        sys.stdout.flush(); sys.stderr.flush()
        os.dup2(_fd1, 1)
        os.dup2(_fd2, 2)
        sys.excepthook = _hook
        sys.argv = list(_argv)
    now = _handlers()
    changed = sorted(k + ":" + ",".join(sorted(set(now.get(k, [])) ^ set(_base_handlers.get(k, []))))
                     for k in set(now) | set(_base_handlers)
                     if now.get(k) != _base_handlers.get(k))
    changed += sorted(set(dir(bpy.types)) ^ _base_types)
    return {"code": code, "stdout": _read(out), "stderr": _read(err),
            "clean": not changed, "changed": changed[:20]}

_reply({"ready": True, "version": bpy.app.version_string})
for _line in sys.stdin:
    if not _line.strip():
        continue
    try:
        _request = json.loads(_line)
    except ValueError:
        _reply({"error": "bad request"})
        continue
    if _request.get("quit"):
        break
    _reply(_run(_request))
_proto.flush()
os._exit(0)
"""

# Like warm_workers.JOB, but the program keeps the request pipe as its stdin
# (a background job would otherwise read /dev/null).
SERVE = """export DISPLAY="${DISPLAY:-:99}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/tmp/runtime-joiner}"
job=0
trap 'if (( job > 0 )); then kill -KILL -- "-$job" 2>/dev/null || true; fi' EXIT
setsid -- "$@" <&0 &
job=$!
wait "$job"
exit "$?"
"""


def enabled() -> bool:
    return os.environ.get("JOINERY_WARM_BLENDER", "1") not in {"0", "false", "no"}


def request_for(argv: list[str]) -> dict[str, Any] | None:
    """The warm request for a cold Blender command, or None when it must run cold.

    Only the two command shapes Joinery's typed operations and fingerprints use
    are taken; anything else (a batch driver with its own arguments, a render,
    another program) runs as before."""
    if len(argv) != 5 or argv[0] != "blender" or argv[1] != "--background":
        return None
    if argv[2] == "--factory-startup" and argv[3] == "--python":
        script = argv[4]
        if script.endswith(".py") and not script.startswith(("/", "-")):
            return {"script": script}
        return None
    if argv[2] == "--factory-startup" and argv[3] == "--python-expr":
        return {"expr": argv[4]}
    if argv[3] == "--python-expr" and argv[2].endswith(".blend"):
        if argv[2].startswith(("/", "-")) or ".." in argv[2].split("/"):
            return None
        return {"open": argv[2], "expr": argv[4]}
    return None


class WarmBlender:
    """One Blender process in one warm container."""

    def __init__(self, container: str, command: list[str] | None = None) -> None:
        self.container = container
        #: How the request loop starts; tests run it without Docker.
        self.command = command or [
            "docker",
            "exec",
            "-i",
            "-w",
            "/work",
            container,
            "bash",
            "--noprofile",
            "--norc",
            "-c",
            SERVE,
            "--",
            "blender",
            "--background",
            "--factory-startup",
            "--python-expr",
            SERVER,
        ]
        self.process: asyncio.subprocess.Process | None = None
        self.ops = 0
        self.last = 0.0
        #: Processes started, requests answered, and requests that left Blender
        #: changed (each followed by a fresh process).
        self.starts = 0
        self.served = 0
        self.unclean = 0
        #: What the last such request changed.
        self.changed: list[str] = []
        self._idle: asyncio.TimerHandle | None = None

    @property
    def alive(self) -> bool:
        return self.process is not None and self.process.returncode is None

    async def start(self) -> None:
        self.process = await asyncio.create_subprocess_exec(
            *self.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            limit=LINE_LIMIT,
        )
        self.ops = 0
        self.starts += 1
        try:
            ready = await self._reply(START_SECONDS)
        except TimeoutError:
            ready = None
        if ready is None or not ready.get("ready"):
            await self.close()
            raise RuntimeError("The warm Blender did not start.")
        self.last = time.monotonic()

    async def _reply(self, timeout: float) -> dict[str, Any] | None:
        """The next reply; None when the process ended or spoke out of protocol.
        Raises TimeoutError when none came in time."""
        process = self.process
        if process is None or process.stdout is None:
            return None
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError
            try:
                line = await asyncio.wait_for(process.stdout.readline(), left)
            except (ValueError, asyncio.LimitOverrunError):
                return None
            if not line:
                return None
            text = line.decode("utf-8", "replace")
            if text.startswith(MARK):
                try:
                    data = json.loads(text[len(MARK) :])
                except ValueError:
                    return None
                return data if isinstance(data, dict) else None

    async def run(self, request: dict[str, Any], timeout: float) -> Result | None:
        """Run one request; None when the warm process could not answer it (run
        it cold). Raises TimeoutError, as a cold run does, when it takes longer
        than ``timeout``; the caller then removes the container with the hung
        process in it."""
        started = time.monotonic()
        self._cancel_idle()
        if self.alive and (self.ops >= MAX_OPS or started - self.last > IDLE_SECONDS):
            await self.close()
        try:
            if not self.alive:
                await self.start()
            process = self.process
            assert process is not None and process.stdin is not None
            process.stdin.write((json.dumps(request) + "\n").encode())
            await process.stdin.drain()
        except (OSError, RuntimeError, ConnectionError):
            await self.close()
            return None
        try:
            reply = await self._reply(max(timeout - (time.monotonic() - started), 1.0))
        except BaseException:
            await self._abandon()
            raise
        self.ops += 1
        self.last = time.monotonic()
        if (
            reply is None
            or not isinstance(reply.get("code"), int)
            or not isinstance(reply.get("stdout"), str)
            or not isinstance(reply.get("stderr"), str)
        ):
            await self.close()
            return None
        result = Result(
            reply["code"] == 0,
            reply["stdout"],
            reply["stderr"],
            time.monotonic() - started,
        )
        self.served += 1
        if not result.ok or reply.get("clean") is not True:
            # A failed request, or one that left Blender changed: fresh next time.
            if reply.get("clean") is not True:
                self.unclean += 1
                self.changed = [str(c) for c in reply.get("changed") or []][:20]
            await self.close()
        else:
            self._schedule_idle()
        return result

    def _cancel_idle(self) -> None:
        if self._idle is not None:
            self._idle.cancel()
            self._idle = None

    def _schedule_idle(self) -> None:
        loop = asyncio.get_running_loop()
        self._idle = loop.call_later(
            IDLE_SECONDS, lambda: loop.create_task(self.close())
        )

    async def _abandon(self) -> None:
        """Drop a process that is mid-request: no quit, it is not listening."""
        self._cancel_idle()
        process, self.process = self.process, None
        if process is not None and process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            with contextlib.suppress(Exception):
                await process.wait()

    async def close(self) -> None:
        self._cancel_idle()
        process, self.process = self.process, None
        if process is None:
            return
        if process.returncode is None:
            with contextlib.suppress(OSError, RuntimeError, ConnectionError):
                if process.stdin is not None:
                    process.stdin.write(b'{"quit": true}\n')
                    await process.stdin.drain()
                    process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), 5)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                await process.wait()
