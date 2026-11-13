"""Tool readiness: the one place that decides whether a tool can do its work.

Every front end reads this: `joinery status`, `joinery doctor`, the addon's
setup step and `/tools`, and the gateway's `discover_tools`. It lives in the
shared core, so Claude Code, Codex, Gemini CLI and opencode all see the same
answer for the same project.

Each tool module (`joinery.tools`) declares its checks as data. This module
runs them in tiers, cheapest first:

- tier 0 (always live): Docker, worker images, setup progress, installed pins;
- tiers 1 and 2: a command inside the offline worker (`--network none`);
- tier 3: a read-only call on the tool's MCP server;
- deep: only with `deep=True`.

Tiers 1 to 3 are cached in `~/.cache/joinery/readiness.json`, keyed by the
worker image id (and the installed source tree for MCP probes), so a rebuilt
image or reinstalled server is checked again. Without `refresh`, a cache miss
is reported as `checking: true` instead of running the probe inline.

Every report is also kept as a snapshot (`status.json`). `current()` answers
from it while it is fresh, so screens that poll never start Docker, and
`cached_tools()` reads it without asking anything at all (the asset workspace,
which refreshes every two seconds).

A tool's state is the worst state among its required checks. Features (such as
Blender's MPFB2 humans) report their own state and never change the tool's.
The desktop app ("Open in ...") is reported separately and never affects state.

Screens show the states from `SURFACE` (ready, preparing, needs_docker,
needs_licence, unavailable) with `STATUS_LABELS`, the plain `summary`
sentence, at most one `action` ({id, label}: start_docker, rebuild,
check_again, get) and `checked_at`. ADR 0030 records the rules.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from joinery import tools as tool_modules
from joinery.catalog import (
    load_catalog,
    load_project_tools,
    removed_notices,
    selection_ids,
)
from joinery.tools.base import (
    Check,
    Feature,
    HostExec,
    ImageCheck,
    InstallCheck,
    McpProbe,
    ToolModule,
    WorkerExec,
)

State = Literal[
    "ready", "downloading", "needs_docker", "needs_image", "needs_licence", "broken"
]
STATES: tuple[State, ...] = (
    "ready",
    "downloading",
    "needs_image",
    "needs_licence",
    "broken",
    "needs_docker",
)
#: Worse states rank higher; a tool shows its worst required state.
RANK: dict[str, int] = {state: rank for rank, state in enumerate(STATES)}
#: The state every screen shows for each internal state (design addendum S).
SURFACE: dict[str, str] = {
    "ready": "ready",
    "downloading": "preparing",
    "needs_docker": "needs_docker",
    "needs_image": "unavailable",
    "needs_licence": "needs_licence",
    "broken": "unavailable",
}
#: The words for each screen state, the same everywhere.
STATUS_LABELS: dict[str, str] = {
    "ready": "Ready",
    "preparing": "Refreshing…",
    "needs_docker": "Needs Docker",
    "needs_licence": "Needs your licence",
    "unavailable": "Not available yet",
}
#: The one thing a person can do about an internal state, as an action id.
ACTION_FOR_STATE: dict[str, str] = {
    "needs_docker": "start_docker",
    "needs_image": "rebuild",
    "needs_licence": "get",
    "broken": "check_again",
}
#: Button text for each action id.
ACTION_LABELS: dict[str, str] = {
    "start_docker": "Start Docker",
    "rebuild": "Refresh tools",
    "check_again": "Check again",
    "get": "Get it",
}
#: The one vocabulary for what Refresh tools fixes. Screens and agents read these
#: words; none of them names a worker, an image, bootstrap, MCP or a revision.
REFRESH_AFTER_UPDATE = "{names} {verb} a refresh after the Joinery update."
REFRESH_FIRST = "{names} {verb} a refresh before {pronoun} can work."
#: Tier-3 results expire after a day even if nothing else changed.
MCP_TTL_SECONDS = 24 * 3600
#: How long a whole report stands before `current()` asks Docker again.
SNAPSHOT_TTL_SECONDS = 30.0
SETUP_LABEL = "org.joinery.sandbox-rev"
#: Where the worker images are built from; their revision is a hash of it.
SANDBOX_DIR = Path(__file__).resolve().parents[1] / "sandbox"


def action(action_id: str | None) -> dict[str, str] | None:
    """`{id, label}` for an action id, or None when there is nothing to do."""
    if not action_id:
        return None
    return {"id": action_id, "label": ACTION_LABELS[action_id]}


def action_for(state: str) -> dict[str, str] | None:
    """The action for an internal state (None for ready and preparing)."""
    return action(ACTION_FOR_STATE.get(state))


def cache_dir() -> Path:
    return Path(
        os.environ.get("JOINERY_READINESS_CACHE", str(Path.home() / ".cache/joinery"))
    )


# --- The boundary to Docker, the host and MCP servers -----------------------


@dataclass(frozen=True)
class Image:
    id: str
    created: str = ""
    revision: str = ""


@dataclass(frozen=True)
class ProbeResult:
    """What an MCP probe returned. `ran` is False when the server is not configured."""

    ran: bool
    ok: bool
    text: str = ""


class Host(Protocol):
    """Everything readiness touches outside Python. Tests supply a fake."""

    def docker(self) -> str:
        """ "" when Docker answers, otherwise why not."""
        ...

    def image(self, name: str) -> Image | None: ...

    def run(
        self, image: str, argv: Sequence[str], entrypoint: str, timeout: int
    ) -> tuple[int, str]: ...

    def host_program(self, program: str) -> Path | None: ...

    def host_run(
        self, binary: Path, args: Sequence[str], timeout: int
    ) -> tuple[int, str]: ...

    def installed(self, tool: str) -> str:
        """ "" when the pinned MCP source is installed and unchanged, else why not."""
        ...

    def tree(self, tool: str) -> str: ...

    def setup(self) -> dict[str, Any] | None: ...

    def expected_revision(self) -> str: ...

    def mcp(self, project: Path, probe: McpProbe) -> ProbeResult: ...

    def desktop(self, project: Path | None, key: str) -> tuple[bool, str]: ...


def _first_line(text: str) -> str:
    for line in text.strip().splitlines():
        if line.strip():
            return line.strip()[:300]
    return ""


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        # os.kill(pid, 0) on Windows sends CTRL_C_EVENT (value 0) to the
        # console instead of checking the process, so ask the kernel.
        return _alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _alive_windows(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    native: Any = ctypes  # WinDLL and get_last_error exist only on Windows
    kernel = native.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        # Access denied means the process exists but belongs to someone else.
        return bool(native.get_last_error() == 5)
    try:
        code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return bool(code.value == 259)  # STILL_ACTIVE
    finally:
        kernel.CloseHandle(handle)


def sandbox_revision(root: Path | None = None) -> str:
    """A short hash of everything the worker images are built from.

    Setup stamps each image with it (`SETUP_LABEL`); an image whose stamp
    differs was built from older sources and needs a rebuild.
    """
    root = root or SANDBOX_DIR
    digest = hashlib.sha256()
    files: list[Path] = []
    try:
        files = sorted(
            p
            for p in root.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts and p.name != ".DS_Store"
        )
        for path in files:
            digest.update(path.relative_to(root).as_posix().encode() + b"\0")
            digest.update(path.read_bytes())
    except OSError:
        return ""
    return digest.hexdigest()[:16] if files else ""


def mark_setup(phase: str, image: str = "") -> None:
    """Record that setup is building `image` now, so readiness says preparing."""
    path = cache_dir() / "setup.json"
    record = {
        "phase": phase,
        "image": image,
        "started": time.time(),
        "pid": os.getpid(),
    }
    with contextlib.suppress(OSError):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f".{os.getpid()}.tmp")
        temporary.write_text(json.dumps(record) + "\n")
        os.replace(temporary, path)


def clear_setup() -> None:
    """Setup is over (finished or failed): nothing is preparing any more."""
    path = cache_dir() / "setup.json"
    with contextlib.suppress(OSError, ValueError, TypeError):
        data = json.loads(path.read_text())
        if int(data.get("pid", 0) or 0) == os.getpid():
            path.unlink()


#: Docker's own words for an image that isn't there (any other failure is a hiccup).
_NO_SUCH_IMAGE = re.compile(r"no such (image|object)", re.IGNORECASE)
#: Pause before asking Docker about an image a second time.
IMAGE_RETRY_SECONDS = 1.0


class ImageUnknown(Exception):
    """Docker didn't answer about an image: it may or may not be there."""


