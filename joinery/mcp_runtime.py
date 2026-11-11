"""Project-owned MCP connections with honest readiness and per-call policy."""

from __future__ import annotations

import ast
import asyncio
import contextlib
import json
import ntpath
import os
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath
from typing import Any

from jsonschema import Draft202012Validator
from mcp import Client, StdioServerParameters
from mcp.types import CallToolResult, TextContent, Tool
from pydantic import BaseModel, ConfigDict, Field

from joinery.hook import mcp_violation
from joinery.policy import Severity, command_violation, load_denylist, normalise


class ServerSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")
    command: str = Field(min_length=1)
    args: list[str] = []
    env: dict[str, str] = {}
    cwd: str = ""
    source: str = "custom"
    revision: str = ""
    container_name: str = Field(default="", pattern=r"^$|^joinery-mcp-[a-f0-9]{32}$")
    worker_image: str = ""
    worker_scope: str = Field(default="", pattern=r"^$|^[a-f0-9]{32}$")


#: Where an offline worker sees the folder it was given.
WORKDIR = "/work"


def map_paths(value: Any, project: PurePath, stage: PurePath) -> Any:
    """Move every absolute path under `project` to the same place under `stage`."""
    if isinstance(value, dict):
        return {k: map_paths(v, project, stage) for k, v in value.items()}
    if isinstance(value, list):
        return [map_paths(v, project, stage) for v in value]
    if isinstance(value, str):
        candidate: PurePath
        original: PurePath
        if isinstance(project, PureWindowsPath):
            # PureWindowsPath also permits exercising Windows drive/UNC/case
            # behavior on other hosts without changing the host's os.name.
            candidate = PureWindowsPath(ntpath.normpath(value))
            original = PureWindowsPath(ntpath.normpath(str(project)))
        else:
            candidate = Path(value)
            original = Path(project)
        if not candidate.is_absolute():
            return value
        if isinstance(project, Path):
            candidate = Path(value).resolve()
            original = project.resolve()
        try:
            relative = candidate.relative_to(original)
        except ValueError:
            return value
        return str(stage.joinpath(*relative.parts))
    return value


def worker_root(spec: ServerSpec) -> Path | None:
    """The host folder a worker server sees as /work, or None for a host server."""
    if spec.command != "docker":
        return None
    for flag, value in zip(spec.args, spec.args[1:], strict=False):
        if flag not in {"-v", "--volume"}:
            continue
        for suffix in (":" + WORKDIR, ":" + WORKDIR + ":ro"):
            if value.endswith(suffix):
                return Path(value[: -len(suffix)])
    return None


def rewrite_root(result: CallToolResult, root: str, new_root: PurePath) -> CallToolResult:
    """Put every path under `root` in a result under `new_root` instead.

    A worker answers with its own paths (/work/...), which mean nothing on the
    host; the agent should read the project paths it asked about.
    """
    pattern = re.compile(
        r"(?<![\w./\\-])"
        + re.escape(root.rstrip("/\\"))
        + r"((?:[/\\][^\s\"'`<>|,;:()\[\]{}]*)?)(?![\w-]|\.\w)"
    )

    def swap(match: re.Match[str]) -> str:
        tail = [part for part in re.split(r"[/\\]", match.group(1)) if part]
        return str(new_root.joinpath(*tail))

    def text(value: Any) -> Any:
        if isinstance(value, str):
            return pattern.sub(swap, value)
        if isinstance(value, dict):
            return {k: text(v) for k, v in value.items()}
        if isinstance(value, list):
            return [text(v) for v in value]
        return value

    content = [
        block.model_copy(update={"text": text(block.text)})
        if isinstance(block, TextContent)
        else block
        for block in result.content
    ]
    return result.model_copy(
        update={
            "content": content,
            "structured_content": text(result.structured_content),
        }
    )


class Readiness(BaseModel):
    name: str
    state: str = "selected"
    detail: str = ""
    tools: list[str] = []
    protocol: str = ""
    inspected: bool = False
    installed: bool = False
    connected: bool = False
    revision: str = ""


class PolicyStop(RuntimeError):
    """A Rule Zero stop must propagate to the agent turn, not become a retry."""


def host_environment() -> dict[str, str]:
    """Inherit runtime paths and selected graphics config, never provider keys."""
    names = (
        "PATH", "HOME", "LANG", "TMPDIR", "SYSTEMROOT", "SystemRoot",
        "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "COMSPEC",
        "HOMEDRIVE", "HOMEPATH", "ProgramFiles", "ProgramFiles(x86)",
        "DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "XDG_RUNTIME_DIR",
        "VK_DRIVER_FILES", "VK_ICD_FILENAMES", "LIBGL_ALWAYS_SOFTWARE",
        "GALLIUM_DRIVER", "MESA_LOADER_DRIVER_OVERRIDE",
    )
    return {key: os.environ[key] for key in names if key in os.environ}


