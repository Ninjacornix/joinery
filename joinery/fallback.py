"""Continue a turn with another agent when the provider runs out of quota (ADR 0080).

When a provider reports a usage or quota limit mid-turn, Joinery offers to
carry on with another agent the user has already connected: the staged change
stays where it is (in the project gateway, which every agent uses alike) and
the other agent gets a summary of the chat and the list of staged files. It is
an offer, never automatic, unless the project names an ``[agents]
fallback_provider``. Joinery brokers no credentials: an agent qualifies only
when its own sign-in is already in place (``joinery.connection.status``).
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from joinery.agents import AGENTS

#: The same words joinery.connection classifies a test message by.
QUOTA = re.compile(
    r"quota|rate[ _-]?limit|usage limit|too many requests|\b429\b|credit balance|"
    r"insufficient[ _]funds|out of credits|limit reached|resource[ _]exhausted",
    re.IGNORECASE,
)
CONTINUE = """\
{name} ran out of quota partway through this request, so you carry it on. \
{summary}

The user's request: {request}

{staged}

Continue the work from where it stopped: look at the staged files before changing \
anything, finish the request, then submit the change for review as usual."""


def is_quota(text: str) -> bool:
    """True when a provider's error words say a usage, quota or rate limit stopped it."""
    return bool(QUOTA.search(text or ""))


def setting(project: Path) -> str:
    """``[agents] fallback_provider``: the agent to continue with without asking ("")."""
    try:
        raw = tomllib.loads((project / "joinery.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    table = raw.get("agents", {})
    value = table.get("fallback_provider", "") if isinstance(table, dict) else ""
    return value if isinstance(value, str) and value in {a.id for a in AGENTS} else ""


def _ready(agent_id: str) -> bool:
    from joinery.connection import status

    try:
        state = status(agent_id)
    except (OSError, ValueError, KeyError):
        return False
    return bool(state["supported"] and state["installed"] and state["connected"])


def candidates(
    current: str, ready: Callable[[str], bool] | None = None
) -> list[dict[str, str]]:
    """The other agents already signed in, as {id, name}, in Joinery's own order."""
    check = ready or _ready
    return [
        {"id": a.id, "name": a.name} for a in AGENTS if a.id != current and check(a.id)
    ]


def staged_lines(changes: list[dict[str, Any]]) -> str:
    """The staged change in words: each file and whether it is new, changed or gone."""
    if not changes:
        return "Nothing is staged yet."
    lines = ["The change staged so far (still staged; keep building on it):"]
    for change in changes[:40]:
        before, after = str(change.get("before", "")), str(change.get("after", ""))
        verb = "new" if not before else "removed" if not after else "changed"
        lines.append(f"- {change.get('path', '')} ({verb})")
    if len(changes) > 40:
        lines.append(f"- and {len(changes) - 40} more files")
    return "\n".join(lines)


def continue_text(
    name: str, request: str, summary: str, changes: list[dict[str, Any]]
) -> str:
    """The prompt the other agent starts from."""
    return CONTINUE.format(
        name=name,
        summary=summary.strip(),
        request=request.strip(),
        staged=staged_lines(changes),
    )
