"""Several agents at once: parallel tasks beside the chat's own request (ADR 0036).

A task is one more agent turn, run exactly like the chat's own (``run_turn``) with
its own turn controller, its own provider session (the chat's provider, model and
effort), its own gateway lane and so its own staged change, reviewed on its own.
The user starts one with "Run alongside" while Journeyman works; the lead agent
starts one with ``start_task``. Tasks can't start tasks.

Nothing here knows which provider runs: the same scheduler serves Codex, Claude
Code, Gemini CLI and opencode, and every task's provider runs the same policy hook
as the chat's own, reporting a Rule Zero stop to the task's gateway endpoint.

A *scripted* task (``start_scripted``, "Make it match") has no provider of its own:
Joinery already knows the typed calls that make its change, so it runs them through
the task's lane and submits. The change is checked, read by the reviewer agent and
reviewed by the user exactly like any other task's.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from bridge.instructions import DEBUG_FIX_PROMPT, TASK_PROMPT
from bridge.protocol import Prompt
from bridge.turn_runner import run_turn
from joinery import agents
from joinery.claims import refusal
from joinery.debugging import DebugRequest, Diagnosis
from joinery.gateway import ProjectGateway, result_text
from joinery.mcp_runtime import PolicyStop
from joinery.peer_review import Reviewer
from joinery.timing import TurnTiming
from joinery.turn import TurnController

Emit = Callable[[dict[str, Any]], None]
#: The debugger agent: (its lane, the error, the model choice) -> the cause and the fix.
Diagnose = Callable[[ProjectGateway, DebugRequest, Any], Awaitable[Diagnosis]]
#: The chat host's live edit: (lane, its turn, the edit, the task id) -> the editor's answer.
LiveEdit = Callable[
    [ProjectGateway, TurnController, dict[str, Any], str],
    Awaitable[dict[str, Any]],
]
#: The states a task card shows; the last four are final.
WORKING, IN_REVIEW = "working", "in_review"
FINAL = frozenset({"accepted", "discarded", "done", "failed", "stopped"})


class TaskBridge(Protocol):
    """What a task needs from its provider (bridge.host.Bridge)."""

    def bind_gateway(self, path: Any) -> None: ...

    async def connect(self, project: Any, profile_id: str = "") -> None: ...

    def send(self, message: Any) -> Any: ...

    def editor_result(self, request_id: str, result: dict[str, Any]) -> None: ...

    async def cancel(self) -> None: ...

    async def close(self) -> None: ...


class TaskEndpoint(Protocol):
    """The task's own gateway endpoint (joinery.gateway_rpc.GatewayRPC, worker=True)."""

    path: Any

    async def start(self) -> None: ...

    async def close(self) -> None: ...


@dataclass
class Task:
    id: str
    title: str
    request: str
    origin: Literal["user", "lead", "debug", "match"]
    gateway: ProjectGateway
    controller: TurnController
    #: The agent role at work now (joinery.roles): a debug task is a debugger, then
    #: the worker that makes its fix.
    role: str = "worker"
    #: The error a debug task is about, and the cause its debugger found.
    error: str = ""
    cause: str = ""
    state: str = WORKING
    step: str = ""
    #: Joinery's or the agent's last line, shown on the task's card.
    message: str = ""
    transaction_id: str = ""
    summary: str = ""
    apps: list[str] = field(default_factory=list)
    started: float = field(default_factory=time.time)
    ended: float = 0.0
    job: asyncio.Task[None] | None = None
    agent: TaskBridge | None = None
    _reply: list[str] = field(default_factory=list)

    def event(self) -> dict[str, Any]:
        meter = self.controller.meter
        return {
            "kind": "task",
            "task_id": self.id,
            "title": self.title,
            "request": self.request,
            "origin": self.origin,
            "role": self.role,
            "error": self.error,
            "cause": self.cause,
            "state": self.state,
            "step": self.step,
            "message": self.message,
            "transaction_id": self.transaction_id,
            #: Its change includes a live edit of the scene on screen.
            "live": self.controller.live_edits > 0,
            "summary": self.summary,
            "apps": list(self.apps),
            "started": self.started,
            "elapsed_s": int((self.ended or time.time()) - self.started),
            "context_used": meter.used,
            "context_window": meter.window,
        }


