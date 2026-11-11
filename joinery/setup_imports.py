"""Reference images and design documents the user adds to a game project.

At creation (the wizard's step 3, `joinery game apply`, `joinery new`) or later
from Project apps (`joinery references add`), the user picks files on their
own disk:

- reference images (PNG, JPEG, WebP) are copied into `art/reference/`, recorded
  in `joinery.lock` as the user's import (`human-import`) and added to the art
  bible's reference board as rows R1, R2, … with the user's note as *Take
  this*; *Not that* stays open for the user;
- design documents (Markdown, text, PDF) are copied into `design/docs/`,
  listed in the game brief under "Design documents", and Markdown and text
  ones draft the brief's still-open sections word for word (`joinery.gdd`).

These are the user's explicit imports, the one way a file from outside enters
a project (CLAUDE.md, Rule Zero). Only the files the user picked are copied:
never the folder around them, never what a document links to, and nothing is
fetched. Every copy lands inside the project's `art/reference/` or
`design/docs/`, under a cleaned-up version of its own name, and a file that is
already there with the same bytes is not copied twice.

Everything is checked before anything is written: `plan` refuses a file that
is missing, a folder, the wrong type (by its suffix and its own first bytes),
too large, or one too many, with a plain sentence naming it. `apply` then
writes into any root, the project itself or a review stage.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Literal

from joinery import brief, gdd
from joinery.bible import BIBLE_PATH
from joinery.bible_schema import REFERENCE_DIR
from joinery.chat_attachments import sniff
from joinery.lockfile import LOCK_NAME, HumanImport, record_imports

DOCS_DIR = "design/docs"
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")
TEXT_SUFFIXES = (".md", ".markdown", ".txt")
DOC_SUFFIXES = (*TEXT_SUFFIXES, ".pdf")
MAX_REFERENCES = 20
MAX_DOCUMENTS = 10
#: Bigger than any picture or design document a person would pick on purpose.
MAX_BYTES = 40 * 1024 * 1024
#: The longest note kept for a reference (one line).
MAX_NOTE = 200
#: Who and what the lock records for a reference the user added.
IMPORTED_BY = "user (added as a reference)"
LICENCE = "reference only, not shipped"
#: Files the wizard leaves beside the project for `game apply`, `brief` and
#: `bible` to consume (multi-line notes and paths never pass through a shell).
REFERENCES_FILE = ".joinery-references.json"
DOCUMENTS_FILE = ".joinery-design-docs.json"
DOCS_HEADING = "Design documents"
DOCS_INTRO = (
    "Your own design documents, copied into the project. Journeyman reads them "
    "with this brief; the brief wins where they disagree, until you change it."
)

Kind = Literal["reference", "document"]
_UNSAFE = re.compile(r"[^\w .()+-]")
_SPACES = re.compile(r"[ _]{2,}")
_BOARD_HEADER = ["#", "reference", "kind", "take this", "not that", "file"]
_ROW_ID = re.compile(r"^\|\s*R(\d+)\s*\|")


@dataclass(frozen=True)
class Planned:
    """One file the user picked, and where it goes."""

    kind: Kind
    source: Path
    #: Project path, posix: art/reference/dusk.jpg or design/docs/gdd.md.
    destination: str
    note: str = ""
    #: False when the same bytes are already at the destination.
    copy: bool = True
    sha256: str = ""


@dataclass
class Plan:
    references: list[Planned] = field(default_factory=list)
    documents: list[Planned] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.references or self.documents)

    def changes(self) -> list[str]:
        """Project paths `apply` would write, for a before/after."""
        paths = [p.destination for p in [*self.references, *self.documents] if p.copy]
        if self.references:
            paths += [LOCK_NAME, BIBLE_PATH.as_posix()]
        if self.documents:
            paths.append(brief.BRIEF_PATH.as_posix())
        return list(dict.fromkeys(paths))


# --- Reading what the user picked ----------------------------------------------


def parse_reference(text: str) -> tuple[Path, str]:
    """`PATH` or `PATH=note` from `--reference`. A path may itself hold `=`.

    The first `=` whose left side is an existing file splits path from note;
    without one the whole text is the path.
    """
    if not Path(text).expanduser().exists():
        start = 0
        while (index := text.find("=", start)) >= 0:
            if Path(text[:index]).expanduser().is_file():
                return Path(text[:index]).expanduser(), text[index + 1 :]
            start = index + 1
    return Path(text).expanduser(), ""


def _note(text: str) -> str:
    one_line = " ".join(str(text).split())
    return one_line[:MAX_NOTE]


def read_answers(project: Path, name: str) -> list[dict[str, str]]:
    """Consume the wizard's `name` file beside the project: a JSON list.

    Each item is a path string or {"path", "note"}. The file is removed
    whatever happens. Missing: [].
    """
    source = project / name
    if not source.is_file():
        return []
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{source}: not valid JSON: {exc}") from exc
    finally:
        source.unlink(missing_ok=True)
    if not isinstance(raw, list):
        raise ValueError(f"{source}: expected a JSON list of files")  # noqa: TRY004
    items: list[dict[str, str]] = []
    for item in raw:
        if isinstance(item, str):
            items.append({"path": item, "note": ""})
        elif isinstance(item, dict) and isinstance(item.get("path"), str):
            items.append({"path": item["path"], "note": str(item.get("note") or "")})
    return items


# --- Checking -----------------------------------------------------------------


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _check_source(path: Path, kind: Kind) -> None:
    name = path.name or str(path)
    if not path.exists():
        raise ValueError(f"Can't find {name}. Pick the file again.")
    if path.is_dir():
        raise ValueError(f"{name} is a folder. Pick the files inside it instead.")
    if not path.is_file():
        raise ValueError(f"{name} isn't a file Joinery can copy.")
    suffix = path.suffix.lower()
    if kind == "reference" and suffix not in IMAGE_SUFFIXES:
        hint = (
            " If it describes the game, add it under Design documents."
            if suffix in DOC_SUFFIXES
            else ""
        )
        raise ValueError(
            f"{name} isn't a picture. References can be PNG, JPEG or WebP images.{hint}"
        )
    if kind == "document" and suffix not in DOC_SUFFIXES:
        raise ValueError(
            f"{name} can't be a design document. Use Markdown (.md), text (.txt) or PDF."
        )
    if path.stat().st_size > MAX_BYTES:
        raise ValueError(f"{name} is larger than 40 MB. Use a smaller copy.")
    with path.open("rb") as stream:
        head = stream.read(4096)
    if kind == "reference" and not sniff(head):
        raise ValueError(
            f"{name} doesn't hold a PNG, JPEG or WebP image. Save it again as one of those."
        )
    if kind == "document" and suffix == ".pdf" and not head.startswith(b"%PDF-"):
        raise ValueError(f"{name} isn't a PDF inside. Export it again as a PDF.")
    if kind == "document" and suffix in TEXT_SUFFIXES:
        try:
            path.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError(
                f"{name} isn't plain text. Save it as UTF-8 text or Markdown."
            ) from None


def safe_name(name: str, fallback: str) -> str:
    """The file's own name, without anything that could leave its folder."""
    clean = unicodedata.normalize("NFC", PurePosixPath(name.replace("\\", "/")).name)
    stem, dot, suffix = clean.rpartition(".")
    if not dot:
        stem, suffix = clean, ""
    stem = _SPACES.sub("_", _UNSAFE.sub("_", stem)).strip(" ._") or fallback
    suffix = re.sub(r"[^A-Za-z0-9]", "", suffix).lower()
    stem = stem[:100]
    return f"{stem}.{suffix}" if suffix else stem


