"""Project memory: ``JOURNEYMAN.md`` at the project root (ADR 0077).

The user's standing instructions and the facts Journeyman learned about the game
("Signals are snake_case.", "The player is 1.8 m tall."), in one plain Markdown file
the user owns. Every agent reads it:

* Joinery puts it in the stable prompt prefix of every turn, for every provider and
  role, after the role's rules and before the project map
  (``bridge.provider_process.turn_text``, ADR 0056). Comments and empty sections are
  left out, and a file that is still only the template adds nothing.
* ``joinery.wiring.write_memory_stubs`` writes small ``CLAUDE.md`` / ``AGENTS.md`` /
  ``GEMINI.md`` files pointing at it (only where the project has none), so a coding
  agent run by hand in the project follows the same rules.

``/remember <text>``, "Remember this" on a chat message and ``joinery memory add``
append one line under "## Remembered" as a reviewed change: nothing is written until
the user accepts it. Journeyman reads at most ``LIMIT`` characters; a longer file is
cut at a line with a note saying so, and ``/remember`` refuses to grow it past that.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from joinery.catalog import ROOT
from joinery.transactions import FileTransaction

FILE = "JOURNEYMAN.md"
TEMPLATE = ROOT / "templates" / "journeyman.md"
#: Characters of the file (comments and empty sections left out) Journeyman reads:
#: about 1,500 tokens, cached with the rest of the stable prefix.
LIMIT = 6000
#: Characters one remembered line may have.
ENTRY_LIMIT = 300
SECTION = "## Remembered"
HEADER = (
    "Project memory (JOURNEYMAN.md): the user's standing instructions and facts "
    "about this game. Follow them unless the user says otherwise in this chat."
)
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_HEADING = re.compile(r"^#{1,6}\s")


def path(project: Path) -> Path:
    return project / FILE


def ensure(project: Path) -> bool:
    """Write ``JOURNEYMAN.md`` from the template when the project has none."""
    target = path(project)
    if target.exists() or target.is_symlink():
        return False
    target.write_text(TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
    return True


def cleaned(text: str) -> str:
    """What an agent needs of the file: no comments, no headings left empty, single
    blank lines. The same bytes for the same file."""
    lines = [line.rstrip() for line in _COMMENT.sub("", text).splitlines()]
    kept: list[str] = []
    for index, line in enumerate(lines):
        if _HEADING.match(line):
            level = len(line) - len(line.lstrip("#"))
            body = False
            for after in lines[index + 1 :]:
                if _HEADING.match(after):
                    if len(after) - len(after.lstrip("#")) <= level:
                        break
                    continue
                if after.strip():
                    body = True
                    break
            if not body:
                continue
        if not line.strip() and (not kept or not kept[-1].strip()):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def _template_only(text: str) -> bool:
    return cleaned(text) == cleaned(TEMPLATE.read_text(encoding="utf-8"))


def read(project: Path) -> str:
    """The file's text, or "" without one (a link or a folder counts as none)."""
    target = path(project)
    if target.is_symlink() or not target.is_file():
        return ""
    try:
        return target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def block(project: Path) -> str:
    """The prompt block for ``project``: "" when the file is missing, empty or still
    the template; cut at ``LIMIT`` characters on a line, with a note."""
    text = read(project)
    if not text or _template_only(text):
        return ""
    body = cleaned(text)
    if not body:
        return ""
    if len(body) > LIMIT:
        cut = body.rfind("\n", 0, LIMIT)
        body = body[: cut if cut > 0 else LIMIT].rstrip() + (
            f"\n[JOURNEYMAN.md is longer than the {LIMIT} characters Journeyman reads: "
            "the rest was left out. If something the user asked for seems missing, "
            "say so.]"
        )
    return HEADER + "\n\n" + body


def entries(text: str) -> list[str]:
    """The lines under "## Remembered"."""
    found: list[str] = []
    inside = False
    for line in _COMMENT.sub("", text).splitlines():
        if _HEADING.match(line):
            inside = line.strip() == SECTION
            continue
        if inside and line.strip():
            found.append(line.strip().removeprefix("- ").strip())
    return found


def status(project: Path) -> dict[str, Any]:
    text = read(project)
    size = len(cleaned(text)) if text and not _template_only(text) else 0
    return {
        "path": FILE,
        "exists": path(project).is_file(),
        "characters": size,
        "limit": LIMIT,
        "over": size > LIMIT,
        "remembered": entries(text),
    }


