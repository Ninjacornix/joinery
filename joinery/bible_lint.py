"""Static checks of the art bible itself, and what Journeyman should ask next.

The checked rules measure assets; this module reads the bible: whether its
rules block parses, whether its own palette meets its contrast and colour-blind
rules, whether each reference says what to take, whether reference files exist
and are locked, which sections are still unset, and which filler words the
user's sections use. It never blocks asset work by itself. The registry tool
and the approval review show its report, and Journeyman turns `suggestions` into one-line offers, never into edits unasked.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import BaseModel, ConfigDict

from joinery import bible_schema, bible_writing
from joinery.bible_rules import BibleRules, palette_problems, parse_rules, rules_text
from joinery.catalog import ROOT
from joinery.lockfile import LOCK_NAME

BIBLE = Path("art") / "bible.md"
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_BOARD_HEADER = ["#", "reference", "kind", "take this", "not that", "file"]
_ID = re.compile(r"^R\d+$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ReferenceRow(_Strict):
    id: str
    reference: str
    kind: str
    take: str
    avoid: str
    file: str = ""
    file_locked: bool = False
    file_exists: bool = False


class Gap(_Strict):
    section: str
    heading: str
    required: bool
    relevant_for: list[str]
    #: When Journeyman should raise it.
    moment: str = ""
    #: The heading is absent altogether (an older or hand-written bible).
    missing: bool = False


class BibleReport(_Strict):
    #: 2 for the current format, 1 for the first, 0 when there is no bible.
    version: int
    problems: list[str] = []
    gaps: list[Gap] = []
    references: list[ReferenceRow] = []
    rules: BibleRules | None = None
    #: (filler word, section heading) pairs; a suggestion, never a problem.
    filler: list[tuple[str, str]] = []


#: Sections where naming a filler word is the point: the game's banned words,
#: the hard bans, the measured rules.
_FILLER_SKIP = ("writing tone", "never", "checked rules")
_FILLER_INSTEAD = "a hex, a size, a shape or a named reference"


def _cells(line: str) -> list[str]:
    body = line.strip()
    body = body.removeprefix("|")
    body = body.removesuffix("|")
    cells = re.split(r"(?<!\\)\|", body)
    return [c.strip().replace("\\|", "|").strip("`").strip() for c in cells]


def _locked_paths(project: Path) -> set[str]:
    lock = project / LOCK_NAME
    try:
        raw = tomllib.loads(lock.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return set()
    return {
        str(entry.get("path", ""))
        for entry in raw.get("asset", [])
        if isinstance(entry, dict)
    }


def references(text: str, project: Path | None = None) -> list[ReferenceRow]:
    """Rows of the reference board table (section 3a).

    With a project, each row's file is also looked up on disk and in
    `joinery.lock`; without one both flags stay False.
    """
    lines = _COMMENT.sub("", text).splitlines()
    locked = _locked_paths(project) if project is not None else set()
    rows: list[ReferenceRow] = []
    index = 0
    while index < len(lines):
        header = [c.lower() for c in _cells(lines[index])]
        if lines[index].strip().startswith("|") and header[:5] == _BOARD_HEADER[:5]:
            index += 1
            while index < len(lines) and lines[index].strip().startswith("|"):
                cells = _cells(lines[index])
                index += 1
                if all(re.fullmatch(r":?-{3,}:?", c) for c in cells if c):
                    continue
                cells += [""] * (6 - len(cells))
                if not any(cells):
                    continue
                file = cells[5]
                exists = (
                    bool(file) and project is not None and (project / file).is_file()
                )
                rows.append(
                    ReferenceRow(
                        id=cells[0],
                        reference=cells[1],
                        kind=cells[2].lower(),
                        take=cells[3],
                        avoid=cells[4],
                        file=file,
                        file_locked=bool(file) and file in locked,
                        file_exists=exists,
                    )
                )
            continue
        index += 1
    return rows


def reference_problems(rows: list[ReferenceRow], project: Path | None) -> list[str]:
    problems: list[str] = []
    seen: set[str] = set()
    for row in rows:
        label = row.id or row.reference or "a reference row"
        if not _ID.match(row.id):
            problems.append(
                f"Reference {row.reference!r} needs an id like R1, so Journeyman can cite it"
            )
        elif row.id in seen:
            problems.append(f"{row.id} is used by more than one reference")
        seen.add(row.id)
        if not row.take:
            problems.append(f"{label}: say what to take from it (Take this)")
        if row.kind and row.kind not in bible_schema.REFERENCE_KINDS:
            problems.append(
                f"{label}: kind {row.kind!r} is not one of "
                + ", ".join(bible_schema.REFERENCE_KINDS)
            )
        if not row.file:
            continue
        path = PurePosixPath(row.file)
        if path.is_absolute() or ".." in path.parts or "://" in row.file:
            problems.append(
                f"{label}: {row.file} must be a project path under "
                f"{bible_schema.REFERENCE_DIR}/"
            )
            continue
        if not path.is_relative_to(bible_schema.REFERENCE_DIR):
            problems.append(
                f"{label}: reference files live under {bible_schema.REFERENCE_DIR}/, "
                f"not {row.file}"
            )
        if project is None:
            continue
        if not row.file_exists:
            problems.append(f"{label}: {row.file} is missing")
        elif not row.file_locked:
            problems.append(
                f"{label}: {row.file} has no {LOCK_NAME} entry (your own import, "
                "or the Krita recipe that made it)"
            )
    return problems


def _split_sections(text: str) -> tuple[str, list[tuple[str, str]]]:
    """(title line, [(heading, body)]) for the level-2 sections."""
    title = ""
    sections: list[tuple[str, list[str]]] = []
    for line in text.splitlines():
        if line.startswith("# ") and not title and not sections:
            title = line
        elif line.startswith("## "):
            sections.append((line, []))
        elif sections:
            sections[-1][1].append(line)
    return title, [(h, "\n".join(body)) for h, body in sections]


def _gaps(text: str) -> list[Gap]:
    title, sections = _split_sections(text)
    found: dict[str, str] = {}
    for heading, body in sections:
        section = bible_schema.find_section(heading)
        if section is not None and section.token and section.token not in found:
            found[section.token] = body
    gaps: list[Gap] = []
    markers = (bible_schema.REQUIRED_MARKER, bible_schema.OPEN_MARKER)
    if bible_schema.REQUIRED_MARKER in title or "{{" in title:
        gaps.append(
            Gap(
                section=bible_schema.TITLE_TOKEN,
                heading="Title",
                required=True,
                relevant_for=["any"],
                moment="Before approving the bible",
            )
        )
    for section in bible_schema.SECTIONS:
        if section.required is None:
            continue
        content = found.get(section.token)
        if (
            content is not None
            and not any(m in content for m in markers)
            and "{{" not in content
        ):
            continue
        gaps.append(
            Gap(
                section=section.token,
                heading=section.heading,
                required=bool(section.required),
                relevant_for=list(section.relevant_for),
                moment=section.moment,
                missing=content is None,
            )
        )
    return gaps


def lint_text(text: str, project: Path | None = None) -> BibleReport:
    """The report for a bible's text; `project` adds file and lock checks."""
    problems: list[str] = []
    rules: BibleRules | None = None
    block = rules_text(text)
    if block is not None:
        try:
            rules = parse_rules(block)
        except ValueError as error:
            problems.append(f"Checked rules are invalid: {error}")
    if rules is not None:
        problems += palette_problems(rules)
    rows = references(text, project)
    problems += reference_problems(rows, project)
    return BibleReport(
        version=bible_schema.version(text),
        problems=problems,
        gaps=_gaps(text),
        references=rows,
        rules=rules,
        filler=bible_writing.filler(text, _FILLER_SKIP),
    )