@dataclass(frozen=True)
class ScriptedCall:
    """One typed call a scripted task makes, with its words for the card."""

    server: str
    tool: str
    arguments: dict[str, Any]
    label: str


def _reserved(targets: list[str] | tuple[str, ...]) -> list[str]:
    """Project paths a task reserves, relative to the project."""
    return sorted(
        {str(t).replace("\\", "/").removeprefix("res://").strip("/") for t in targets}
        - {""}
    )


class Crew:
    """Runs the parallel tasks of one chat host."""

    def __init__(
        self,
        gateway: ProjectGateway,
        emit: Emit,
        *,
        agent: str,
        profile: str,
        limit: int,
        bridge: Callable[[str], TaskBridge],
        endpoint: Callable[
            [ProjectGateway, Callable[[str], Awaitable[None]]], TaskEndpoint
        ],
        on_stop: Callable[[str], Awaitable[None]],
        live: LiveEdit | None = None,
        reviewer: Callable[[ProjectGateway, TurnController], Reviewer | None]
        | None = None,
        debugger: Diagnose | None = None,
    ) -> None:
        self.gateway = gateway
        self.emit = emit
        self.agent = agent
        self.profile = profile
        #: Turns at once, the chat's own included; 1 turns tasks off.
        self.limit = limit
        self._bridge = bridge
        self._endpoint = endpoint
        self._on_stop = on_stop
        self._live = live
        #: Gives each task's turn its reviewer agent (bridge.reviewer.ReviewAgent.equip).
        self._reviewer = reviewer
        self._debugger = debugger
        self.tasks: dict[str, Task] = {}
        #: A provider's own live edit waiting for the editor: {request id: task}.
        self._editor: dict[str, Task] = {}

    # --- Asking -------------------------------------------------------------------------

    @property
    def provider(self) -> str:
        """The provider's name as people know it ("Codex CLI")."""
        try:
            return agents.get(self.agent).name
        except (KeyError, ValueError):
            return self.agent.capitalize() or "the agent"

    def working(self) -> list[Task]:
        return [t for t in self.tasks.values() if t.state == WORKING]

    def owner(self, transaction_id: str) -> Task | None:
        """The task whose change ``transaction_id`` is in review.

        Its change may be decided while its agent still writes its last words,
        as the chat's own can.
        """
        if not transaction_id:
            return None
        return next(
            (
                t
                for t in self.tasks.values()
                if t.state not in FINAL
                and (
                    (t.state == IN_REVIEW and t.transaction_id == transaction_id)
                    or (
                        t.controller.review_open
                        and t.controller.review_id == transaction_id
                    )
                )
            ),
            None,
        )

    def status(self, task_id: str) -> dict[str, Any]:
        task = self.tasks.get(task_id)
        if task is None:
            raise ValueError("No task has that id.")
        event = task.event()
        event.pop("kind")
        return event

    # --- Starting -----------------------------------------------------------------------

    async def start(
        self,
        title: str,
        request: str,
        *,
        origin: Literal["user", "lead"],
        prompt: Prompt,
        targets: list[str] | tuple[str, ...] = (),
    ) -> Task:
        """Start one task now, or raise in plain words why it can't start."""
        if self.limit <= 1:
            raise ValueError(
                "Parallel tasks are off for this project ([agents] parallel = 1 in "
                "joinery.toml). Do this part yourself."
            )
        if len(self.working()) >= self.limit - 1:
            raise ValueError(
                f"{len(self.working())} tasks are already running, as many as this "
                "project allows at once. Wait for one to finish, or do this yourself."
            )
        task = self._open(title, request, origin, prompt, targets)
        work = prompt.model_copy(
            update={
                "text": TASK_PROMPT.format(request=request),
                "primer": "",
            }
        )
        task.job = asyncio.create_task(self._run(task, work))
        return task

    async def start_scripted(
        self,
        title: str,
        request: str,
        calls: list[ScriptedCall],
        *,
        prompt: Prompt,
        targets: list[str] | tuple[str, ...] = (),
        facts: list[str] | tuple[str, ...] = (),
    ) -> Task:
        """Start a task that makes its change with known typed calls, no agent of its
        own. Its claims on ``targets`` come first; a held file refuses it in plain
        words. The caller decides how many run at once."""
        task = self._open(title, request, "match", prompt, targets)
        task.step = "Waiting for its apps…"
        task.job = asyncio.create_task(
            self._scripted(task, calls, list(facts), prompt.selection.model_dump())
        )
        return task

    async def _scripted(
        self,
        task: Task,
        calls: list[ScriptedCall],
        facts: list[str],
        selection: dict[str, Any],
    ) -> None:
        lane = task.gateway
        controller = task.controller
        timing = TurnTiming()
        lane.timing = timing
        outcome = "failed"
        try:
            controller.selection = selection
            controller.begin(task.request)
            for call in calls:
                lane.progress(call.label + "…")
                result = await lane.call(call.server, call.tool, call.arguments)
                if result.is_error:
                    raise RuntimeError(f"{call.label}: {result_text(result)[-400:]}")
            submitted = await controller.submit(task.title, "joinery", facts)
            if not submitted.final:
                # Nobody is left to fix what the checks found: it stops here.
                await controller.end(True, last=True)
            last = controller.last_result
            if last is not None and last.status in {"in_review", "nothing_to_review"}:
                outcome = "done"
            elif last is not None and last.problems:
                task.message = "; ".join(p.message for p in last.problems)[:300]
        except asyncio.CancelledError:
            outcome = "stopped"
            with contextlib.suppress(Exception):
                await controller.cancel("stopped")
        except PolicyStop as exc:
            task.message = str(exc)[:300]
            with contextlib.suppress(Exception):
                await controller.cancel("stopped")
            await self._on_stop(str(exc))
        except (
            OSError,
            RuntimeError,
            ValueError,
            TypeError,
            KeyError,
            TimeoutError,
        ) as exc:
            task.message = str(exc)[:300] or "The change couldn't be made."
            with contextlib.suppress(Exception):
                await controller.cancel("failed")
        finally:
            lane.timing = None
        await self._conclude(task, outcome)

    def _open(
        self,
        title: str,
        request: str,
        origin: Literal["user", "lead", "debug", "match"],
        prompt: Prompt,
        targets: list[str] | tuple[str, ...] = (),
        *,
        error: str = "",
    ) -> Task:
        """A new task's lane, turn and card; its claims on ``targets`` first. A task
        about an ``error`` starts as its debugger."""
        reserved = _reserved(targets)
        claims = self.gateway.claims
        path, held_by = claims.first_conflict(set(reserved), "")
        if path:
            raise ValueError(refusal(path, held_by))
        task_id = uuid.uuid4().hex
        lane = self.gateway.lane(task_id, title)
        lane.claims.claim(task_id, reserved)
        lane.editor_context = dict(prompt.editor_context)
        lane.target_context = prompt.target_context
        lane.request_summary = title
        lane.attachments = prompt.attachments
        holder: list[Task] = []
        controller = TurnController(
            lane, lambda event: self._emit(holder[0], event), self.agent
        )
        if self._reviewer is not None:
            controller.reviewer = self._reviewer(lane, controller)
        task = Task(task_id, title, request, origin, lane, controller)
        task.step = f"Starting {self.provider}…"
        if error:
            task.role, task.error, task.step = "debugger", error, "Reading the error…"
        holder.append(task)
        lane.progress = lambda text: self._progress(task, text)
        live = self._live
        if live is not None:
            # Once the main request is idle, a task may change the scene on screen.
            lane.edit_scene = lambda payload: live(lane, controller, payload, task_id)
        self.tasks[task_id] = task
        self.emit(task.event())
        return task

    # --- Debugging ----------------------------------------------------------------------

    def debugging(self) -> Task | None:
        """The debug task still working, if any: one error is debugged at a time."""
        return next(
            (t for t in self.working() if t.origin == "debug"),
            None,
        )

    async def debug(self, request: DebugRequest, prompt: Prompt) -> Task:
        """Debug one error: a debugger finds the cause, then a worker makes the fix
        as a normal staged change, checked and reviewed like any other. One card
        follows both ("Debugging: <error>")."""
        if self._debugger is None:
            raise ValueError("Debugging isn't available with this agent.")
        if self.debugging() is not None:
            raise ValueError("Journeyman is already debugging an error; wait for it.")
        task = self._open(
            request.title, request.error, "debug", prompt, error=request.error
        )
        task.job = asyncio.create_task(self._debug(task, request, prompt))
        return task

    async def _debug(self, task: Task, request: DebugRequest, prompt: Prompt) -> None:
        assert self._debugger is not None
        try:
            found = await self._debugger(task.gateway, request, prompt.selection)
        except asyncio.CancelledError:
            task.state = "stopped"
            await self._settle(task)
            return
        except (
            OSError,
            RuntimeError,
            ValueError,
            TypeError,
            KeyError,
            TimeoutError,
            NotImplementedError,
        ) as exc:
            task.state = "failed"
            task.message = str(exc) or "The debugger stopped unexpectedly."
            await self._settle(task)
            return
        task.cause = found.cause
        if not found.fix:
            task.state = "done"
            task.message = "Cause: " + found.cause[:280]
            await self._settle(task)
            return
        reserved = _reserved(found.targets)
        path, held_by = task.gateway.claims.first_conflict(set(reserved), task.id)
        if path:
            task.state = "failed"
            task.message = refusal(path, held_by)
            await self._settle(task)
            return
        task.gateway.claims.claim(task.id, reserved)
        # The fix is made by a worker, in this task's own staged change.
        task.role = "worker"
        task.step = "Making the fix…"
        self.emit(task.event())
        work = prompt.model_copy(
            update={
                "text": DEBUG_FIX_PROMPT.format(
                    error=request.error, cause=found.cause, fix=found.fix
                ),
                "primer": "",
            }
        )
        await self._run(task, work)

    async def _settle(self, task: Task) -> None:
        """A task that ends before any turn of its own ran."""
        task.ended = time.time()
        task.step = ""
        await self._finish(task)
        self.emit(task.event())

    # --- Running ------------------------------------------------------------------------

    async def _run(self, task: Task, prompt: Prompt) -> None:
        lane = task.gateway
        endpoint = self._endpoint(lane, self._on_stop)
        agent: TaskBridge | None = None
        timing = TurnTiming()
        lane.timing = timing
        outcome = "failed"
        try:
            await endpoint.start()
            agent = task.agent = self._bridge(self.agent)
            agent.bind_gateway(endpoint.path)
            await agent.connect(lane.project, self.profile)
            outcome = await run_turn(
                task.controller,
                agent,
                prompt,
                lambda event: self._emit(task, event),
                timing,
                request=task.request,
            )
        except asyncio.CancelledError:
            outcome = "stopped"
            with contextlib.suppress(Exception):
                if agent is not None:
                    await agent.cancel()
            with contextlib.suppress(Exception):
                await task.controller.cancel("stopped")
        except (
            OSError,
            RuntimeError,
            ValueError,
            TypeError,
            KeyError,
            TimeoutError,
            NotImplementedError,
        ) as exc:
            task.message = str(exc) or "The task's agent stopped unexpectedly."
            with contextlib.suppress(Exception):
                await task.controller.cancel("failed")
        finally:
            lane.timing = None
            if agent is not None:
                with contextlib.suppress(Exception):
                    await agent.close()
            with contextlib.suppress(Exception):
                await endpoint.close()
        await self._conclude(task, outcome)

    async def _conclude(self, task: Task, outcome: str) -> None:
        """A task's turn is over: in review, or final with its lane closed."""
        task.ended = time.time()
        task.step = ""
        controller = task.controller
        if task.state in FINAL:
            # Decided while its agent still wrote its last words.
            await self._finish(task)
        elif controller.review_open and (controller.review_id or controller.live_edits):
            task.transaction_id = task.controller.review_id
            task.state = IN_REVIEW
        else:
            if outcome == "done" and not task.message:
                task.message = "Nothing needed changing."
            task.state = "stopped" if outcome in {"stopped", "cancelled"} else outcome
            await self._finish(task)
        task.apps = list(task.apps)
        self.emit(task.event())

    def _emit(self, task: Task, event: dict[str, Any]) -> None:
        kind = event.get("kind")
        if kind == "message_delta":
            if event.get("new_message"):
                task._reply = []
            task._reply.append(str(event.get("text", "")))
        elif kind == "message":
            text = str(event.get("text", "")).strip()
            if text:
                task.message = text[:300]
        elif kind == "notice":
            task.message = str(event.get("text", ""))[:300]
        elif kind == "editor_action":
            # The editor applies it like the chat's own; the answer comes back here.
            self._editor[str(event.get("request_id", ""))] = task
            self.emit({**event, "owner": task.id})
            return
        elif kind == "file_preview":
            task.transaction_id = str(event.get("transaction_id", ""))
            task.summary = str(event.get("summary", ""))
        elif kind == "done":
            reply = "".join(task._reply).strip()
            if reply and not task.message:
                task.message = reply.splitlines()[-1][:300]
        self.emit({**event, "task_id": task.id})

    def _progress(self, task: Task, text: str) -> None:
        task.step = text
        self.emit({"kind": "progress", "text": text, "task_id": task.id})
        tx = task.gateway.transaction
        tools = list(tx.tools) if tx is not None else []
        if tools != task.apps:
            task.apps = tools
            self.emit(task.event())

    # --- Deciding and stopping --------------------------------------------------------

    async def decide(self, transaction_id: str, accept: bool) -> dict[str, Any]:
        """The user's Accept or Discard on a task's change."""
        task = self.owner(transaction_id)
        if task is None:
            raise ValueError("That change isn't waiting in a running task.")
        decision = await task.controller.decide(transaction_id, accept)
        task.state = "accepted" if accept else "discarded"
        task.message = str(decision.get("message", ""))
        task.step = ""
        job = task.job
        if job is None or job.done():
            await self._finish(task)
        # Otherwise the lane closes when its agent is done (_run).
        self.emit(task.event())
        return {**decision, "task_id": task.id}

    def editor_result(self, request_id: str, result: dict[str, Any]) -> bool:
        """The editor answered a task's own live edit; False when none waits."""
        task = self._editor.pop(request_id, None)
        if task is None:
            return False
        task.controller.live_result(request_id, bool(result.get("success", False)))
        if task.agent is not None:
            task.agent.editor_result(request_id, result)
        return True

    async def live_decided(self, task_id: str, accepted: bool) -> bool:
        """The user decided the live preview ``task_id`` left on screen."""
        task = self.tasks.get(task_id)
        if task is None:
            return False
        task.controller.live_decided()
        if task.state == IN_REVIEW and not task.transaction_id:
            task.state = "accepted" if accepted else "discarded"
            await self._finish(task)
            self.emit(task.event())
        return True

    async def stop(self, task_id: str) -> None:
        task = self.tasks.get(task_id)
        if task is None:
            raise ValueError("No task has that id.")
        job = task.job
        if task.state == WORKING and job is not None and not job.done():
            job.cancel()
            await asyncio.gather(job, return_exceptions=True)

    async def stop_all(self) -> int:
        """Stop every task still working; returns how many were stopped."""
        running = self.working()
        await asyncio.gather(
            *(self.stop(task.id) for task in running), return_exceptions=True
        )
        return len(running)

    async def _finish(self, task: Task) -> None:
        """The task is over: its lane, its staged apps and its claims go."""
        with contextlib.suppress(Exception):
            await task.gateway.close()

    async def close(self) -> None:
        await self.stop_all()
        for task in self.tasks.values():
            if task.state == IN_REVIEW:
                await self._finish(task)
