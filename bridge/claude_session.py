"""Claude Code's documented print/stream-json transport, retaining its session id."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from bridge.models import ModelChoice
from bridge.protocol import (
    AgentEvent,
    Done,
    Message,
    MessageDelta,
    Prompt,
    ToolActivity,
    Usage,
)
from bridge.provider_process import (
    INSTRUCTIONS,
    prompt_text,
    proxy_config,
    readonly_command,
    require_provider,
    stop_process,
)
from joinery.catalog import ROOT
from joinery.chat_attachments import encoded
from joinery.platform_support import launch_argv
from joinery.wiring import _command_str


def user_input(prompt: Prompt) -> bytes:
    """What goes to Claude's stdin: plain text, or with attached images one stream-json
    user message whose content is the text and each image as a base64 block."""
    if not prompt.attachments:
        return prompt_text(prompt).encode()
    content: list[dict[str, Any]] = [
        {"type": "text", "text": prompt_text(prompt, images_inline=True)}
    ]
    for path in prompt.attachments:
        media, data = encoded(Path(path))
        content.append(
            {
                "type": "image",
                "source": {"type": "base64", "media_type": media, "data": data},
            }
        )
    message = {"type": "user", "message": {"role": "user", "content": content}}
    return (json.dumps(message) + "\n").encode()


def _count(source: dict[str, Any], key: str) -> int:
    value = source.get(key)
    return value if isinstance(value, int) and value > 0 else 0


class ClaudeUsage:
    """This session's token use, from Claude Code's stream-json events: the last
    assistant message's usage is what the context holds; each result adds its turn's
    usage to the totals and names the model's context window."""

    def __init__(self) -> None:
        self.model = ""
        self.context = 0
        self.window = 0
        self.input = 0
        self.output = 0
        self.cached = 0

    def read(self, event: dict[str, Any]) -> Usage | None:
        """Take one stream-json event; returns a Usage when a turn's result arrives."""
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            self.model = str(event.get("model") or self.model)
        elif kind == "assistant":
            message = event.get("message")
            if isinstance(message, dict):
                self.model = str(message.get("model") or self.model)
                usage = message.get("usage")
                if isinstance(usage, dict):
                    self.context = sum(
                        _count(usage, key)
                        for key in (
                            "input_tokens",
                            "cache_creation_input_tokens",
                            "cache_read_input_tokens",
                            "output_tokens",
                        )
                    )
        elif kind == "result":
            usage = event.get("usage")
            if isinstance(usage, dict):
                cached = _count(usage, "cache_read_input_tokens")
                self.input += (
                    _count(usage, "input_tokens")
                    + _count(usage, "cache_creation_input_tokens")
                    + cached
                )
                self.output += _count(usage, "output_tokens")
                self.cached += cached
            models = event.get("modelUsage")
            if isinstance(models, dict):
                entries = {k: v for k, v in models.items() if isinstance(v, dict)}
                main = entries.get(self.model) or max(
                    entries.values(),
                    key=lambda entry: _count(entry, "outputTokens"),
                    default={},
                )
                self.window = _count(main, "contextWindow") or self.window
            if self.context or self.input or self.output:
                return Usage(
                    model=self.model,
                    context_used=self.context,
                    context_window=self.window,
                    input_tokens=self.input,
                    output_tokens=self.output,
                    cached_tokens=self.cached,
                )
        return None


