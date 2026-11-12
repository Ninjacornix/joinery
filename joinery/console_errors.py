"""Console errors, grouped: what the sidebar's Errors list shows and what gets fixed (ADR 0075).

Errors (and, when the project asks, warnings) come from four places: the editor's
Output panel (a script that fails to parse on save, the editor's own errors), the
running game's log, playtest runs and failed GUT tests. Each is read into an
:class:`Entry` (its kind, message and place in the code), and entries with the same
message and place are one :class:`Group` with a count. Joinery's own lines and
known engine noise are never listed; ``[agents] ignore_errors`` in joinery.toml
adds the project's own.

``[agents] fix_errors`` says what happens to a new group: ``"ask"`` (the default)
offers to fix it, ``"auto"`` starts the debugger and a worker as a background task
at once, and ``"off"`` leaves the console alone. A fix is always a normal reviewed
change the user accepts or discards. The limits that keep the automatic mode from
running away live with the groups: how many fix at once, a pause between starts,
two failed tries per group at most, and no fix for an error that a change still
waiting for review brought.

Everything here is plain data and plain files: ``.joinery/errors.json`` keeps the
groups and the counts (seen, fixed, accepted) between sessions.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import re
import sys
import time
import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from joinery.roles import set_agents_values
from joinery.transactions import atomic_json

MODES = ("ask", "auto", "off")
#: Sources an entry can come from, as the Errors list names them.
SOURCES = {
    "output": "Output",
    "game": "game log",
    "playtest": "playtest",
    "test": "tests",
}
#: Fixes at once, and seconds between two automatic starts, unless joinery.toml says.
AT_ONCE, AT_ONCE_MAX = 1, 3
COOLDOWN = 30.0
#: Failed fixes of one group before Joinery stops trying it on its own.
MAX_FAILURES = 2
#: Groups kept on disk; fixed and ignored ones go first.
KEEP = 200
#: Lines kept around an entry for the debugger.
CONTEXT_BEFORE, CONTEXT_AFTER = 4, 8
MAX_MESSAGE = 300
MAX_PATTERNS = 40
STATE = ".joinery/errors.json"

#: Lines that are never the project's problem: Joinery's own, and engine noise every
#: run prints. The project adds its own with ``[agents] ignore_errors``.
OWN = ("addons/joinery", "[Joinery]")
NOISE = (
    "ObjectDB instances leaked at exit",
    "resources still in use at exit",
    "Pages in use exist at exit",
    "RID allocations of type",
    "Orphan StringName",
    "was leaked at exit",
)

#: Group states. ``open`` waits for a fix; ``queued`` waits for a free agent;
#: ``fixing`` and ``in_review`` follow its task; ``fixed`` was accepted; ``ignored``
#: the user set aside; ``stopped`` failed twice; ``from_change`` came with a change
#: still waiting for review, so it is that change's, not a new one to fix.
OPEN, QUEUED, FIXING, IN_REVIEW = "open", "queued", "fixing", "in_review"
FIXED, IGNORED, STOPPED, FROM_CHANGE = "fixed", "ignored", "stopped", "from_change"
#: States the Errors list shows; the rest are history.
LISTED = (FIXING, QUEUED, IN_REVIEW, OPEN, STOPPED, FROM_CHANGE, IGNORED)

# "SCRIPT ERROR: …", "ERROR: …", "WARNING: …" and the user's push_error/push_warning.
_CONSOLE = re.compile(
    r"^\s*(?P<label>USER SCRIPT ERROR|SCRIPT ERROR|USER ERROR|ERROR|USER WARNING|WARNING"
    r"|Parse Error):\s*(?P<message>.+)$"
)
# "res://player.gd:12 - Parse Error: Identifier "x" not declared…" (Output on save).
_PARSE = re.compile(
    r"^\s*(?P<where>res://[^\s:]+:\d+)\s*-\s*Parse Error:\s*(?P<message>.+)$"
)
# The editor's view of the running game: "E 0:00:01:0234   player.gd:42 @ _process(): …".
_REMOTE = re.compile(r"^\s*(?P<level>[EW])\s+\d+:\d+:\d+(?::\d+)?\s+(?P<rest>.+)$")
_REMOTE_PLACE = re.compile(
    r"^(?:(?P<where>[\w./ -]+?\.\w+:\d+)\s+@\s+)?(?P<function>[\w.<>]+)\(\):\s*(?P<message>.+)$"
)
_AT = re.compile(r"^\s+at:\s*(?:[^(]*?)\s*\((?P<where>[^)]*)\)\s*$")
_SOURCE_LINE = re.compile(r"<GDScript Source>\s*(?P<where>[\w./ -]+?\.\w+:\d+)")
_RES_LINE = re.compile(r"(res://[^\s:()]+):(\d+)")
# GUT: the script, the test, "[Failed]: …" and "at line N".
_GUT_SCRIPT = re.compile(r"^\s*(?P<script>res://\S+\.gd)\s*$")
_GUT_TEST = re.compile(r"^\s*[*-]\s+(?P<test>test_\w+)")
_GUT_FAILED = re.compile(r"^\s*\[Failed\]:\s*(?P<message>.+)$")
_GUT_LINE = re.compile(r"^\s*at line (?P<line>\d+)")
# Parts of a message that change from run to run.
_VOLATILE = (
    (re.compile(r"<(\w+)#-?\d+>"), r"<\1>"),
    (re.compile(r"\b0x[0-9a-fA-F]+\b"), "0x…"),
    (re.compile(r"#-?\d{4,}"), "#…"),
    (re.compile(r"\b\d{7,}\b"), "…"),
)


# --- settings --------------------------------------------------------------------------


@dataclass(frozen=True)
class Settings:
    """``[agents] fix_errors``, ``fix_warnings``, ``ignore_errors``,
    ``fix_errors_at_once`` and ``fix_errors_cooldown`` in joinery.toml."""

    mode: str = "ask"
    warnings: bool = False
    ignore: tuple[str, ...] = ()
    at_once: int = AT_ONCE
    cooldown: float = COOLDOWN


def settings(project: Path) -> Settings:
    try:
        raw = tomllib.loads((project / "joinery.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Settings()
    table = raw.get("agents", {})
    if not isinstance(table, dict):
        return Settings()
    mode = table.get("fix_errors", "ask")
    warnings = table.get("fix_warnings", False)
    ignore = table.get("ignore_errors", [])
    at_once = table.get("fix_errors_at_once", AT_ONCE)
    cooldown = table.get("fix_errors_cooldown", COOLDOWN)
    return Settings(
        mode=mode if mode in MODES else "ask",
        warnings=warnings if isinstance(warnings, bool) else False,
        ignore=tuple(
            p.strip()[:200]
            for p in (ignore if isinstance(ignore, list) else [])[:MAX_PATTERNS]
            if isinstance(p, str) and p.strip()
        ),
        at_once=max(1, min(at_once, AT_ONCE_MAX))
        if isinstance(at_once, int) and not isinstance(at_once, bool)
        else AT_ONCE,
        cooldown=max(0.0, min(float(cooldown), 3600.0))
        if isinstance(cooldown, (int, float)) and not isinstance(cooldown, bool)
        else COOLDOWN,
    )


def set_mode(project: Path, mode: str) -> None:
    """Save ``[agents] fix_errors``, keeping every other line of joinery.toml."""
    if mode not in MODES:
        raise ValueError('fix_errors is "ask", "auto" or "off".')
    set_agents_values(project, {"fix_errors": f'"{mode}"'})
    if settings(project).mode != mode:
        raise ValueError("joinery.toml keeps [agents] in a form Joinery can't edit.")


def set_ignore(project: Path, patterns: Iterable[str]) -> None:
    """Save ``[agents] ignore_errors``: the project's own lines never to list."""
    clean = [" ".join(str(p).split()) for p in patterns if str(p).strip()]
    if len(clean) > MAX_PATTERNS or any(
        len(p) > 200 or '"' in p or "\\" in p for p in clean
    ):
        raise ValueError(
            f"Up to {MAX_PATTERNS} plain lines of at most 200 characters, no quotes."
        )
    set_agents_values(
        project, {"ignore_errors": "[" + ", ".join(f'"{p}"' for p in clean) + "]"}
    )


