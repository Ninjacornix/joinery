"""One user request, run the same way for every provider.

The provider streams its turn; Joinery owns what happens around it: the turn
starts in the controller, live edits are admitted by the controller, and when
the provider is done Joinery submits whatever the agent left unsubmitted and
says plainly when a change didn't make it.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable, Coroutine
from pathlib import Path
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
from joinery import done_when, metrics
from joinery.timing import TurnTiming
from joinery.turn import MAX_CONTINUATIONS, Problem, SubmitResult, TurnController


class AgentBridge(Protocol):
    def send(self, message: SidebarMessage) -> Any: ...

    def editor_result(self, request_id: str, result: dict[str, Any]) -> None: ...


Emit = Callable[[dict[str, Any]], None]


#: Each check's failure in plain words; the raw output stays with the agent.
PLAIN = {
    "protected": "it changed project settings only project setup may change",
    "live_conflict": "it changed one scene both live and as a saved file",
    "references": "{paths} {verb} a file the change doesn't include",
    "assets": "an asset has no valid provenance record",
    "dependencies": "it adds a package Joinery doesn't allow",
    "art_bible": "it breaks the art direction's rules",
    "godot_import": "Godot couldn't import it",
    "tests": "the project's tests failed",
    "conflict": "another accepted change touched the same file",
    "reviewer": "the reviewer found problems",
}


def _plain(problem: Problem) -> str:
    paths = [Path(p).name for p in problem.paths[:2]]
    text = PLAIN.get(problem.check, "")
    if not text:
        return ""
    if "{paths}" in text:
        if not paths:
            return "it uses a file the change doesn't include"
        return text.format(
            paths=" and ".join(paths), verb="use" if len(paths) > 1 else "uses"
        )
    return text + (" (" + ", ".join(paths) + ")" if paths else "")


def failure_notice(result: SubmitResult) -> dict[str, Any]:
    """Joinery's own line when a change didn't pass: the agent's words aren't the last.

    Plain words only; the checks' raw output went to the agent."""
    if any(p.check in {"checks", "review"} for p in result.problems):
        text = (
            "Joinery couldn't check Journeyman's change, so nothing was changed. "
            + (
                "Make sure Docker is running, then ask again."
                if any(p.check == "checks" for p in result.problems)
                else "Ask again in a moment."
            )
        )
    else:
        reasons = [r for r in dict.fromkeys(map(_plain, result.problems)) if r]
        text = (
            "Journeyman's change didn't pass Joinery's checks"
            + (": " + "; ".join(reasons[:2]) if reasons else "")
            + ". Nothing was changed."
        )
    return {"kind": "notice", "level": "warn", "text": text}


def continuation(prompt: Prompt, result: SubmitResult) -> Prompt:
    """The same turn, asked once more: fix what the checks found and submit."""
    problems = "\n".join(f"- {p.check}: {p.message}" for p in result.problems)
    return prompt.model_copy(
        update={
            "text": CONTINUE_AFTER_FAILED_SUBMIT.format(problems=problems),
            "attachments": (),
            # The session already has the summary from this turn's first prompt.
            "primer": "",
        }
    )


def _usage(controller: TurnController, usage: Usage | None) -> metrics.Totals | None:
    """This provider prompt's tokens, from the session totals it reported."""
    if usage is None:
        return None
    now = (usage.input_tokens, usage.output_tokens, usage.cached_tokens)
    turn = metrics.usage_delta(controller.usage_totals, now)
    controller.usage_totals = now
    return turn


async def stream(
    controller: TurnController,
    bridge: AgentBridge,
    prompt: Prompt,
    emit: Emit,
    timing: TurnTiming,
    asked: int = 0,
) -> bool:
    """Stream one provider prompt to the sidebar; returns the provider's ``Done.ok``.

    The prompt is one agent-turn span of the change, with the tokens the
    provider reported for it."""
    ok = False
    usage: Usage | None = None
    attrs = metrics.agent_attributes(controller.agent_id, "lead")
    attrs[metrics.ROUND] = asked
    attrs[metrics.TASK] = getattr(controller.gateway, "task_id", "")
    with timing.trace.span(metrics.AGENT_TURN, "invoke_agent lead", attrs) as span:
        try:
            ok, usage = await _stream(controller, bridge, prompt, emit, timing)
        finally:
            if timing.model:
                span[metrics.REQUEST_MODEL] = timing.model
            if usage is not None and usage.model:
                span[metrics.RESPONSE_MODEL] = usage.model
            span.update(metrics.token_attributes(_usage(controller, usage)))
    return ok


