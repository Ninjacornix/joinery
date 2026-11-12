"""Godot's own check of a staged script, right after the write that changed it.

A parse error used to surface at import, precheck or submit, several steps after
the write that caused it. Now, when Godot's ``write_file`` (or a patch) stages a
``.gd``, a scene or resource with built-in scripts, or a ``.gdshader``, the
gateway checks it at once and puts the problems that write *added* into the tool
result, so the agent fixes them in the same step (ADR 0078; the before/after idea
is SWE-agent's windowed edit linting, the bounded retries aider's reflections).

How it checks, in the offline worker (never on the host: a project's ``@tool``
scripts and editor plugins run when Godot opens it):

- GDScript: Godot's own language server, from a headless editor kept running on
  a read-only mount of the stage. It answers in milliseconds once up, with
  warnings as well as errors and every ``class_name`` of the project. It is
  started when the stage first uses Godot and restarted when a script the agent
  writes uses a ``class_name`` it did not know.
- When the language server is not up in time or fails: ``--check-only`` on the
  written file (errors only, the first parse error only, not compared).
- Shaders: one headless run that sets the code on a ``Shader``, which the dummy
  renderer still parses; the old and new text in the same run.

Anything that goes wrong here leaves the write as it was, says it was not
checked, and precheck still runs every check at submit.
"""

from __future__ import annotations

import asyncio
import atexit
import base64
import contextlib
import json
import re
import shutil
import subprocess
import time
import uuid
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from joinery.worker_identity import docker_owner_args

#: The files a write is checked for.
SCRIPTS = frozenset({".gd"})
RESOURCES = frozenset({".tscn", ".tres"})
SHADERS = frozenset({".gdshader"})
CHECKED = SCRIPTS | RESOURCES | SHADERS
#: Writes of one file in a row that may add errors before the result says to stop
#: and rethink (aider's max_reflections).
MAX_REFLECTIONS = 3
#: Seconds a check waits for the language server to come up before it falls back.
READY_WAIT = 20.0
#: Seconds the language server is given to start at all.
START_TIMEOUT = 120.0
#: Seconds one check may take.
CHECK_TIMEOUT = 15.0
#: Problems listed per write; the rest are counted.
LISTED = 12
#: Text passed to a one-shot shader run on its command line, at most.
SHADER_LIMIT = 96_000
#: The worker image with Godot in it.
IMAGE = "joinery-mcp-worker"
#: Seconds a check container lives at most, should Joinery die without removing it.
LIFETIME = 6 * 60 * 60

ERROR = "error"
WARNING = "warning"


@dataclass(frozen=True)
class Diagnostic:
    """One problem Godot reports in a file."""

    file: str
    line: int
    severity: str
    message: str

    def text(self) -> str:
        return f"{self.file}:{self.line} {self.severity}: {self.message}"


def new_only(
    before: Iterable[Diagnostic], after: Iterable[Diagnostic]
) -> list[Diagnostic]:
    """The problems in ``after`` that ``before`` did not have.

    Lines move when an edit adds or removes some, so a problem is the same one
    when its file, severity and message are; each earlier one cancels one later.
    """
    left = Counter((d.file, d.severity, d.message) for d in before)
    out: list[Diagnostic] = []
    for diagnostic in after:
        key = (diagnostic.file, diagnostic.severity, diagnostic.message)
        if left[key]:
            left[key] -= 1
        else:
            out.append(diagnostic)
    return out


def checked(path: str) -> bool:
    return PurePosixPath(path).suffix in CHECKED


# --- built-in scripts in scenes and resources -----------------------------------

_SUB_RESOURCE = re.compile(
    r'^\[sub_resource type="GDScript" id="([^"]*)"', re.MULTILINE
)
_SOURCE = re.compile(r'^script/source = "', re.MULTILINE)


def builtin_scripts(text: str) -> list[tuple[str, int, str]]:
    """The GDScript sub-resources of a ``.tscn``/``.tres``: (id, the file line its
    source starts on, the source)."""
    found: list[tuple[str, int, str]] = []
    for match in _SUB_RESOURCE.finditer(text):
        end = text.find("\n[", match.end())
        section_end = len(text) if end < 0 else end
        source = _SOURCE.search(text, match.end(), section_end)
        if source is None:
            continue
        start = source.end()
        chars: list[str] = []
        index = start
        while index < len(text):
            char = text[index]
            if char == "\\" and index + 1 < len(text):
                chars.append(text[index + 1])
                index += 2
                continue
            if char == '"':
                break
            chars.append(char)
            index += 1
        found.append(
            (match.group(1), text.count("\n", 0, source.start()) + 1, "".join(chars))
        )
    return found


