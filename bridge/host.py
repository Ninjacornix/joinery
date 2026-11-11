"""Agent-neutral façade the Journeyman sidebar talks to.

The sidebar does not know which agent is running. It opens a `Bridge` for a
chosen agent and sends `SidebarMessage`s; the bridge picks the adapter, checks
the agent is safe to run, and streams back `AgentEvent`s. Framed stdio carries
Codex chat to Godot; the safety checks apply to every adapter.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from bridge.adapters import AgentAdapter, adapter_for
from bridge.models import ModelChoice, route
from bridge.protocol import AgentEvent, Execution, Models, Prompt, SidebarMessage
from joinery.agents import get
from joinery.roles import spec
from joinery.wiring import unwireable


class GateUnavailable(RuntimeError):
    """Raised when an agent would run without the creation-only gate."""


class Bridge:
    """Routes one sidebar session to one agent, with the gate enforced first."""

    def __init__(
        self, agent_id: str, *, inspection_only: bool = False, role: str = ""
    ) -> None:
        self.agent = get(agent_id)
        if agent_id in unwireable():
            # Fail-closed: never run an agent whose Rule Zero gate cannot hold.
            raise GateUnavailable(
                f"{self.agent.name} has no reliable blocking hook; refusing to run it "
                f"so Rule Zero stays enforced. Use a gated agent (see `joinery agents`)."
            )
        self._adapter: AgentAdapter = adapter_for(agent_id)
        self.model_choices: list[ModelChoice] = []
        if role:
            # The role's own transport: read-only roles get the specialists' one.
            self.role = spec(role).role
            self._adapter.assign_role(self.role)
            if spec(role).read_only:
                inspection_only = True
        else:
            self.role = "lead"
        if inspection_only:
            self._adapter.restrict_to_inspection()

    @property
    def ready(self) -> bool:
        """Whether this agent can actually run a turn today (adapter built)."""
        return self._adapter.ready

    def bind_gateway(self, path: Path) -> None:
        self._adapter.bind_gateway(path)

    def allow_stage(self, stage: str) -> None:
        """Let its own shell and file tools reach the staged change ``stage`` (an
        id; "" for none), and no other (``joinery.stage_access``)."""
        self._adapter.allow_stage(stage)

    async def connect(self, project: Path, profile_id: str = "") -> None:
        await self._adapter.connect(project, profile_id)
        await self.refresh_models()

    async def refresh_models(self) -> None:
        self.model_choices = await self._adapter.models()

    def editor_result(self, request_id: str, result: dict[str, Any]) -> None:
        self._adapter.editor_result(request_id, result)

    async def close(self) -> None:
        await self._adapter.close()

    async def cancel(self) -> None:
        await self._adapter.cancel()

    @property
    def handle(self) -> str:
        """The provider's resumable id for the current conversation ("" when none)."""
        return self._adapter.handle

    async def resume(self, handle: str) -> bool:
        """Continue a saved conversation in the provider's own session, if it can."""
        return await self._adapter.resume(handle)

    async def new_session(self) -> None:
        await self._adapter.new_session()

    async def send(self, message: SidebarMessage) -> AsyncIterator[AgentEvent]:
        """Forward one message and stream the agent's events back to the sidebar."""
        if not self._adapter.ready:
            raise NotImplementedError(
                f"the {self.agent.name} adapter is not implemented yet"
            )
        if isinstance(message, Prompt):
            model, effort, mode = route(
                message.selection, message.text, self.model_choices
            )
            message = message.model_copy(update={"model": model.id, "effort": effort})
            yield Execution(model=model.id, name=model.name, effort=effort, mode=mode)
        async for event in self._adapter.run(message):
            if isinstance(event, Models):
                self.model_choices = event.models
            yield event
