"""Gemini/OpenCode ACP v1 sessions over supervised, bidirectional JSON-RPC."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from bridge.models import ModelChoice, route
from bridge.protocol import (
    AgentEvent,
    Done,
    Execution,
    MessageDelta,
    Models,
    Prompt,
    ToolActivity,
    Usage,
)
from bridge.provider_process import (
    prompt_text,
    proxy_config,
    readonly_command,
    require_provider,
    stop_process,
)
from joinery.api_profiles import list_public, runtime
from joinery.chat_attachments import encoded
from joinery.mcp_runtime import PolicyStop, validate_arguments
from joinery.platform_support import launch_argv


def _count(source: dict[str, Any], key: str) -> int:
    value = source.get(key)
    return value if isinstance(value, int) and value > 0 else 0


class ACPUsage:
    """Token use from agents that report it over ACP: a `usage_update` session update
    gives the context (`used` of `size`), and a prompt response's `usage` the turn's
    tokens, added to the session totals. Agents that report neither show nothing."""

    def __init__(self) -> None:
        self.model = ""
        self.used = 0
        self.size = 0
        self.input = 0
        self.output = 0
        self.cached = 0

    def _event(self) -> Usage:
        return Usage(
            model=self.model,
            context_used=self.used,
            context_window=self.size,
            input_tokens=self.input,
            output_tokens=self.output,
            cached_tokens=self.cached,
        )

    def context(self, update: dict[str, Any]) -> Usage | None:
        self.used = _count(update, "used") or self.used
        self.size = _count(update, "size") or self.size
        return self._event() if self.used or self.size else None

    def turn(self, usage: object) -> Usage | None:
        if not isinstance(usage, dict):
            return None
        cached = _count(usage, "cachedReadTokens")
        self.input += _count(usage, "inputTokens") + cached
        self.output += _count(usage, "outputTokens") + _count(usage, "thoughtTokens")
        self.cached += cached
        if not self.used:
            # Without a context report, the last turn's total stands in for it.
            self.used = _count(usage, "totalTokens")
        return self._event() if self.input or self.output else None


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
        self.inspection_only = False
        self.model_choices: list[ModelChoice] = []
        self.current_model = ""
        self.profile_model = ""
        self.config_options: list[dict[str, Any]] = []
        #: The agent takes image content blocks (ACP promptCapabilities.image); without it
        #: attached images are listed by path in the prompt text.
        self.image_input = False
        self.usage = ACPUsage()

    async def connect(self, project: Path, profile_id: str = "") -> None:
        executable = (
            await require_provider(self.agent, project)
            if not profile_id
            else self.agent
        )
        env = dict(os.environ)
        if self.inspection_only:
            env["JOINERY_INSPECTION_ONLY"] = "1"
            if self.gateway_socket is not None:
                env["JOINERY_INSPECTION_ENDPOINT"] = str(self.gateway_socket)
        extra: list[str] = []
        if profile_id:
            driver, extra, values = runtime(profile_id)
            if driver != self.agent:
                raise ValueError("Select the API profile's configured provider.")
            env.update(values)
            self.profile_model = str(
                next(p for p in list_public() if p["id"] == profile_id)["model"]
            )
        self.project = project.resolve()
        if self.gateway_socket is None:
            raise ValueError(
                "Connect the project gateway before starting the provider."
            )
        args = (
            [executable, *extra, "--acp"]
            if self.agent == "gemini"
            else [executable, *extra, "--standalone", "acp"]
        )
        self.process = await asyncio.create_subprocess_exec(
            *readonly_command(launch_argv(args[0], args[1:]), project),
            cwd=project,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=os.name != "nt",
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
            capabilities = initialized.get("agentCapabilities") or {}
            prompts = capabilities.get("promptCapabilities") or {}
            self.image_input = bool(prompts.get("image"))
            config = proxy_config(self.gateway_socket)
            config["name"] = "joinery-registry"
            config["env"] = [
                {"name": key, "value": value} for key, value in config["env"].items()
            ]
            session = await self.request(
                "session/new", {"cwd": str(project), "mcpServers": [config]}
            )
            self.session_id = str(session["sessionId"])
            models = session.get("models") or {}
            self.current_model = str(models.get("currentModelId") or "")
            self.model_choices = [
                ModelChoice(
                    id=str(m["modelId"]),
                    name=str(m.get("name") or m["modelId"]),
                    is_default=m["modelId"] == self.current_model,
                )
                for m in models.get("availableModels", [])
            ]
            self.load_config(session.get("configOptions", []))
        except BaseException:
            await self.close()
            raise

    async def models(self) -> list[ModelChoice]:
        return self.model_choices

    @staticmethod
    def values(option: dict[str, Any]) -> list[dict[str, str]]:
        values = []
        for group in option.get("options", []):
            for entry in group.get("options", [group]):
                if isinstance(entry.get("value"), str):
                    values.append(
                        {
                            "id": entry["value"],
                            "name": str(entry.get("name") or entry["value"]),
                        }
                    )
        return values

    def config(self, category: str) -> dict[str, Any] | None:
        return next(
            (
                item
                for item in self.config_options
                if item.get("type") == "select"
                and (item.get("category") or item.get("id")) == category
            ),
            None,
        )

    def load_config(self, options: list[dict[str, Any]]) -> None:
        self.config_options = options
        model = self.config("model")
        thought = self.config("thought_level")
        self.model_choices = [
            choice.model_copy(update={"efforts": (), "default_effort": ""})
            for choice in self.model_choices
        ]
        if model:
            self.current_model = str(model.get("currentValue") or "")
            self.model_choices = [
                ModelChoice(
                    id=value["id"],
                    name=value["name"],
                    is_default=value["id"] == self.current_model,
                )
                for value in self.values(model)
            ]
        if thought:
            self.model_choices = [
                choice.model_copy(
                    update={
                        "efforts": tuple(value["id"] for value in self.values(thought)),
                        "default_effort": str(thought.get("currentValue") or ""),
                    }
                )
                for choice in self.model_choices
            ]
        if self.profile_model:
            self.model_choices = [
                choice
                for choice in self.model_choices
                if choice.id in {self.profile_model, "joinery/" + self.profile_model}
            ]

    async def set_config(self, option: dict[str, Any], value: str) -> None:
        if value not in {entry["id"] for entry in self.values(option)}:
            raise ValueError(
                "This provider no longer offers the selected configuration value."
            )
        if option.get("currentValue") != value:
            result = await self.request(
                "session/set_config_option",
                {
                    "sessionId": self.session_id,
                    "configId": option["id"],
                    "value": value,
                },
            )
            self.load_config(result.get("configOptions", self.config_options))
            accepted = next(
                (
                    item
                    for item in self.config_options
                    if item.get("id") == option["id"]
                ),
                None,
            )
            if accepted is None or accepted.get("currentValue") != value:
                raise ValueError(
                    "The provider did not accept the requested model or reasoning setting."
                )

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

    def prompt_blocks(self, prompt: Prompt) -> list[dict[str, Any]]:
        """The ACP prompt: the text, then each attached image when the agent takes images."""
        inline = self.image_input and bool(prompt.attachments)
        blocks: list[dict[str, Any]] = [
            {"type": "text", "text": prompt_text(prompt, images_inline=inline)}
        ]
        if inline:
            for path in prompt.attachments:
                media, data = encoded(Path(path))
                blocks.append({"type": "image", "mimeType": media, "data": data})
        return blocks

    async def send(self, prompt: Prompt) -> AsyncIterator[AgentEvent]:
        if not self.session_id:
            raise ValueError("The provider session is not connected.")
        if prompt.model and prompt.model != self.current_model:
            model = self.config("model")
            if model:
                await self.set_config(model, prompt.model)
            else:
                await self.request(
                    "session/set_model",
                    {"sessionId": self.session_id, "modelId": prompt.model},
                )
            self.current_model = prompt.model
        if self.config_options:
            choice, effort, mode = route(
                prompt.selection.model_copy(update={"model": prompt.model}),
                prompt.text,
                self.model_choices,
            )
            thought = self.config("thought_level")
            if thought and effort:
                await self.set_config(thought, effort)
            yield Models(models=self.model_choices)
            yield Execution(model=choice.id, name=choice.name, effort=effort, mode=mode)
        task = asyncio.create_task(
            self.request(
                "session/prompt",
                {
                    "sessionId": self.session_id,
                    "prompt": self.prompt_blocks(prompt),
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
                if update.get("sessionUpdate") == "config_option_update":
                    self.load_config(update.get("configOptions", []))
                    yield Models(models=self.model_choices)
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
                elif (
                    update.get("sessionUpdate") == "usage_update"
                    and (usage := self.usage.context(update)) is not None
                ):
                    yield usage
            response = await task
            if (usage := self.usage.turn(response.get("usage"))) is not None:
                yield usage
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
