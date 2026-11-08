"""Gemini/OpenCode ACP v1 sessions over supervised, bidirectional JSON-RPC."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from bridge.protocol import AgentEvent, Done, MessageDelta, Prompt, ToolActivity
from bridge.provider_process import (
    prompt_text,
    proxy_config,
    readonly_command,
    require_provider,
    stop_process,
)
from joinery.api_profiles import runtime
from joinery.mcp_runtime import PolicyStop, validate_arguments


class ACPSession:
    def __init__(self, agent: str) -> None:
        self.agent = agent
        self.gateway_socket: Path | None = None
        self.project = Path()
        self.process: asyncio.subprocess.Process | None = None
        self.session_id = ""
        self.sequence = 0
        self.pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self.events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.tasks: list[asyncio.Task[Any]] = []

    async def connect(self, project: Path, profile_id: str = "") -> None:
        executable = (
            await require_provider(self.agent, project)
            if not profile_id
            else self.agent
        )
        env = dict(os.environ)
        extra: list[str] = []
        if profile_id:
            driver, extra, values = runtime(profile_id)
            if driver != self.agent:
                raise ValueError("Select the API profile's configured provider.")
            env.update(values)
        self.project = project.resolve()
        if self.gateway_socket is None:
            raise ValueError(
                "Connect the project gateway before starting the provider."
            )
        args = [executable, *extra, "--acp"] if self.agent == "gemini" else [executable, *extra, "--standalone", "acp"]
        self.process = await asyncio.create_subprocess_exec(
            *readonly_command(args, project),
            cwd=project,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            limit=16_000_000,
        )
        assert self.process.stderr
        self.tasks = [
            asyncio.create_task(self._read()),
            asyncio.create_task(self.process.stderr.read()),
        ]
        try:
            initialized = await self.request(
                "initialize",
                {
                    "protocolVersion": 1,
                    "clientCapabilities": {},
                    "clientInfo": {"name": "joinery", "version": "1"},
                },
            )
            if initialized.get("protocolVersion") != 1:
                raise ValueError(
                    "This provider does not speak the supported ACP protocol version."
                )
            config = proxy_config(self.gateway_socket)
            config["name"] = "joinery-registry"
            config["env"] = [
                {"name": key, "value": value} for key, value in config["env"].items()
            ]
            session = await self.request(
                "session/new", {"cwd": str(project), "mcpServers": [config]}
            )
            self.session_id = str(session["sessionId"])
        except BaseException:
            await self.close()
            raise

    async def _write(self, value: dict[str, Any]) -> None:
        if (
            self.process is None
            or self.process.stdin is None
            or self.process.returncode is not None
        ):
            raise ConnectionError("The provider process is no longer connected.")
        self.process.stdin.write((json.dumps(value) + "\n").encode())
        await self.process.stdin.drain()

    async def request(
        self, method: str, params: dict[str, Any], timeout: int = 60
    ) -> dict[str, Any]:
        self.sequence += 1
        identifier = self.sequence
        future: asyncio.Future[dict[str, Any]] = (
            asyncio.get_running_loop().create_future()
        )
        self.pending[identifier] = future
        try:
            await self._write(
                {"jsonrpc": "2.0", "id": identifier, "method": method, "params": params}
            )
            return await asyncio.wait_for(future, timeout)
        finally:
            self.pending.pop(identifier, None)

    async def _read(self) -> None:
        assert self.process and self.process.stdout
        try:
            while line := await self.process.stdout.readline():
                data = json.loads(line)
                if "method" in data and "id" in data:
                    await self._client_request(data)
                elif "method" in data:
                    await self.events.put(data)
                elif data.get("id") in self.pending:
                    future = self.pending[data["id"]]
                    if not future.done():
                        if "error" in data:
                            future.set_exception(
                                RuntimeError(
                                    str(
                                        data["error"].get(
                                            "message", "Provider protocol error"
                                        )
                                    )
                                )
                            )
                        else:
                            future.set_result(data.get("result", {}))
        except (OSError, ValueError, RuntimeError) as exc:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(RuntimeError(str(exc)))
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(ConnectionError("Provider connection closed."))

    async def _client_request(self, data: dict[str, Any]) -> None:
        if data["method"] != "session/request_permission":
            await self._write(
                {
                    "jsonrpc": "2.0",
                    "id": data["id"],
                    "error": {
                        "code": -32601,
                        "message": "Use the project gateway for document operations.",
                    },
                }
            )
            return
        params = data.get("params", {})
        call = params.get("toolCall", {})
        allowed = False
        try:
            validate_arguments(call.get("rawInput", {}))
            title = str(call.get("title", "")).replace("_", "-")
            allowed = (
                call.get("kind") in {"read", "search"} or "joinery-registry" in title
            )
        except PolicyStop as exc:
            await self.events.put(
                {"method": "joinery/stop", "params": {"reason": str(exc)}}
            )
        options = params.get("options", [])
        option = next(
            (
                o
                for o in options
                if o.get("kind") == ("allow_once" if allowed else "reject_once")
            ),
            None,
        )
        outcome = (
            {"outcome": "selected", "optionId": option["optionId"]}
            if option
            else {"outcome": "cancelled"}
        )
        await self._write(
            {"jsonrpc": "2.0", "id": data["id"], "result": {"outcome": outcome}}
        )

    async def send(self, prompt: Prompt) -> AsyncIterator[AgentEvent]:
        if not self.session_id:
            raise ValueError("The provider session is not connected.")
        task = asyncio.create_task(
            self.request(
                "session/prompt",
                {
                    "sessionId": self.session_id,
                    "prompt": [{"type": "text", "text": prompt_text(prompt)}],
                },
                1800,
            )
        )
        streamed = False
        try:
            while not task.done() or not self.events.empty():
                next_event = asyncio.create_task(self.events.get())
                done, _ = await asyncio.wait(
                    {next_event, task}, return_when=asyncio.FIRST_COMPLETED
                )
                if next_event not in done:
                    next_event.cancel()
                    await asyncio.gather(next_event, return_exceptions=True)
                    continue
                event = next_event.result()
                if event["method"] == "joinery/stop":
                    await self.cancel()
                    raise PolicyStop(event["params"]["reason"])
                update = event.get("params", {}).get("update", {})
                if (
                    update.get("sessionUpdate") == "agent_message_chunk"
                    and update.get("content", {}).get("type") == "text"
                ):
                    yield MessageDelta(
                        text=str(update["content"]["text"]), new_message=not streamed
                    )
                    streamed = True
                elif update.get("sessionUpdate") == "tool_call":
                    yield ToolActivity(
                        server=self.agent, tool=str(update.get("title", "tool"))
                    )
            response = await task
            yield Done(ok=response.get("stopReason") == "end_turn")
        finally:
            if not task.done():
                await self.cancel()
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def cancel(self) -> None:
        if self.session_id and self.process and self.process.returncode is None:
            with contextlib.suppress(OSError, RuntimeError):
                await self._write(
                    {
                        "jsonrpc": "2.0",
                        "method": "session/cancel",
                        "params": {"sessionId": self.session_id},
                    }
                )

    async def close(self) -> None:
        await self.cancel()
        await stop_process(self.process)
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()
