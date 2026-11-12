"""Edit part of a text file by find/replace pairs instead of resending it whole.

An agent changing three lines of a 400-line script through Godot's ``write_file``
would otherwise write all 400 lines back, paying for them as output tokens and
risking unrelated edits. The gateway accepts ``patch: [{find, replace}, …]`` in
place of ``content``, applies the pairs to the staged file and sends the result
on as ``content``: the Godot MCP and the review see an ordinary write.

A ``find`` is looked for exactly first. When it is not in the file as written,
it is compared line by line with growing tolerance (ADR 0078), like codex's
``seek_sequence``: trailing whitespace, then indentation, then typographic
punctuation (curly quotes, dashes, odd spaces). Every tier still needs exactly
one match, and no tier ever equates different tokens: only whitespace around a
line and look-alike punctuation are forgiven. Lines the replacement carries over
unchanged keep the file's own text, and new lines take the file's indentation.
"""

from __future__ import annotations

import difflib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

#: Pairs in one patch at most.
MAX_PAIRS = 50

EXACT = "exact"
TRAILING = "trailing whitespace"
INDENT = "indentation"
PUNCTUATION = "punctuation"

_PUNCTUATION = str.maketrans(
    {
        **dict.fromkeys("\u2010\u2011\u2012\u2013\u2014\u2015\u2212", "-"),
        **dict.fromkeys("\u2018\u2019\u201a\u201b", "'"),
        **dict.fromkeys("\u201c\u201d\u201e\u201f", '"'),
        **dict.fromkeys(
            "\u00a0\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
            "\u202f\u205f\u3000",
            " ",
        ),
    }
)

#: Each tolerant tier: its name and how it sees one line.
_TIERS: tuple[tuple[str, Callable[[str], str]], ...] = (
    (TRAILING, lambda line: line.rstrip()),
    (INDENT, lambda line: line.strip()),
    (PUNCTUATION, lambda line: line.translate(_PUNCTUATION).strip()),
)


@dataclass(frozen=True)
class Applied:
    """A patched text and, per pair, the tolerance its ``find`` needed."""

    text: str
    tolerances: tuple[str, ...]

    def note(self) -> str:
        """One line naming the pairs that needed a tolerance ("" when none did)."""
        loose = [
            f"pair {number} matched ignoring {tier}"
            for number, tier in enumerate(self.tolerances, 1)
            if tier != EXACT
        ]
        return ("Patch: " + "; ".join(loose) + ".") if loose else ""


def apply_patch(text: str, patch: Any) -> str:
    """``text`` with each ``{find, replace}`` applied in order (see :func:`apply`)."""
    return apply(text, patch).text


def apply(text: str, patch: Any) -> Applied:
    """``text`` with each ``{find, replace}`` applied in order. Each ``find`` must be
    found exactly once in the text as it is at that step, so a pair never lands in
    the wrong place."""
    if not isinstance(patch, list) or not 1 <= len(patch) <= MAX_PAIRS:
        raise ValueError(
            f"patch is a list of 1 to {MAX_PAIRS} {{find, replace}} pairs."
        )
    tolerances: list[str] = []
    for number, pair in enumerate(patch, 1):
        if (
            not isinstance(pair, dict)
            or set(pair) != {"find", "replace"}
            or not isinstance(pair["find"], str)
            or not isinstance(pair["replace"], str)
            or not pair["find"]
        ):
            raise ValueError(
                f"patch pair {number}: give exactly find (non-empty) and replace, both text."
            )
        text, tier = _apply_pair(text, pair["find"], pair["replace"], number)
        tolerances.append(tier)
    return Applied(text, tuple(tolerances))


def _apply_pair(text: str, find: str, replace: str, number: int) -> tuple[str, str]:
    found = text.count(find)
    if found == 1:
        return text.replace(find, replace, 1), EXACT
    if found > 1:
        raise ValueError(
            f"patch pair {number}: find matches {found} times in the file; it must "
            "match exactly once. Add the lines around it to make it unique."
        )
    lines = text.split("\n")
    pattern = _lines(find)
    if not pattern or not any(line.strip() for line in pattern):
        raise ValueError(_missing(number, lines, pattern))
    for tier, seen in _TIERS:
        wanted = [seen(line) for line in pattern]
        size = len(wanted)
        starts = [
            start
            for start in range(len(lines) - size + 1)
            if all(seen(lines[start + i]) == wanted[i] for i in range(size))
        ]
        if len(starts) > 1:
            raise ValueError(
                f"patch pair {number}: find matches {len(starts)} places when "
                f"{tier} is ignored; it must match exactly once. Add the lines "
                "around it to make it unique."
            )
        if starts:
            start = starts[0]
            window = lines[start : start + size]
            new = _replacement(window, pattern, _lines(replace), seen, tier)
            return "\n".join(lines[:start] + new + lines[start + size :]), tier
    raise ValueError(_missing(number, lines, pattern))


