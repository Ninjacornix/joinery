"""Agent-neutral bridge between the Journeyman sidebar and a coding agent.

The sidebar speaks one protocol (`bridge.protocol`) to a `Bridge`
(`bridge.host`); a per-agent adapter (`bridge.adapters`) translates to the
chosen agent. This keeps the sidebar, and the rest of Joinery, agent-agnostic.
"""

from __future__ import annotations

from bridge.host import Bridge, GateUnavailable

__all__ = ["Bridge", "GateUnavailable"]