async def _stream(
    controller: TurnController,
    bridge: AgentBridge,
    prompt: Prompt,
    emit: Emit,
    timing: TurnTiming,
) -> tuple[bool, Usage | None]:
    gateway = controller.gateway
    ok = False
    usage: Usage | None = None
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
            usage = event
        if isinstance(event, EditorAction):
            # A provider's own scene tool (Codex) is admitted like edit_scene.
            try:
                scene = controller.admit_live_edit(event.context)
            except ValueError as exc:
                bridge.editor_result(
                    event.request_id, {"success": False, "message": str(exc)}
                )
                continue
            try:
                # A model made in this change is loaded from its stage (ADR 0055).
                staged = await gateway.staged_files(dict(event.payload))
            except ValueError as exc:
                controller.record_live_edit(scene, False)
                bridge.editor_result(
                    event.request_id, {"success": False, "message": str(exc)}
                )
                continue
            event = event.model_copy(update={"staged": staged})
            controller.expect_live(event.request_id, scene, event.payload)
        if isinstance(event, Done):
            ok = event.ok
            continue
        emit(event.model_dump())
    return ok, usage


async def settle_compaction(controller: TurnController) -> None:
    """Wait for a session summary still being written.

    A Stop of the waiting request never cancels the summary itself (``wait``
    doesn't cancel what it waits for), so it is saved and the next request
    starts fresh."""
    task = controller.compaction
    if task is None:
        return
    if not task.done():
        controller.gateway.progress("Summarising the session…")
    await asyncio.wait({task})
    if controller.compaction is task:
        controller.compaction = None


async def run_turn(
    controller: TurnController,
    bridge: AgentBridge,
    prompt: Prompt,
    emit: Emit,
    timing: TurnTiming,
    compact: Callable[[], Coroutine[Any, Any, Any]] | None = None,
    *,
    request: str = "",
    chat_id: str = "",
    todo: done_when.Todo | None = None,
) -> str:
    """Run one user request; returns its outcome ("done" or "failed").

    ``todo`` is the to-do the user pressed Go on (ADR 0060): its done-when items are
    checked once the change passes Joinery's checks.

    A turn whose change was dropped (it failed its checks, or needs the user
    first) is "failed" even when the provider finished well. The session
    summary goes ahead of the prompt only in the chat it summarises.

    When the provider is done, a change the agent didn't submit is submitted for
    it. If that fails with problems the agent can fix, the agent is asked once
    more in the same turn; a change that still fails is dropped, and Joinery says
    so in its own line.
    """
    gateway = controller.gateway
    # The turn's spans: the chat host shares its timing with the gateway already.
    lent = getattr(gateway, "timing", None) is None
    if lent:
        gateway.timing = timing
    outcome = "failed"
    try:
        outcome = await _run_turn(
            controller, bridge, prompt, emit, timing, compact, request, chat_id, todo
        )
    except asyncio.CancelledError:
        outcome = "stopped"
        raise
    finally:
        controller.close_send_back()
        _save_metrics(controller, timing, outcome)
        if lent and gateway.timing is timing:
            gateway.timing = None
    return outcome


def _save_metrics(controller: TurnController, timing: TurnTiming, outcome: str) -> None:
    """Write the change's spans; measuring never fails the change it measures."""
    # Only a change this turn put in review: a question asked while an earlier
    # change waits is a request of its own.
    last = controller.last_result
    transaction = last.transaction_id if last is not None else ""
    trace = timing.trace
    if trace.path is not None:
        return
    trace.attributes.update(
        {
            metrics.OUTCOME: outcome,
            metrics.TRANSACTION: transaction,
            metrics.AGENT: controller.agent_id,
            metrics.TASK: getattr(controller.gateway, "task_id", ""),
            metrics.RESPONSE_MODEL: timing.model,
            metrics.SUBMITS: controller.attempts
            + controller.review_rounds
            + controller.check_rounds,
            # The reviewer's and the done-when checks' send-backs alike.
            metrics.SEND_BACKS: controller.review_rounds + controller.check_rounds,
            metrics.PRECHECKS: getattr(controller, "prechecks", 0),
        }
    )
    project = getattr(controller.gateway, "project", None)
    if project is None:
        return  # a host without a project folder keeps no evidence
    with contextlib.suppress(OSError, ValueError):
        trace.save(project)


async def _run_turn(
    controller: TurnController,
    bridge: AgentBridge,
    prompt: Prompt,
    emit: Emit,
    timing: TurnTiming,
    compact: Callable[[], Coroutine[Any, Any, Any]] | None,
    request: str,
    chat_id: str,
    todo: done_when.Todo | None = None,
) -> str:
    # The session may be being summarised while the user reviewed; wait for it.
    await settle_compaction(controller)
    if controller.primer and controller.primer_chat == chat_id:
        prompt = prompt.model_copy(update={"primer": controller.primer})
    controller.primer = controller.primer_chat = ""
    # The reviewer reads the change with the same model choice.
    controller.selection = prompt.selection.model_dump()
    controller.begin(request or prompt.text, todo)
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
        timing.trace.attributes[metrics.CONTINUATIONS] = asked
        ok = await stream(
            controller, bridge, continuation(prompt, result), emit, timing, asked
        )
    last = controller.last_result
    dropped = (
        last is not None and last.final and last.status in {"failed", "needs_user"}
    )
    if last is not None and last.status == "failed" and last.final:
        emit(failure_notice(last))
    emit(Done(ok=ok).model_dump())
    if compact is not None and controller.needs_compaction():
        # Runs while the user reviews; the next request waits for it.
        controller.compaction = asyncio.create_task(compact())
    return "done" if ok and not dropped else "failed"
