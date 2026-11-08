"""Framed local chat transport used by the editor's polled native pipes."""

from __future__ import annotations

import asyncio
import contextlib
import getpass
import json
import os
import sys
import threading
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, BinaryIO, Protocol

from bridge.host import Bridge
from bridge.protocol import Done, EditorAction, Message, Prompt
from joinery.art_approval import approve, review
from joinery.editor_ops import validate_edit
from joinery.gateway import ProjectGateway
from joinery.gateway_rpc import GatewayRPC


def emit(event: dict[str, Any]) -> None:
    print(json.dumps(event), flush=True)


class ThreadInput:
    """Read synchronous Windows anonymous stdin pipes without blocking asyncio.

    Godot uses CreatePipe, not overlapped named pipes. Proactor connect_read_pipe
    cannot safely register those handles. This daemon owns no editor state and
    exits on EOF; shutdown never waits for a blocked executor thread.
    """

    def __init__(self, stream: BinaryIO, reader: asyncio.StreamReader) -> None:
        self.closed = threading.Event()
        loop = asyncio.get_running_loop()

        def deliver(callback: Callable[..., None], *values: Any) -> None:
            if not self.closed.is_set():
                with contextlib.suppress(RuntimeError):
                    loop.call_soon_threadsafe(callback, *values)

        def pump() -> None:
            try:
                while not self.closed.is_set():
                    line = stream.readline(1_000_001)
                    if len(line) > 1_000_000:
                        deliver(
                            reader.set_exception,
                            ValueError("Chat input exceeds the message size limit."),
                        )
                        return
                    if not line:
                        deliver(reader.feed_eof)
                        return
                    deliver(reader.feed_data, line)
            except OSError:
                deliver(reader.set_exception, ConnectionError("Chat input closed."))

        self.thread = threading.Thread(target=pump, name="joinery-stdin", daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.closed.set()


class InputTransport(Protocol):
    def close(self) -> None: ...


async def input_reader(stream: BinaryIO) -> tuple[asyncio.StreamReader, InputTransport]:
    reader = asyncio.StreamReader(limit=1_000_000)
    if os.name == "nt":
        return reader, ThreadInput(stream, reader)
    transport, _ = await asyncio.get_running_loop().connect_read_pipe(
        lambda: asyncio.StreamReaderProtocol(reader), stream
    )
    return reader, transport


async def serve(project: Path, agent_id: str, profile_id: str = "") -> int:
    bridge: Bridge | None = None
    active: asyncio.Task[None] | None = None
    try:
        gateway = ProjectGateway(project)
    except (OSError, ValueError) as exc:
        emit(
            {
                "kind": "connection",
                "state": "error",
                "agent": agent_id,
                "message": str(exc),
            }
        )
        return 1
    editor_calls: dict[str, asyncio.Future[dict[str, Any]]] = {}
    gateway.progress = lambda text: emit({"kind": "progress", "text": text})

    async def edit_scene(payload: dict[str, Any]) -> dict[str, Any]:
        context = gateway.editor_context
        known = json.loads(context.get("selection_instances", "{}"))
        edit = validate_edit(project, payload, tuple(known))
        if active is None or active.done():
            raise ValueError("Editor actions require an active user request.")
        if gateway.transaction is not None:
            scene = context.get("scene_path", "").removeprefix("res://")
            if any(change["path"] == scene for change in gateway.transaction.diff()):
                raise ValueError(
                    "The saved scene is already staged; do not mix saved-file and live changes to that scene."
                )
        request_id = uuid.uuid4().hex
        future: asyncio.Future[dict[str, Any]] = (
            asyncio.get_running_loop().create_future()
        )
        editor_calls[request_id] = future
        emit(
            EditorAction(
                request_id=request_id,
                payload=edit.model_dump(mode="json"),
                context=context,
                targets=tuple(known),
            ).model_dump()
        )
        try:
            result = await asyncio.wait_for(future, 30)
            gateway.live_edit_pending = bool(result.get("success"))
            return result
        finally:
            editor_calls.pop(request_id, None)

    gateway.edit_scene = edit_scene

    async def policy_stop(reason: str) -> None:
        if bridge is not None:
            with contextlib.suppress(Exception):
                await bridge.cancel()
        if active is not None and not active.done():
            active.cancel()
            await asyncio.gather(active, return_exceptions=True)
        await gateway.cancel()
        emit(
            Message(
                text="Stopped by project policy: " + reason, level="warn"
            ).model_dump()
        )
        emit(Done(ok=False).model_dump())

    rpc_server: GatewayRPC | None = None
    try:
        rpc_server = GatewayRPC(gateway, policy_stop)
        reader, transport = await input_reader(sys.stdin.buffer)
    except (OSError, ValueError) as exc:
        if rpc_server is not None:
            await rpc_server.close()
        else:
            await gateway.close()
        emit(
            {
                "kind": "connection",
                "state": "error",
                "agent": agent_id,
                "message": str(exc),
            }
        )
        return 1
    assert rpc_server is not None

    async def turn(data: dict[str, Any]) -> None:
        assert bridge is not None
        try:
            context = data.get("targets", [])
            if not isinstance(context, list):
                raise TypeError("Editor selection is invalid.")
            targets = tuple(
                {str(k): str(v) for k, v in entry.items()}
                for entry in context
                if isinstance(entry, dict)
            )
            editor = data.get("editor_context", {})
            if not isinstance(editor, dict):
                raise TypeError("Editor scene context is invalid.")
            prompt = Prompt(
                text=str(data["text"]),
                project_dir=str(project),
                targets=tuple(entry.get("id", "") for entry in targets),
                target_context=targets,
                editor_context={str(k): str(v) for k, v in editor.items()},
            )
            gateway.editor_context = prompt.editor_context
            gateway.request_summary = prompt.text
            gateway.progress("Thinking through your request…")
            async for event in bridge.send(prompt):
                if isinstance(event, EditorAction):
                    if gateway.transaction is not None and any(
                        change["path"]
                        == prompt.editor_context.get("scene_path", "").removeprefix(
                            "res://"
                        )
                        for change in gateway.transaction.diff()
                    ):
                        bridge.editor_result(
                            event.request_id,
                            {
                                "success": False,
                                "message": "Finish the staged-file review before live scene changes.",
                            },
                        )
                        continue
                    gateway.live_edit_pending = True
                if isinstance(event, Done):
                    if event.ok:
                        preview = await gateway.finish()
                        if preview is not None:
                            emit(preview)
                    else:
                        await gateway.cancel()
                emit(event.model_dump())
        except (
            OSError,
            RuntimeError,
            ValueError,
            TypeError,
            KeyError,
            TimeoutError,
        ) as exc:
            await gateway.cancel()
            with contextlib.suppress(Exception):
                await bridge.cancel()
            emit(
                Message(
                    text=str(exc) or "Codex could not complete this message.",
                    level="warn",
                ).model_dump()
            )
            emit(Done(ok=False).model_dump())
            if isinstance(exc, OSError):
                emit({"kind": "connection", "state": "error", "message": str(exc)})

    try:
        bridge = Bridge(agent_id)
        if not project.is_dir() or not (project / "project.godot").is_file():
            raise ValueError("Open a Godot project before connecting chat.")
        await rpc_server.start()
        bridge.bind_gateway(rpc_server.path)
        await bridge.connect(project, profile_id)
        emit(
            {
                "kind": "connection",
                "state": "connected",
                "agent": agent_id,
                "mode": "reviewed-scene-edits",
            }
        )
        while line := await reader.readline():
            try:
                data = json.loads(line)
                if not isinstance(data, dict):
                    raise TypeError("Invalid chat message.")
                if data.get("kind") == "decision":
                    if active is not None and not active.done():
                        raise ValueError(
                            "Wait for the current turn to finish before accepting file changes."
                        )
                    decision = await gateway.decide(
                        str(data.get("transaction_id", "")),
                        bool(data.get("accept", False)),
                    )
                    emit(decision)
                    if "art_review" in decision:
                        emit(decision["art_review"])
                elif data.get("kind") == "rollback_decision":
                    emit(gateway.rollback_decision(str(data.get("transaction_id", ""))))
                elif data.get("kind") == "art_review":
                    emit(review(project))
                elif data.get("kind") == "art_approval":
                    expected_sha256 = str(data.get("sha256", ""))
                    if not expected_sha256:
                        raise ValueError(
                            "Review the art direction before approving it."
                        )
                    approve(project, getpass.getuser(), expected_sha256)
                    emit(
                        Message(
                            text="This version of the project art direction is approved."
                        ).model_dump()
                    )
                elif data.get("kind") == "live_decision":
                    gateway.live_edit_pending = False
                elif data.get("kind") == "editor_result":
                    result = data.get("result")
                    if not isinstance(result, dict):
                        raise TypeError("Invalid editor tool result.")
                    if not result.get("success", False):
                        gateway.live_edit_pending = False
                    request_id = str(data.get("request_id", ""))
                    if request_id in editor_calls:
                        editor_calls[request_id].set_result(result)
                    else:
                        bridge.editor_result(request_id, result)
                elif data.get("kind") == "cancel":
                    await gateway.cancel()
                    if active is not None and not active.done():
                        with contextlib.suppress(Exception):
                            await bridge.cancel()
                        active.cancel()
                        await asyncio.gather(active, return_exceptions=True)
                        emit(Done(ok=False).model_dump())
                elif data.get("kind") == "prompt":
                    if active is not None and not active.done():
                        emit(
                            Message(
                                text="Codex is still responding.", level="warn"
                            ).model_dump()
                        )
                    elif str(data.get("text", "")).strip():
                        rpc_server.stop_reason = ""
                        active = asyncio.create_task(turn(data))
                else:
                    raise ValueError("Unknown chat message.")
            except (TypeError, ValueError, OSError, RuntimeError, TimeoutError) as exc:
                emit(Message(text=str(exc), level="warn").model_dump())
        return 0
    except (
        OSError,
        RuntimeError,
        ValueError,
        TypeError,
        KeyError,
        TimeoutError,
    ) as exc:
        emit(
            {
                "kind": "connection",
                "state": "error",
                "agent": agent_id,
                "message": str(exc),
            }
        )
        return 1
    finally:
        if active is not None and not active.done():
            active.cancel()
            await asyncio.gather(active, return_exceptions=True)
        if bridge is not None:
            await bridge.close()
        await rpc_server.close()
        transport.close()


def run(project: Path, agent_id: str, profile_id: str = "") -> int:
    return asyncio.run(serve(project.resolve(), agent_id, profile_id))
