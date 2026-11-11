"""The design check: what in the game breaks it, or goes against its own brief.

`joinery design --project P` reads what the project really has: the game brief
(`design/brief.md`), design documents the user added (`design/docs/`), the input
map, scripts and scenes, blockout layouts (`design/blockouts/`) and the newest
playtest report. Each finding names the place (file, line, node), what the player
would experience and why it is a problem. It never changes the game.

It flags only two kinds of thing: what conflicts with the project's own brief or
design document (a core verb with no input binding, a win the brief promises that
nothing in the code reaches, a mechanic the document describes that the game never
implements), and what breaks the game for a player (an objective that can't be
reached, a room that traps the player, a pickup that gives no feedback, controls
never shown, the same player tuned differently from level to level). Taste is never
flagged: a finding that can't cite the brief, the document or the breakage is not
one.

The deterministic checks are in `joinery.design_checks`. An optional agent pass
(`bridge.designer`, the read-only ``designer`` role) adds judgement calls through
``report_design``; :func:`agent_findings` keeps only those whose evidence points at
a real place in the project.
"""

from __future__ import annotations

import hashlib
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from joinery import brief as brief_mod

Severity = Literal["high", "medium", "low"]
SEVERITY_ORDER: dict[str, int] = {"high": 0, "medium": 1, "low": 2}
#: Where a finding came from: Joinery's own checks, the designer agent, a playtest.
Source = Literal["check", "agent", "playtest"]
#: What an agent finding rests on: the brief, a design document, or a broken game.
Basis = Literal["brief", "gdd", "broken"]

#: Never read: Godot's cache, Joinery's state, plugins, recipes, tests, agent config.
SKIP_DIRS = frozenset(
    {
        ".godot",
        ".joinery",
        ".git",
        "addons",
        "recipes",
        "node_modules",
        "tests",
        "test",
        ".claude",
        ".codex",
        ".gemini",
        ".opencode",
        "build",
        "export",
    }
)
SCRIPT_SUFFIXES = (".gd", ".cs")
SCENE_SUFFIXES = (".tscn", ".tres")
DOC_SUFFIXES = (".md", ".markdown", ".txt")
DOCS_DIR = "design/docs"
#: Larger files are skipped (generated data, not design).
MAX_BYTES = 400_000
MAX_FILES = 3000
#: Findings an agent may report in one pass.
AGENT_LIMIT = 8


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Evidence(_Model):
    """One place in the project a finding rests on."""

    #: Project-relative path ("game/player/player.gd", "design/brief.md").
    path: str
    #: 1-based line; 0 for the whole file.
    line: int = 0
    #: A node path in a scene, an input action, a heading, a playtest seed.
    node: str = ""
    #: The words at that place, short.
    quote: str = ""

    def where(self) -> str:
        place = self.path + (f":{self.line}" if self.line else "")
        return place + (f" ({self.node})" if self.node else "")


class Finding(_Model):
    id: str
    #: Which check found it ("unbound-verb", "no-win", …; "agent" for the agent pass).
    check: str
    severity: Severity
    #: The one line the sidebar shows.
    title: str
    #: What the player would experience.
    player: str
    #: Why it is a problem: the brief line it breaks, or what breaks.
    why: str
    evidence: list[Evidence]
    source: Source = "check"
    #: The request "Ask agent to fix" puts in the composer: a normal reviewed request.
    request: str = ""


class Report(_Model):
    project: str
    summary: str
    findings: list[Finding] = []
    #: What was read ("design/brief.md", "14 scripts", …), so a clean report says why.
    read: list[str] = []
    #: What couldn't be read or checked, and why.
    skipped: list[str] = []
    #: `[agents] design_check_every` in joinery.toml (0: off).
    check_every: int = 0


# --- the project as text -----------------------------------------------------------


@dataclass
class Text:
    """A project file as text."""

    path: str
    text: str
    lines: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.lines:
            self.lines = self.text.replace("\r\n", "\n").split("\n")

    def find(self, pattern: str | re.Pattern[str], start: int = 0) -> int:
        """The 1-based line of the first match at or after ``start`` (0-based), or 0."""
        regex = re.compile(pattern) if isinstance(pattern, str) else pattern
        for index in range(max(0, start), len(self.lines)):
            if regex.search(self.lines[index]):
                return index + 1
        return 0


@dataclass
class Project:
    root: Path
    scripts: dict[str, Text] = field(default_factory=dict)
    scenes: dict[str, Text] = field(default_factory=dict)
    godot: Text | None = None
    brief: Text | None = None
    docs: list[Text] = field(default_factory=list)
    actions: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)

    def code(self) -> dict[str, Text]:
        """Scripts and scenes: everything the game runs."""
        return {**self.scripts, **self.scenes}


