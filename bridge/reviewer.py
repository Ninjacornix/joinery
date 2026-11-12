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
    ReviewPlan,
    ReviewRequest,
    notes_from,
    notes_in_text,
    skip_message,
)
from joinery.roles import ReviewerSettings, reviewer_settings, spec
from joinery.routing import RoutingSettings, review_route, routing_settings
from joinery.turn import TurnController

#: Characters of reply a read-only agent may write before Joinery stops reading.
REPLY_CHARS = 12_000
#: Seconds the light model's first pass may take; the main model keeps its own.
LIGHT_SECONDS = 45


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
    tier: str = "",
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
        _record(gateway, role, agent, model, usage, clock, tier)
    return list(serving.reports), "".join(parts), model


def _record(
    gateway: ProjectGateway,
    role: str,
    agent: str,
    model: str,
    usage: Usage | None,
    clock: metrics.Clock,
    tier: str = "",
) -> None:
    """The role's turn as one agent-turn span of the change. Its provider session
    is its own, so the totals it reported are all this turn's."""
    timing = gateway.timing
    if timing is None:
        return
    attrs = metrics.agent_attributes(agent, role, usage.model if usage else "", tier)
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

    @property
    def routing(self) -> RoutingSettings:
        return routing_settings(self.gateway.project)

    def plan(self, request: ReviewRequest) -> ReviewPlan:
        """Skip the review (``[agents] review``), give a light model the first pass on
        a low-risk change (``light_model``), or the main model; and its time."""
        settings, routing = self.settings, self.routing
        route = review_route(routing, request.risk, self.agent)
        if route == "skip":
            message = (
                'Reviewer off for this project ([agents] review = "never").'
                if routing.review == "never"
                else skip_message(request.risk)
            )
            return ReviewPlan("skip", 0.0, message)
        seconds = float(settings.seconds)
        if route == "light":
            seconds += min(LIGHT_SECONDS, settings.seconds)
        return ReviewPlan(route, seconds)

    def prompt(
        self, request: ReviewRequest, settings: ReviewerSettings, model: str = ""
    ) -> Prompt:
        previous = (
            "; last round you asked for: "
            + " | ".join(note.text for note in request.previous)
            + ". Check those were fixed."
            if request.previous
            else ""
        )
        parts = REVIEWER_PROMPT.fill(
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
        if model or settings.model:
            selection["model"] = model or settings.model
            if model:
                # The author's effort may not exist on the light model.
                selection.pop("effort", None)
        return Prompt(
            text=parts["text"],
            rules=parts["rules"],
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
        plan = self.plan(request)
        if plan.route == "skip":
            return PeerReview("skipped", message=plan.message)
        if plan.route == "light":
            light = await self._review(
                request,
                settings,
                model=self.routing.light_model(self.agent),
                tier="light",
                seconds=min(LIGHT_SECONDS, settings.seconds),
            )
            if not light.unsure():
                return light
            # A light model never decides alone: the main model reviews again.
            main = await self._review(request, settings, tier="main")
            main.escalated = True
            main.elapsed_s += light.elapsed_s
            return main
        return await self._review(request, settings, tier="main")

    async def _review(
        self,
        request: ReviewRequest,
        settings: ReviewerSettings,
        *,
        model: str = "",
        tier: str,
        seconds: float = 0.0,
    ) -> PeerReview:
        """One reviewer turn on ``model`` ("" for the main one)."""
        started = time.monotonic()
        result = await self._turn(
            request, settings, model=model, tier=tier, seconds=seconds
        )
        if isinstance(result, PeerReview):
            result.elapsed_s = time.monotonic() - started
            return result
        reports, reply, ran = result
        notes = notes_from(reports[-1]) if reports else notes_in_text(reply)
        elapsed = time.monotonic() - started
        if notes is None:
            return PeerReview(
                "error",
                message="The reviewer gave no findings, so this opened without its notes.",
                elapsed_s=elapsed,
                model=ran,
                tier=tier,
            )
        return PeerReview("reviewed", notes, elapsed_s=elapsed, model=ran, tier=tier)

    async def _turn(
        self,
        request: ReviewRequest,
        settings: ReviewerSettings,
        *,
        model: str,
        tier: str,
        seconds: float,
    ) -> tuple[list[dict[str, Any]], str, str] | PeerReview:
        try:
            async with asyncio.timeout(seconds or settings.seconds):
                return await _run_role(
                    self.gateway,
                    "reviewer",
                    agent=self.agent,
                    profile=self.profile,
                    prompt=self.prompt(request, settings, model),
                    bridge=self._bridge,
                    endpoint=self._endpoint,
                    tier=tier,
                )
        except TimeoutError:
            return PeerReview(
                "timeout",
                message="The reviewer ran out of time, so this opened without its notes.",
                model=model or settings.model,
                tier=tier,
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
                model=model or settings.model,
                tier=tier,
            )


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
        parts = DEBUGGER_PROMPT.fill(
            error=request.error,
            locations=", ".join(request.locations()) or "(no file named)",
            output=request.output or "(none sent)",
            log=request.log or "(none)",
        )
        return Prompt(
            text=parts["text"],
            rules=parts["rules"],
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