class ClaudeSession:
    def __init__(self) -> None:
        self.usage = ClaudeUsage()
        self.gateway_socket: Path | None = None
        self.project = Path()
        self.executable = ""
        self.session_id = str(uuid.uuid4())
        self.started = False
        self.process: asyncio.subprocess.Process | None = None
        self.inspection_only = False
        self.effort_supported = False

    async def connect(self, project: Path, profile_id: str = "") -> None:
        if profile_id:
            raise ValueError(
                "This API profile uses Codex or opencode; select its configured driver."
            )
        self.executable = await require_provider("claude", project)
        self.project = project.resolve()
        if self.gateway_socket is None:
            raise ValueError("Connect the project gateway before starting Claude.")
        probe = await asyncio.create_subprocess_exec(
            *launch_argv(self.executable, ["--help"]),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            output, _ = await asyncio.wait_for(probe.communicate(), 5)
            self.effort_supported = b"--effort" in output
        finally:
            await stop_process(probe)

    async def models(self) -> list[ModelChoice]:
        # Print mode does not expose an account model inventory. Keep its
        # configured model, only offering effort if this CLI advertises it.
        return [
            ModelChoice(
                id="",
                name="Claude configured model",
                efforts=("low", "medium", "high") if self.effort_supported else (),
                default_effort="medium" if self.effort_supported else "",
                is_default=True,
            )
        ]

    async def send(self, prompt: Prompt) -> AsyncIterator[AgentEvent]:
        if not self.executable or self.gateway_socket is None:
            raise ValueError("Claude is not connected.")
        hook = _command_str(str(ROOT), "claude")
        settings = {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "mcp__.*|Bash|WebFetch",
                        "hooks": [{"type": "command", "command": hook, "timeout": 30}],
                    }
                ]
            }
        }
        args = [
            self.executable,
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--include-partial-messages",
            "--permission-prompts",
            "none",
            "--allowedTools",
            "mcp__joinery-registry__*",
            "mcp__joinery_registry__*",
            "--append-system-prompt",
            INSTRUCTIONS,
            "--settings",
            json.dumps(settings),
            "--mcp-config",
            json.dumps(
                {"mcpServers": {"joinery-registry": proxy_config(self.gateway_socket)}}
            ),
            "--resume" if self.started else "--session-id",
            self.session_id,
        ]
        env = dict(os.environ, DISABLE_TELEMETRY="1", DISABLE_ERROR_REPORTING="1")
        if prompt.effort:
            args += ["--effort", prompt.effort]
        if prompt.attachments:
            # Images go in as content blocks, which only the stream-json input takes.
            args += ["--input-format", "stream-json"]
        if self.inspection_only:
            env["JOINERY_INSPECTION_ONLY"] = "1"
            env["JOINERY_INSPECTION_ENDPOINT"] = str(self.gateway_socket)
            args += ["--disallowedTools", "Task", "Agent"]
        self.process = await asyncio.create_subprocess_exec(
            *readonly_command(launch_argv(args[0], args[1:]), self.project),
            cwd=self.project,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=os.name != "nt",
            limit=16_000_000,
        )
        assert self.process.stdin and self.process.stdout and self.process.stderr
        self.process.stdin.write(user_input(prompt))
        await self.process.stdin.drain()
        self.process.stdin.close()
        stderr = asyncio.create_task(self.process.stderr.read())
        streamed = False
        ok = False
        try:
            while line := await self.process.stdout.readline():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("session_id"):
                    self.session_id = str(event["session_id"])
                    self.started = True
                if isinstance(event, dict) and (usage := self.usage.read(event)):
                    yield usage
                kind = event.get("type")
                if kind == "stream_event":
                    delta = event.get("event", {}).get("delta", {})
                    if delta.get("type") == "text_delta":
                        yield MessageDelta(
                            text=str(delta.get("text", "")), new_message=not streamed
                        )
                        streamed = True
                elif kind == "assistant":
                    for part in event.get("message", {}).get("content", []):
                        if part.get("type") == "text" and not streamed:
                            yield Message(text=str(part["text"]))
                        elif part.get("type") == "tool_use":
                            yield ToolActivity(
                                server="claude", tool=str(part.get("name", ""))
                            )
                    streamed = False
                elif kind == "result":
                    ok = (
                        not event.get("is_error", False)
                        and event.get("subtype") == "success"
                    )
                    if not ok:
                        yield Message(
                            text="Claude could not finish this request: "
                            + str(
                                event.get(
                                    "result", event.get("subtype", "unknown error")
                                )
                            ),
                            level="warn",
                        )
            await self.process.wait()
            if self.process.returncode and not ok:
                yield Message(
                    text="Claude process exited with an error. Check provider authentication and the local diagnostics.",
                    level="warn",
                )
            yield Done(ok=ok and self.process.returncode == 0)
        finally:
            await stop_process(self.process)
            await stderr

    async def cancel(self) -> None:
        await stop_process(self.process)

    async def close(self) -> None:
        await self.cancel()
