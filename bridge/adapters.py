"""Per-agent adapters: translate the neutral protocol to one agent's interface.

The bridge holds the agent-neutral logic; an adapter is the only place that
knows how a specific agent is launched and how its streaming output maps onto
`AgentEvent`s. Adapters are keyed by the same ids as `joinery.agents`, so the
gate wiring and the bridge agree on what "codex" means.

Provider transports share the same project gateway, live editor operations,
offline creative workers and review decisions. Authentication and transport
readiness are checked separately; an absent CLI is never reported connected.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from bridge.acp_session import ACPSession
from bridge.claude_session import ClaudeSession
from bridge.codex_session import CodexSession
from bridge.models import ModelChoice
from bridge.protocol import AgentEvent, SidebarMessage
from joinery.agents import Agent, get


class AgentAdapter(ABC):
    """Drives one coding agent on behalf of the sidebar."""

    agent_id: str
    #: Transport implementation, separate from authentication and connected state.
    ready: bool = False

    def bind_gateway(self, path: Path) -> None:
        """Attach the project MCP gateway before connecting the provider."""

    async def connect(self, project: Path, profile_id: str = "") -> None:
        raise NotImplementedError(
            f"the {self.agent.name} chat transport is not implemented yet"
        )

    def editor_result(self, request_id: str, result: dict[str, Any]) -> None:
        raise NotImplementedError("Editor tools are not available for this agent.")

    async def close(self) -> None:
        pass

    async def models(self) -> list[ModelChoice]:
        return []

    def restrict_to_inspection(self) -> None:
        raise NotImplementedError(
            "This provider has no read-only specialist transport."
        )

    async def cancel(self) -> None:
        pass

    @property
    def agent(self) -> Agent:
        return get(self.agent_id)

    @abstractmethod
    def run(self, message: SidebarMessage) -> AsyncIterator[AgentEvent]:
        """Send one sidebar message to the agent and stream back its events."""
        raise NotImplementedError


class _SessionAdapter(AgentAdapter):
    """One provider transport; all scene and creative tools use the gateway."""

    ready = True

    def __init__(self, agent_id: str, session: ClaudeSession | ACPSession) -> None:
        self.agent_id = agent_id
        self.session = session

    def bind_gateway(self, path: Path) -> None:
        self.session.gateway_socket = path

    async def connect(self, project: Path, profile_id: str = "") -> None:
        await self.session.connect(project, profile_id)

    async def models(self) -> list[ModelChoice]:
        return await self.session.models()

    def restrict_to_inspection(self) -> None:
        self.session.inspection_only = True

    async def run(self, message: SidebarMessage) -> AsyncIterator[AgentEvent]:
        from bridge.protocol import Prompt

        if not isinstance(message, Prompt):
            raise TypeError("This transport expects a chat prompt.")
        async for event in self.session.send(message):
            yield event

    async def cancel(self) -> None:
        await self.session.cancel()

    async def close(self) -> None:
        await self.session.close()


class ClaudeAdapter(_SessionAdapter):
    def __init__(self) -> None:
        super().__init__("claude", ClaudeSession())


class CodexAdapter(AgentAdapter):
    """Persistent Codex chat via the local app-server protocol."""

    def __init__(self) -> None:
        self.agent_id = "codex"
        self.session = CodexSession()

    ready = True

    def bind_gateway(self, path: Path) -> None:
        self.session.gateway_socket = path

    async def connect(self, project: Path, profile_id: str = "") -> None:
        await self.session.connect(project, profile_id)

    async def models(self) -> list[ModelChoice]:
        return await self.session.models()

    def restrict_to_inspection(self) -> None:
        self.session.inspection_only = True

    async def run(self, message: SidebarMessage) -> AsyncIterator[AgentEvent]:
        from bridge.protocol import Prompt

        if not isinstance(message, Prompt):
            raise TypeError("This transport expects a chat prompt.")
        async for event in self.session.send(message):
            yield event

    def editor_result(self, request_id: str, result: dict[str, Any]) -> None:
        self.session.editor_result(request_id, result)

    async def cancel(self) -> None:
        await self.session.cancel()

    async def close(self) -> None:
        await self.session.close()


class GeminiAdapter(_SessionAdapter):
    def __init__(self) -> None:
        super().__init__("gemini", ACPSession("gemini"))


class OpencodeAdapter(_SessionAdapter):
    def __init__(self) -> None:
        super().__init__("opencode", ACPSession("opencode"))


_ADAPTERS: dict[str, type[AgentAdapter]] = {
    "claude": ClaudeAdapter,
    "codex": CodexAdapter,
    "gemini": GeminiAdapter,
    "opencode": OpencodeAdapter,
}


def adapter_for(agent_id: str) -> AgentAdapter:
    """Return the adapter for an agent id, validating the id against the catalog."""
    get(agent_id)  # Raises KeyError for an unknown agent.
    return _ADAPTERS[agent_id]()


def ready_agents() -> list[str]:
    """Agent ids whose adapter can actually run a turn today."""
    return [aid for aid in _ADAPTERS if adapter_for(aid).ready]
