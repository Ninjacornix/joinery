"""Draft the game brief's open sections from the user's own design document.

A design document (GDD) the user adds is copied into `design/docs/` and listed
in the brief. When it is Markdown or plain text, its headings are read and a
section whose heading names a brief section ("Core loop", "Target audience",
"Controls") is copied, word for word, into that brief section, but only while
the brief section is still open (`_Not decided yet._`, or a starting point's
"Not decided yet. …" line). Nothing is summarised,
reworded or guessed: a heading this table does not know is left in the
document, and so is every brief section the document does not name. Each
copied block ends with the file and heading it came from.

PDFs are not read: their text comes out of a PDF in reading-order fragments
(columns, tables, headers), and every supported agent reads a PDF itself, so
the brief says the document is there instead of drafting from a guess.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

#: The longest block copied into one brief section; the rest stays in the file.
MAX_BLOCK = 1500
#: Brief section heading -> GDD heading phrases that mean it, most specific first.
#: Matched as whole words in the GDD heading, case-insensitive.
SYNONYMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Pitch", ("elevator pitch", "pitch", "logline", "high concept", "one line")),
    ("Player fantasy", ("player fantasy", "fantasy")),
    ("Core loop", ("core loop", "gameplay loop", "game loop", "core gameplay")),
    (
        "Player verbs",
        ("player verbs", "verbs", "mechanics", "player actions", "abilities"),
    ),
    ("Controls per device", ("controls", "control scheme", "input")),
    ("Camera", ("camera",)),
    (
        "Win, lose, progression",
        (
            "progression",
            "win and lose",
            "win conditions",
            "lose conditions",
            "goals",
            "objectives",
        ),
    ),
    ("Session length", ("session length", "session", "play time", "playtime")),
    ("Platforms and stores", ("platforms", "platform", "stores", "distribution")),
    ("Online", ("multiplayer", "online", "networking", "co-op", "coop")),
    ("Saving", ("saving", "save system", "saves", "persistence")),
    ("Languages", ("languages", "localisation", "localization", "translations")),
    ("Accessibility goals", ("accessibility",)),
    ("Monetisation", ("monetisation", "monetization", "business model", "pricing")),
    (
        "Scope and milestones",
        ("scope", "milestones", "roadmap", "schedule", "timeline"),
    ),
    (
        "Audience and content",
        ("target audience", "audience", "content rating", "rating"),
    ),
    (
        "Genre and references",
        ("genre", "inspirations", "influences", "references", "comparable games"),
    ),
)

_ATX = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
_SETEXT = re.compile(r"^(=+|-+)[ \t]*$")
_FENCE = re.compile(r"^[ \t]*(```|~~~)")
_NUMBER = re.compile(r"^[\d.)\s]+")
#: A brief section heading, as `brief.sections` reads it.
_BRIEF_HEADING = re.compile(r"^##\s+(?:\d+\.\s*)?(.+?)\s*$", re.MULTILINE)
#: An open section's own line: the marker alone, or a starting point's
#: "Not decided yet. …" sentence (the blank game's core loop).
_STANDALONE = re.compile(r"^_?Not decided yet\._?(?:[ \t].*)?$", re.MULTILINE)


@dataclass(frozen=True)
class Part:
    """One section of a design document: its heading, level and own text."""

    heading: str
    level: int
    body: str


def outline(text: str) -> list[Part]:
    """Every headed section of a Markdown or text document, in order.

    ATX (`## Core loop`) and setext (a line underlined with `===` or `---`)
    headings count; lines inside fenced code do not. A section's body runs to
    the next heading of the same or a higher level, so its subsections come
    with it.
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    marks: list[
        tuple[int, int, str, int]
    ] = []  # (start line, level, heading, body start)
    fenced = False
    for index, line in enumerate(lines):
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        atx = _ATX.match(line)
        if atx:
            marks.append((index, len(atx.group(1)), atx.group(2).strip(), index + 1))
            continue
        if (
            index > 0
            and _SETEXT.match(line)
            and lines[index - 1].strip()
            and not _ATX.match(lines[index - 1])
            and (index < 2 or not lines[index - 2].strip())
        ):
            level = 1 if line.lstrip().startswith("=") else 2
            marks.append((index - 1, level, lines[index - 1].strip(), index + 1))
    parts: list[Part] = []
    for position, (_start, level, heading, body_start) in enumerate(marks):
        end = len(lines)
        for later in marks[position + 1 :]:
            if later[1] <= level:
                end = later[0]
                break
        parts.append(Part(heading, level, "\n".join(lines[body_start:end]).strip()))
    return parts


def _words(text: str) -> str:
    cleaned = _NUMBER.sub("", text.lower())
    return " " + re.sub(r"[^a-z0-9-]+", " ", cleaned).strip() + " "


def brief_section(heading: str) -> str:
    """The brief section a design-document heading names, or ""."""
    words = _words(heading)
    for section, phrases in SYNONYMS:
        if any(f" {phrase} " in words for phrase in phrases):
            return section
    return ""


def _demoted(body: str) -> str:
    """The body with its own headings as bold lines, so the brief keeps its sections."""
    out: list[str] = []
    fenced = False
    for line in body.split("\n"):
        if _FENCE.match(line):
            fenced = not fenced
        atx = None if fenced else _ATX.match(line)
        out.append(f"**{atx.group(2).strip()}**" if atx else line)
    return "\n".join(out).strip()


def _capped(body: str, source: str) -> str:
    if len(body) <= MAX_BLOCK:
        return body
    cut = body.rfind("\n\n", 0, MAX_BLOCK)
    if cut < MAX_BLOCK // 3:
        cut = body.rfind("\n", 0, MAX_BLOCK)
    if cut < MAX_BLOCK // 3:
        cut = MAX_BLOCK
    return body[:cut].rstrip() + f"\n\n(The rest is in {source}.)"


def matches(documents: Iterable[tuple[str, str]]) -> dict[str, tuple[str, Part]]:
    """Brief section -> (document path, part) for the first part that names it.

    `documents` are (project path, text) pairs, in the order the user added
    them. Parts with no text of their own are skipped.
    """
    found: dict[str, tuple[str, Part]] = {}
    for path, text in documents:
        for part in outline(text):
            section = brief_section(part.heading)
            if section and section not in found and part.body.strip():
                found[section] = (path, part)
    return found


def draft_open_sections(
    text: str, documents: Sequence[tuple[str, str]]
) -> tuple[str, list[str]]:
    """The brief with its open sections drafted from the documents, and which.

    Only a section that still holds the open marker changes. A marker on a
    line of its own is replaced by the copied block; a section whose markers
    sit inside a line ("- Audience: _Not decided yet._") keeps them and gets
    the block below, so it still reads as open until the user decides.
    """
    found = matches(documents)
    heads = list(_BRIEF_HEADING.finditer(text))
    pieces: list[str] = [text[: heads[0].start()] if heads else text]
    drafted: list[str] = []
    for position, head in enumerate(heads):
        end = heads[position + 1].start() if position + 1 < len(heads) else len(text)
        heading, body = head.group(1), text[head.end() : end]
        if heading in found and "Not decided yet." in body:
            path, part = found[heading]
            block = (
                _capped(_demoted(part.body), path)
                + f'\n\n_From {path}, "{part.heading}"._'
            )
            marker = _STANDALONE.search(body)
            if marker is not None:
                body = body[: marker.start()] + block + body[marker.end() :]
            else:
                body = body.rstrip("\n") + "\n\n" + block + "\n\n"
            drafted.append(heading)
        pieces.append(text[head.start() : head.end()] + body)
    return "".join(pieces), drafted