# --- class names ----------------------------------------------------------------

_CLASS_NAME = re.compile(r"^\s*(?:@\w+\s+)*class_name\s+([A-Za-z_]\w*)", re.MULTILINE)
_SKIP_DIRS = frozenset({".godot", ".git", ".joinery", "__pycache__"})


class ClassNames:
    """The ``class_name``s a stage's scripts declare, re-read only when a file changes."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._seen: dict[Path, tuple[int, int, frozenset[str]]] = {}

    def current(self) -> frozenset[str]:
        names: set[str] = set()
        seen: dict[Path, tuple[int, int, frozenset[str]]] = {}
        for path in self._scripts(self.root):
            try:
                stat = path.stat()
            except OSError:
                continue
            old = self._seen.get(path)
            if old and old[:2] == (stat.st_mtime_ns, stat.st_size):
                found = old[2]
            else:
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                found = frozenset(_CLASS_NAME.findall(text))
            seen[path] = (stat.st_mtime_ns, stat.st_size, found)
            names |= found
        self._seen = seen
        return frozenset(names)

    @staticmethod
    def _scripts(root: Path) -> Iterable[Path]:
        stack = [root]
        while stack:
            folder = stack.pop()
            try:
                entries = list(folder.iterdir())
            except OSError:
                continue
            for entry in entries:
                if entry.is_symlink():
                    continue
                if entry.is_dir():
                    if entry.name not in _SKIP_DIRS:
                        stack.append(entry)
                elif entry.suffix == ".gd":
                    yield entry


# --- where Godot runs -------------------------------------------------------------


class Runner(Protocol):
    """Where the checks' Godot runs: the stage, as that Godot sees it."""

    #: The stage's folder as Godot sees it ("/work" in the worker).
    root: str
    #: The Godot and Python commands there.
    godot: str
    python: str

    async def start(self) -> None: ...

    def port(self) -> int:
        """A free port there for a new language server."""
        ...

    def command(self, argv: list[str], *, stdin: bool = False) -> list[str]:
        """``argv`` as a host command that runs it there."""
        ...

    async def put(self, name: str, text: str) -> str:
        """Writes a helper file there (never in the stage); its path there."""
        ...

    async def close(self) -> None: ...


#: Check containers this process started and has not removed yet.
_RUNNING: set[str] = set()