# --- reading ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Entry:
    """One error, warning or failed test, as read."""

    #: "error", "warning" or "test".
    kind: str
    message: str
    #: Where in the project: "res://player.gd:42", or "" when the line names none.
    where: str = ""
    source: str = "output"
    #: The lines around it, for the debugger.
    context: str = ""
    #: The line as Godot printed it.
    line: str = ""


def _where(text: str) -> str:
    where = text.strip().replace("\\", "/")
    if where and "://" not in where and re.search(r"\.\w+:\d+$", where):
        where = "res://" + where.lstrip("/")
    return where if where.startswith(("res://", "user://")) else ""


def _context(lines: list[str], index: int) -> str:
    start = max(0, index - CONTEXT_BEFORE)
    return "\n".join(lines[start : index + CONTEXT_AFTER + 1])


def parse(text: str, source: str = "output") -> list[Entry]:
    """The errors, warnings and failed tests in console text, in order."""
    lines = text.splitlines()
    found: list[Entry] = []
    script, test = "", ""
    for index, line in enumerate(lines):
        follow = lines[index + 1 : index + 7]
        gut_script = _GUT_SCRIPT.match(line)
        if gut_script is not None:
            script, test = gut_script.group("script"), ""
            continue
        gut_test = _GUT_TEST.match(line)
        if gut_test is not None:
            test = gut_test.group("test")
            continue
        gut = _GUT_FAILED.match(line)
        if gut is not None:
            at = next(
                (_GUT_LINE.match(f) for f in follow[:2] if _GUT_LINE.match(f)), None
            )
            where = script + (f":{at.group('line')}" if at and script else "")
            message = (test + ": " if test else "") + gut.group("message").strip()
            found.append(
                Entry(
                    "test", message, where, "test", _context(lines, index), line.strip()
                )
            )
            continue
        parse_error = _PARSE.match(line)
        if parse_error is not None:
            found.append(
                Entry(
                    "error",
                    "Parse Error: " + parse_error.group("message").strip(),
                    parse_error.group("where"),
                    source,
                    _context(lines, index),
                    line.strip(),
                )
            )
            continue
        console = _CONSOLE.match(line)
        if console is not None:
            label = console.group("label")
            kind = "warning" if "WARNING" in label else "error"
            message = console.group("message").strip()
            if label == "Parse Error":
                message = "Parse Error: " + message
            where = ""
            for after in follow:
                if _CONSOLE.match(after) or _REMOTE.match(after):
                    break
                at = _AT.match(after)
                if at is not None and not where:
                    where = _where(at.group("where"))
                script_line = _RES_LINE.search(after)
                if script_line is not None and not where.startswith("res://"):
                    where = f"{script_line.group(1)}:{script_line.group(2)}"
            found.append(
                Entry(
                    kind, message, where, source, _context(lines, index), line.strip()
                )
            )
            continue
        remote = _REMOTE.match(line)
        if remote is not None:
            rest = remote.group("rest").strip()
            place = _REMOTE_PLACE.match(rest)
            message = place.group("message").strip() if place else rest
            where = _where(place.group("where") or "") if place else ""
            for after in follow[:3]:
                source_line = _SOURCE_LINE.search(after)
                if source_line is not None and not where:
                    where = _where(source_line.group("where"))
            kind = "warning" if remote.group("level") == "W" else "error"
            found.append(
                Entry(
                    kind, message, where, source, _context(lines, index), line.strip()
                )
            )
    return found