def entry(text: str) -> str:
    """One remembered line from what the user typed: single spaces, one line."""
    line = " ".join(str(text).split()).removeprefix("- ").strip()
    if not line:
        raise ValueError("Write what Journeyman should remember.")
    if len(line) > ENTRY_LIMIT:
        raise ValueError(
            f"Keep it to one short line ({ENTRY_LIMIT} characters at most); "
            f"this one has {len(line)}."
        )
    return line


def remembered(text: str, line: str) -> str:
    """``text`` with ``line`` added as the last item under "## Remembered" (the
    section is added at the end when there is none)."""
    item = entry(line)
    if item.casefold() in {e.casefold() for e in entries(text)}:
        raise ValueError("JOURNEYMAN.md already has that.")
    lines = text.splitlines()
    start = next((i for i, x in enumerate(lines) if x.strip() == SECTION), -1)
    if start < 0:
        body = text.rstrip("\n")
        result = (body + "\n\n" if body else "") + f"{SECTION}\n- {item}\n"
    else:
        end = next(
            (i for i in range(start + 1, len(lines)) if _HEADING.match(lines[i])),
            len(lines),
        )
        # After the section's last line (its comment included), before any blanks.
        at = max(
            [i + 1 for i in range(start + 1, end) if lines[i].strip()],
            default=start + 1,
        )
        lines.insert(at, f"- {item}")
        result = "\n".join(lines) + "\n"
    grown = len(cleaned(result))
    if grown > LIMIT:
        raise ValueError(
            f"JOURNEYMAN.md would be longer than the {LIMIT} characters Journeyman "
            "reads. Shorten it first: it's a plain file at the project root."
        )
    return result


def append(root: Path, line: str) -> str:
    """Add ``line`` to ``root``'s JOURNEYMAN.md (a staged copy); returns the item."""
    target = path(root)
    if target.is_symlink():
        raise ValueError("JOURNEYMAN.md is a link; Journeyman won't write through it.")
    text = (
        target.read_text(encoding="utf-8")
        if target.is_file()
        else TEMPLATE.read_text(encoding="utf-8")
    )
    target.write_text(remembered(text, line), encoding="utf-8")
    return entry(line)


def summary(line: str) -> str:
    """The change's title: "Remember: Signals are snake_case"."""
    item = entry(line)
    return "Remember: " + (item if len(item) <= 60 else item[:59].rstrip() + "…")


def stage(project: Path, line: str) -> FileTransaction:
    """The addition as a change waiting for review, kept across sessions."""
    item = entry(line)
    tx = FileTransaction(project)
    tx.note_tool("joinery")
    tx.begin(summary(item))
    try:
        append(tx.stage, item)
        tx.review()
        tx.hold("joinery memory")
    except BaseException:
        if tx.state in {"staging", "review"}:
            tx.reject()
        raise
    return tx


# --- CLI ---------------------------------------------------------------------------


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """``joinery memory --project P [show|add TEXT|prompt] [--json]``."""
    parser = subparsers.add_parser(
        "memory",
        help="the project's JOURNEYMAN.md: show it, or add a line for review",
    )
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument(
        "action", nargs="?", default="show", choices=("show", "add", "prompt")
    )
    parser.add_argument("text", nargs="*", help="the line to remember (add)")
    parser.add_argument("--json", action="store_true", help="print JSON")


def run(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve()
    if not (project / "project.godot").is_file():
        message = f"No Godot project at {project}."
        print(json.dumps({"error": message}) if args.json else message)
        return 1
    if args.action == "prompt":
        print(block(project))
        return 0
    if args.action == "add":
        try:
            tx = stage(project, " ".join(args.text))
        except (OSError, ValueError) as exc:
            print(json.dumps({"error": str(exc)}) if args.json else str(exc))
            return 2
        data = {"transaction_id": tx.id, "summary": tx.summary, "state": tx.state}
        if args.json:
            print(json.dumps(data))
        else:
            print(
                f"{tx.summary}: waiting for your review in the Review tab, or "
                f"joinery transactions accept --project {project} --id {tx.id}"
            )
        return 0
    found = status(project)
    if args.json:
        print(json.dumps(found))
        return 0
    if not found["exists"]:
        print(f"No {FILE} yet. joinery setup writes one.")
        return 0
    print(
        f"{FILE}: {found['characters']} of {LIMIT} characters Journeyman reads"
        + (" (over: the end is left out)" if found["over"] else "")
    )
    for item in found["remembered"]:
        print(f"- {item}")
    return 0