@atexit.register
def _remove_running() -> None:
    for name in tuple(_RUNNING):
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(
                ["docker", "rm", "--force", name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
    _RUNNING.clear()


class Sandbox:
    """One offline worker container per stage, the stage mounted read-only."""

    root = "/work"
    godot = "godot"
    python = "python3"

    def __init__(self, stage: Path, cache: Path | None = None) -> None:
        self.stage = stage
        #: The project's own import cache, lent read-only to a stage that has none
        #: yet, so the editor starts without importing every asset again.
        self.cache = cache
        self.name = "joinery-check-" + uuid.uuid4().hex[:12]
        self._started = False
        self._port = 6004

    def port(self) -> int:
        # The container has its own network namespace; a restart takes the next
        # port so it never races the old server for it.
        self._port += 1
        return self._port

    async def start(self) -> None:
        if shutil.which("docker") is None:
            raise RuntimeError("Docker is not available")
        process = await asyncio.create_subprocess_exec(
            "docker",
            "run",
            "-d",
            "--rm",
            *docker_owner_args(),
            "--platform",
            "linux/amd64",
            "--network",
            "none",
            "--name",
            self.name,
            "--label",
            "joinery.scope=write-check",
            "--pids-limit",
            "256",
            "--memory",
            "2g",
            "-v",
            f"{self.stage.resolve()}:/work:ro",
            *self._cache_mount(),
            "-w",
            "/work",
            IMAGE,
            "sleep",
            str(LIFETIME),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _out, error = await asyncio.wait_for(process.communicate(), 60)
        if process.returncode != 0:
            raise RuntimeError(error.decode(errors="replace")[-300:].strip())
        self._started = True
        _RUNNING.add(self.name)

    def _cache_mount(self) -> list[str]:
        if (
            self.cache is None
            or not self.cache.is_dir()
            or (self.stage / ".godot").exists()
        ):
            return []
        return ["-v", f"{self.cache.resolve()}:/work/.godot:ro"]

    def command(self, argv: list[str], *, stdin: bool = False) -> list[str]:
        return ["docker", "exec", *(["-i"] if stdin else []), self.name, *argv]

    async def put(self, name: str, text: str) -> str:
        target = "/tmp/" + name
        process = await asyncio.create_subprocess_exec(
            *self.command(["sh", "-c", f"cat > {target}"], stdin=True),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(process.communicate(text.encode()), 30)
        if process.returncode != 0:
            raise RuntimeError("could not prepare the check")
        return target

    async def close(self) -> None:
        if not self._started:
            return
        self._started = False
        _RUNNING.discard(self.name)
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            process = await asyncio.create_subprocess_exec(
                "docker",
                "rm",
                "--force",
                self.name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(process.wait(), 20)


# --- the language server ----------------------------------------------------------

#: Runs inside the worker: starts Godot's language server on the stage and relays
#: its socket to stdin/stdout, so the host speaks LSP over the exec's pipes (the
#: worker has no network to reach the port from outside).
RELAY = r"""
import os, select, socket, subprocess, sys, time
godot, root, port = sys.argv[1], sys.argv[2], int(sys.argv[3])
os.environ.setdefault("HOME", "/tmp")
editor = subprocess.Popen(
    [godot, "--headless", "--editor", "--path", root, "--lsp-port", str(port)],
    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    start_new_session=True,
)
sock = None
deadline = time.monotonic() + 110
while time.monotonic() < deadline and editor.poll() is None:
    try:
        sock = socket.create_connection(("127.0.0.1", port), timeout=1)
        break
    except OSError:
        time.sleep(0.05)
if sock is None:
    editor.kill()
    sys.exit(3)
inp, out = sys.stdin.buffer.raw, sys.stdout.buffer.raw
try:
    while True:
        ready = select.select([inp, sock], [], [])[0]
        if inp in ready:
            data = os.read(inp.fileno(), 65536)
            if not data:
                break
            sock.sendall(data)
        if sock in ready:
            data = sock.recv(65536)
            if not data:
                break
            while data:
                data = data[out.write(data) :]
finally:
    editor.kill()
"""


class LanguageServer:
    """One Godot language server reached over the relay's pipes."""

    def __init__(self, runner: Runner) -> None:
        self.runner = runner
        self.port = runner.port()
        self._process: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task[None] | None = None
        self._waiting: dict[str, asyncio.Future[list[dict[str, Any]]]] = {}
        self._replies: dict[int, asyncio.Future[Any]] = {}
        self._versions: dict[str, int] = {}
        self._lock = asyncio.Lock()

    @property
    def alive(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def start(self) -> None:
        self._process = await asyncio.create_subprocess_exec(
            *self.runner.command(
                [
                    self.runner.python,
                    "-I",
                    "-c",
                    RELAY,
                    self.runner.godot,
                    self.runner.root,
                    str(self.port),
                ],
                stdin=True,
            ),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        self._reader = asyncio.create_task(self._read())
        root_uri = PurePosixPath(self.runner.root).as_uri()
        reply = asyncio.get_running_loop().create_future()
        self._replies[1] = reply
        self._send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"processId": None, "rootUri": root_uri, "capabilities": {}},
            }
        )
        await asyncio.wait_for(reply, START_TIMEOUT)
        self._send({"jsonrpc": "2.0", "method": "initialized", "params": {}})

    def uri(self, path: str) -> str:
        return PurePosixPath(self.runner.root, path).as_uri()

    async def diagnose(self, path: str, text: str) -> list[dict[str, Any]]:
        """The server's diagnostics for ``text`` as the content of ``path``."""
        uri = self.uri(path)
        async with self._lock:
            future = asyncio.get_running_loop().create_future()
            self._waiting[uri] = future
            version = self._versions.get(uri, 0) + 1
            self._versions[uri] = version
            if version == 1:
                self._send(
                    {
                        "jsonrpc": "2.0",
                        "method": "textDocument/didOpen",
                        "params": {
                            "textDocument": {
                                "uri": uri,
                                "languageId": "gdscript",
                                "version": version,
                                "text": text,
                            }
                        },
                    }
                )
            else:
                self._send(
                    {
                        "jsonrpc": "2.0",
                        "method": "textDocument/didChange",
                        "params": {
                            "textDocument": {"uri": uri, "version": version},
                            "contentChanges": [{"text": text}],
                        },
                    }
                )
            try:
                return await asyncio.wait_for(future, CHECK_TIMEOUT)
            finally:
                self._waiting.pop(uri, None)

    def _send(self, message: dict[str, Any]) -> None:
        assert self._process is not None and self._process.stdin is not None
        body = json.dumps(message).encode()
        self._process.stdin.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)

    async def _read(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        stream = self._process.stdout
        try:
            while True:
                header = await stream.readuntil(b"\r\n\r\n")
                length = next(
                    int(line.split(b":", 1)[1])
                    for line in header.split(b"\r\n")
                    if line.lower().startswith(b"content-length:")
                )
                message = json.loads(await stream.readexactly(length))
                if message.get("method") == "textDocument/publishDiagnostics":
                    params = message.get("params") or {}
                    waiting = self._waiting.get(str(params.get("uri", "")))
                    if waiting is not None and not waiting.done():
                        waiting.set_result(list(params.get("diagnostics") or []))
                elif "id" in message and "method" not in message:
                    reply = self._replies.pop(int(message["id"]), None)
                    if reply is not None and not reply.done():
                        reply.set_result(message.get("result"))
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ValueError):
            pass
        finally:
            gone = RuntimeError("Godot's language server stopped")
            for future in [*self._waiting.values(), *self._replies.values()]:
                if not future.done():
                    future.set_exception(gone)

    async def close(self) -> None:
        process, self._process = self._process, None
        if process is not None and process.returncode is None:
            with contextlib.suppress(OSError):
                if process.stdin is not None:
                    process.stdin.close()
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()
        if self._reader is not None:
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._reader


def _lsp_diagnostics(
    file: str, items: list[dict[str, Any]], offset: int = 0
) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    for item in items:
        severity = item.get("severity", 1)
        if severity not in {1, 2}:
            continue  # information and hints are not problems
        line = int(((item.get("range") or {}).get("start") or {}).get("line", 0))
        out.append(
            Diagnostic(
                file,
                line + 1 + offset,
                ERROR if severity == 1 else WARNING,
                " ".join(str(item.get("message", "")).split()),
            )
        )
    return out


# --- one-shot runs ------------------------------------------------------------------

#: Sets each shader's code on a Shader (the dummy renderer still parses it); a
#: MainLoop, so the project's autoloads never start.
SHADER_CHECK = """extends MainLoop

func _initialize() -> void:
	var items: Array = JSON.parse_string(Marshalls.base64_to_utf8(OS.get_cmdline_user_args()[0]))
	for index in items.size():
		printerr("JOINERY_SHADER %d" % index)
		var shader := Shader.new()
		shader.code = str(items[index])
		shader.get_shader_uniform_list()  # compiles it
	printerr("JOINERY_SHADER_END")

func _process(_delta: float) -> bool:
	return true
"""
_SHADER_ERROR = re.compile(
    r"^SHADER ERROR: (?P<message>.*)\n\s*at: .*?\((?P<file>[^()]*):(?P<line>\d+)\)",
    re.MULTILINE,
)
_PARSE_ERROR = re.compile(
    r"^(?:SCRIPT ERROR: )?Parse Error: (?P<message>.*)\n\s*at: .*?\((?P<file>res://[^()]*?):(?P<line>\d+)\)",
    re.MULTILINE,
)


async def _run(argv: list[str], timeout: float) -> str:
    process = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _out, error = await asyncio.wait_for(process.communicate(), timeout)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    return error.decode(errors="replace")


# --- the check ----------------------------------------------------------------------


@dataclass
class Outcome:
    """What one write's check found."""

    path: str
    #: The problems this write added.
    new: list[Diagnostic] = field(default_factory=list)
    #: "language-server", "check-only", "shader" or "" (not checked).
    backend: str = ""
    seconds: float = 0.0
    #: Why it was not checked, or how the check fell short.
    note: str = ""
    #: Writes of this file in a row that have added errors.
    streak: int = 0
    #: False when the problems are the file's, not only this write's.
    compared: bool = True

    @property
    def errors(self) -> int:
        return sum(d.severity == ERROR for d in self.new)

    @property
    def warnings(self) -> int:
        return sum(d.severity == WARNING for d in self.new)

    def message(self) -> str:
        """What the agent reads after the write ("" when there is nothing to say)."""
        if not self.backend:
            return (
                f"Godot could not check {self.path} now ({self.note}); precheck will."
                if self.note
                else ""
            )
        if not self.new:
            return ""
        what = "new problem" if len(self.new) == 1 else "new problems"
        head = (
            f"Godot check of {self.path}: {len(self.new)} {what} from this write"
            if self.compared
            else f"Godot check of {self.path}: {len(self.new)} problems in the file"
        )
        lines = [head + ". Fix them now, before anything else:"]
        lines += ["- " + d.text() for d in self.new[:LISTED]]
        if len(self.new) > LISTED:
            lines.append(f"- and {len(self.new) - LISTED} more")
        if self.note:
            lines.append(self.note)
        if self.streak >= MAX_REFLECTIONS:
            lines.append(
                f"This is write {self.streak} of {self.path} in a row that adds errors. "
                "Stop patching: read the whole file and rethink the change, or tell "
                "the user what is in the way."
            )
        return "\n".join(lines)


class WriteCheck:
    """The checks of one stage's script writes, with its warm language server."""

    def __init__(
        self, stage: Path, runner: Runner | None = None, *, cache: Path | None = None
    ) -> None:
        self.stage = stage
        self.runner: Runner = runner or Sandbox(stage, cache)
        self.classes = ClassNames(stage)
        self.streaks: dict[str, int] = {}
        self._runner_started: asyncio.Task[None] | None = None
        self._server: LanguageServer | None = None
        self._server_task: asyncio.Task[LanguageServer] | None = None
        self._server_classes: frozenset[str] = frozenset()
        self._server_failed = ""
        self._shader_script = ""
        #: The project's classes changed since the server started: restart it
        #: after this check, in the background.
        self._stale = False

    # -- lifecycle --

    def warm(self) -> None:
        """Start the language server in the background (the first check then waits
        less, or not at all)."""
        if self._server_task is None and not self._server_failed:
            self._server_task = asyncio.create_task(self._start_server())

    async def _runner(self) -> None:
        if self._runner_started is None:
            self._runner_started = asyncio.create_task(self.runner.start())
        await asyncio.shield(self._runner_started)

    async def _start_server(self) -> LanguageServer:
        await self._runner()
        classes = self.classes.current()
        server = LanguageServer(self.runner)
        try:
            await server.start()
        except BaseException:
            await server.close()
            raise
        self._server_classes = classes
        self._server = server
        return server

    async def _restart(self) -> None:
        old, self._server = self._server, None
        self._server_task = None
        if old is not None:
            await old.close()
        self._stale = False
        self.warm()

    async def _ready(self, wait: float) -> LanguageServer | None:
        if self._server_failed:
            return None
        self.warm()
        task = self._server_task
        assert task is not None
        try:
            return await asyncio.wait_for(asyncio.shield(task), wait)
        except TimeoutError:
            return None
        except Exception as error:  # noqa: BLE001 - any failure means "fall back"
            self._server_failed = str(error) or type(error).__name__
            return None

    async def close(self) -> None:
        task, self._server_task = self._server_task, None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
        if self._server is not None:
            await self._server.close()
            self._server = None
        if self._runner_started is not None:
            with contextlib.suppress(BaseException):
                await self._runner_started
        await self.runner.close()

    # -- checks --

    async def check(self, path: str, before: str | None, after: str) -> Outcome:
        """The problems the write of ``path`` (from ``before`` to ``after``) added."""
        started = time.monotonic()
        outcome = Outcome(path)
        suffix = PurePosixPath(path).suffix
        try:
            if suffix in SHADERS:
                await self._shader(outcome, before, after)
            elif suffix in SCRIPTS:
                await self._script(outcome, path, before, after)
            elif suffix in RESOURCES:
                await self._builtin(outcome, path, before, after)
        except Exception as error:  # noqa: BLE001 - a failed check never fails the write
            outcome.backend = ""
            outcome.note = (str(error) or type(error).__name__)[:200]
        outcome.seconds = round(time.monotonic() - started, 3)
        if self._stale:
            await self._restart()
        if outcome.backend:
            if outcome.errors:
                self.streaks[path] = self.streaks.get(path, 0) + 1
            else:
                self.streaks.pop(path, None)
            outcome.streak = self.streaks.get(path, 0)
        return outcome

    async def _server_for(self, text: str) -> LanguageServer | None:
        """The language server, restarted first when ``text`` uses a class name it
        has not seen (it reads the project's classes once, when it starts)."""
        current = self.classes.current()
        server = await self._ready(READY_WAIT)
        if server is None:
            return None
        missing = {
            name
            for name in current - self._server_classes
            if re.search(rf"\b{re.escape(name)}\b", text)
        }
        if missing:
            await self._restart()
            return await self._ready(START_TIMEOUT)
        if not server.alive:
            # It stopped: this check falls back, the next finds a new one.
            await self._restart()
            return None
        self._stale = current != self._server_classes
        return server

    async def _script(
        self, outcome: Outcome, path: str, before: str | None, after: str
    ) -> None:
        server = await self._server_for(after)
        if server is None:
            await self._check_only(outcome, path)
            return
        outcome.backend = "language-server"
        old = (
            _lsp_diagnostics(path, await server.diagnose(path, before))
            if before is not None
            else []
        )
        new = _lsp_diagnostics(path, await server.diagnose(path, after))
        outcome.new = new_only(old, new)

    async def _builtin(
        self, outcome: Outcome, path: str, before: str | None, after: str
    ) -> None:
        scripts = builtin_scripts(after)
        if not scripts:
            return
        server = await self._server_for("\n".join(s for _i, _l, s in scripts))
        if server is None:
            outcome.note = self._server_failed or "Godot's language server is starting"
            return
        outcome.backend = "language-server"
        old_scripts = {i: (line, s) for i, line, s in builtin_scripts(before or "")}
        found: list[Diagnostic] = []
        for identifier, line, source in scripts:
            virtual = f".godot/joinery-check/{_safe(path)}-{_safe(identifier)}.gd"
            new = _lsp_diagnostics(
                path, await server.diagnose(virtual, source), line - 1
            )
            if identifier in old_scripts:
                old_line, old_source = old_scripts[identifier]
                old = _lsp_diagnostics(
                    path, await server.diagnose(virtual, old_source), old_line - 1
                )
                new = new_only(old, new)
            found += new
        outcome.new = found

    async def _check_only(self, outcome: Outcome, path: str) -> None:
        await self._runner()
        output = await _run(
            self.runner.command(
                [
                    self.runner.godot,
                    "--headless",
                    "--path",
                    self.runner.root,
                    "--check-only",
                    "--script",
                    "res://" + path,
                ]
            ),
            CHECK_TIMEOUT * 2,
        )
        outcome.backend = "check-only"
        outcome.compared = False
        outcome.note = (
            "Errors only, and only the first parse error: Godot's language server "
            "was not ready."
        )
        outcome.new = [
            Diagnostic(
                match.group("file").removeprefix("res://"),
                int(match.group("line")),
                ERROR,
                match.group("message").strip(),
            )
            for match in _PARSE_ERROR.finditer(output)
        ]

    async def _shader(self, outcome: Outcome, before: str | None, after: str) -> None:
        texts = [after] if before is None else [after, before]
        payload = base64.b64encode(json.dumps(texts).encode()).decode()
        if len(payload) > SHADER_LIMIT:
            outcome.note = "the shader is too long to check here"
            return
        await self._runner()
        if not self._shader_script:
            self._shader_script = await self.runner.put(
                "joinery_shader_check.gd", SHADER_CHECK
            )
        output = await _run(
            self.runner.command(
                [
                    self.runner.godot,
                    "--headless",
                    "--path",
                    self.runner.root,
                    "--script",
                    self._shader_script,
                    "--",
                    payload,
                ]
            ),
            CHECK_TIMEOUT * 2,
        )
        if "JOINERY_SHADER_END" not in output:
            raise RuntimeError("the shader check did not finish")
        parts = re.split(r"^JOINERY_SHADER(?: (\d+)|_END)$", output, flags=re.MULTILINE)
        found: dict[int, list[Diagnostic]] = {}
        for index in range(1, len(parts) - 1, 2):
            if parts[index] is None:
                continue
            found[int(parts[index])] = [
                Diagnostic(
                    outcome.path,
                    int(match.group("line")),
                    ERROR,
                    match.group("message").strip(),
                )
                for match in _SHADER_ERROR.finditer(parts[index + 1])
            ]
        outcome.backend = "shader"
        outcome.new = new_only(found.get(1, []), found.get(0, []))


def _safe(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", text)