def from_playtest(items: Iterable[Mapping[str, Any]]) -> list[Entry]:
    """A playtest report's console items (script and engine errors, warnings)."""
    kinds = {"script_error": "error", "shader_error": "error", "error": "error"}
    found: list[Entry] = []
    for item in items:
        kind = str(item.get("kind", ""))
        if kind not in kinds and kind != "warning":
            continue
        message = re.sub(r"\s*\(×\d+\)$", "", str(item.get("detail", "")).strip())
        if not message:
            continue
        repro = str(item.get("repro", ""))
        found.extend(
            [
                Entry(
                    kinds.get(kind, "warning"),
                    message,
                    _where(str(item.get("where", ""))),
                    "playtest",
                    "Seen in a playtest. " + repro,
                    message,
                )
            ]
            * max(1, min(int(item.get("count", 1) or 1), 999))
        )
    return found


def normalise(message: str) -> str:
    """The message with what changes between runs taken out, so repeats group."""
    text = " ".join(message.split())[:MAX_MESSAGE]
    for pattern, replacement in _VOLATILE:
        text = pattern.sub(replacement, text)
    return text.rstrip(".")


def group_id(entry: Entry) -> str:
    kind = "warning" if entry.kind == "warning" else entry.kind
    key = f"{kind}|{normalise(entry.message)}|{entry.where}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def ignored(entry: Entry, patterns: Iterable[str] = ()) -> bool:
    """Joinery's own lines, engine noise, and the project's ``ignore_errors``: each
    pattern matches anywhere in the message or place, case aside, ``*`` for any text."""
    text = f"{entry.message} {entry.where} {entry.line}"
    if any(own in text for own in OWN) or any(noise in text for noise in NOISE):
        return True
    lower = text.lower()
    for pattern in patterns:
        wanted = pattern.lower()
        if ("*" in wanted and fnmatch.fnmatch(lower, f"*{wanted}*")) or (
            "*" not in wanted and wanted in lower
        ):
            return True
    return False