def validate_schema(schema: Any) -> None:
    """MCP schemas are data, never permission to retrieve remote references."""
    if isinstance(schema, dict):
        for key, value in schema.items():
            if (
                key in {"$ref", "$dynamicRef"}
                and isinstance(value, str)
                and not value.startswith("#")
            ):
                raise ValueError("MCP schemas may reference only local definitions.")
            validate_schema(value)
    elif isinstance(schema, list):
        for value in schema:
            validate_schema(value)


def validate_arguments(arguments: Any) -> None:
    """Apply the same denylist to embedded commands and Python fallbacks."""
    if isinstance(arguments, dict):
        for value in arguments.values():
            validate_arguments(value)
    elif isinstance(arguments, list):
        for value in arguments:
            validate_arguments(value)
    elif isinstance(arguments, str):
        denylist = load_denylist()
        verdict = command_violation(arguments, denylist)
        if verdict is not None:
            raise PolicyStop(verdict.reason)
        with contextlib.suppress(SyntaxError):
            tree = ast.parse(arguments)
            for node in ast.walk(tree):
                modules = []
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    modules = [node.module]
                for module in modules:
                    if normalise(module.split(".")[0]) in denylist.packages:
                        raise PolicyStop(f"{module} is a denied generative package")


def result_text(result: CallToolResult) -> str:
    """The text blocks of a tool result, joined."""
    return "\n".join(
        str(getattr(block, "text", "")) for block in result.content if hasattr(block, "text")
    ).strip()


def probe_problem(name: str, result: CallToolResult) -> str:
    """Why a read-only probe did not prove the app works, or "" when it did.

    An error result fails. So does a capabilities answer that is false: the
    offline worker did not start (`runtime_available: false`), the answer says
    `ok: false`, or every listed tool reports its application missing.
    """
    text = result_text(result)
    if result.is_error:
        first = next((line.strip() for line in text.splitlines() if line.strip()), "")
        return f"{name} failed: {first[:300] or 'no detail'}"
    try:
        data = json.loads(text)
    except ValueError:
        return ""
    if not isinstance(data, dict):
        return ""
    if data.get("runtime_available") is False:
        return f"{name}: the offline worker did not start"
    if data.get("ok") is False:
        return f"{name}: {str(data.get('error') or data.get('detail') or 'not ready')[:300]}"
    listed = [t for t in data.get("tools", []) if isinstance(t, dict)]
    if listed and not any(t.get("application_installed", True) for t in listed):
        return f"{name}: the application is missing from the worker"
    return ""


@dataclass
class _Request:
    tool: str
    arguments: dict[str, Any]
    future: asyncio.Future[CallToolResult]
    timeout: float


