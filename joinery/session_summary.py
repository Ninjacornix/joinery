"""A short summary of a chat session, so a fresh provider session can pick up the thread.

When a conversation nears the model's context window, Joinery asks the agent
for a summary and adds what it recorded itself: the changes the user accepted,
discarded or still has open, and the recent requests. A short block of pinned
facts (the changes and their outcomes, the open to-do, the console errors still
open, the user's own answers) is kept word for word: it never passes through the
agent's summary, and the answers carry over from one summary to the next
(ADR 0080). The summary is saved, and the next prompt of the fresh session
carries it.

Storage is a small interface (:class:`SummaryStore`): the chat host keeps each
summary in the open saved chat (``bridge.chat_record``), and
:class:`FileSummaryStore` keeps them in .joinery when no chat can be saved.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Protocol

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
#: Open console errors a summary pins, at most.
PINNED_ERRORS = 5
#: Pinned lines that only the earlier summary still knows: kept in the next one.
CARRIED = ("The user answered:", "The user decided:")
PINNED_HEADING = "Pinned facts (kept word for word through every summary):"


class SessionSummary(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    created: float = Field(default_factory=time.time)
    agent: str = ""
    text: str
    facts: list[str] = []
    #: Pinned lines beyond the change outcomes: the to-do, open errors, answers.
    pinned: list[str] = []


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


def _todo_line(todo: Any) -> str:
    """The open to-do in one line: its request, steps and done-when items."""
    steps = "; ".join(f"{n}) {s}" for n, s in enumerate(todo.steps, 1))
    line = f'To-do for "{todo.request}": {steps}'
    checks = [str(c.text) for c in getattr(todo, "done_when", [])]
    if checks:
        line += ". Done when: " + "; ".join(checks)
    return line + "."


def _open_errors(project: Path) -> list[str]:
    from joinery.console_errors import FIXED, IGNORED, Book

    try:
        groups = Book.load(project).listed()
    except (OSError, ValueError):
        return []
    lines: list[str] = []
    for group in groups:
        if group.status in {FIXED, IGNORED}:
            continue
        where = f" ({group.where})" if group.where else ""
        lines.append(f"Open {group.kind or 'error'}{where}: {group.message}")
    return lines[:PINNED_ERRORS]


def pinned(
    project: Path,
    todo: Any = None,
    answers: list[str] | None = None,
    earlier: SessionSummary | None = None,
) -> list[str]:
    """What a summary pins besides the change outcomes: the open to-do (a
    done_when.Todo), the console errors still open, the user's answers to the
    agent's questions, and the answers and decisions the earlier summary pinned."""
    lines: list[str] = []
    if todo is not None and getattr(todo, "steps", None):
        lines.append(_todo_line(todo))
    lines += _open_errors(project)
    if earlier is not None:
        lines += [p for p in earlier.pinned if p.startswith(CARRIED)]
    lines += [f"The user answered: {a.strip()}" for a in answers or [] if a.strip()]
    return list(dict.fromkeys(lines))


def build(
    agent_text: str, recorded: list[str], pinned_lines: list[str] | None = None
) -> str:
    """The summary a fresh session starts from: the agent's own, then the pinned
    facts (the change outcomes and ``pinned_lines``, never cut), then the recent
    requests."""
    parts: list[str] = []
    if agent_text.strip():
        parts.append(agent_text.strip()[:MAX_AGENT_TEXT])
    asked = [f for f in recorded if f.startswith("The user asked:")]
    kept = [f for f in recorded if f not in asked] + list(pinned_lines or [])
    if kept:
        parts.append(PINNED_HEADING + "\n" + "\n".join("- " + f for f in kept))
    if asked:
        parts.append("What Joinery recorded:\n" + "\n".join("- " + f for f in asked))
    return "\n\n".join(parts)