def short_where(where: str) -> str:
    """ "res://scripts/player.gd:42" → "player.gd:42"."""
    return where.rsplit("/", 1)[-1] if where else ""


def where_file(where: str) -> str:
    """ "res://scripts/player.gd:42" → "scripts/player.gd" (project-relative)."""
    path = re.sub(r":\d+$", "", where)
    return path.removeprefix("res://") if path.startswith("res://") else ""


# --- groups ----------------------------------------------------------------------------


@dataclass
class Group:
    """One distinct error: same message, same place, however many times it showed."""

    id: str
    kind: str
    message: str
    where: str = ""
    #: Times seen, per source; the count is the most any one source saw, so the same
    #: error read from Output and from the game's log counts once.
    counts: dict[str, int] = field(default_factory=dict)
    first_seen: float = 0.0
    last_seen: float = 0.0
    status: str = OPEN
    failures: int = 0
    #: The user discarded or stopped its fix: Joinery doesn't start another on its own.
    declined: bool = False
    task_id: str = ""
    transaction_id: str = ""
    #: The change waiting for review that brought it (status from_change).
    change: str = ""
    #: Why it waits, or why Joinery stopped, in plain words.
    note: str = ""
    line: str = ""
    context: str = ""

    @property
    def count(self) -> int:
        return max(self.counts.values(), default=0)

    def label(self) -> str:
        """ "Invalid get index 'health' on null (player.gd:42)"."""
        place = short_where(self.where)
        text = self.message if len(self.message) <= 90 else self.message[:89] + "…"
        return text + (f" ({place})" if place else "")

    def fixing_title(self) -> str:
        return "Fixing: " + self.label()

    def fixed_title(self) -> str:
        """The change's title once its fix waits for review."""
        times = self.count
        return "Fixed: " + self.label() + (f" — {times} times" if times > 1 else "")

    def view(self) -> dict[str, Any]:
        """What the Errors list shows."""
        return {
            "id": self.id,
            "kind": self.kind,
            "message": self.message,
            "where": self.where,
            "place": short_where(self.where),
            "label": self.label(),
            "count": self.count,
            "sources": [SOURCES.get(s, s) for s in self.counts],
            "status": self.status,
            "failures": self.failures,
            "declined": self.declined,
            "task_id": self.task_id,
            "transaction_id": self.transaction_id,
            "note": self.note,
            "line": self.line,
            "last_seen": self.last_seen,
        }


def _empty_totals() -> dict[str, int]:
    return {
        "seen": 0,
        "groups": 0,
        "fixes": 0,
        "ready": 0,
        "accepted": 0,
        "discarded": 0,
        "failed": 0,
        "stopped": 0,
    }