@dataclass
class MCPConnection:
    spec: ServerSpec
    project: Path
    readiness: Readiness = field(init=False)
    tools: dict[str, Tool] = field(default_factory=dict, init=False)
    _task: asyncio.Task[None] | None = field(default=None, init=False)
    _ready: asyncio.Future[None] | None = field(default=None, init=False)
    _queue: asyncio.Queue[_Request | None] = field(
        default_factory=asyncio.Queue, init=False
    )

    def __post_init__(self) -> None:
        self.readiness = Readiness(
            name=self.spec.name, installed=True, revision=self.spec.revision
        )

    async def connect(self, timeout: float = 30) -> None:
        if self._task is not None and not self._task.done():
            if self._ready is not None:
                await asyncio.wait_for(asyncio.shield(self._ready), timeout)
            return
        verdict = mcp_violation(self.spec.name, "", self.project)
        if verdict:
            if verdict.severity is Severity.STOP:
                raise PolicyStop(verdict.reason)
            raise PermissionError(verdict.reason)
        if self.spec.worker_image:
            check = await asyncio.create_subprocess_exec(
                "docker",
                "image",
                "inspect",
                self.spec.worker_image,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                code = await asyncio.wait_for(check.wait(), 10)
            except TimeoutError:
                check.kill()
                await check.wait()
                code = 1
            if code:
                self.readiness.state = "runtime-missing"
                self.readiness.detail = f"Offline image {self.spec.worker_image} is unavailable or Docker is not running. Run joinery bootstrap --project <project>."
                raise ValueError(self.readiness.detail)
        self._ready = asyncio.get_running_loop().create_future()
        self._task = asyncio.create_task(self._serve())
        try:
            await asyncio.wait_for(asyncio.shield(self._ready), timeout)
        except BaseException:
            await self.close()
            raise

    async def _serve(self) -> None:
        # The SDK's process/cancel scope is opened and closed in this same task.
        # Neither model API keys nor the host's complete environment are inherited.
        env = host_environment()
        env.update(self.spec.env)
        parameters = StdioServerParameters(
            command=self.spec.command,
            args=self.spec.args,
            env=env,
            cwd=self.spec.cwd or self.project,
        )
        self.readiness.state = "starting"
        active: _Request | None = None
        try:
            mode = "legacy" if self.spec.name in {"godot", "blender", "inkscape", "claude-collider"} else "auto"
            async with Client(parameters, read_timeout_seconds=30, mode=mode) as client:
                self.readiness.state = "connected"
                self.readiness.connected = True
                self.readiness.protocol = client.protocol_version
                cursor: str | None = None
                while True:
                    page = await client.list_tools(cursor=cursor)
                    for tool in page.tools:
                        if tool.name in self.tools:
                            raise ValueError("Server returned duplicate tool names.")
                        Draft202012Validator.check_schema(tool.input_schema)
                        validate_schema(tool.input_schema)
                        self.tools[tool.name] = tool
                    cursor = page.next_cursor
                    if cursor is None:
                        break
                self.readiness.tools = sorted(self.tools)
                self.readiness.state = "discovered"
                assert self._ready is not None
                self._ready.set_result(None)
                while (active := await self._queue.get()) is not None:
                    try:
                        result = await client.call_tool(
                            active.tool,
                            active.arguments,
                            read_timeout_seconds=active.timeout,
                        )
                        if not active.future.done():
                            active.future.set_result(result)
                    except (Exception, BaseExceptionGroup) as exc:
                        if not active.future.done():
                            active.future.set_exception(
                                RuntimeError(
                                    f"{self.spec.name} tool call failed: {type(exc).__name__}"
                                )
                            )
                        raise
        except asyncio.CancelledError:
            raise
        except (Exception, BaseExceptionGroup) as exc:  # noqa: BLE001 - report isolated server failures
            self.readiness.state = "error"
            self.readiness.detail = f"{self.spec.name} could not connect or complete a request ({type(exc).__name__})."
            if self._ready is not None and not self._ready.done():
                self._ready.set_exception(RuntimeError(self.readiness.detail))
        finally:
            self.readiness.connected = False
            if active is not None and not active.future.done():
                active.future.set_exception(ConnectionError("MCP connection closed."))
            while not self._queue.empty():
                request = self._queue.get_nowait()
                if request is not None and not request.future.done():
                    request.future.set_exception(
                        ConnectionError("MCP connection closed.")
                    )
            if self.spec.container_name:
                cleanup = await asyncio.create_subprocess_exec(
                    "docker",
                    "rm",
                    "--force",
                    self.spec.container_name,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                try:
                    await asyncio.wait_for(cleanup.wait(), timeout=10)
                except TimeoutError:
                    cleanup.kill()
                    await cleanup.wait()
            if self.spec.worker_scope:
                listing = await asyncio.create_subprocess_exec(
                    "docker",
                    "ps",
                    "-aq",
                    "--filter",
                    "label=joinery.scope=" + self.spec.worker_scope,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                try:
                    data, _ = await asyncio.wait_for(listing.communicate(), 10)
                    containers = data.decode().split()
                    if containers:
                        cleanup = await asyncio.create_subprocess_exec(
                            "docker",
                            "rm",
                            "--force",
                            *containers,
                            stdout=asyncio.subprocess.DEVNULL,
                            stderr=asyncio.subprocess.DEVNULL,
                        )
                        await asyncio.wait_for(cleanup.wait(), 10)
                except TimeoutError:
                    if listing.returncode is None:
                        listing.kill()
                        await listing.wait()
            if self.readiness.state != "error":
                self.readiness.state = "disconnected"

    async def call(
        self, tool: str, arguments: dict[str, Any], timeout: float = 60
    ) -> CallToolResult:
        verdict = mcp_violation(self.spec.name, tool, self.project, arguments)
        if verdict is not None:
            if verdict.severity is Severity.STOP:
                raise PolicyStop(verdict.reason)
            raise PermissionError(verdict.reason)
        if self._task is None or self._task.done():
            self.tools.clear()
            await self.connect()
        definition = self.tools.get(tool)
        if definition is None:
            raise ValueError(f"{self.spec.name} does not expose {tool}.")
        error = next(
            Draft202012Validator(definition.input_schema).iter_errors(arguments), None
        )
        if error is not None:
            location = ".".join(str(part) for part in error.path) or "arguments"
            raise ValueError(
                f"Invalid {self.spec.name}.{tool} input at {location}: {error.validator} constraint failed."
            )
        root = worker_root(self.spec)
        if root is not None:
            # The worker sees its folder as /work: host paths go in as worker
            # paths, and worker paths come back as host paths.
            arguments = map_paths(arguments, root, PurePosixPath(WORKDIR))
        future: asyncio.Future[CallToolResult] = (
            asyncio.get_running_loop().create_future()
        )
        await self._queue.put(_Request(tool, arguments, future, timeout))
        try:
            result = await asyncio.wait_for(asyncio.shield(future), timeout + 2)
        except (TimeoutError, asyncio.CancelledError):
            future.cancel()
            await self.close()
            raise
        return result if root is None else rewrite_root(result, WORKDIR, root)

    async def probe(self, tool: str, arguments: dict[str, Any]) -> CallToolResult:
        """Only the caller's explicit read-only app probe can establish inspected.

        A probe fails, and the connection is not inspected, when the call
        returns an error or its capabilities say the app is missing; the reason
        goes into `readiness.detail`. The result is returned either way.
        """
        result = await self.call(tool, arguments)
        problem = probe_problem(f"{self.spec.name}.{tool}", result)
        if problem:
            self.readiness.inspected = False
            self.readiness.detail = problem
        else:
            self.readiness.inspected = True
        return result

    async def close(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self.tools.clear()