def _read(path: Path, rel: str, skipped: list[str]) -> Text | None:
    try:
        if path.stat().st_size > MAX_BYTES:
            skipped.append(f"{rel}: too large to read")
            return None
        return Text(rel, path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        skipped.append(f"{rel}: couldn't be read")
        return None


def load(root: Path) -> Project:
    """Read the project's scripts, scenes, input map, brief and design documents."""
    project = Project(root.resolve())
    count = 0
    for path in sorted(project.root.rglob("*")):
        rel_parts = path.relative_to(project.root).parts
        if any(part in SKIP_DIRS for part in rel_parts[:-1]) or not path.is_file():
            continue
        rel = PurePosixPath(*rel_parts).as_posix()
        if path.suffix in SCRIPT_SUFFIXES or path.suffix in SCENE_SUFFIXES:
            count += 1
            if count > MAX_FILES:
                project.skipped.append(f"more than {MAX_FILES} scripts and scenes")
                break
            text = _read(path, rel, project.skipped)
            if text is not None:
                bucket = (
                    project.scripts
                    if path.suffix in SCRIPT_SUFFIXES
                    else project.scenes
                )
                bucket[rel] = text
    godot = project.root / "project.godot"
    if godot.is_file():
        project.godot = _read(godot, "project.godot", project.skipped)
        if project.godot is not None:
            project.actions = brief_mod.parse_input_map(project.godot.text)
    brief_path = project.root / brief_mod.BRIEF_PATH
    if brief_path.is_file():
        project.brief = _read(
            brief_path, brief_mod.BRIEF_PATH.as_posix(), project.skipped
        )
    docs = project.root / DOCS_DIR
    if docs.is_dir():
        for path in sorted(docs.rglob("*")):
            if path.is_file() and path.suffix.lower() in DOC_SUFFIXES:
                rel = path.relative_to(project.root).as_posix()
                text = _read(path, rel, project.skipped)
                if text is not None:
                    project.docs.append(text)
    return project


# --- reading helpers the checks share ----------------------------------------------

_STRING = re.compile(r'"(?:[^"\\\n]|\\.)*"' + r"|'(?:[^'\\\n]|\\.)*'")


def code_part(line: str) -> str:
    """A GDScript or C# line without its comment (a ``#`` or ``//`` outside strings)."""
    out: list[str] = []
    quote = ""
    index = 0
    while index < len(line):
        char = line[index]
        if quote:
            out.append(char)
            if char == "\\" and index + 1 < len(line):
                out.append(line[index + 1])
                index += 2
                continue
            if char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
            out.append(char)
        elif char == "#" or line.startswith("//", index):
            break
        else:
            out.append(char)
        index += 1
    return "".join(out)


def strings(line: str) -> list[str]:
    """The string literals on a line, without their quotes."""
    return [match.group(0)[1:-1] for match in _STRING.finditer(code_part(line))]


@dataclass
class Section:
    """One ``##`` section of a Markdown file, with where it starts."""

    heading: str
    #: 1-based line of the heading.
    line: int
    #: (1-based line, text) of the body.
    body: list[tuple[int, str]]

    def text(self) -> str:
        return "\n".join(text for _, text in self.body)


_HEADING = re.compile(r"^(#{1,6})\s+(?:\d+\.\s*)?(.+?)\s*#*\s*$")


def sections(source: Text, level: int | None = None) -> list[Section]:
    """Headed sections of a Markdown file (``level`` 2 for the brief's ``##``)."""
    found: list[Section] = []
    fenced = False
    current: Section | None = None
    for index, line in enumerate(source.lines, start=1):
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        match = None if fenced else _HEADING.match(line)
        if match and (level is None or len(match.group(1)) == level):
            current = Section(match.group(2), index, [])
            found.append(current)
        elif match and level is not None and len(match.group(1)) < level:
            current = None
        elif current is not None:
            current.body.append((index, line))
    return found


def brief_section(project: Project, name: str) -> Section | None:
    """The brief's section called ``name`` (numbers ignored). Parts still marked
    "_Not decided yet._" are the caller's to skip: nothing undecided is a promise."""
    if project.brief is None:
        return None
    for section in sections(project.brief, 2):
        if section.heading.lower().startswith(name.lower()):
            return section
    return None


def finding_id(check: str, evidence: list[Evidence], extra: str = "") -> str:
    key = "|".join([check, extra] + [e.where() for e in evidence])
    return check + "-" + hashlib.sha1(key.encode()).hexdigest()[:8]


def make(
    check: str,
    severity: Severity,
    title: str,
    player: str,
    why: str,
    evidence: list[Evidence],
    fix: str,
    *,
    source: Source = "check",
    key: str = "",
) -> Finding:
    """A finding, with the request "Ask agent to fix" sends."""
    return Finding(
        id=finding_id(check, evidence, key or title),
        check=check,
        severity=severity,
        title=title,
        player=player,
        why=why,
        evidence=evidence,
        source=source,
        request=fix_request(title, player, why, evidence, fix),
    )


def fix_request(
    title: str, player: str, why: str, evidence: list[Evidence], fix: str
) -> str:
    """A complete request for Journeyman: what the design check found and where."""
    places = ", ".join(e.where() for e in evidence[:4])
    return (
        f"The design check found: {title}. {player} {why} "
        f"(See {places}.) {fix} Change only what this needs."
    ).strip()


# --- the check ---------------------------------------------------------------------


def check(root: Path) -> Report:
    """Every deterministic design check over the project. Reads only."""
    from joinery import design_checks

    project = load(root)
    findings: list[Finding] = []
    skipped = list(project.skipped)
    for run in design_checks.CHECKS:
        try:
            findings += run(project)
        except (OSError, ValueError) as exc:  # one check failing never hides the rest
            skipped.append(f"{run.__name__.strip('_')}: {exc}")
    findings = _unique(findings)
    findings.sort(key=lambda f: (SEVERITY_ORDER[f.severity], f.check, f.title))
    read: list[str] = []
    if project.brief is not None:
        read.append(project.brief.path)
    read += [doc.path for doc in project.docs]
    if project.actions:
        read.append(f"{len(project.actions)} input actions")
    read.append(f"{len(project.scripts)} scripts")
    read.append(f"{len(project.scenes)} scenes")
    return Report(
        project=str(project.root),
        summary=summary(findings),
        findings=findings,
        read=read,
        skipped=skipped,
        check_every=check_every(project.root),
    )


def _unique(findings: list[Finding]) -> list[Finding]:
    seen: set[str] = set()
    kept: list[Finding] = []
    for item in findings:
        if item.id not in seen:
            seen.add(item.id)
            kept.append(item)
    return kept


def summary(findings: list[Finding]) -> str:
    if not findings:
        return "No design problems found"
    noun = "design problem" if len(findings) == 1 else "design problems"
    return f"{len(findings)} {noun}"


# --- the agent pass's findings -----------------------------------------------------


def _project_file(root: Path, raw: str) -> tuple[Path, str] | None:
    clean = raw.strip().replace("\\", "/").removeprefix("res://").lstrip("/")
    if not clean or ".." in PurePosixPath(clean).parts:
        return None
    path = (root / clean).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        return None
    return path, PurePosixPath(clean).as_posix()


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _evidence(root: Path, raw: Any) -> tuple[Evidence | None, str]:
    """One cited place, checked against the file; (None, why) when it doesn't hold."""
    if not isinstance(raw, dict):
        return None, "evidence is not an object"
    found = _project_file(root, str(raw.get("path", "")))
    if found is None:
        return None, f"{raw.get('path', '')!s} is not a file in the project"
    path, rel = found
    try:
        if path.stat().st_size > MAX_BYTES:
            return None, f"{rel} is too large to check"
        lines = path.read_text(encoding="utf-8").replace("\r\n", "\n").split("\n")
    except (OSError, UnicodeDecodeError):
        return None, f"{rel} can't be read"
    line = raw.get("line", 0) or 0
    if not isinstance(line, int) or isinstance(line, bool) or line < 0:
        return None, f"{rel}: line is not a number"
    if line > len(lines):
        return None, f"{rel} has no line {line}"
    node = str(raw.get("node", "") or "").strip()[:200]
    quote = str(raw.get("quote", "") or "").strip()[:300]
    if quote:
        window = lines[max(0, line - 3) : line + 2] if line else lines
        if _squash(quote) not in _squash("\n".join(window)):
            return None, f"{rel}{':' + str(line) if line else ''} doesn't say {quote!r}"
    if node:
        leaf = node.rstrip("/").split("/")[-1]
        text = "\n".join(lines)
        needle = f'name="{leaf}"' if rel.endswith(".tscn") else leaf
        if needle not in text:
            return None, f"{rel} has no {node!r}"
    if not (line or node or quote):
        return None, f"{rel}: name the line, node or words"
    return Evidence(path=rel, line=line, node=node, quote=quote), ""


def agent_findings(
    root: Path, params: dict[str, Any]
) -> tuple[list[Finding], list[str]]:
    """The designer agent's findings that cite real evidence, and why the rest were dropped.

    Each finding needs a severity, a title, what the player experiences, why, its
    basis ("brief", "gdd" or "broken") and at least one place in the project that
    holds: the file exists, the line is in it, the quoted words are there, the node
    is in the scene. A finding resting on the brief or a design document must cite
    one (a file under design/). Anything else is dropped, never shown.
    """
    root = root.resolve()
    raw = params.get("findings")
    if not isinstance(raw, list):
        raise TypeError(
            "findings is a list of {severity, title, player, why, basis, evidence}."
        )
    kept: list[Finding] = []
    dropped: list[str] = []
    for index, item in enumerate(raw[:AGENT_LIMIT]):
        label = f"finding {index + 1}"
        if not isinstance(item, dict):
            dropped.append(f"{label}: not an object")
            continue
        title = " ".join(str(item.get("title", "")).split())[:140]
        player = " ".join(str(item.get("player", "")).split())[:400]
        why = " ".join(str(item.get("why", "")).split())[:600]
        severity = str(item.get("severity", ""))
        basis = str(item.get("basis", ""))
        if not title or not player or not why:
            dropped.append(
                f"{label}: needs a title, what the player experiences and why"
            )
            continue
        if severity not in SEVERITY_ORDER:
            dropped.append(f"{label}: severity is high, medium or low")
            continue
        if basis not in ("brief", "gdd", "broken"):
            dropped.append(
                f"{label}: basis is brief, gdd or broken (taste is never a finding)"
            )
            continue
        cited = item.get("evidence")
        if not isinstance(cited, list) or not cited:
            dropped.append(f"{label} ({title}): no evidence")
            continue
        places: list[Evidence] = []
        problem = ""
        for entry in cited[:6]:
            place, problem = _evidence(root, entry)
            if place is None:
                break
            places.append(place)
        if problem:
            dropped.append(f"{label} ({title}): {problem}")
            continue
        if basis in ("brief", "gdd") and not any(
            p.path.startswith("design/") for p in places
        ):
            dropped.append(
                f"{label} ({title}): rests on the {basis} but cites no line of it"
            )
            continue
        fix = " ".join(str(item.get("fix", "")).split())[:600]
        kept.append(
            make(
                "agent",
                severity,  # type: ignore[arg-type]
                title,
                player,
                why,
                places,
                fix,
                source="agent",
            )
        )
    for item in raw[AGENT_LIMIT:]:
        dropped.append(f"more than {AGENT_LIMIT} findings: {str(item)[:60]}")
    return kept, dropped


# --- settings ----------------------------------------------------------------------

#: Choices the settings row offers (0: off).
EVERY_CHOICES = (0, 1, 3, 5, 10)


def check_every(project: Path) -> int:
    """``[agents] design_check_every`` in joinery.toml: check after every N accepted
    changes (0: off), beside the other checks' ``access_check_every`` and
    ``perf_check_every``."""
    try:
        raw = tomllib.loads((project / "joinery.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    table = raw.get("agents", {})
    value = table.get(EVERY_KEY, 0) if isinstance(table, dict) else 0
    if not isinstance(value, int) or isinstance(value, bool):
        return 0
    return max(0, min(value, 100))


#: The key in joinery.toml's [agents] table.
EVERY_KEY = "design_check_every"
_TABLE = re.compile(r"(?ms)^\[agents\][^\n]*\n(.*?)(?=^\[|\Z)")


def set_check_every(project: Path, every: int) -> int:
    """Save ``[agents] design_check_every``, keeping every other line of joinery.toml."""
    if not 0 <= every <= 100:
        raise ValueError("design_check_every is from 0 (off) to 100.")
    path = project / "joinery.toml"
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    line = f"{EVERY_KEY} = {every}"
    match = _TABLE.search(text)
    if match:
        body = match.group(1)
        key = re.compile(rf"(?m)^{EVERY_KEY}\s*=.*$")
        if key.search(body):
            body = key.sub(line, body, count=1)
        else:
            body = body.rstrip("\n") + ("\n" if body.strip() else "") + line + "\n"
        if not body.endswith("\n"):
            body += "\n"
        updated = text[: match.start(1)] + body + text[match.end(1) :]
    else:
        lead = text.rstrip("\n") + "\n\n" if text.strip() else ""
        updated = lead + "[agents]\n" + line + "\n"
    try:
        tomllib.loads(updated)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"joinery.toml can't be read: {exc}") from None
    path.write_text(updated, encoding="utf-8")
    saved = check_every(project)
    if saved != every:
        raise ValueError("joinery.toml keeps [agents] in a form Joinery can't edit.")
    return saved
