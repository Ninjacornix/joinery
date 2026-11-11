"""A short summary of a chat session, so a fresh provider session can pick up the thread.

When a conversation nears the model's context window, Joinery asks the agent
for a summary and adds what it recorded itself: the changes the user accepted,
discarded or still has open, and the recent requests. The summary is saved, and
the next prompt of the fresh session carries it.

Storage is a small interface (:class:`SummaryStore`): the chat host keeps each
summary in the open saved chat (``bridge.chat_record``), and
:class:`FileSummaryStore` keeps them in .joinery when no chat can be saved.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field

from joinery.transactions import atomic_json, list_transactions

#: Longest summary the agent's part may add, in characters.
MAX_AGENT_TEXT = 6000
#: Summaries a project keeps; older ones are removed.
KEEP = 20
#: How each decided or open change is named in the summary.
OUTCOMES = {
    "accepted": "Accepted",
    "rejected": "Discarded",
    "reverted": "Reverted",
    "review": "Waiting for the user's review",
}


class SessionSummary(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    created: float = Field(default_factory=time.time)
    agent: str = ""
    text: str
    facts: list[str] = []


class SummaryStore(Protocol):
    def save(self, summary: SessionSummary) -> None: ...

    def latest(self) -> SessionSummary | None: ...


class FileSummaryStore:
    """Summaries as .joinery/summaries/<created>-<id>.json, newest kept."""

    def __init__(self, project: Path) -> None:
        self.folder = project / ".joinery/summaries"

    def _files(self) -> list[Path]:
        return sorted(self.folder.glob("*.json")) if self.folder.is_dir() else []

    def save(self, summary: SessionSummary) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        name = f"{int(summary.created * 1000):014d}-{summary.id}.json"
        atomic_json(self.folder / name, summary.model_dump())
        for old in self._files()[:-KEEP]:
            old.unlink(missing_ok=True)

    def latest(self) -> SessionSummary | None:
        for path in reversed(self._files()):
            try:
                return SessionSummary.model_validate(json.loads(path.read_text()))
            except (OSError, ValueError):
                continue
        return None


def facts(project: Path, since: float, requests: list[str]) -> list[str]:
    """What Joinery itself knows of this session: its changes and the recent requests."""
    found: list[str] = []
    for tx in list_transactions(project):
        outcome = OUTCOMES.get(tx.state)
        if outcome is None or max(tx.created, tx.decided) < since:
            continue
        found.append(f"{outcome}: {tx.summary or 'Project changes'}")
    found += [f"The user asked: {text}" for text in requests[-10:] if text]
    return found


def build(agent_text: str, recorded: list[str]) -> str:
    """The summary a fresh session starts from: the agent's own, then Joinery's facts."""
    parts: list[str] = []
    if agent_text.strip():
        parts.append(agent_text.strip()[:MAX_AGENT_TEXT])
    if recorded:
        parts.append("What Joinery recorded:\n" + "\n".join("- " + f for f in recorded))
    return "\n\n".join(parts)