def _lines(text: str) -> list[str]:
    """``text`` as whole lines; a final line break ends the last line, it is not an
    empty line of its own."""
    lines = text.split("\n")
    if len(lines) > 1 and lines[-1] == "":
        lines.pop()
    return lines


def _replacement(
    window: list[str],
    pattern: list[str],
    replace: list[str],
    seen: Callable[[str], str],
    tier: str,
) -> list[str]:
    """The lines that take the matched ``window``'s place.

    A replacement line that is one of the find's lines, carried over, is the file's
    own line. A new line gets the file's indentation in place of the find's.
    """
    carried: dict[int, str] = {}
    matcher = difflib.SequenceMatcher(
        None, [seen(line) for line in pattern], [seen(line) for line in replace], False
    )
    for tag, a0, _a1, b0, b1 in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(b1 - b0):
                carried[b0 + offset] = window[a0 + offset]
    eol = "\r" if window and window[0].endswith("\r") else ""
    shift = _indentation(window, pattern) if tier != TRAILING else None
    out: list[str] = []
    for index, line in enumerate(replace):
        if index in carried:
            out.append(carried[index])
            continue
        if shift is not None:
            line = shift(line)
        if eol and not line.endswith("\r"):
            line += eol
        out.append(line)
    return out


def _lead(line: str) -> str:
    """The line's indentation."""
    return line[: len(line) - len(line.lstrip(" \t"))]


def _indentation(window: list[str], pattern: list[str]) -> Callable[[str], str]:
    """Moves a line from the find's indentation to the file's.

    The first non-blank find line and the file line it matched fix the base. The
    indentation past the base is converted between tabs and spaces when the file and
    the find use different ones (one space level is the smallest space step seen).
    """
    pairs = [(p, w) for p, w in zip(pattern, window, strict=True) if p.strip()]
    find_base = _lead(pairs[0][0]) if pairs else ""
    file_base = _lead(pairs[0][1]) if pairs else ""
    file_tabs = any(_lead(w).startswith("\t") for _p, w in pairs)
    unit = _space_unit([p for p, _w in pairs], len(find_base))
    file_unit = _space_unit([w for _p, w in pairs], 0)

    def shift(line: str) -> str:
        indent = _lead(line)
        if not line.strip() or not indent.startswith(find_base):
            return line
        extra = indent[len(find_base) :]
        if file_tabs and " " in extra:
            extra = extra.replace("\t", " " * unit)
            extra = "\t" * (len(extra) // unit) + " " * (len(extra) % unit)
        elif not file_tabs and "\t" in extra:
            extra = extra.replace("\t", " " * file_unit)
        return file_base + extra + line[len(indent) :]

    return shift


def _space_unit(lines: list[str], base: int) -> int:
    """The smallest step of space indentation past ``base`` (4 when there is none)."""
    steps = {
        len(_lead(line)) - base
        for line in lines
        if line.strip() and "\t" not in _lead(line)
    }
    return min((step for step in steps if step > 0), default=4)


def _missing(number: int, lines: list[str], pattern: list[str]) -> str:
    """Why a find matched nowhere, with the file's closest lines to copy from."""
    message = (
        f"patch pair {number}: find is not in the file, even ignoring whitespace "
        "around lines and look-alike punctuation. Read the file and copy the exact "
        "lines, with enough around them to be unique."
    )
    first = next((line.strip() for line in pattern if line.strip()), "")
    if not first or not lines:
        return message
    scored = [
        (difflib.SequenceMatcher(None, first, line.strip()).ratio(), index)
        for index, line in enumerate(lines)
        if line.strip()
    ]
    if not scored:
        return message
    best, index = max(scored)
    if best < 0.6:
        return message
    size = max(len(pattern), 1)
    near = "\n".join(
        f"{at + 1}: {lines[at].rstrip()}"
        for at in range(index, min(index + size, len(lines)))
    )
    return message + " Closest lines:\n" + near