def _folder(project: Path, relative: str) -> Path:
    """`project/relative`, refused when a link would take it out of the project."""
    root = project.resolve()
    folder = project / relative
    probe = folder
    while not probe.exists() and probe != project:
        probe = probe.parent
    if not probe.resolve().is_relative_to(root) or (
        folder.exists() and not folder.resolve().is_relative_to(root)
    ):
        raise ValueError(
            f"{relative} points outside this project. Joinery only copies into the project."
        )
    return folder


def _existing(folder: Path) -> dict[str, str]:
    """casefolded name -> name of the files already in `folder`."""
    if not folder.is_dir():
        return {}
    return {child.name.casefold(): child.name for child in folder.iterdir()}


def _place(
    project: Path,
    kind: Kind,
    source: Path,
    note: str,
    taken: dict[str, str],
    placed: dict[str, Planned],
) -> Planned | None:
    """Where one file goes; None when this batch already has the same bytes."""
    relative = REFERENCE_DIR if kind == "reference" else DOCS_DIR
    folder = _folder(project, relative)
    resolved = source.resolve()
    digest = _sha256(resolved)
    if digest in placed:
        return None
    if resolved.parent == folder.resolve():
        # Already in the project's folder: record it, copy nothing.
        planned = Planned(
            kind, resolved, f"{relative}/{resolved.name}", note, False, digest
        )
        placed[digest] = planned
        taken[resolved.name.casefold()] = resolved.name
        return planned
    name = safe_name(source.name, "reference" if kind == "reference" else "document")
    stem, dot, suffix = name.rpartition(".")
    if not dot:
        stem, suffix = name, ""
    number = 1
    while True:
        candidate = (
            name
            if number == 1
            else f"{stem}-{number}" + (f".{suffix}" if suffix else "")
        )
        existing = taken.get(candidate.casefold())
        if existing is None:
            break
        on_disk = folder / existing
        if on_disk.is_file() and _sha256(on_disk) == digest:
            planned = Planned(
                kind, resolved, f"{relative}/{existing}", note, False, digest
            )
            placed[digest] = planned
            return planned
        number += 1
    taken[candidate.casefold()] = candidate
    destination = f"{relative}/{candidate}"
    if not (project / destination).resolve().is_relative_to(project.resolve()):
        raise ValueError(f"{source.name} can't be copied into {relative}.")
    planned = Planned(kind, resolved, destination, note, True, digest)
    placed[digest] = planned
    return planned


