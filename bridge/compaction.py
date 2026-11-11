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


async def ask(bridge: SessionBridge, project: Path) -> str:
    """The agent's summary: its text only, never shown in the chat."""
    text = ""
    event: AgentEvent
    async for event in bridge.send(
        Prompt(text=SUMMARY_REQUEST, project_dir=str(project))
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
        try:
            text = await asyncio.wait_for(ask(bridge, project), seconds)
        except (TimeoutError, OSError, RuntimeError, ValueError, TypeError):
            with contextlib.suppress(Exception):
                await bridge.cancel()
            text = ""
        recorded = session_summary.facts(project, since, controller.requests)
        summary = SessionSummary(
            agent=controller.agent_id,
            text=session_summary.build(text, recorded),
            facts=recorded,
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
