"""Keeps the open chat's saved file (joinery.chats) in step with the conversation.

The sidebar names the chat each prompt belongs to ("" for a new one). Before the turn,
:meth:`ChatRecorder.switch` makes it the open chat: the provider's own session is resumed
when it is the same provider and connection that saved it and the provider still has
it; otherwise a fresh session starts and the first prompt carries a compact summary of
the old chat. Everything the host then shows the sidebar is recorded as it goes.
Recording never stops a turn: a chat that can't be saved only stops being saved.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from joinery import chats
from joinery.session_summary import FileSummaryStore, SessionSummary

#: Task states worth a line in the saved chat.
TASK_OUTCOMES = frozenset(
    {"in_review", "accepted", "discarded", "done", "failed", "stopped"}
)


class Sessions(Protocol):
    """What the recorder needs from the bridge."""

    @property
    def handle(self) -> str: ...

    async def resume(self, handle: str) -> bool: ...

    async def new_session(self) -> None: ...


class ChatRecorder:
    def __init__(self, project: Path, provider: str, profile: str = "") -> None:
        self.project = project
        self.provider = provider
        self.profile = profile
        #: The open chat ("" until the first prompt of a new chat).
        self.chat_id = ""
        #: The old chat's summary, sent ahead of the next prompt.
        self._prime = ""
        #: The provider session already holds a conversation (a turn ran or one resumed).
        self._used = False
        self._reply: list[str] = []
        self._steps: list[str] = []
        self._model = ""
        self._broken = False
        #: Where a session summary goes when no chat can be saved.
        self._summaries = FileSummaryStore(project)
        #: The last state recorded for each parallel task.
        self._tasks: dict[str, str] = {}

    async def switch(self, chat_id: str, sessions: Sessions) -> dict[str, Any] | None:
        """Open [chat_id] before a prompt ("" starts a new chat). Returns the event that
        tells the sidebar how it continued, or None when nothing changed."""
        if chat_id == self.chat_id and (chat_id or not self._used):
            return None
        try:
            meta = chats.read(self.project, chat_id)[0] if chat_id else None
        except (OSError, ValueError):
            meta = None
        if meta is None:
            # A new chat, or one that is gone: the provider starts over.
            if self._used:
                await sessions.new_session()
            self.chat_id, self._prime, self._used = "", "", False
            self._broken = False
            return None
        mode = "summary"
        same = (
            str(meta.get("provider", "")) == self.provider
            and str(meta.get("profile", "")) == self.profile
        )
        handle = str(meta.get("handle", ""))
        if same and handle and await sessions.resume(handle):
            mode = "resumed"
        else:
            if self._used:
                await sessions.new_session()
            _, entries = chats.read(self.project, chat_id)
            self._prime = chats.summary(self.project, meta, entries)
        self.chat_id = chat_id
        self._used = mode == "resumed"
        self._broken = False
        self._save(
            lambda: chats.append(
                self.project,
                chat_id,
                {"type": "session", "provider": self.provider, "mode": mode},
            )
        )
        if mode == "summary":
            self._save(
                lambda: chats.update(
                    self.project,
                    chat_id,
                    provider=self.provider,
                    profile=self.profile,
                    handle="",
                )
            )
        return {
            "kind": "chat",
            "chat_id": chat_id,
            "mode": mode,
            "message": chats.SUMMARY_NOTE if mode == "summary" else "",
        }

    def begin(
        self, data: dict[str, Any], attachments: tuple[str, ...]
    ) -> tuple[str, dict[str, Any] | None]:
        """Record the user's request. Returns the text for the agent (with the old chat's
        summary ahead of it, once) and, for a new chat, the event naming it."""
        text = str(data.get("text", ""))
        event: dict[str, Any] | None = None
        if not self.chat_id:
            try:
                self.chat_id = chats.create(
                    self.project, text, self.provider, self.profile
                )
                event = {"kind": "chat", "chat_id": self.chat_id, "mode": "new"}
            except (OSError, ValueError):
                self._broken = True
        targets = data.get("targets", [])
        self._save(
            lambda: chats.append(
                self.project,
                self.chat_id,
                {
                    "type": "user",
                    "text": text,
                    "targets": [t for t in targets if isinstance(t, dict)]
                    if isinstance(targets, list)
                    else [],
                    "attachments": list(attachments),
                },
            )
        )
        self._reply, self._steps = [], []
        if self._prime:
            return self._prime + "\n\n" + text, event
        return text, event

    def observe(self, event: dict[str, Any]) -> None:
        """Record one event the host shows the sidebar."""
        if not self.chat_id or self._broken:
            return
        kind = event.get("kind")
        if event.get("task_id"):
            self._observe_task(event)
            return
        if kind == "message_delta":
            if event.get("new_message") and self._reply:
                self._flush()
            self._reply.append(str(event.get("text", "")))
        elif kind == "message":
            self._flush()
            text = str(event.get("text", ""))
            level = str(event.get("level", "info"))
            entry: dict[str, Any] = (
                {"type": "agent", "text": text}
                if level == "info"
                else {"type": "notice", "text": text, "level": level}
            )
            self._save(lambda: chats.append(self.project, self.chat_id, entry))
        elif kind == "progress":
            text = str(event.get("text", "")).strip()
            if text and text not in self._steps:
                self._steps.append(text)
                self._save(
                    lambda: chats.append(
                        self.project, self.chat_id, {"type": "step", "text": text}
                    )
                )
        elif kind == "notice":
            # Joinery's own line ("didn't pass its checks", "fresh session").
            self._flush()
            notice = {
                "type": "notice",
                "text": str(event.get("text", "")),
                "level": str(event.get("level", "info")),
            }
            self._save(lambda: chats.append(self.project, self.chat_id, notice))
        elif kind == "execution":
            self._model = str(event.get("name") or event.get("model") or "")
        elif kind in {"file_preview", "file_decision"}:
            change: dict[str, Any] = {
                "type": "change",
                "transaction_id": str(event.get("transaction_id", "")),
                "summary": str(event.get("summary", "")),
                "state": "review"
                if kind == "file_preview"
                else "accepted"
                if event.get("accepted")
                else "rejected",
            }
            self._flush()
            self._save(lambda: chats.append(self.project, self.chat_id, change))
        elif kind == "done":
            self._flush()
            # The agent has the summary now; later prompts go without it.
            self._prime = ""
        elif kind == "timing":
            self._flush()
            turn: dict[str, Any] = {
                "type": "turn",
                "outcome": str(event.get("outcome", "")),
                "total_ms": int(event.get("total_ms", 0) or 0),
                "model": self._model,
            }
            self._save(lambda: chats.append(self.project, self.chat_id, turn))

    def _observe_task(self, event: dict[str, Any]) -> None:
        """A parallel task's events: only its start, its change and its outcome are kept;
        its own words stay on its card."""
        if event.get("kind") != "task":
            return
        state = str(event.get("state", ""))
        task_id = str(event.get("task_id", ""))
        started = state == "working" and task_id not in self._tasks
        if not started and state not in TASK_OUTCOMES:
            return
        if state == "in_review" and self._tasks.get(task_id) == "in_review":
            return
        self._tasks[task_id] = state
        entry: dict[str, Any] = {
            "type": "task",
            "task_id": task_id,
            "title": str(event.get("title", "")),
            "state": "started" if started else state,
            "origin": str(event.get("origin", "")),
        }
        if started:
            entry["request"] = str(event.get("request", ""))
        for key in ("transaction_id", "summary", "message"):
            if event.get(key):
                entry[key] = str(event[key])
        # Not flushed: the chat's own reply may still be streaming beside it.
        self._save(lambda: chats.append(self.project, self.chat_id, entry))

    def finish(self, sessions: Sessions) -> None:
        """After a turn: remember the provider's handle so the chat can be resumed."""
        self._flush()
        if not self.chat_id or self._broken:
            return
        self._used = True
        fields = {"handle": sessions.handle, "updated": chats.now()}
        if self._model:
            fields["model"] = self._model
        self._save(lambda: chats.update(self.project, self.chat_id, **fields))

    def save(self, summary: SessionSummary) -> None:
        """Keep a session summary in the open chat (a session_summary.SummaryStore);
        without a saved chat it goes to .joinery/summaries.

        The provider session it summarises is about to be replaced, so the chat
        forgets its handle: reopened later, it continues from the summary rather
        than resuming the nearly full session."""
        if not self.chat_id or self._broken:
            self._summaries.save(summary)
            return
        entry = {
            "type": "summary",
            "id": summary.id,
            "agent": summary.agent,
            "text": summary.text,
            "facts": summary.facts,
        }
        self._save(lambda: chats.append(self.project, self.chat_id, entry))
        self._save(lambda: chats.update(self.project, self.chat_id, handle=""))

    def latest(self) -> SessionSummary | None:
        """The open chat's newest session summary."""
        if not self.chat_id or self._broken:
            return self._summaries.latest()
        try:
            _, entries = chats.read(self.project, self.chat_id)
        except (OSError, ValueError):
            return None
        for entry in reversed(entries):
            if entry.get("type") == "summary":
                return SessionSummary(
                    id=str(entry.get("id", "")) or SessionSummary(text="").id,
                    agent=str(entry.get("agent", "")),
                    text=str(entry.get("text", "")),
                    facts=[str(f) for f in entry.get("facts", [])],
                )
        return None

    def _flush(self) -> None:
        text = "".join(self._reply)
        self._reply = []
        if text.strip() and self.chat_id and not self._broken:
            self._save(
                lambda: chats.append(
                    self.project, self.chat_id, {"type": "agent", "text": text}
                )
            )

    def _save(self, write: Callable[[], object]) -> None:
        if not self.chat_id or self._broken:
            return
        with contextlib.suppress(OSError, ValueError):
            write()