def plan(
    project: Path,
    references: Sequence[tuple[Path, str]] = (),
    documents: Sequence[Path] = (),
) -> Plan:
    """Check every picked file and decide where it goes. Writes nothing.

    Raises ValueError with one plain sentence for the first file that can't
    be added; nothing is copied then.
    """
    if len(references) > MAX_REFERENCES:
        raise ValueError(f"Add up to {MAX_REFERENCES} references at a time.")
    if len(documents) > MAX_DOCUMENTS:
        raise ValueError(f"Add up to {MAX_DOCUMENTS} design documents at a time.")
    for source, _note_text in references:
        _check_source(source, "reference")
    for source in documents:
        _check_source(source, "document")
    result = Plan()
    taken = _existing(_folder(project, REFERENCE_DIR))
    placed: dict[str, Planned] = {}
    for source, note in references:
        planned = _place(project, "reference", source, _note(note), taken, placed)
        if planned is not None:
            result.references.append(planned)
    taken = _existing(_folder(project, DOCS_DIR))
    placed = {}
    for source in documents:
        planned = _place(project, "document", source, "", taken, placed)
        if planned is not None:
            result.documents.append(planned)
    return result


def plan_from_args(
    project: Path,
    references: Iterable[str] = (),
    documents: Iterable[str] = (),
    answers: Sequence[str] = (),
) -> Plan:
    """`--reference PATH[=note]` and `--design-doc PATH`, plus the wizard's
    files beside the project named in `answers` (each consumed)."""
    picked = [parse_reference(text) for text in references]
    docs = [Path(text).expanduser() for text in documents]
    if REFERENCES_FILE in answers:
        picked += [
            (Path(item["path"]), item["note"])
            for item in read_answers(project, REFERENCES_FILE)
        ]
    if DOCUMENTS_FILE in answers:
        docs += [Path(item["path"]) for item in read_answers(project, DOCUMENTS_FILE)]
    return plan(project, picked, docs)


def summary(chosen: Plan) -> str:
    """The reviewed change's one line."""
    parts: list[str] = []
    if chosen.references:
        parts.append(
            _count(len(chosen.references), "reference image", "reference images")
        )
    if chosen.documents:
        parts.append(
            _count(len(chosen.documents), "design document", "design documents")
        )
    return "Add " + " and ".join(parts)


def plan_notes(project: Path, chosen: Plan) -> list[str]:
    """What else the change does, in plain words, for the before/after."""
    notes: list[str] = []
    if chosen.references:
        bible_path = project / BIBLE_PATH
        if not bible_path.is_file():
            notes.append(
                "There's no art bible yet, so the references only go into art/reference/."
            )
        elif (
            board_rows(bible_path.read_text(encoding="utf-8"), chosen.references)
            is None
        ):
            notes.append(
                "The art bible has no reference board. Upgrade it in /art to list them."
            )
        else:
            notes.append(
                "Pictures go on the art bible's reference board. That changes the "
                "bible, so approve it again in /art afterwards."
            )
    if chosen.documents:
        if (project / brief.BRIEF_PATH).is_file():
            notes.append(
                "Documents are listed in the game brief, and its open sections are "
                "copied from matching headings in Markdown or text ones."
            )
        else:
            notes.append(
                "There's no game brief yet. Journeyman reads them from design/docs/."
            )
    return notes


# --- Writing ------------------------------------------------------------------


