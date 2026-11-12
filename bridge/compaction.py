"""Continue a long conversation in a fresh provider session, from a short summary.

The same for every provider: Joinery asks the agent for a summary in a
read-only turn (the controller refuses every change and submit meanwhile),
adds what it recorded itself, saves it, starts a fresh provider session and
gives the summary to the next prompt. If the agent's summary fails, Joinery's
own facts stand alone.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from bridge.instructions import SUMMARY_REQUEST
from bridge.models import Selection
from bridge.protocol import (
    AgentEvent,
    Done,
    EditorAction,
    Message,
    MessageDelta,
    Prompt,
    SidebarMessage,
    Usage,
)
from joinery import session_summary
from joinery.routing import routing_settings
from joinery.session_summary import SessionSummary, SummaryStore
from joinery.turn import TurnController

#: Seconds the agent has to write its summary.
SUMMARY_SECONDS = 90
NOTICE = (
    "The conversation was getting long, so Journeyman continues in a fresh session "
    "from a short summary."
)


class SessionBridge(Protocol):
    def send(self, message: SidebarMessage) -> Any: ...

    def editor_result(self, request_id: str, result: dict[str, Any]) -> None: ...

    async def cancel(self) -> None: ...

    async def new_session(self) -> None: ...


async def ask(bridge: SessionBridge, project: Path, model: str = "") -> str:
    """The agent's summary: its text only, never shown in the chat. ``model`` is
    the light model to write it ("" for the session's own)."""
    text = ""
    event: AgentEvent
    async for event in bridge.send(
        Prompt(
            text=SUMMARY_REQUEST,
            project_dir=str(project),
            selection=Selection(model=model),
        )
    ):
        if isinstance(event, MessageDelta | Message):
            text += event.text
            if len(text) > session_summary.MAX_AGENT_TEXT:
                text = text[: session_summary.MAX_AGENT_TEXT]
        elif isinstance(event, EditorAction):
            bridge.editor_result(
                event.request_id,
                {"success": False, "message": "No changes while summarising."},
            )
        elif isinstance(event, Done) and not event.ok:
            return ""
    return text


async def _ask_within(
    bridge: SessionBridge, project: Path, model: str, seconds: float
) -> str:
    try:
        return await asyncio.wait_for(ask(bridge, project, model), seconds)
    except (TimeoutError, OSError, RuntimeError, ValueError, TypeError):
        with contextlib.suppress(Exception):
            await bridge.cancel()
        return ""


async def compact(
    controller: TurnController,
    bridge: SessionBridge,
    store: SummaryStore,
    project: Path,
    emit: Callable[[dict[str, Any]], None],
    since: float = 0.0,
    seconds: float = SUMMARY_SECONDS,
    chat_id: str = "",
) -> SessionSummary:
    """Summarise the session and continue in a fresh provider session.

    ``chat_id`` is the chat summarised: the summary primes only its next prompt."""
    controller.begin_compaction()
    try:
        text = ""
        light = routing_settings(project).light_model(controller.agent_id)
        if light:
            # The light model writes it first; on no text the session's own model
            # tries again with the time left.
            text = await _ask_within(bridge, project, light, seconds / 2)
        if not text.strip():
            text = await _ask_within(
                bridge, project, "", seconds / 2 if light else seconds
            )
        recorded = session_summary.facts(project, since, controller.requests)
        try:
            earlier = store.latest()
        except (OSError, ValueError):
            earlier = None
        held = session_summary.pinned(
            project,
            controller.todo or controller.pending_todo,
            list(getattr(controller, "answers", []) or []),
            earlier,
        )
        summary = SessionSummary(
            agent=controller.agent_id,
            text=session_summary.build(text, recorded, held),
            facts=recorded,
            pinned=held,
        )
        store.save(summary)
        await bridge.new_session()
        controller.primer, controller.primer_chat = summary.text, chat_id
        controller.meter.reset()
        emit({"kind": "notice", "level": "info", "text": NOTICE})
        emit(Usage().model_dump())
        return summary
    finally:
        controller.end_compaction()
