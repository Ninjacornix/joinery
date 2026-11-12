"""Edit part of a text file by exact find/replace pairs instead of resending it whole.

An agent changing three lines of a 400-line script through Godot's ``write_file``
would otherwise write all 400 lines back, paying for them as output tokens and
risking unrelated edits. The gateway accepts ``patch: [{find, replace}, …]`` in
place of ``content``, applies the pairs to the staged file and sends the result
on as ``content``: the Godot MCP and the review see an ordinary write.
"""

from __future__ import annotations

from typing import Any

#: Pairs in one patch at most.
MAX_PAIRS = 50


def apply_patch(text: str, patch: Any) -> str:
    """``text`` with each ``{find, replace}`` applied in order. Each ``find`` must be
    found exactly once in the text as it is at that step, so a pair never lands in
    the wrong place."""
    if not isinstance(patch, list) or not 1 <= len(patch) <= MAX_PAIRS:
        raise ValueError(
            f"patch is a list of 1 to {MAX_PAIRS} {{find, replace}} pairs."
        )
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
        found = text.count(pair["find"])
        if found != 1:
            raise ValueError(
                f"patch pair {number}: find matches {found} times in the file; it must "
                "match exactly once. Read the file and copy the exact lines, with enough "
                "around them to be unique."
            )
        text = text.replace(pair["find"], pair["replace"], 1)
    return text