def copy_files(root: Path, chosen: Plan) -> list[str]:
    """Copy the picked files into `root` (the project or a review stage).

    Returns the project paths written. `art/reference/` keeps out of Godot's
    imports; `design/docs/` too, so a document is never an imported resource.
    """
    written: list[str] = []
    for relative in (REFERENCE_DIR, DOCS_DIR):
        if not any(
            p.destination.startswith(relative + "/")
            for p in [*chosen.references, *chosen.documents]
        ):
            continue
        folder = _folder(root, relative)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / ".gdignore").touch()
    for planned in [*chosen.references, *chosen.documents]:
        if not planned.copy:
            continue
        target = root / planned.destination
        if not target.resolve().is_relative_to(root.resolve()):
            raise ValueError(f"{planned.destination} would be outside the project.")
        shutil.copyfile(planned.source, target)
        if _sha256(target) != planned.sha256:
            target.unlink(missing_ok=True)
            raise ValueError(
                f"{planned.source.name} changed while it was copied. Try again."
            )
        written.append(planned.destination)
    return written


def record_lock(root: Path, chosen: Plan) -> int:
    """Add a `human-import` entry for each reference without one. Returns how many."""
    return record_imports(
        root,
        [
            HumanImport(
                path=planned.destination,
                sha256=planned.sha256,
                origin="human-import",
                imported_by=IMPORTED_BY,
                licence=LICENCE,
            )
            for planned in chosen.references
        ],
    )


def _cell(text: str) -> str:
    return text.replace("|", "\\|").strip()


def _label(destination: str) -> str:
    stem = PurePosixPath(destination).stem
    words = " ".join(re.sub(r"[_-]+", " ", stem).split())
    return words or stem


def board_rows(text: str, chosen: Sequence[Planned]) -> tuple[str, int] | None:
    """The bible with a board row for each reference not on it yet, and how many.

    None when the bible has no reference board (a first-format bible).
    Rows continue the numbering: R1, R2, … The note is *Take this*; Kind and
    *Not that* stay empty for the user.
    """
    lines = text.split("\n")
    header = next(
        (
            i
            for i, line in enumerate(lines)
            if line.strip().startswith("|")
            and [c.strip().lower() for c in line.strip().strip("|").split("|")][:6]
            == _BOARD_HEADER
        ),
        None,
    )
    if header is None:
        return None
    end = header + 1
    while end < len(lines) and lines[end].strip().startswith("|"):
        end += 1
    table = lines[header + 1 : end]
    numbers = [int(m.group(1)) for row in table if (m := _ROW_ID.match(row.strip()))]
    on_board = {
        cells[-1].strip().strip("`")
        for row in table
        if len(cells := row.strip().strip("|").split("|")) >= 6
    }
    rows: list[str] = []
    next_id = max(numbers, default=0) + 1
    for planned in chosen:
        if planned.destination in on_board:
            continue
        on_board.add(planned.destination)
        rows.append(
            f"| R{next_id} | {_cell(_label(planned.destination))} |  | "
            f"{_cell(planned.note)} |  | {planned.destination} |"
        )
        next_id += 1
    if not rows:
        return text, 0
    return "\n".join([*lines[:end], *rows, *lines[end:]]), len(rows)


def add_board_rows(root: Path, chosen: Plan) -> int | None:
    """Put the references on `root`'s art bible board. None: no bible or no board."""
    path = root / BIBLE_PATH
    if not chosen.references or not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    updated = board_rows(text, chosen.references)
    if updated is None:
        return None
    new_text, count = updated
    if count:
        path.write_text(new_text, encoding="utf-8")
    return count


def _kind_word(destination: str) -> str:
    suffix = PurePosixPath(destination).suffix.lower()
    if suffix == ".pdf":
        return "PDF"
    if suffix in (".md", ".markdown"):
        return "Markdown"
    return "text"


def documents_section(
    text: str, chosen: Sequence[Planned], drafted: dict[str, list[str]]
) -> str:
    """The brief with every new document listed under "Design documents".

    The section goes first, before the pitch, and is created when missing.
    """
    bullets: list[str] = []
    for planned in chosen:
        if f"`{planned.destination}`" in text:
            continue
        kind = _kind_word(planned.destination)
        line = f"- `{planned.destination}` ({kind})"
        sections = drafted.get(planned.destination, [])
        if sections:
            line += ": drafted " + ", ".join(sections) + " from it."
        elif kind == "PDF":
            line += ": not copied into the brief. Open it to read it."
        else:
            line += ": no open section of the brief matched its headings."
        bullets.append(line)
    if not bullets:
        return text
    heading = re.compile(r"^##\s+" + re.escape(DOCS_HEADING) + r"\s*$", re.MULTILINE)
    found = heading.search(text)
    if found is not None:
        following = re.compile(r"^##\s", re.MULTILINE).search(text, found.end())
        end = following.start() if following else len(text)
        body = text[found.end() : end].rstrip("\n")
        return (
            text[: found.end()] + body + "\n" + "\n".join(bullets) + "\n\n" + text[end:]
        )
    block = f"## {DOCS_HEADING}\n{DOCS_INTRO}\n\n" + "\n".join(bullets) + "\n\n"
    first = re.compile(r"^##\s", re.MULTILINE).search(text)
    if first is None:
        return text.rstrip("\n") + "\n\n" + block
    return text[: first.start()] + block + text[first.start() :]