def lint(project: Path) -> BibleReport:
    path = project / BIBLE
    if not path.is_file():
        return BibleReport(version=0, problems=["The project has no art bible yet."])
    return lint_text(path.read_text(errors="replace"), project)


def project_dimension(project: Path) -> str | None:
    """`2d`, `3d` or `ui` from the project's blueprint, when it has one."""
    try:
        config = tomllib.loads((project / "joinery.toml").read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return None
    game = config.get("game")
    blueprint = game.get("blueprint") if isinstance(game, dict) else None
    if not isinstance(blueprint, str) or not re.fullmatch(r"[a-z0-9-]+", blueprint):
        return None
    try:
        raw = tomllib.loads(
            (ROOT / "blueprints" / blueprint / "blueprint.toml").read_text()
        )
    except (OSError, tomllib.TOMLDecodeError):
        return None
    dimension = raw.get("dimension")
    return dimension if dimension in {"2d", "3d", "ui"} else None


def _phrase(heading: str) -> str:
    """'Shape language' -> 'shape language'; 'VFX' and '3D rules' stay."""
    if len(heading) > 1 and heading[0].isupper() and heading[1].islower():
        return heading[0].lower() + heading[1:]
    return heading


def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _sentence(moment: str, gaps: list[Gap], state: str) -> str:
    names = _join([_phrase(g.heading) for g in gaps])
    one = len(gaps) == 1
    noun = "section is" if one else "sections are"
    return f"{moment}: the {names} {noun} {state}. Ask about {'it' if one else 'them'}."


def _relevance(gap: Gap, dimension: str | None) -> int | None:
    """Sort key: 0 for the project's own kind, 1 for any game, None to drop."""
    if dimension is None:
        return 0
    if dimension in gap.relevant_for:
        return 0
    if "any" in gap.relevant_for:
        return 1
    return None


def suggestions_for(report: BibleReport, dimension: str | None) -> list[str]:
    if report.version == 0:
        return [
            (
                "There is no art bible yet. Offer to draft one with the user: vision, "
                "look, palette, sound and what to never do."
            )
        ]
    out: list[str] = []
    required = [g for g in report.gaps if g.required]
    if required:
        out.append(_sentence("Before approving the bible", required, "not set yet"))
    out += [f"The bible needs a fix: {problem}." for problem in report.problems[:3]]
    if len(report.problems) > 3:
        out.append(f"The bible has {len(report.problems) - 3} more problems to fix.")
    if report.version < bible_schema.VERSION:
        out.append(
            "This bible uses the first format. Offer the upgrade: it keeps every "
            "line, adds the new sections as open, and needs approving again."
        )
    elif not report.references:
        out.append(
            "The reference board is empty. Ask for two or three references, what to "
            "take from each and what to leave."
        )
    open_gaps = [
        (rank, index, gap)
        for index, gap in enumerate(g for g in report.gaps if not g.required)
        if (rank := _relevance(gap, dimension)) is not None and not gap.missing
    ]
    open_gaps.sort(key=lambda item: (item[0], item[1]))
    grouped: dict[str, list[Gap]] = {}
    for _rank, _index, gap in open_gaps:
        grouped.setdefault(gap.moment, []).append(gap)
    out += [_sentence(moment, gaps, "open") for moment, gaps in grouped.items()]
    wording = bible_writing.suggestion("bible", report.filler, _FILLER_INSTEAD)
    if wording is not None:
        out.append(wording)
    return out


def suggestions(project: Path, dimension: str | None = None) -> list[str]:
    """Short, ordered prompts for Journeyman: required first, then by relevance."""
    return suggestions_for(lint(project), dimension or project_dimension(project))


def summary(project: Path) -> dict[str, Any]:
    """JSON-safe report for the registry tool."""
    report = lint(project)
    return {
        "version": report.version,
        "rules": None
        if report.rules is None
        else report.rules.model_dump(mode="json", by_alias=True),
        "problems": list(report.problems),
        "gaps": [gap.model_dump(mode="json") for gap in report.gaps],
        "references": [row.model_dump(mode="json") for row in report.references],
        "suggestions": suggestions_for(report, project_dimension(project)),
    }
