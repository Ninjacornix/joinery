"""Agent-neutral façade the Journeyman sidebar talks to.

The sidebar does not know which agent is running. It opens a `Bridge` for a
chosen agent and sends `SidebarMessage`s; the bridge picks the adapter, checks
the agent is safe to run, and streams back `AgentEvent`s. The local socket
transport that carries this to the Godot side is Phase 2; the contract and the
safety checks live here so they hold for every agent from the start.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from bridge.adapters import AgentAdapter, adapter_for
from bridge.protocol import AgentEvent, SidebarMessage
from joinery.agents import get
from joinery.wiring import unwireable


class GateUnavailable(RuntimeError):
    """Raised when an agent would run without the creation-only gate."""


class Bridge:
    """Routes one sidebar session to one agent, with the gate enforced first."""

    def __init__(self, agent_id: str) -> None:
        self.agent = get(agent_id)
        if agent_id in unwireable():
            # Fail-closed: never run an agent whose Rule Zero gate cannot hold.
            raise GateUnavailable(
                f"{self.agent.name} has no reliable blocking hook; refusing to run it "
                f"so Rule Zero stays enforced. Use a gated agent (see `joinery agents`)."
            )
        self._adapter: AgentAdapter = adapter_for(agent_id)

    @property
    def ready(self) -> bool:
        """Whether this agent can actually run a turn today (adapter built)."""
        return self._adapter.ready

    async def send(self, message: SidebarMessage) -> AsyncIterator[AgentEvent]:
        """Forward one message and stream the agent's events back to the sidebar."""
        if not self._adapter.ready:
            raise NotImplementedError(
                f"the {self.agent.name} adapter is not implemented yet (Phase 2)"
            )
        async for event in self._adapter.run(message):
            yield event
