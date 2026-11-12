"""The reviewer and debugger agents: read-only turns behind their own gateway endpoint.

Each runs as one bounded turn of the chat's provider (the same for Codex, Claude
Code, Gemini CLI and opencode) with its role (``joinery.roles``) set on both the
provider's policy hook and the gateway endpoint, so it can only read, and report
through its own tool. The turn controller and the task scheduler call them; they
never decide anything themselves.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Protocol

from bridge.instructions import DEBUGGER_PROMPT, REVIEWER_PROMPT
from bridge.models import Selection
from bridge.protocol import Done, Execution, Message, MessageDelta, Prompt, Usage
from joinery import debugging, metrics
from joinery.debugging import DebugRequest, Diagnosis, diagnosis_from
from joinery.gateway import ProjectGateway
from joinery.peer_review import (
    PeerReview,
    ReviewRequest,
    notes_from,
    notes_in_text,
)
from joinery.roles import ReviewerSettings, reviewer_settings, spec
from joinery.turn import TurnController

#: Characters of reply a read-only agent may write before Joinery stops reading.
REPLY_CHARS = 12_000


class RoleBridge(Protocol):
    """A provider running one role (bridge.host.Bridge with ``role``)."""

    def bind_gateway(self, path: Any) -> None: ...

    def allow_stage(self, stage: str) -> None: ...

    async def connect(self, project: Path, profile_id: str = "") -> None: ...

    def send(self, message: Any) -> Any: ...

    async def close(self) -> None: ...


class RoleEndpoint(Protocol):
    """The role's gateway endpoint (joinery.gateway_rpc.GatewayRPC with ``role``)."""

    path: Any
    reports: list[dict[str, Any]]
    reproduce: Callable[[str], Awaitable[dict[str, Any]]] | None

    async def start(self) -> None: ...

    async def close(self) -> None: ...


BridgeFactory = Callable[[str, str], RoleBridge]
EndpointFactory = Callable[[ProjectGateway, str], RoleEndpoint]


async def _run_role(
    gateway: ProjectGateway,
    role: str,
    *,
    agent: str,
    profile: str,
    prompt: Prompt,
    bridge: BridgeFactory,
    endpoint: EndpointFactory,
    prepare: Callable[[RoleEndpoint], None] | None = None,
) -> tuple[list[dict[str, Any]], str, str]:
    """One read-only turn of ``role``: (its reports, its reply, the model it ran)."""
    serving = endpoint(gateway, role)
    if prepare is not None:
        prepare(serving)
    child: RoleBridge | None = None
    parts: list[str] = []
    model = ""
    usage: Usage | None = None
    clock = metrics.Clock()
    try:
        await serving.start()
        child = bridge(agent, role)
        child.bind_gateway(serving.path)
        # Its own shell and file tools may read the change it was given, no other.
        staged = gateway.transaction
        child.allow_stage(staged.id if staged is not None else "")
        await child.connect(gateway.project, profile)
        async for event in child.send(prompt):
            if isinstance(event, Execution):
                model = event.name or event.model
            elif isinstance(event, (Message, MessageDelta)):
                parts.append(event.text)
                if sum(len(part) for part in parts) > REPLY_CHARS:
                    break
            elif isinstance(event, Usage):
                usage = event
            elif isinstance(event, Done):
                break
    finally:
        if child is not None:
            with contextlib.suppress(Exception):
                await child.close()
        with contextlib.suppress(Exception):
            await serving.close()
        _record(gateway, role, agent, model, usage, clock)
    return list(serving.reports), "".join(parts), model


def _record(
    gateway: ProjectGateway,
    role: str,
    agent: str,
    model: str,
    usage: Usage | None,
    clock: metrics.Clock,
) -> None:
    """The role's turn as one agent-turn span of the change. Its provider session
    is its own, so the totals it reported are all this turn's."""
    timing = gateway.timing
    if timing is None:
        return
    attrs = metrics.agent_attributes(agent, role, usage.model if usage else "")
    attrs[metrics.REQUEST_MODEL] = model
    attrs[metrics.TASK] = gateway.task_id
    attrs.update(
        metrics.token_attributes(
            None
            if usage is None
            else (usage.input_tokens, usage.output_tokens, usage.cached_tokens)
        )
    )
    timing.trace.add(
        metrics.AGENT_TURN, "invoke_agent " + role, clock.wall, clock.end(), attrs
    )


