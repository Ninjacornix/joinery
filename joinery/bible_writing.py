"""How the documents Joinery writes into a game are worded, and a filler check.

The art bible and the game brief are read by the user and by every agent before
each change, so a vague word in them turns into vague work. This module holds
the writing notes both documents carry, the filler words they warn about, and a
check that finds those words in the user's own sections.

The check only ever suggests. A filler word is never a lint problem, never
blocks approval and never changes a line: Journeyman offers plainer words when
the user asks, the same as any other suggestion.
"""

from __future__ import annotations

import re
from collections.abc import Collection

#: Words that sound like a description and say nothing a tool can act on.
FILLER_WORDS: tuple[str, ...] = (
    "stunning",
    "immersive",
    "seamless",
    "vibrant",
    "elevate",
    "delve",
    "tapestry",
    "unleash",
    "cutting-edge",
    "game-changer",
)

#: Each filler word with the forms that mean the same. "Elevated" (a walkway)
#: and "cutting edge" (a blade) are left alone: in a game they are things.
_FORMS: dict[str, str] = {
    "stunning": r"stunning(?:ly)?",
    "immersive": r"immersive(?:ly|ness)?",
    "seamless": r"seamless(?:ly)?",
    "vibrant": r"vibrant(?:ly)?",
    "elevate": r"elevat(?:e|es|ing)",
    "delve": r"delv(?:e|es|ed|ing)",
    "tapestry": r"tapestr(?:y|ies)",
    "unleash": r"unleash(?:es|ed|ing)?",
    "cutting-edge": r"cutting-edge",
    "game-changer": r"game[- ]chang(?:er|ers|ing)",
}
_FILLER = re.compile(
    r"(?<![\w-])(?:"
    + "|".join(f"(?P<w{i}>{_FORMS[w]})" for i, w in enumerate(FILLER_WORDS))
    + r")(?![\w-])",
    re.IGNORECASE,
)
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
#: Quoted or code text is the user naming a word, not using it.
_QUOTED = re.compile(r"`[^`\n]*`|\"[^\"\n]*\"|“[^”\n]*”")
_HEADING = re.compile(r"^##\s+(?:\d+[a-z]?\.\s*)?(.+?)\s*$")

_FILLER_LIST = ", ".join(FILLER_WORDS)

#: The notes at the top of a new art bible. An HTML comment: the /art dialog
#: and the lint skip it, and it sits above the first section.
BIBLE_GUIDE = f"""<!-- How to write in this file
Notes for you and Journeyman, not part of the bible.
- One rule per line. Keep each section short.
- Concrete nouns over adjectives: "chipped oak, iron bands", not "rustic and charming".
- A number wherever one exists: hex colours, metres, triangles, px/m, ms, BPM, LUFS.
- Name real references, what to take from each and what to leave.
- Say what to avoid as plainly as what to do.
- Only your taste. Leave a section Open rather than guess; Journeyman asks, it never fills one in for you.
- No filler: {_FILLER_LIST}.
- No em-dash chains, no lists padded to three, no "not just X but Y".
- Sentence case in headings, labels and table cells.
-->"""

#: The visible note at the top of a new game brief, in the same voice as the
#: brief's other notes. The /brief dialog shows the file as it is, so this is a
#: short quote rather than a hidden comment.
BRIEF_GUIDE = (
    "> How to write in this file: one idea per line, plain words, and a number where "
    'one exists ("a run lasts 8 to 12 minutes", not "fast-paced"). Name real games '
    "and what to take from each. Leave a section not decided rather than guess.\n"
    f"> Skip filler ({_FILLER_LIST}), em-dash chains and "
    '"not just X but Y".'
)

#: What every agent is told before it writes or edits one of these documents.
AGENT_GUIDE = (
    "When you write or edit the art bible, the game brief or another project "
    "document: one idea per line and short sections; concrete nouns over "
    "adjectives; a number wherever one exists (hex, metres, triangles, ms, LUFS); "
    "name real references with what to take from each and what to leave; say what "
    "to avoid as plainly as what to do; sentence case. No filler words "
    f"({_FILLER_LIST}), no em-dash chains, no lists padded to three, no "
    '"not just X but Y". Never invent the user\'s taste: leave a section open and '
    "ask."
)

#: How many filler words a suggestion names before it summarises the rest.
_MAX_NAMED = 5


def _word(match: re.Match[str]) -> str:
    for index, word in enumerate(FILLER_WORDS):
        if match.group(f"w{index}") is not None:
            return word
    return match.group(0).lower()


def filler(text: str, skip: Collection[str] = ()) -> list[tuple[str, str]]:
    """`(word, section heading)` for each filler word in the user's sections.

    Only text under a `## ` heading counts. Comments, `>` notes, tables of
    examples in quotes, code and quoted words are skipped, and so is every
    section whose heading (lower case, without number or "(checked)") is in
    `skip`. Each word is reported once per section, in reading order.
    """
    skipped = {s.lower() for s in skip}
    found: list[tuple[str, str]] = []
    heading = ""
    fenced = False
    for raw in _COMMENT.sub("", text).splitlines():
        line = raw.strip()
        if line.startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        match = _HEADING.match(line)
        if match is not None and not line.startswith("###"):
            heading = match.group(1)
            continue
        name = re.sub(r"\s*\(.*?\)\s*$", "", heading).lower()
        if not heading or name in skipped or line.startswith(">"):
            continue
        for hit in _FILLER.finditer(_QUOTED.sub("", line)):
            pair = (_word(hit), heading)
            if pair not in found:
                found.append(pair)
    return found


def suggestion(document: str, hits: list[tuple[str, str]], instead: str) -> str | None:
    """One line naming the filler words and where they are, or None.

    `document` is "bible" or "brief"; `instead` says what to write in their
    place, as the end of a sentence.
    """
    if not hits:
        return None
    named = [
        f"{word} ({heading.removesuffix(' (checked)')})"
        for word, heading in hits[:_MAX_NAMED]
    ]
    more = len(hits) - _MAX_NAMED
    listed = ", ".join(named) + (f" and {more} more" if more > 0 else "")
    return f"Filler words in the {document}: {listed}. Say what each means here instead: {instead}."
