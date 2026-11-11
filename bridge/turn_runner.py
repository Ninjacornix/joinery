"""One user request, run the same way for every provider.

The provider streams its turn; Joinery owns what happens around it: the turn
starts in the controller, live edits are admitted by the controller, and when
the provider is done Joinery submits whatever the agent left unsubmitted and
says plainly when a change didn't make it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any, Protocol

from bridge.instructions import CONTINUE_AFTER_FAILED_SUBMIT
from bridge.protocol import (
    AgentEvent,
    Done,
    EditorAction,
    Execution,
    Message,
    MessageDelta,
    Prompt,
    SidebarMessage,
    ToolActivity,
    Usage,
)
from joinery.timing import TurnTiming
from joinery.turn import MAX_CONTINUATIONS, SubmitResult, TurnController


class AgentBridge(Protocol):
    def send(self, message: SidebarMessage) -> Any: ...

    def editor_result(self, request_id: str, result: dict[str, Any]) -> None: ...


Emit = Callable[[dict[str, Any]], None]


def failure_notice(result: SubmitResult) -> dict[str, Any]:
    """Joinery's own line when a change didn't pass: the agent's words aren't the last."""
    problems = "; ".join(p.message[:240] for p in result.problems[:2])
    return {
        "kind": "notice",
        "level": "warn",
        "text": "Journeyman's change didn't pass Joinery's checks"
        + (": " + problems.rstrip(".") if problems else "")
        + ". Nothing was changed.",
    }


def continuation(prompt: Prompt, result: SubmitResult) -> Prompt:
    """The same turn, asked once more: fix what the checks found and submit."""
    problems = "\n".join(f"- {p.check}: {p.message}" for p in result.problems)
    return prompt.model_copy(
        update={
            "text": CONTINUE_AFTER_FAILED_SUBMIT.format(problems=problems),
            "attachments": (),
        }
    )


async def stream(
    controller: TurnController,
    bridge: AgentBridge,
    prompt: Prompt,
    emit: Emit,
    timing: TurnTiming,
) -> bool:
    """Stream one provider prompt to the sidebar; returns the provider's ``Done.ok``."""
    gateway = controller.gateway
    ok = False
    event: AgentEvent
    async for event in bridge.send(prompt):
        if isinstance(event, Execution):
            timing.model, timing.effort, timing.mode = (
                event.model,
                event.effort,
                event.mode,
            )
        if isinstance(event, (MessageDelta, Message)):
            timing.response()
        if isinstance(event, ToolActivity):
            gateway.progress("Using " + event.server.capitalize() + "…")
        if isinstance(event, Usage):
            controller.meter.update(event.context_used, event.context_window)
        if isinstance(event, EditorAction):
            # A provider's own scene tool (Codex) is admitted like edit_scene.
            try:
                scene = controller.admit_live_edit(event.context)
            except ValueError as exc:
                bridge.editor_result(
                    event.request_id, {"success": False, "message": str(exc)}
                )
                continue
            controller.expect_live(event.request_id, scene)
        if isinstance(event, Done):
            ok = event.ok
            continue
        emit(event.model_dump())
    return ok


async def run_turn(
    controller: TurnController,
    bridge: AgentBridge,
    prompt: Prompt,
    emit: Emit,
    timing: TurnTiming,
    compact: Callable[[], Coroutine[Any, Any, Any]] | None = None,
    *,
    request: str = "",
) -> str:
    """Run one user request; returns its outcome ("done" or "failed").

    When the provider is done, a change the agent didn't submit is submitted for
    it. If that fails with problems the agent can fix, the agent is asked once
    more in the same turn; a change that still fails is dropped, and Joinery says
    so in its own line.
    """
    if controller.compaction is not None:
        # The session is being summarised while the user reviewed; wait for it.
        if not controller.compaction.done():
            controller.gateway.progress("Summarising the session…")
        await asyncio.gather(controller.compaction, return_exceptions=True)
        controller.compaction = None
    if controller.primer:
        prompt = prompt.model_copy(update={"primer": controller.primer})
        controller.primer = ""
    controller.begin(request or prompt.text)
    controller.gateway.progress("Thinking through your request…")
    ok = await stream(controller, bridge, prompt, emit, timing)
    asked = 0
    while True:
        result = await controller.end(ok, last=asked >= MAX_CONTINUATIONS)
        if result is None or result.status != "failed" or result.final:
            break
        # Joinery submitted the change the agent left, and it failed: the agent
        # gets one chance in this turn to fix it.
        asked += 1
        ok = await stream(
            controller, bridge, continuation(prompt, result), emit, timing
        )
    last = controller.last_result
    if last is not None and last.status == "failed" and last.final:
        emit(failure_notice(last))
    emit(Done(ok=ok).model_dump())
    if compact is not None and controller.needs_compaction():
        # Runs while the user reviews; the next request waits for it.
        controller.compaction = asyncio.create_task(compact())
    return "done" if ok else "failed"