class ReviewAgent:
    """Runs the reviewer agent on one turn's submitted change (a ``Reviewer``)."""

    def __init__(
        self,
        gateway: ProjectGateway,
        *,
        agent: str,
        profile: str,
        bridge: BridgeFactory,
        endpoint: EndpointFactory,
    ) -> None:
        self.gateway = gateway
        self.agent = agent
        self.profile = profile
        self._bridge = bridge
        self._endpoint = endpoint

    @property
    def settings(self) -> ReviewerSettings:
        """Read each time: the Settings page may change them while the chat runs."""
        return reviewer_settings(self.gateway.project)

    @property
    def active(self) -> bool:
        """It will review: ``[agents] reviewer`` is on."""
        return self.settings.enabled

    def equip(self, controller: TurnController) -> TurnController:
        """Make this the reviewer of ``controller``'s turns."""
        settings = self.settings
        controller.reviewer = self
        controller.max_review_rounds = settings.rounds
        controller.review_seconds = settings.seconds
        return controller

    def prompt(self, request: ReviewRequest, settings: ReviewerSettings) -> Prompt:
        previous = (
            "; last round you asked for: "
            + " | ".join(note.text for note in request.previous)
            + ". Check those were fixed."
            if request.previous
            else ""
        )
        text = REVIEWER_PROMPT.format(
            request=request.request or "(none given)",
            summary=request.summary or "(none given)",
            round=request.round,
            previous=previous,
            stage=request.stage,
            changes="\n".join(f"- {c['path']} ({c['change']})" for c in request.changes)
            or "- (none)",
            checks=request.checks,
            diff=request.diff or "(no text files changed)",
            bible=request.bible or "(this project has no art bible yet)",
            brief=request.brief or "(this project has no game brief)",
        )
        selection = dict(request.selection)
        if settings.model:
            selection["model"] = settings.model
        return Prompt(
            text=text,
            project_dir=str(self.gateway.project),
            editor_context=dict(self.gateway.editor_context),
            selection=Selection.model_validate(
                {k: v for k, v in selection.items() if k in Selection.model_fields}
            ),
            attachments=request.images,
        )

    async def __call__(self, request: ReviewRequest) -> PeerReview:
        settings = self.settings
        if not settings.enabled:
            return PeerReview("off")
        started = time.monotonic()
        reports: list[dict[str, Any]] = []
        reply = model = ""
        try:
            async with asyncio.timeout(settings.seconds):
                reports, reply, model = await _run_role(
                    self.gateway,
                    "reviewer",
                    agent=self.agent,
                    profile=self.profile,
                    prompt=self.prompt(request, settings),
                    bridge=self._bridge,
                    endpoint=self._endpoint,
                )
        except TimeoutError:
            return PeerReview(
                "timeout",
                message="The reviewer ran out of time, so this opened without its notes.",
                elapsed_s=time.monotonic() - started,
                model=model or settings.model,
            )
        except (
            OSError,
            RuntimeError,
            ValueError,
            TypeError,
            KeyError,
            NotImplementedError,
        ):
            return PeerReview(
                "error",
                message="The reviewer couldn't run, so this opened without its notes.",
                elapsed_s=time.monotonic() - started,
                model=model or settings.model,
            )
        notes = notes_from(reports[-1]) if reports else notes_in_text(reply)
        elapsed = time.monotonic() - started
        if notes is None:
            return PeerReview(
                "error",
                message="The reviewer gave no findings, so this opened without its notes.",
                elapsed_s=elapsed,
                model=model,
            )
        return PeerReview("reviewed", notes, elapsed_s=elapsed, model=model)


class Debugger:
    """Runs the debugger agent on one error; returns its diagnosis."""

    def __init__(
        self,
        *,
        agent: str,
        profile: str,
        bridge: BridgeFactory,
        endpoint: EndpointFactory,
        reproduce: Callable[[Path, str], Awaitable[dict[str, Any]]] = (
            debugging.reproduce
        ),
    ) -> None:
        self.agent = agent
        self.profile = profile
        self._bridge = bridge
        self._endpoint = endpoint
        self._reproduce = reproduce

    def prompt(
        self, gateway: ProjectGateway, request: DebugRequest, selection: Selection
    ) -> Prompt:
        text = DEBUGGER_PROMPT.format(
            error=request.error,
            locations=", ".join(request.locations()) or "(no file named)",
            output=request.output or "(none sent)",
            log=request.log or "(none)",
        )
        return Prompt(
            text=text,
            project_dir=str(gateway.project),
            editor_context=dict(gateway.editor_context),
            selection=selection,
        )

    async def diagnose(
        self,
        gateway: ProjectGateway,
        request: DebugRequest,
        selection: Selection | None = None,
    ) -> Diagnosis:
        """The cause and the fix; raises in plain words when there is none."""
        project = gateway.project

        def prepare(serving: RoleEndpoint) -> None:
            async def run(scene: str) -> dict[str, Any]:
                gateway.progress("Reproducing the error offline…")
                return await self._reproduce(project, scene or request.scene)

            serving.reproduce = run

        gateway.progress(spec("debugger").step)
        try:
            async with asyncio.timeout(spec("debugger").seconds):
                reports, _reply, _model = await _run_role(
                    gateway,
                    "debugger",
                    agent=self.agent,
                    profile=self.profile,
                    prompt=self.prompt(gateway, request, selection or Selection()),
                    bridge=self._bridge,
                    endpoint=self._endpoint,
                    prepare=prepare,
                )
        except TimeoutError:
            raise ValueError(
                "The debugger ran out of time before it found the cause."
            ) from None
        if not reports:
            raise ValueError("The debugger couldn't find the cause.")
        return diagnosis_from(reports[-1])