class Book:
    """The project's error groups and counts, kept in ``.joinery/errors.json``."""

    def __init__(self, project: Path) -> None:
        self.project = project
        self.groups: dict[str, Group] = {}
        #: Metrics: occurrences seen, distinct groups, fixes started, fixes that
        #: reached review, accepted, discarded, failed tries, groups Joinery gave up on.
        self.totals = _empty_totals()

    @property
    def path(self) -> Path:
        return self.project / STATE

    @classmethod
    def load(cls, project: Path) -> Book:
        book = cls(project)
        try:
            raw = json.loads(book.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return book
        if not isinstance(raw, dict):
            return book
        known = set(Group.__dataclass_fields__)
        for item in (
            raw.get("groups", []) if isinstance(raw.get("groups"), list) else []
        ):
            if not isinstance(item, dict) or not item.get("id"):
                continue
            try:
                group = Group(**{k: v for k, v in item.items() if k in known})
            except TypeError:
                continue
            if group.status in (FIXING, QUEUED, IN_REVIEW):
                # Its task belonged to a chat host that has gone; it waits again.
                group.status, group.task_id, group.transaction_id = OPEN, "", ""
            book.groups[group.id] = group
        totals = raw.get("totals", {})
        if isinstance(totals, dict):
            for key in book.totals:
                value = totals.get(key, 0)
                book.totals[key] = value if isinstance(value, int) else 0
        return book

    def save(self) -> None:
        kept = sorted(
            self.groups.values(),
            key=lambda g: (g.status not in (FIXED, IGNORED), g.last_seen),
        )[-KEEP:]
        self.groups = {g.id: g for g in kept}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(
            self.path,
            {
                "version": 1,
                "totals": dict(self.totals),
                "groups": [asdict(g) for g in self.groups.values()],
            },
        )

    def add(self, entry: Entry, now: float | None = None) -> tuple[Group, bool]:
        """Count one entry; returns its group and whether the group is new."""
        stamp = time.time() if now is None else now
        identifier = group_id(entry)
        group = self.groups.get(identifier)
        fresh = group is None
        if group is None:
            group = Group(
                id=identifier,
                kind=entry.kind,
                message=" ".join(entry.message.split())[:MAX_MESSAGE],
                where=entry.where,
                first_seen=stamp,
                line=entry.line[:MAX_MESSAGE],
                context=entry.context[-4000:],
            )
            self.groups[identifier] = group
            self.totals["groups"] += 1
        group.counts[entry.source] = group.counts.get(entry.source, 0) + 1
        group.last_seen = stamp
        if entry.context and not group.context:
            group.context = entry.context[-4000:]
        self.totals["seen"] += 1
        return group, fresh

    def listed(self, limit: int = 30) -> list[Group]:
        """The groups the Errors list shows: being fixed first, then waiting, newest
        first within each, ignored last."""
        shown = [g for g in self.groups.values() if g.status in LISTED]
        shown.sort(key=lambda g: (LISTED.index(g.status), -g.last_seen))
        return shown[:limit]


def totals(project: Path) -> dict[str, int]:
    """The error counts for ``joinery metrics --errors``."""
    return dict(Book.load(project).totals)


def render_totals(counts: Mapping[str, int]) -> str:
    if not counts.get("seen"):
        return "No console errors have been seen in this project yet."
    return (
        f"Errors seen: {counts['seen']} ({counts['groups']} distinct)\n"
        f"Fixes started: {counts['fixes']}, ready for review: {counts['ready']}, "
        f"accepted: {counts['accepted']}, discarded: {counts['discarded']}\n"
        f"Failed tries: {counts['failed']}; given up after {MAX_FAILURES} tries: "
        f"{counts['stopped']}"
    )


# --- `joinery fix-errors` --------------------------------------------------------------


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--set", choices=MODES, default=None, help="ask, auto or off")
    parser.add_argument("--json", action="store_true")


def main(args: argparse.Namespace) -> int:
    """The Settings page's "Fix console errors automatically" switch."""
    project = args.project.resolve()
    try:
        if args.set is not None:
            set_mode(project, args.set)
        chosen = settings(project)
        counts = totals(project)
    except (OSError, ValueError) as exc:
        if args.json:
            print(json.dumps({"error": str(exc)}))
        else:
            print(str(exc), file=sys.stderr)
        return 1
    data = {
        "fix_errors": chosen.mode,
        "auto": chosen.mode == "auto",
        "warnings": chosen.warnings,
        "ignore_errors": list(chosen.ignore),
        "totals": counts,
    }
    if args.json:
        print(json.dumps(data))
    else:
        print(
            {
                "ask": "Console errors: Joinery offers to fix each new one.",
                "auto": "Console errors: Joinery starts a fix for each new one.",
                "off": "Console errors: not watched.",
            }[chosen.mode]
        )
    return 0
