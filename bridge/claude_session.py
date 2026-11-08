"""Claude Code's documented print/stream-json transport, retaining its session id."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

from bridge.protocol import (
    AgentEvent,
    Done,
    Message,
    MessageDelta,
    Prompt,
    ToolActivity,
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
from joinery.platform_support import launch_argv
from joinery.wiring import _command_str


class ClaudeSession:
    def __init__(self) -> None:
        self.gateway_socket: Path | None = None
        self.project = Path()
        self.executable = ""
        self.session_id = str(uuid.uuid4())
        self.started = False
        self.process: asyncio.subprocess.Process | None = None

    async def connect(self, project: Path, profile_id: str = "") -> None:
        if profile_id:
            raise ValueError(
                "This API profile uses Codex or opencode; select its configured driver."
            )
        self.executable = await require_provider("claude", project)
        self.project = project.resolve()
        if self.gateway_socket is None:
            raise ValueError("Connect the project gateway before starting Claude.")

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
        self.process.stdin.write(prompt_text(prompt).encode())
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