def _parse_image(stdout: str) -> Image:
    identifier, _, rest = stdout.strip().partition("\t")
    created, _, labels = rest.partition("\t")
    try:
        parsed = json.loads(labels) or {}
    except json.JSONDecodeError:
        parsed = {}
    return Image(identifier, created, str(parsed.get(SETUP_LABEL, "")))


class DockerHost:
    """The real boundary: Docker CLI, the installed MCP cache and the desktop."""

    def docker(self) -> str:
        if shutil.which("docker") is None:
            return "Docker is not installed."
        try:
            done = subprocess.run(
                ["docker", "info", "--format", "{{.ServerVersion}}"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return "Docker is not running."
        return "" if done.returncode == 0 else "Docker is not running."

    def image(self, name: str) -> Image | None:
        """The image, None when Docker says it doesn't exist.

        Raises `ImageUnknown` when Docker couldn't say (a busy daemon, a
        timeout): asked twice and cross-checked with `docker image ls`, so a
        hiccup is never reported as a missing image.
        """
        reason = ""
        for attempt in range(2):
            if attempt:
                time.sleep(IMAGE_RETRY_SECONDS)
            try:
                done = subprocess.run(
                    [
                        "docker",
                        "image",
                        "inspect",
                        "--format",
                        "{{.Id}}\t{{.Created}}\t{{json .Config.Labels}}",
                        name,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                reason = str(exc) or type(exc).__name__
                continue
            if done.returncode == 0:
                return _parse_image(done.stdout)
            reason = _first_line(done.stderr) or f"exit code {done.returncode}"
            if _NO_SUCH_IMAGE.search(done.stderr):
                return None
        try:
            listed = subprocess.run(
                ["docker", "image", "ls", "--quiet", name],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ImageUnknown(reason or str(exc)) from None
        if listed.returncode == 0 and not listed.stdout.strip():
            return None
        raise ImageUnknown(reason)

    def run(
        self, image: str, argv: Sequence[str], entrypoint: str, timeout: int
    ) -> tuple[int, str]:
        command = [
            "docker",
            "run",
            "--rm",
            "--platform",
            "linux/amd64",
            "--network",
            "none",
        ]
        if entrypoint:
            command += ["--entrypoint", entrypoint]
        try:
            done = subprocess.run(
                [*command, image, *argv],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return 124, f"timed out after {timeout} s"
        except OSError as exc:
            return 127, str(exc)
        return done.returncode, done.stdout + "\n" + done.stderr

    def host_program(self, program: str) -> Path | None:
        if program == "engine_binary":
            from joinery.distribution import engine_binary

            try:
                return engine_binary()
            except ValueError:
                return None
        found = shutil.which(program)
        return Path(found) if found else None

    def host_run(
        self, binary: Path, args: Sequence[str], timeout: int
    ) -> tuple[int, str]:
        try:
            done = subprocess.run(
                [str(binary), *args],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return 124, f"timed out after {timeout} s"
        except OSError as exc:
            return 127, str(exc)
        return done.returncode, done.stdout + "\n" + done.stderr

    def installed(self, tool: str) -> str:
        from joinery.server_install import load_installed

        try:
            load_installed(tool)
        except (OSError, ValueError, KeyError, AssertionError) as exc:
            return str(exc) or "not installed"
        return ""

    def tree(self, tool: str) -> str:
        from joinery.server_install import installation

        try:
            data = json.loads((installation(tool) / "server.json").read_text())
        except (OSError, ValueError, KeyError):
            return ""
        return str(data.get("tree_sha256", ""))

    def setup(self) -> dict[str, Any] | None:
        path = cache_dir() / "setup.json"
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or not _alive(int(data.get("pid", 0) or 0)):
            return None
        return data

    def expected_revision(self) -> str:
        # Images built before the label existed carry none and count as current.
        return os.environ.get("JOINERY_SANDBOX_REV") or sandbox_revision()

    def mcp(self, project: Path, probe: McpProbe) -> ProbeResult:
        try:
            return asyncio.run(_mcp_probe(project, probe))
        except RuntimeError as exc:
            return ProbeResult(True, False, str(exc))

    def desktop(self, project: Path | None, key: str) -> tuple[bool, str]:
        from joinery.desktop import launcher, overrides

        try:
            argv = launcher(key, overrides(project) if project else {})
        except ValueError as exc:
            return False, str(exc)
        return True, argv[-1]


async def _mcp_probe(project: Path, probe: McpProbe) -> ProbeResult:
    from joinery.mcp_runtime import MCPConnection
    from joinery.server_install import configured

    specs, _missing = configured(project)
    spec = next((s for s in specs if s.name == probe.server), None)
    if spec is None:
        # Not configured (not selected, or not installed, which InstallCheck
        # reports): nothing to probe.
        return ProbeResult(False, False)
    connection = MCPConnection(spec, project)
    arguments = {
        key: str(project.resolve()) if value == "{project}" else value
        for key, value in probe.arguments().items()
    }
    try:
        await connection.connect()
        result = await connection.call(probe.tool, arguments)
        text = "\n".join(
            getattr(block, "text", "")
            for block in result.content
            if hasattr(block, "text")
        )
        return ProbeResult(True, not result.is_error, text)
    except (OSError, ValueError, RuntimeError, TimeoutError, PermissionError) as exc:
        return ProbeResult(True, False, str(exc) or type(exc).__name__)
    finally:
        await connection.close()


# --- Evaluating one tool -----------------------------------------------------


@dataclass
class Outcome:
    state: State = "ready"
    detail: str = ""
    version: str = ""
    #: False when the check was not run and nothing was cached.
    known: bool = True
    #: True when there was nothing to check (an MCP probe without a project).
    skipped: bool = False
    #: The check came from an InstallCheck (changes the wording).
    install: bool = False
    #: The detail is the check's own user-facing wording, not raw output.
    explicit: bool = False
    #: The image is there but was built from older sources (after an update).
    stale: bool = False


@dataclass
class _Context:
    host: Host
    project: Path | None
    selected: set[str]
    refresh: bool
    deep: bool
    cache: dict[str, Any]
    now: float
    servers: dict[str, str] = field(default_factory=dict)
    removed: set[str] = field(default_factory=set)
    docker_reason: str | None = None
    images: dict[str, Image | None] = field(default_factory=dict)
    #: Images Docker couldn't say anything about this time.
    unsure: set[str] = field(default_factory=set)
    setup: dict[str, Any] | None = None
    setup_read: bool = False
    updates: dict[str, Any] = field(default_factory=dict)
    #: Run only the checks the cache cannot answer (a background refresh).
    fill: bool = False

    def docker(self) -> str:
        if self.docker_reason is None:
            self.docker_reason = self.host.docker()
        return self.docker_reason

    def image(self, name: str) -> Image | None:
        if name not in self.images:
            try:
                self.images[name] = None if self.docker() else self.host.image(name)
            except ImageUnknown:
                # Docker didn't answer: "checking", never "not built".
                self.images[name] = None
                self.unsure.add(name)
        return self.images[name]

    def downloading(self, image: str) -> bool:
        if not self.setup_read:
            self.setup = self.host.setup()
            self.setup_read = True
        if not self.setup:
            return False
        target = str(self.setup.get("image", ""))
        return not target or target == image


def _key(tool: str, check: Check, *parts: str) -> str:
    digest = hashlib.sha256(repr(check).encode()).hexdigest()[:16]
    return "|".join((tool, type(check).__name__, digest, *parts))


def _cached(ctx: _Context, key: str, ttl: float | None = None) -> Outcome | None:
    if ctx.refresh:
        return None
    raw = ctx.cache.get(key)
    if not isinstance(raw, dict):
        return None
    if ttl is not None and ctx.now - float(raw.get("at", 0)) > ttl:
        return None
    state = raw.get("state")
    if state not in RANK:
        return None
    return Outcome(
        state,
        str(raw.get("detail", "")),
        str(raw.get("version", "")),
        explicit=bool(raw.get("explicit", False)),
        stale=bool(raw.get("stale", False)),
    )


#: What the host boundary returns when a check ran out of time. A timeout (a
#: slow emulated start, a busy machine) says nothing lasting about the image,
#: so it is reported but never cached; the next refresh runs the check again.
TIMED_OUT = 124


#: `docker run`'s own failure (the daemon refused), before the check ran at all.
DOCKER_FAILED = 125


def _timed_out(code: int, output: str) -> bool:
    return (code == TIMED_OUT and output.startswith("timed out after")) or (
        code == DOCKER_FAILED
    )


def _store_run(
    ctx: _Context, key: str, code: int, output: str, outcome: Outcome
) -> Outcome:
    """Cache a command check's result unless the command timed out."""
    if _timed_out(code, output) and outcome.state != "ready":
        ctx.updates.pop(key, None)
        ctx.removed.add(key)
        return outcome
    return _store(ctx, key, outcome)


def _store(ctx: _Context, key: str, outcome: Outcome) -> Outcome:
    ctx.updates[key] = {
        "state": outcome.state,
        "detail": outcome.detail,
        "version": outcome.version,
        "explicit": outcome.explicit,
        "stale": outcome.stale,
        "at": ctx.now,
    }
    return outcome


def _match(check: WorkerExec | HostExec, code: int, output: str) -> Outcome:
    version = ""
    if check.version:
        found = re.search(check.version, output)
        version = found.group(1) if found else ""
    if code == 0 and re.search(check.expect, output):
        return Outcome("ready", "", version)
    if _timed_out(code, output):
        # Not "missing from the image": the check never got to answer.
        return Outcome("broken", _first_line(output), version)
    state: State = getattr(check, "on_failure", "broken")
    reason = check.failure or _first_line(output) or f"exit code {code}"
    # Missing from an image that is there: the image predates this Joinery.
    return Outcome(
        state,
        reason,
        version,
        explicit=bool(check.failure),
        stale=state == "needs_image",
    )


def _image_gate(ctx: _Context, image: str) -> Outcome | None:
    """The state a check on `image` inherits when the image itself is not usable."""
    if ctx.docker():
        return Outcome("needs_docker", ctx.docker())
    if ctx.downloading(image):
        return Outcome("downloading", image)
    found = ctx.image(image)
    if found is None and image in ctx.unsure:
        return Outcome(known=False, detail="Docker didn't answer")
    if found is None:
        return Outcome("needs_image", f"{image} is not built")
    expected = ctx.host.expected_revision()
    if expected and found.revision and found.revision != expected:
        return Outcome("needs_image", f"{image} is out of date", stale=True)
    return None


def _evaluate(ctx: _Context, module: ToolModule, check: Check) -> Outcome:
    if isinstance(check, ImageCheck):
        return _image_gate(ctx, check.image) or Outcome()
    if isinstance(check, InstallCheck):
        reason = ctx.host.installed(check.tool)
        if reason:
            return Outcome("needs_image", reason, install=True)
        return Outcome(install=True)
    if isinstance(check, WorkerExec):
        gate = _image_gate(ctx, check.image)
        if gate is not None:
            return gate
        image = ctx.image(check.image)
        assert image is not None
        key = _key(module.id, check, image.id)
        cached = _cached(ctx, key)
        if cached is not None:
            return cached
        if not (ctx.refresh or ctx.fill):
            return Outcome(known=False)
        code, output = ctx.host.run(
            check.image, check.argv, check.entrypoint, check.timeout
        )
        return _store_run(ctx, key, code, output, _match(check, code, output))
    if isinstance(check, HostExec):
        binary = ctx.host.host_program(check.program)
        if binary is None:
            return Outcome("broken", check.failure or f"{check.program} not found")
        try:
            stat = binary.stat()
            identity = f"{binary}:{stat.st_mtime_ns}:{stat.st_size}"
        except OSError:
            identity = str(binary)
        key = _key(module.id, check, identity)
        cached = _cached(ctx, key)
        if cached is not None:
            return cached
        if not (ctx.refresh or ctx.fill):
            return Outcome(known=False)
        code, output = ctx.host.host_run(binary, check.args, check.timeout)
        return _store_run(ctx, key, code, output, _match(check, code, output))
    # McpProbe
    if ctx.project is None or module.id not in ctx.selected:
        # Nothing to connect to: the tool is not selected for a project.
        return Outcome(skipped=True)
    image_id = ""
    if check.image:
        gate = _image_gate(ctx, check.image)
        if gate is not None:
            return gate
        found = ctx.image(check.image)
        image_id = found.id if found else ""
    key = _key(module.id, check, image_id, _server_tree(ctx, check.server))
    cached = _cached(ctx, key, MCP_TTL_SECONDS)
    if cached is not None:
        return cached
    if not (ctx.refresh or ctx.fill):
        return Outcome(known=False)
    result = ctx.host.mcp(ctx.project, check)
    if not result.ran:
        return Outcome(skipped=True)
    outcome = _probe_outcome(check, result)
    if outcome.state != "ready":
        # A failed call is never cached: the next status asks again.
        ctx.updates.pop(key, None)
        ctx.removed.add(key)
        return outcome
    return _store(ctx, key, outcome)


def _server_tree(ctx: _Context, server: str) -> str:
    tool = ctx.servers.get(server)
    return ctx.host.tree(tool) if tool else "first-party"


def _probe_outcome(check: McpProbe, result: ProbeResult) -> Outcome:
    failure = check.failure or f"{check.server}.{check.tool} failed"
    if not result.ok:
        return Outcome("broken", _first_line(result.text) or failure)
    if check.expect and not re.search(check.expect, result.text):
        return Outcome(
            "broken", failure + ": " + (_first_line(result.text) or "no result")
        )
    if check.capability:
        try:
            data = json.loads(result.text)
        except json.JSONDecodeError:
            return Outcome("broken", failure + ": unreadable capabilities")
        entry = next(
            (t for t in data.get("tools", []) if t.get("id") == check.capability), None
        )
        if not data.get("runtime_available"):
            return Outcome("broken", "the offline worker did not start")
        if entry is None or not entry.get("application_installed"):
            return Outcome(
                "needs_image",
                check.failure or f"{check.capability} is missing from the worker",
                explicit=bool(check.failure),
                stale=True,
            )
    return Outcome("ready")


def mcp_probes(server: str, tool_ids: Iterable[str] | None = None) -> list[McpProbe]:
    """The tool modules' read-only probes for one MCP server, required ones first.

    `tool_ids` limits them to the project's tools: a shared server (joinery-paint
    serves Krita, GIMP and Inkscape) is only asked about the tools in use.
    """
    wanted = set(tool_ids) if tool_ids is not None else None
    found: list[McpProbe] = []
    for module in tool_modules.REGISTRY.values():
        if wanted is not None and module.id not in wanted:
            continue
        for check in module.checks:
            if (
                isinstance(check, McpProbe)
                and check.server == server
                and check not in found
            ):
                found.append(check)
    return sorted(found, key=lambda check: bool(check.feature))


def judge_probe(check: McpProbe, ok: bool, text: str) -> tuple[str, str]:
    """(state, detail) for one probe answer, by the same rules as `status`."""
    outcome = _probe_outcome(check, ProbeResult(True, ok, text))
    return outcome.state, outcome.detail


def _worst(outcomes: Iterable[Outcome]) -> Outcome:
    worst = Outcome()
    for outcome in outcomes:
        if RANK[outcome.state] > RANK[worst.state]:
            worst = outcome
    return worst


def _feature_text(feature: Feature, outcome: Outcome, name: str) -> str:
    if outcome.state == "ready":
        return feature.label
    if outcome.state == "needs_docker":
        return f"{feature.label}: needs Docker running."
    if outcome.state == "downloading":
        return f"{feature.label}: refreshing…"
    if outcome.state == "needs_image":
        label = feature.label[:1].upper() + feature.label[1:]
        return refresh_text([label], after_update=outcome.stale)
    prefix = feature.broken or f"{feature.label} failed"
    return f"{prefix}: {outcome.detail}" if outcome.detail else prefix


def _tool_text(module: ToolModule, outcome: Outcome) -> str:
    name = module.name
    if outcome.state == "needs_docker":
        if "not installed" in outcome.detail:
            return (
                f"{name} runs offline in Docker, which isn't installed yet. "
                "Install Docker Desktop, then check again."
            )
        return f"{name} runs offline in Docker. Start Docker Desktop, then check again."
    if outcome.state == "downloading":
        return f"Refreshing {name}…"
    if outcome.state == "needs_image":
        # What is missing stays in `detail`; the sentence is the same for every tool.
        return refresh_text([name], after_update=outcome.stale)
    if outcome.state == "needs_licence":
        return f"{name} needs your own licence."
    prefix = module.broken.format(name=name)
    return f"{prefix}: {outcome.detail}" if outcome.detail else prefix


def names(items: Sequence[str]) -> str:
    """ "Blender", "Blender and Krita", "Blender, Krita and GIMP"."""
    if len(items) < 2:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def refresh_text(
    apps: Sequence[str], *, after_update: bool, plural: bool = False
) -> str:
    """What Refresh tools fixes, for one app or several, in the one wording."""
    template = REFRESH_AFTER_UPDATE if after_update else REFRESH_FIRST
    several = plural or len(apps) > 1
    return template.format(
        names=names(apps),
        verb="need" if several else "needs",
        pronoun="they" if several else "it",
    )


def notice(tools: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """The sidebar's one line about the project's apps, or None when all is well.

    One problem at a time, the one to fix first: a refresh already running,
    Docker, then a refresh, then a licence, then an app that failed its check. Every app with that problem is
    named in the one sentence, and `action` is the button that fixes it.
    """
    chosen = [
        t
        for t in tools
        if t.get("selected", True) and not t.get("custom") and t.get("state")
    ]
    order = ("downloading", "needs_docker", "needs_image", "needs_licence", "broken")
    for state in order:
        apps = [t for t in chosen if t.get("state") == state]
        if not apps:
            continue
        found = [str(t.get("name", t.get("id", ""))) for t in apps]
        verb = "need" if len(found) > 1 else "needs"
        if state == "downloading":
            text = f"Refreshing {names(found)}…"
        elif state == "needs_docker":
            text = f"{names(found)} {verb} Docker, which isn't running."
        elif state == "needs_image":
            stale = any(t.get("after_update") for t in apps)
            text = refresh_text(found, after_update=stale)
        elif state == "needs_licence":
            text = f"{names(found)} {verb} your licence."
        else:
            text = f"{names(found)} {'aren' if len(found) > 1 else 'isn'}'t working right now."
        return {
            "state": state,
            "text": text,
            "tools": [str(t.get("id", "")) for t in apps],
            "action": action_for(state),
        }
    return None


def _clean(text: str) -> str:
    """Collapse the gap an unknown version leaves ("Blender  ready")."""
    return re.sub(r" {2,}", " ", text).strip()


def tool_status(ctx: _Context, module: ToolModule) -> dict[str, Any]:
    """Run (or recall) every check of one tool and describe the result."""
    results: list[tuple[Check, Outcome]] = []
    #: Scopes (a feature id, or "" for the tool itself) whose first failing
    #: check already decided their state. Later checks in that scope are not
    #: run: a probe that needs a missing program would only fail again and hide
    #: the clearer reason (GIMP absent from the worker is "rebuild", not
    #: "failed to start").
    decided: set[str] = set()
    for check in module.checks:
        if check.tier == "deep" and not ctx.deep:
            continue
        if check.feature in decided:
            results.append((check, Outcome(skipped=True)))
            continue
        outcome = _evaluate(ctx, module, check)
        results.append((check, outcome))
        if outcome.known and not outcome.skipped and outcome.state != "ready":
            decided.add(check.feature)
    required = [o for c, o in results if not c.feature and not o.skipped]
    overall = _worst(o for o in required if o.known)
    version = (
        next((o.version for o in required if o.version), "")
        or next((o.version for _, o in results if o.version), "")
        or module.version
    )
    checking = any(not o.known for _, o in results)
    features: list[dict[str, Any]] = []
    ready_labels: list[str] = []
    for feature in module.features:
        own = [o for c, o in results if c.feature == feature.id and not o.skipped]
        worst = _worst(o for o in own if o.known)
        feature_checking = any(not o.known for o in own)
        if worst.state == "ready" and own and not feature_checking:
            ready_labels.append(feature.label)
        features.append(
            {
                "id": feature.id,
                "label": feature.label,
                "state": worst.state,
                "status": SURFACE[worst.state],
                "checking": feature_checking,
                "detail": _feature_text(feature, worst, module.name),
                "action": action_for(worst.state),
            }
        )
    if overall.state == "ready":
        summary = module.ready_summary.format(name=module.name, version=version)
        if ready_labels:
            labels = ", ".join(ready_labels)
            summary += ". " + labels[:1].upper() + labels[1:] + "."
        if checking:
            summary = f"Checking {module.name}… " + summary
    else:
        summary = _tool_text(module, overall)
    image = ctx.images.get(module.worker_image)
    surface = SURFACE[overall.state]
    entry: dict[str, Any] = {
        "id": module.id,
        "name": module.name,
        "state": overall.state,
        "version": version,
        "checking": checking,
        "summary": _clean(summary),
        "detail": overall.detail,
        # What every screen shows: the state, its words, why, and what to do.
        "status": surface,
        "status_label": STATUS_LABELS[surface],
        "status_reason": "" if overall.state == "ready" else _clean(summary),
        "action": action_for(overall.state),
        "features": features,
        "checked_at": _stamp(ctx.now),
        "image_id": image.id if image else "",
        # Needs a refresh because Joinery was updated (not a first-time setup).
        "after_update": overall.state == "needs_image" and overall.stale,
    }
    if module.desktop:
        available, where = ctx.host.desktop(ctx.project, module.desktop)
        entry["desktop"] = {
            "available": available,
            "path": where if available else "",
            "label": (
                f"Open in {module.name}"
                if available
                else f"Desktop {module.name} not found: Open in {module.name} is "
                "unavailable. Editing still works offline."
            ),
        }
    else:
        entry["desktop"] = {"available": False, "path": "", "label": ""}
    return entry


# --- The whole report ---------------------------------------------------------


def load_cache() -> dict[str, Any]:
    try:
        data = json.loads((cache_dir() / "readiness.json").read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_cache(cache: Mapping[str, Any]) -> None:
    path = cache_dir() / "readiness.json"
    with contextlib.suppress(OSError):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f".{os.getpid()}.tmp")
        temporary.write_text(json.dumps(cache, indent=1, sort_keys=True) + "\n")
        os.replace(temporary, path)


def _stamp(moment: float) -> str:
    return datetime.fromtimestamp(moment, UTC).isoformat(timespec="seconds")


@dataclass
class _Scope:
    """Which tools a report covers, and what the project chose."""

    ids: list[str]
    selected: set[str]
    notices: list[str]
    custom: list[dict[str, Any]]


def _scope(
    project: Path | None, *, tool: str | None, catalog: bool, now: float
) -> _Scope:
    every = load_catalog()
    selected: set[str] = set()
    notices: list[str] = []
    custom: list[dict[str, Any]] = []
    if project is not None and (project / "joinery.toml").is_file():
        chosen = load_project_tools(project / "joinery.toml")
        selected = set(selection_ids(every, chosen))
        notices = removed_notices(chosen)
        custom = [
            {
                "id": c.id,
                "name": c.name,
                "state": "ready",
                "custom": True,
                "checking": False,
                "summary": f"{c.name}: your own tool; Joinery does not check it.",
                "status": "ready",
                "status_label": STATUS_LABELS["ready"],
                "status_reason": "",
                "action": None,
                "checked_at": _stamp(now),
                "features": [],
            }
            for c in chosen.custom
        ]
    if catalog or project is None:
        ids = [t.id for t in every.tool]
    else:
        ids = [t.id for t in every.tool if t.id in selected]
    if tool is not None:
        if tool not in tool_modules.REGISTRY:
            raise ValueError(f"Unknown tool {tool!r}.")
        ids = [tool]
        custom = []
    return _Scope(
        [i for i in ids if i in tool_modules.REGISTRY], selected, notices, custom
    )


def status(
    project: Path | None = None,
    *,
    tool: str | None = None,
    catalog: bool = False,
    refresh: bool = False,
    fill: bool = False,
    deep: bool = False,
    host: Host | None = None,
) -> dict[str, Any]:
    """Readiness for a project's tools (or every catalog tool with `catalog`).

    Without `refresh`, tiers 1 to 3 come from the cache and a miss is reported
    as `checking`; run again with `refresh` (or `spawn_refresh`) to fill it.
    With `fill`, only the checks the cache cannot answer run now.
    """
    host = host or DockerHost()
    now = time.time()
    scope = _scope(project, tool=tool, catalog=catalog, now=now)
    every = load_catalog()
    cache = load_cache()
    ctx = _Context(host, project, scope.selected, refresh, deep, cache, now, fill=fill)
    ctx.servers = {t.mcp.server: t.id for t in every.tool if t.mcp is not None}
    report_tools = [tool_status(ctx, tool_modules.REGISTRY[i]) for i in scope.ids]
    for entry in report_tools:
        entry["selected"] = entry["id"] in scope.selected
    if ctx.updates or ctx.removed & set(cache):
        merged = load_cache()
        merged.update(ctx.updates)
        for key in ctx.removed:
            merged.pop(key, None)
        save_cache(merged)
    docker_reason = ctx.docker_reason
    docker = "unchecked" if docker_reason is None else (docker_reason or "ok")
    _save_snapshot(report_tools, docker, now)
    return {
        "docker": docker,
        "checking": any(t["checking"] for t in report_tools),
        "checked_at": _stamp(now),
        "tools": [*report_tools, *scope.custom],
        "removed": scope.notices,
        "notice": notice(report_tools),
    }


# --- The snapshot every polling screen reads -------------------------------------


def load_snapshot() -> dict[str, Any]:
    """The last report of every tool: {"docker", "at", "tools": {id: entry}}."""
    try:
        data = json.loads((cache_dir() / "status.json").read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or not isinstance(data.get("tools"), dict):
        return {}
    return data


def _save_snapshot(
    entries: Sequence[Mapping[str, Any]], docker: str, now: float
) -> None:
    if not entries:
        return
    snapshot = load_snapshot() or {"tools": {}}
    for entry in entries:
        kept = {key: value for key, value in entry.items() if key != "selected"}
        snapshot["tools"][str(entry["id"])] = {**kept, "at": now}
    snapshot["docker"] = docker
    snapshot["at"] = now
    path = cache_dir() / "status.json"
    with contextlib.suppress(OSError):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f".{os.getpid()}.tmp")
        temporary.write_text(json.dumps(snapshot, indent=1, sort_keys=True) + "\n")
        os.replace(temporary, path)


def cached_tools() -> dict[str, dict[str, Any]]:
    """Each tool's last known entry, by id, without asking Docker or anything else.

    For screens that refresh often (the asset workspace). An entry keeps its
    own `checked_at`, so an old answer never looks new.
    """
    tools = load_snapshot().get("tools", {})
    return {
        str(key): dict(value) for key, value in tools.items() if isinstance(value, dict)
    }


def _snapshot_report(
    scope: _Scope, max_age: float, now: float, host: Host
) -> dict[str, Any] | None:
    """The report from the snapshot, or None when any part is missing or stale."""
    snapshot = load_snapshot()
    known = snapshot.get("tools", {})
    entries = []
    for tool_id in scope.ids:
        entry = known.get(tool_id)
        if not isinstance(entry, dict) or "status" not in entry:
            return None
        if now - float(entry.get("at", 0)) > max_age:
            return None
        # While setup builds a worker, the answer changes by the minute.
        if entry.get("state") == "downloading":
            return None
        entries.append({**entry, "selected": tool_id in scope.selected})
    if host.setup():
        return None
    return {
        "docker": str(snapshot.get("docker", "unchecked")),
        "checking": any(e.get("checking") for e in entries),
        "checked_at": _stamp(min((float(e["at"]) for e in entries), default=now)),
        "cached": True,
        "tools": [*entries, *scope.custom],
        "removed": scope.notices,
        "notice": notice(entries),
    }


def current(
    project: Path | None = None,
    *,
    tool: str | None = None,
    catalog: bool = False,
    host: Host | None = None,
    max_age: float = SNAPSHOT_TTL_SECONDS,
    background: bool = True,
) -> dict[str, Any]:
    """Readiness for screens that ask often: the snapshot while it is fresh.

    Within `max_age` seconds nothing outside Python runs. Otherwise the cheap
    checks run now (`status`) and, with `background`, the checks the cache
    cannot answer run in a detached process (`refreshing: true`).
    """
    host = host or DockerHost()
    now = time.time()
    scope = _scope(project, tool=tool, catalog=catalog, now=now)
    report = _snapshot_report(scope, max_age, now, host)
    if report is None:
        report = status(project, tool=tool, catalog=catalog, host=host)
    if background and report["checking"]:
        report["refreshing"] = spawn_refresh(project, catalog=catalog)
    return report


def agents() -> dict[str, dict[str, Any]]:
    """Each agent's connection in the same report: summary and verified flag.

    Slow (each agent's own status command runs), so only on request.
    """
    from joinery import connection
    from joinery.agents import all_agents

    found: dict[str, dict[str, Any]] = {}
    for agent in all_agents():
        try:
            data = connection.status(agent.id)
        except (OSError, ValueError, KeyError) as exc:
            found[agent.id] = {"summary": f"{agent.name}: {exc}", "verified": False}
            continue
        found[agent.id] = {
            "name": data.get("name", agent.name),
            "installed": bool(data.get("installed")),
            "connected": bool(data.get("connected")),
            "verified": bool(data.get("verified")),
            "summary": str(data.get("summary", "")),
        }
    return found


def spawn_refresh(
    project: Path | None,
    *,
    deep: bool = False,
    full: bool = False,
    catalog: bool = False,
) -> bool:
    """Fill the cache in a detached process; False when one is already running.

    By default only the checks the cache cannot answer run (`--fill`); `full`
    runs every check again.
    """
    lock = cache_dir() / "readiness.pid"
    with contextlib.suppress(OSError, ValueError):
        if _alive(int(lock.read_text().strip())):
            return False
    mode = "--refresh" if full else "--fill"
    argv = [sys.executable, "-m", "joinery.readiness", mode, "--quiet"]
    if project is not None:
        argv += ["--project", str(project.resolve())]
    if catalog:
        argv.append("--catalog")
    if deep:
        argv.append("--deep")
    lock.parent.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    lock.write_text(str(process.pid))
    return True


def _release_refresh_lock() -> None:
    """Called by the background refresh as it ends, so the next one can start.

    Without this, a refresh spawned by a long-running process stays a zombie
    whose pid still answers, and no later refresh would ever start.
    """
    lock = cache_dir() / "readiness.pid"
    with contextlib.suppress(OSError, ValueError):
        if int(lock.read_text().strip()) == os.getpid():
            lock.unlink()


def lines(report: Mapping[str, Any]) -> list[str]:
    """The same report as plain text, one tool per line plus degraded features."""
    marks = {"ready": "ok  ", "downloading": "... ", "needs_docker": "DOCK"}
    out = []
    for entry in report["tools"]:
        mark = marks.get(entry["state"], "--  " if entry.get("custom") else "FIX ")
        out.append(f"  [{mark}] {entry['name']:<16} {entry['summary']}")
        for feature in entry.get("features", []):
            if feature["state"] != "ready":
                out.append(f"         {'':<16}   {feature['detail']}")
        desktop = entry.get("desktop") or {}
        if desktop.get("label") and not desktop.get("available"):
            out.append(f"         {'':<16}   {desktop['label']}")
    out += [f"  {notice}" for notice in report.get("removed", [])]
    return out


def main(argv: Sequence[str] | None = None) -> int:
    """`joinery status` entry point: prints the report as JSON (or text)."""
    parser = argparse.ArgumentParser(prog="joinery status")
    parser.add_argument("--project", type=Path)
    parser.add_argument("--tool")
    parser.add_argument("--catalog", action="store_true", help="every catalog tool")
    parser.add_argument("--refresh", action="store_true", help="run every check now")
    parser.add_argument(
        "--fill", action="store_true", help="run only the checks the cache lacks"
    )
    parser.add_argument("--deep", action="store_true", help="include the slow checks")
    parser.add_argument(
        "--background",
        action="store_true",
        help="answer from the cache and refresh missing checks in the background",
    )
    parser.add_argument(
        "--agents", action="store_true", help="add each agent's connection"
    )
    parser.add_argument("--json", action="store_true", help="JSON (the default)")
    parser.add_argument("--text", action="store_true", help="plain text, not JSON")
    parser.add_argument("--quiet", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    project = args.project.resolve() if args.project else None
    try:
        if args.background and not (args.refresh or args.fill or args.deep):
            report = current(project, tool=args.tool, catalog=args.catalog)
        else:
            report = status(
                project,
                tool=args.tool,
                catalog=args.catalog,
                refresh=args.refresh,
                fill=args.fill,
                deep=args.deep,
            )
            if args.background and report["checking"]:
                report["refreshing"] = spawn_refresh(
                    project, deep=args.deep, catalog=args.catalog
                )
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}))
        return 2
    finally:
        if (args.refresh or args.fill) and args.quiet:
            _release_refresh_lock()
    if args.agents:
        report["agents"] = agents()
    if args.quiet:
        return 0
    if args.text:
        print("\n".join(lines(report)))
    else:
        print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
