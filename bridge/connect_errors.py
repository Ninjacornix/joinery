"""Why the chat could not connect, as a code the sidebar turns into its one fix.

A connection error event carries ``reason`` beside its words: ``not_installed`` (Get),
``signed_out`` (Sign in), ``outdated`` (Update), ``gate`` (Repair: the project's policy
hook), ``sandbox`` (the read-only project boundary is missing), ``quota`` and ``crashed``
(Reconnect). ``details`` is the provider's own last words (its stderr), with secrets
taken out, for the Output panel and "Copy details".
"""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Iterable
from typing import Any, Literal

from joinery.connection import _AUTH, _QUOTA, INSTALL_URLS

Reason = Literal[
    "not_installed", "signed_out", "outdated", "gate", "sandbox", "quota", "crashed"
]
#: How many of a provider's last stderr lines are kept.
TAIL_LINES = 20

_SECRET = re.compile(
    r"(?i)\b(?:sk|pk|rk)-[A-Za-z0-9_\-]{8,}"
    r"|\bbearer\s+[A-Za-z0-9._\-~+/=]{8,}"
    r"|\b((?:[a-z_]*api[_-]?key|[a-z_]*token|secret|password|authorization)"
    r"[\"']?\s*[:=]\s*)[\"']?(?:bearer\s+)?[^\s\"',}]+[\"']?"
)


class ConnectError(ValueError):
    """A connection refused for a known reason; ``url`` is where its fix is."""

    def __init__(
        self, message: str, reason: Reason, *, url: str = "", details: str = ""
    ) -> None:
        super().__init__(message)
        self.reason: Reason = reason
        self.url = url
        self.details = details


class ProviderGone(ConnectionError):
    """The provider's process ended under the chat; ``details`` is its stderr tail."""

    reason: Reason = "crashed"

    def __init__(self, message: str, details: str = "") -> None:
        super().__init__(message)
        self.details = details


def redact(text: str, secrets: Iterable[str] = ()) -> str:
    """``text`` without API keys, tokens or passwords (and without ``secrets``)."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redacted]")
    return _SECRET.sub(lambda m: (m.group(1) or "") + "[redacted]", text)


class StderrTail:
    """The last lines a provider wrote to stderr, kept as they arrive."""

    def __init__(self, lines: int = TAIL_LINES) -> None:
        self._max = lines
        self._lines: deque[str] = deque(maxlen=lines)
        self._partial = ""

    def feed(self, chunk: bytes | str) -> None:
        text = chunk.decode("utf-8", "replace") if isinstance(chunk, bytes) else chunk
        parts = (self._partial + text).split("\n")
        self._partial = parts.pop()
        self._lines.extend(line.rstrip() for line in parts if line.strip())

    def text(self, secrets: Iterable[str] = ()) -> str:
        lines = [*self._lines, *([self._partial] if self._partial.strip() else [])]
        return redact("\n".join(lines[-self._max :]), secrets)


def tail(text: str, lines: int = TAIL_LINES, secrets: Iterable[str] = ()) -> str:
    kept = StderrTail(lines)
    kept.feed(text)
    return kept.text(secrets)


def reason_of(exc: BaseException) -> Reason:
    """The reason a connection failed: the one raised, else read from its words."""
    reason = getattr(exc, "reason", None)
    if isinstance(reason, str) and reason:
        return reason  # type: ignore[return-value]
    text = str(exc)
    if re.search(r"not installed", text, re.IGNORECASE):
        return "not_installed"
    if re.search(r"policy hook|hooks changed", text, re.IGNORECASE):
        return "gate"
    if re.search(r"sandbox|bubblewrap|bwrap|read-only project", text, re.IGNORECASE):
        return "sandbox"
    if _QUOTA.search(text):
        return "quota"
    if _AUTH.search(text):
        return "signed_out"
    if re.search(r"update|out of date|too old|unknown option", text, re.IGNORECASE):
        return "outdated"
    return "crashed"


def event(agent_id: str, name: str, exc: BaseException) -> dict[str, Any]:
    """The connection error event for ``exc``."""
    reason = reason_of(exc)
    found: dict[str, Any] = {
        "kind": "connection",
        "state": "error",
        "agent": agent_id,
        "name": name,
        "message": str(exc) or f"{name} stopped.",
        "reason": reason,
    }
    url = getattr(exc, "url", "") or (
        INSTALL_URLS.get(agent_id, "")
        if reason in {"not_installed", "outdated"}
        else ""
    )
    if url:
        found["url"] = url
    details = getattr(exc, "details", "")
    if isinstance(details, str) and details:
        found["details"] = redact(details)
    return found
