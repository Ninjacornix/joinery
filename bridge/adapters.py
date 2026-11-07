"""Per-agent adapters: translate the neutral protocol to one agent's interface.

The bridge holds the agent-neutral logic; an adapter is the only place that
knows how a specific agent is launched and how its streaming output maps onto
`AgentEvent`s. Adapters are keyed by the same ids as `joinery.agents`, so the
gate wiring and the bridge agree on what "codex" means.

These are declared seams, not finished integrations. Each adapter states whether
it is `ready`; the bridge refuses to start an agent whose adapter is not, rather
than pretend. Phase 2 implements the transports (Claude via the Agent SDK, the
others via their CLIs / app-server protocols).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from bridge.protocol import AgentEvent, SidebarMessage
from joinery.agents import Agent, get


class AgentAdapter(ABC):
    """Drives one coding agent on behalf of the sidebar."""

    agent_id: str
    #: False until the transport for this agent is implemented (Phase 2).
    ready: bool = False

    @property
    def agent(self) -> Agent:
        return get(self.agent_id)

    @abstractmethod
    def run(self, message: SidebarMessage) -> AsyncIterator[AgentEvent]:
        """Send one sidebar message to the agent and stream back its events."""
        raise NotImplementedError


class _Unimplemented(AgentAdapter):
    """Seam for an agent whose transport is not built yet."""

    ready = False

    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id

    async def run(self, message: SidebarMessage) -> AsyncIterator[AgentEvent]:
        raise NotImplementedError(
            f"the {self.agent.name} adapter is not implemented yet"
        )
        # Make the method an async generator for type-checkers.
        yield  # pragma: no cover  # type: ignore[unreachable]


class ClaudeAdapter(_Unimplemented):
    """Claude Code via the Agent SDK (local). Transport lands in Phase 2."""

    def __init__(self) -> None:
        super().__init__("claude")


class CodexAdapter(_Unimplemented):
    """Codex CLI via its app-server protocol. Transport lands in Phase 2."""

    def __init__(self) -> None:
        super().__init__("codex")


class GeminiAdapter(_Unimplemented):
    """Gemini CLI. Transport lands in Phase 2."""

    def __init__(self) -> None:
        super().__init__("gemini")


class OpencodeAdapter(_Unimplemented):
    """opencode via its server API. Transport lands in Phase 2."""

    def __init__(self) -> None:
        super().__init__("opencode")


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
