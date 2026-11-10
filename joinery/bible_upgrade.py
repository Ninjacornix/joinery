"""Upgrade a first-format art bible to the second, as a reviewed edit.

The upgrade only adds. Every existing line and heading stays, byte for byte and
in order; what is added is the version marker, each missing section with the
*open* marker, an empty reference board under References, the notes on how to
write in the file (a comment under the marker), and inside the checked-rules
block `schema = 2` plus the new rule tables, commented out, so no existing value
changes and nothing new is enforced until the user removes a `#`. Because only
open markers are added, a bible that could be approved before can still be
approved after.

Upgrading changes the bible's bytes, so its approval no longer matches: the user
reviews the change and approves again. It is offered, never automatic.
"""

from __future__ import annotations

import re
import tomllib
from typing import Any

from joinery import bible_schema
from joinery.bible_presets import DEFAULT_RULES, KEY_TABLES, family_chunks
from joinery.bible_writing import BIBLE_GUIDE

_FENCE = re.compile(r"^```toml[ \t]*$")
_BOARD = (
    "### Reference board",
    "| # | Reference | Kind | Take this | Not that | File |",
    "|---|---|---|---|---|---|",
    "",
    "> One row per reference: R1, R2, … Kind is one of "
    + " ".join(f"`{k}`" for k in bible_schema.REFERENCE_KINDS)
    + ". Files live under `art/reference/`.",
)
_NEW_RULES_NOTE = (
    "# New checks in format 2. Remove the # in front of a table and its lines "
    "to turn one on."
)


def _section_block(section: bible_schema.Section) -> list[str]:
    return [
        f"## {bible_schema.heading_text(section)}",
        section.hint,
        "",
        bible_schema.OPEN_MARKER,
        "",
    ]


def _heading_index(lines: list[str]) -> dict[str, int]:
    """Section token -> line index of its heading, for the first occurrence."""
    found: dict[str, int] = {}
    for index, line in enumerate(lines):
        if line.startswith("## "):
            section = bible_schema.find_section(line)
            if section is not None and section.token and section.token not in found:
                found[section.token] = index
    return found


def _section_end(lines: list[str], start: int) -> int:
    for index in range(start + 1, len(lines)):
        if lines[index].startswith("## "):
            return index
    return len(lines)


def _lookup(rules: dict[str, Any], path: str) -> Any:
    node: Any = rules
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _commented(line: str) -> str:
    if not line.strip() or line.lstrip().startswith("#"):
        return line
    return "# " + line


def _new_rule_lines(existing: dict[str, Any]) -> list[str]:
    """Commented default tables for every family or key the block lacks."""
    out: list[str] = []
    for path, lines in family_chunks(DEFAULT_RULES):
        if not path:
            continue  # schema is added on its own; exclude keeps its default
        base = path.removesuffix(".*")
        present = _lookup(existing, base)
        if path not in KEY_TABLES:
            if present is not None:
                continue
            kept = lines
        else:
            table = present if isinstance(present, dict) else {}
            kept = [lines[0]] + [
                line
                for line in lines[1:]
                if line.split("=", 1)[0].strip().strip('"') not in table
            ]
            if len(kept) == 1:
                continue
        if out:
            out.append("")
        out += [_commented(line) for line in kept]
    return out


def _upgrade_block(lines: list[str]) -> list[str]:
    """Lines with schema = 2 and the new commented tables in the rules block."""
    rules_heading = next(
        (
            i
            for i, line in enumerate(lines)
            if line.startswith("#") and "checked rules" in line.lower()
        ),
        None,
    )
    search_from = rules_heading if rules_heading is not None else 0
    start = next(
        (i for i in range(search_from, len(lines)) if _FENCE.match(lines[i])), None
    )
    if start is None and rules_heading is not None:
        start = next((i for i, line in enumerate(lines) if _FENCE.match(line)), None)
    if start is None:
        block = ["schema = 2", "", _NEW_RULES_NOTE, *_new_rule_lines({})]
        heading = bible_schema.SECTIONS[17]
        addition = [
            f"## {heading.heading}",
            (
                "Measured on every changed asset before you review it. Remove a # to "
                "turn a rule on."
            ),
            "",
            "```toml",
            *block,
            "```",
            "",
        ]
        never = _heading_index(lines).get("bans")
        at = never if never is not None else len(lines)
        return lines[:at] + addition + lines[at:]
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith("```")), None
    )
    if end is None:
        return lines
    body = "\n".join(lines[start + 1 : end])
    try:
        existing = tomllib.loads(body)
    except tomllib.TOMLDecodeError:
        return lines  # an invalid block is the user's to fix; lint reports it
    head = [] if "schema" in existing else ["schema = 2", ""]
    tail = _new_rule_lines(existing)
    if tail:
        tail = ["", _NEW_RULES_NOTE, *tail]
    return lines[: start + 1] + head + lines[start + 1 : end] + tail + lines[end:]


def upgrade_text(v1: str) -> str:
    """The second-format text of a first-format bible; unchanged if already new."""
    if bible_schema.version(v1) >= bible_schema.VERSION:
        return v1
    trailing = v1.endswith("\n")
    lines = v1.splitlines()
    title = next((i for i, line in enumerate(lines) if line.startswith("# ")), None)
    at = 0 if title is None else title + 1
    notes = [] if "How to write in this file" in v1 else BIBLE_GUIDE.splitlines()
    lines = lines[:at] + [bible_schema.VERSION_MARKER, *notes] + lines[at:]

    headings = _heading_index(lines)
    references = headings.get("references")
    if references is not None:
        end = _section_end(lines, references)
        section_text = "\n".join(lines[references:end]).lower()
        if "| # | reference |" not in section_text:
            insert = end
            while insert > references + 1 and not lines[insert - 1].strip():
                insert -= 1
            lines = lines[:insert] + ["", *_BOARD] + lines[insert:]

    headings = _heading_index(lines)
    missing = [
        s
        for s in bible_schema.SECTIONS
        if s.required is False and s.token not in headings
    ]
    if missing:
        added: list[str] = []
        for section in missing:
            added += _section_block(section)
        anchor = headings.get("checked_rules", headings.get("bans"))
        if anchor is None:
            if lines and lines[-1].strip():
                added = ["", *added]
            lines = lines + added
        else:
            lines = lines[:anchor] + added + lines[anchor:]

    lines = _upgrade_block(lines)
    text = "\n".join(lines)
    return text + "\n" if trailing else text
