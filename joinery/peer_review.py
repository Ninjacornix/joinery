"""A second agent reads every submitted change before the user sees it.

When a change passes Joinery's mechanical checks, the reviewer agent (role
``reviewer``, read-only) reads it against the request, the art bible, the game
brief, the diff, the staged images and the checks' results, and reports notes:
``info``, ``warn`` or ``block``. A ``block`` goes back to the author agent as a
fixable problem, at most ``rounds`` times; then the review opens and any notes
left are shown with it. The reviewer is time-boxed: on a timeout or an error the
review opens without it and says so.

Nothing here knows which provider runs the reviewer; the chat host supplies a
:data:`Reviewer` (``bridge/reviewer.py``) that runs one with the same provider and
model as the author's, unless ``[agents] reviewer_model`` names another.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from joinery.reasons import REVIEW_RULES

Severity = Literal["info", "warn", "block"]
SEVERITIES: tuple[Severity, ...] = ("info", "warn", "block")
#: How the reviewer's part of the review went.
Status = Literal["reviewed", "timeout", "error", "off", "skipped"]
MAX_NOTES = 12
NOTE_CHARS = 400
#: Images the reviewer sees with the change (the providers' own limit is about this).
MAX_IMAGES = 4
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})
#: How much of each document the reviewer's prompt carries.
DIFF_CHARS = 14_000
BIBLE_CHARS = 6_000
BRIEF_CHARS = 3_000
BIBLE_PATH = "art/bible.md"
BRIEF_PATH = "design/brief.md"


@dataclass(frozen=True)
class ReviewNote:
    severity: Severity
    text: str
    file: str = ""
    #: What kind of problem it is (``joinery.reasons.REVIEW_RULES``); "" untold.
    rule: str = ""

    def as_dict(self) -> dict[str, str]:
        data = {"severity": self.severity, "text": self.text}
        if self.file:
            data["file"] = self.file
        if self.rule:
            data["rule"] = self.rule
        return data


@dataclass
class PeerReview:
    """The reviewer's findings on one submit, or why there are none."""

    status: Status
    notes: list[ReviewNote] = field(default_factory=list)
    #: One plain line for the user when the reviewer didn't finish ("" otherwise).
    message: str = ""
    elapsed_s: float = 0.0
    model: str = ""

    def blocking(self) -> list[ReviewNote]:
        return [note for note in self.notes if note.severity == "block"]

    def status_dict(self, rounds: int) -> dict[str, Any]:
        """The ``reviewer`` field of the review: how its review went."""
        data: dict[str, Any] = {
            "status": self.status,
            "rounds": rounds,
            "elapsed_s": round(self.elapsed_s, 1),
        }
        if self.message:
            data["message"] = self.message
        if self.model:
            data["model"] = self.model
        return data


@dataclass(frozen=True)
class ReviewRequest:
    """Everything the reviewer reads, gathered when the checks passed."""

    project: Path
    stage: Path
    request: str
    summary: str
    changes: list[dict[str, str]]
    diff: str
    bible: str
    brief: str
    checks: str
    images: tuple[str, ...]
    #: The author's model choice ({mode, model, effort}); the reviewer uses it too.
    selection: dict[str, Any]
    #: 1 for the first review of this turn's change, 2 after one send-back, …
    round: int
    #: The notes the author was asked to fix last time, to check they were.
    previous: tuple[ReviewNote, ...] = ()


#: Runs one review: the chat host's reviewer agent, or a test's fake.
Reviewer = Callable[[ReviewRequest], Awaitable[PeerReview]]


def notes_from(payload: Any) -> list[ReviewNote]:
    """The reviewer's report ({notes: [{severity, text, file?}]}), checked.

    Raises ValueError in words the reviewer can act on.
    """
    raw = payload.get("notes") if isinstance(payload, dict) else payload
    if not isinstance(raw, list):
        raise TypeError("notes is a list of {severity, text, file?}.")
    if len(raw) > MAX_NOTES:
        raise ValueError(f"Report at most {MAX_NOTES} notes; keep the most important.")
    found: list[ReviewNote] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise TypeError("Each note is {severity, text, file?}.")
        severity = str(entry.get("severity", "")).strip().lower()
        text = " ".join(str(entry.get("text", "")).split())
        path = str(entry.get("file", "") or "").strip()
        if severity not in SEVERITIES:
            raise ValueError("severity is one of info, warn or block.")
        if not 1 <= len(text) <= NOTE_CHARS:
            raise ValueError(f"Each note's text is one to {NOTE_CHARS} characters.")
        if len(path) > 400 or "\n" in path:
            raise ValueError("file is one project path.")
        rule = str(entry.get("rule", "") or "").strip().lower()
        if rule and rule not in REVIEW_RULES:
            raise ValueError("rule is one of " + ", ".join(REVIEW_RULES) + ".")
        found.append(ReviewNote(severity, text, path.removeprefix("res://"), rule))
    return found


_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def notes_in_text(text: str) -> list[ReviewNote] | None:
    """Notes the reviewer wrote as JSON in its reply instead of calling the tool."""
    for candidate in [*_FENCE.findall(text), text.strip()]:
        try:
            data = json.loads(candidate)
        except ValueError:
            continue
        try:
            return notes_from(data)
        except (TypeError, ValueError):
            continue
    return None


def _read(path: Path, limit: int) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text if len(text) <= limit else text[:limit] + "\n[…cut]"


def request_for(
    *,
    project: Path,
    stage: Path,
    request: str,
    summary: str,
    changes: list[dict[str, str]],
    diff: str,
    checks: str,
    selection: dict[str, Any],
    round: int,
    previous: tuple[ReviewNote, ...] = (),
) -> ReviewRequest:
    """Gather what the reviewer reads. The bible and brief come from the stage, so a
    change to them is reviewed as changed."""
    images = tuple(
        str(stage / change["path"])
        for change in changes
        if Path(change["path"]).suffix.lower() in IMAGE_SUFFIXES
        and change.get("change") != "deleted"
        and (stage / change["path"]).is_file()
    )[:MAX_IMAGES]
    return ReviewRequest(
        project=project,
        stage=stage,
        request=request.strip()[:2000],
        summary=summary.strip()[:160],
        changes=[{"path": c["path"], "change": c["change"]} for c in changes][:60],
        diff=diff if len(diff) <= DIFF_CHARS else diff[:DIFF_CHARS] + "\n[…cut]",
        bible=_read(stage / BIBLE_PATH, BIBLE_CHARS),
        brief=_read(stage / BRIEF_PATH, BRIEF_CHARS),
        checks=checks,
        images=images,
        selection=dict(selection),
        round=round,
        previous=previous,
    )


def one_line(notes: list[ReviewNote], reviewer: dict[str, Any]) -> str:
    """The review's notes in one line ("Reviewer: 1 warning, 2 notes"); "" for none."""
    status = str(reviewer.get("status", ""))
    if status in {"timeout", "error"}:
        return str(reviewer.get("message", "")) or "Opened without the reviewer."
    if not notes:
        return "Reviewer: no notes." if status == "reviewed" else ""
    counts = {s: sum(1 for n in notes if n.severity == s) for s in SEVERITIES}
    parts = []
    if counts["block"]:
        parts.append(f"{counts['block']} unresolved")
    if counts["warn"]:
        parts.append(f"{counts['warn']} warning" + ("s" if counts["warn"] > 1 else ""))
    if counts["info"]:
        parts.append(f"{counts['info']} note" + ("s" if counts["info"] > 1 else ""))
    return "Reviewer: " + ", ".join(parts)
