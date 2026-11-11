"""Which staged change a provider's own shell and file tools may touch.

Every staged change lives in its own folder under ``.joinery/transactions/``. The
gateway's tools already read a change through its stage and refuse any other change's
folder (ADR 0041); this is the same rule for the provider's native tools, judged by
the shared policy hook for Codex, Claude Code, Gemini CLI and opencode alike.

Joinery starts each provider session with ``JOINERY_STAGE`` set: the id of the one
staged change it may read (the change under review, for a reviewer), or ``none``.
With it set, a shell command or native file tool whose arguments reach into
``.joinery/transactions/`` is refused unless every such path is inside that one
change's folder, and a read-only role may not write there at all. A session Joinery
did not start (no ``JOINERY_STAGE``) is judged as before.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from typing import Any

from joinery.policy import Severity, Verdict

#: The session's allowed staged change ("none": no staged change at all).
STAGE_ENV = "JOINERY_STAGE"
NO_STAGE = "none"
#: A staged change's id: what transactions.py names a change folder.
_STAGE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_SEP = r"[/\\]+"
_CHANGES = re.compile(rf"\.joinery{_SEP}transactions(?=[/\\'\"\s]|$)", re.IGNORECASE)
_GLOB_IN_JOINERY = re.compile(r"\.joinery[/\\][^\s'\"]*[*?\[{]")

REFUSED = (
    "No agent reads another change's staged folder. Name project files by their "
    "project path (res://… or the project folder), or read the stage folder your "
    "prompt gives you."
)
READ_ONLY = "This agent only reads; it can't write into a staged change."


def stage_id(value: str) -> str:
    """The allowed change id from ``JOINERY_STAGE`` ("" when none is allowed)."""
    value = value.strip()
    if value == NO_STAGE or not _STAGE_ID.fullmatch(value):
        return ""
    return value


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _strings(item)


def _allowed_rest(text: str, allowed: str) -> str:
    """``text`` with each path inside the allowed change's folder taken out.

    A path that climbs back out of it (``…/<id>/..``) is left in, so it is judged.
    """
    if not allowed:
        return text
    inside = re.compile(
        rf"\.joinery{_SEP}transactions{_SEP}{re.escape(allowed)}"
        rf"(?![A-Za-z0-9_-])(?!{_SEP}\.\.(?:[/\\'\"\s]|$))"
    )
    return inside.sub("", text)


def touches_changes(arguments: Any, allowed: str = "") -> bool:
    """Some argument reaches into a staged change other than ``allowed``.

    Besides a plain path, a glob under ``.joinery`` and a command that names
    ``.joinery`` and ``transactions`` apart (``cd .joinery && ls transactions``) count.
    """
    for text in _strings(arguments):
        rest = _allowed_rest(text, allowed)
        if _CHANGES.search(rest) or _GLOB_IN_JOINERY.search(rest):
            return True
        lowered = rest.lower()
        if ".joinery" in lowered and "transactions" in lowered:
            return True
    return False


def names_changes(arguments: Any) -> bool:
    """Some argument names a ``.joinery/transactions`` path, the allowed one included."""
    return any(_CHANGES.search(text) for text in _strings(arguments))


def stage_violation(
    allowed: str, arguments: Any, *, read_only: bool, writes: bool
) -> Verdict | None:
    """A provider-native call's verdict on staged-change folders (see the module)."""
    if read_only and writes and names_changes(arguments):
        return Verdict(Severity.DENY, READ_ONLY)
    if touches_changes(arguments, stage_id(allowed)):
        return Verdict(Severity.DENY, REFUSED)
    return None
