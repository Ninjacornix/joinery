"""Private project IPC. Approval is deliberately absent from the model-facing API."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import secrets
import shutil
import sys
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Literal

from joinery.gateway import ProjectGateway
from joinery.mcp_runtime import PolicyStop
from joinery.platform_support import private_directory
from joinery.read_access import read_only


def search_tools(data: dict[str, Any], query: str) -> dict[str, Any]:
    """Rank words rather than requiring an entire sentence to be a substring."""
    words = set(re.findall(r"[a-z0-9]+", query.lower())) - {
        "a",
        "an",
        "and",
        "the",
        "to",
        "for",
        "with",
        "in",
        "of",
    }
    candidates = []
    for tool in data["tools"]:
        haystack = set(
            re.findall(
                r"[a-z0-9]+",
                (
                    tool["server"]
                    + " "
                    + tool["name"]
                    + " "
                    + tool.get("description", "")
                ).lower(),
            )
        )
        score = len(words & haystack)
        if not words or score:
            candidates.append((score, tool))
    candidates.sort(key=lambda entry: -entry[0])
    return {
        "servers": [
            {
                **{k: v for k, v in state.items() if k != "tools"},
                "tool_count": len(state.get("tools", [])),
            }
            for state in data["servers"]
        ],
        "tools": [
            {
                "server": tool["server"],
                "name": tool["name"],
                "description": tool.get("description", "")[:500],
            }
            for _, tool in candidates[:24]
        ],
        "total_matches": len(candidates),
        "hint": "Search by application or operation to narrow results; read tool_schema before calling. Native document operations save, export, import and record provenance automatically. Final validation and a visual review are prepared when the turn finishes.",
    }


class GatewayRPC:
    def __init__(
        self,
        gateway: ProjectGateway,
        on_stop: Callable[[str], Awaitable[None]] | None = None,
        *,
        transport: Literal["unix", "tcp"] | None = None,
        inspection_only: bool = False,
    ) -> None:
        self.gateway = gateway
        self.transport = transport or ("tcp" if os.name == "nt" else "unix")
        # Unix socket names have a short OS limit. Windows uses its user's temp
        # directory instead; its ACL is tightened before creating the endpoint.
        self.directory = Path(
            tempfile.mkdtemp(
                prefix="joinery-rpc-", dir=None if os.name == "nt" else "/tmp"
            )
        )
        try:
            private_directory(self.directory)
        except BaseException:
            shutil.rmtree(self.directory, ignore_errors=True)
            raise
        self.path = self.directory / (
            "gateway.endpoint.json" if self.transport == "tcp" else "gateway.sock"
        )
        self._token = secrets.token_urlsafe(32) if self.transport == "tcp" else ""
        self.server: asyncio.Server | None = None
        self.tasks: set[asyncio.Task[Any]] = set()
        self.on_stop = on_stop
        self.stop_reason = ""
        self.inspection_only = inspection_only
        self.delegate: (
            Callable[[list[dict[str, str]]], Awaitable[list[dict[str, str]]]] | None
        ) = None

    async def start(self) -> None:
        if self.transport == "tcp":
            self.server = await asyncio.start_server(
                self._client, "127.0.0.1", 0, limit=4_000_000
            )
            port = self.server.sockets[0].getsockname()[1]
            descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as stream:
                json.dump(
                    {
                        "version": 1,
                        "host": "127.0.0.1",
                        "port": port,
                        "token": self._token,
                    },
                    stream,
                )
        else:
            if sys.platform == "win32":
                raise ValueError("Native Windows gateways require local TCP transport.")
            else:
                self.server = await asyncio.start_unix_server(
                    self._client, str(self.path), limit=4_000_000
                )
                os.chmod(self.path, 0o600)

    async def _client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        task = asyncio.current_task()
        if task is not None:
            self.tasks.add(task)
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=30)
            request = json.loads(line)
            if not isinstance(request, dict):
                raise TypeError("Invalid gateway request.")
            if self._token:
                token = request.get("token")
                if not isinstance(token, str) or not secrets.compare_digest(
                    token, self._token
                ):
                    raise ValueError("Gateway authentication failed.")
            method = request.get("method")
            params = request.get("params", {})
            if not isinstance(params, dict):
                raise TypeError("Invalid gateway parameters.")
            if self.stop_reason:
                raise PolicyStop(self.stop_reason)
            if method == "policy_stop" and self.inspection_only:
                reason = params.get("reason")
                if not isinstance(reason, str) or not reason:
                    raise ValueError("Missing specialist policy-stop reason.")
                raise PolicyStop(reason[:1000])
            if method == "inventory":
                data = await self.gateway.discover()
                if self.inspection_only:
                    data = {
                        **data,
                        "tools": [
                            t
                            for t in data["tools"]
                            if read_only(t["server"], t["name"])
                        ],
                    }
                data = search_tools(data, str(params.get("query", "")))
            elif method == "schema":
                connection = await self.gateway.connection(str(params["server"]))
                data = connection.tools[str(params["tool"])].model_dump(
                    by_alias=True, exclude_none=True
                )
            elif method == "call":
                if self.inspection_only and not read_only(
                    str(params["server"]), str(params["tool"])
                ):
                    raise ValueError(
                        "Specialists can only inspect project tools; mutations stay on the main thread."
                    )
                arguments = params.get("arguments", {})
                if not isinstance(arguments, dict):
                    raise ValueError("Tool arguments must be an object.")
                result = await self.gateway.call(
                    str(params["server"]), str(params["tool"]), arguments
                )
                data = self.gateway.serialise(result)
                if str(params["server"]) == "godot" and (
                    str(params["tool"]).startswith("game_")
                    or params["tool"] in {"run_project", "stop_project"}
                ):
                    data["joinery"] = {
                        "target": "running-game",
                        "persistent": False,
                        "requires_review": bool(
                            self.gateway.transaction and self.gateway.transaction.diff()
                        ),
                    }
                elif self.gateway.transaction is not None:
                    data["joinery"] = {"status": "staged", "requires_review": True}
            elif method == "editor_context":
                data = {"editor": self.gateway.editor_context}
            elif method == "editor_view":
                # Read-only, so specialists may look too.
                if self.gateway.view_editor is None:
                    raise ValueError("No live editor is connected to this gateway.")
                data = await self.gateway.view_editor()
            elif method == "editor_edit":
                if self.inspection_only:
                    raise ValueError("Specialists cannot change the live editor.")
                if self.gateway.edit_scene is None:
                    raise ValueError("No live editor is connected to this gateway.")
                data = await self.gateway.edit_scene(params)
            elif method == "delegate":
                if self.inspection_only or self.delegate is None:
                    raise ValueError("Nested specialist delegation is unavailable.")
                jobs = params.get("tasks")
                if (
                    not isinstance(jobs, list)
                    or not 1 <= len(jobs) <= 2
                    or any(
                        not isinstance(job, dict)
                        or set(job) != {"name", "question"}
                        or any(
                            not isinstance(v, str) or not 1 <= len(v) <= 4000
                            for v in job.values()
                        )
                        for job in jobs
                    )
                ):
                    raise ValueError(
                        "Supply one or two named read-only inspection questions (up to 4000 characters)."
                    )
                data = {"results": await self.delegate(jobs)}
            else:
                raise ValueError(
                    "The model interface supports inventory, schema and call; no approval methods."
                )
            response = {"ok": True, "result": data}
        except PolicyStop as exc:
            first = not self.stop_reason
            self.stop_reason = str(exc)
            response = {"ok": False, "stop": True, "error": self.stop_reason}
            if first and self.on_stop is not None:
                asyncio.ensure_future(self.on_stop(self.stop_reason))
        except (
            OSError,
            ValueError,
            TypeError,
            KeyError,
            RuntimeError,
            TimeoutError,
        ) as exc:
            response = {"ok": False, "error": str(exc)}
        except asyncio.CancelledError:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()
            return
        finally:
            if task is not None:
                self.tasks.discard(task)
        try:
            writer.write((json.dumps(response) + "\n").encode())
            await writer.drain()
        finally:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()

    async def close(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        if not self.inspection_only:
            await self.gateway.close()
        shutil.rmtree(self.directory, ignore_errors=True)


async def rpc(path: Path, method: str, params: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {"method": method, "params": params}
    if path.name.endswith(".endpoint.json"):
        endpoint = json.loads(path.read_text())
        if (
            not isinstance(endpoint, dict)
            or endpoint.get("version") != 1
            or endpoint.get("host") != "127.0.0.1"
            or type(endpoint.get("port")) is not int
            or not 1 <= endpoint["port"] <= 65535
            or not isinstance(endpoint.get("token"), str)
            or len(endpoint["token"]) < 32
        ):
            raise ValueError("Invalid local gateway endpoint.")
        reader, writer = await asyncio.open_connection(
            "127.0.0.1", endpoint["port"], limit=4_000_000
        )
        payload["token"] = endpoint["token"]
    else:
        if sys.platform == "win32":
            raise ValueError("Native Windows requires a local gateway endpoint file.")
        else:
            reader, writer = await asyncio.open_unix_connection(
                str(path), limit=4_000_000
            )
    try:
        writer.write((json.dumps(payload) + "\n").encode())
        await writer.drain()
        data = json.loads(await asyncio.wait_for(reader.readline(), timeout=900))
        if not data["ok"]:
            raise RuntimeError(data["error"])
        result: dict[str, Any] = data["result"]
        return result
    finally:
        writer.close()
        await writer.wait_closed()
