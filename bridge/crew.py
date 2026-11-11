"""Several agents at once: parallel tasks beside the chat's own request (ADR 0036).

A task is one more agent turn, run exactly like the chat's own (``run_turn``) with
its own turn controller, its own provider session (the chat's provider, model and
effort), its own gateway lane and so its own staged change, reviewed on its own.
The user starts one with "Run alongside" while Journeyman works; the lead agent
starts one with ``start_task``. Tasks can't start tasks.

Nothing here knows which provider runs: the same scheduler serves Codex, Claude
Code, Gemini CLI and opencode, and every task's provider runs the same policy hook
as the chat's own, reporting a Rule Zero stop to the task's gateway endpoint.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from bridge.instructions import TASK_PROMPT
from bridge.protocol import Prompt
from bridge.turn_runner import run_turn
from joinery import agents
from joinery.claims import refusal
from joinery.gateway import ProjectGateway
from joinery.timing import TurnTiming
from joinery.turn import TurnController

Emit = Callable[[dict[str, Any]], None]
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
    origin: Literal["user", "lead"]
    gateway: ProjectGateway
    controller: TurnController
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
        reserved = sorted(
            {
                str(t).replace("\\", "/").removeprefix("res://").strip("/")
                for t in targets
            }
            - {""}
        )
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
        holder: list[Task] = []
        controller = TurnController(
            lane, lambda event: self._emit(holder[0], event), self.agent
        )
        task = Task(task_id, title, request, origin, lane, controller)
        task.step = f"Starting {self.provider}…"
        holder.append(task)
        lane.progress = lambda text: self._progress(task, text)
        live = self._live
        if live is not None:
            # Once the main request is idle, a task may change the scene on screen.
            lane.edit_scene = lambda payload: live(lane, controller, payload, task_id)
        self.tasks[task_id] = task
        self.emit(task.event())
        work = prompt.model_copy(
            update={
                "text": TASK_PROMPT.format(request=request),
                "primer": "",
            }
        )
        task.job = asyncio.create_task(self._run(task, work))
        return task

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
