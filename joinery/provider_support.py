"""Platform usability shared by onboarding and the gated provider launchers."""

from __future__ import annotations

import sys
from typing import TypedDict

from joinery.agents import get

WINDOWS_BOUNDARY_REASON = (
    "This provider does not yet have a native Windows read-only host boundary. "
    "Use Codex on Windows, or run this provider in a supported Linux/macOS host."
)


class Usability(TypedDict):
    supported: bool
    reason: str


def usability(agent_id: str) -> Usability:
    get(agent_id)
    reason = (
        WINDOWS_BOUNDARY_REASON
        if sys.platform == "win32" and agent_id != "codex"
        else ""
    )
    return {"supported": not reason, "reason": reason}


def require_supported(agent_id: str) -> None:
    result = usability(agent_id)
    if not result["supported"]:
        raise ValueError(result["reason"])