def update_brief(root: Path, chosen: Plan) -> list[str] | None:
    """List the documents in `root`'s brief and draft its open sections from them.

    Returns the brief sections drafted, or None when there is no brief.
    """
    path = root / brief.BRIEF_PATH
    if not chosen.documents or not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    drafted: dict[str, list[str]] = {}
    every: list[str] = []
    for planned in chosen.documents:
        if planned.destination.lower().endswith(".pdf"):
            continue
        source = root / planned.destination
        content = (source if source.is_file() else planned.source).read_text(
            encoding="utf-8"
        )
        text, sections = gdd.draft_open_sections(text, [(planned.destination, content)])
        drafted[planned.destination] = sections
        every += sections
    text = documents_section(text, chosen.documents, drafted)
    path.write_text(text, encoding="utf-8")
    return every


# --- In words -----------------------------------------------------------------


def _count(number: int, one: str, many: str) -> str:
    return f"{number} {one if number == 1 else many}"


def reference_message(count: int, rows: int | None) -> str:
    added = f"Added {_count(count, 'reference image', 'reference images')} to {REFERENCE_DIR}/"
    if rows is None:
        return (
            added + ". Put them on the art bible's reference board when you write it."
        )
    return (
        added
        + " and the art bible's reference board. Say what to avoid in each in /art."
    )


def document_message(count: int, drafted: list[str] | None) -> str:
    added = (
        f"Added {_count(count, 'design document', 'design documents')} to {DOCS_DIR}/"
    )
    if drafted is None:
        return added + ". Journeyman reads them with the game brief."
    if drafted:
        return added + f" and drafted {', '.join(drafted)} in the game brief from them."
    return added + " and listed them in the game brief."


def apply(root: Path, chosen: Plan) -> list[dict[str, str]]:
    """Copy, lock, put on the board and list in the brief, inside `root`.

    Returns step outcomes ({step, state, message}) for the front ends.
    """
    if not chosen:
        return []
    copy_files(root, chosen)
    steps: list[dict[str, str]] = []
    if chosen.references:
        record_lock(root, chosen)
        rows = add_board_rows(root, chosen)
        steps.append(
            {
                "step": "references",
                "state": "done",
                "message": reference_message(len(chosen.references), rows),
            }
        )
    if chosen.documents:
        drafted = update_brief(root, chosen)
        steps.append(
            {
                "step": "design-docs",
                "state": "done",
                "message": document_message(len(chosen.documents), drafted),
            }
        )
    return steps


# --- For agents ---------------------------------------------------------------

#: How much of one document, and of all of them, an agent gets as text.
MAX_DOC_TEXT = 20_000
MAX_DOCS_TEXT = 60_000


def design_documents(project: Path) -> list[dict[str, object]]:
    """Every file in `design/docs/`, with the text of Markdown and text ones.

    PDFs come with `text: None` and a note to open them; long text is cut
    (`truncated: True`) so one tool reply stays small.
    """
    folder = project / DOCS_DIR
    if not folder.is_dir() or not folder.resolve().is_relative_to(project.resolve()):
        return []
    found: list[dict[str, object]] = []
    budget = MAX_DOCS_TEXT
    for path in sorted(folder.iterdir(), key=lambda p: p.name.casefold()):
        if path.is_symlink() or not path.is_file() or path.name.startswith("."):
            continue
        suffix = path.suffix.lower()
        if suffix not in DOC_SUFFIXES:
            continue
        entry: dict[str, object] = {
            "path": f"{DOCS_DIR}/{path.name}",
            "kind": _kind_word(path.name),
            "bytes": path.stat().st_size,
        }
        if suffix == ".pdf":
            entry["text"] = None
            entry["note"] = "A PDF: open the file to read it."
        else:
            text = path.read_text(encoding="utf-8", errors="replace")
            limit = min(MAX_DOC_TEXT, max(budget, 0))
            entry["text"] = text[:limit]
            entry["truncated"] = len(text) > limit
            budget -= min(len(text), limit)
        found.append(entry)
    return found
