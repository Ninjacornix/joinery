"""Saved Journeyman chats: one JSON-lines file per chat in ``<project>/.joinery/chats/``.

The first line is the chat itself (title, provider, profile, the provider's resumable
handle); every later line is one thing that happened in it, oldest first: the user's
request (with its target chips and the paths of attached images, never the images),
Journeyman's replies and steps, the changes it proposed and how each turn ended.

The chat host writes these files; the sidebar reads them to list and restore chats. The
folder is kept out of git and out of Godot's import. Files are size-bounded: a chat that
grows past :data:`MAX_BYTES` drops its oldest entries, and only the newest
:data:`MAX_CHATS` chats are kept.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import secrets
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from joinery.platform_support import lock_file, unlock_file

#: Where chats are saved, relative to the game project.
CHAT_DIR = ".joinery/chats"
VERSION = 1
#: Chats kept per project; older ones are removed when a new chat starts.
MAX_CHATS = 100
#: One chat's file; past it, the oldest entries are dropped.
MAX_BYTES = 1_000_000
#: Characters kept of any one text in a chat.
MAX_TEXT = 16_000
TITLE_CHARS = 80
#: Turns of an old chat that go into the summary a fresh agent session starts from.
SUMMARY_TURNS = 8
#: What the sidebar says when a chat continues without the provider's own session.
SUMMARY_NOTE = "Continued with a summary; the agent starts fresh."
_ID = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$")
#: Entry types a chat file holds after its first line.
ENTRY_TYPES = {"user", "agent", "step", "notice", "change", "turn", "session"}


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def folder(project: Path) -> Path:
    """The chats folder, made on first use and kept out of git and Godot's import."""
    path = project / CHAT_DIR
    path.mkdir(parents=True, exist_ok=True)
    for marker, text in ((".gitignore", "*\n"), (".gdignore", "")):
        if not (path / marker).exists():
            (path / marker).write_text(text)
    return path


def path_for(project: Path, chat_id: str) -> Path:
    if not _ID.match(chat_id):
        raise ValueError("Unknown chat.")
    return project / CHAT_DIR / (chat_id + ".jsonl")


def new_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S", time.gmtime()) + "-" + secrets.token_hex(3)


def title_from(text: str) -> str:
    """A chat's title: its first request on one line, cut at a word near TITLE_CHARS."""
    line = " ".join(text.split())
    if len(line) <= TITLE_CHARS:
        return line
    cut = line[:TITLE_CHARS].rsplit(" ", 1)[0]
    return (cut if len(cut) > TITLE_CHARS // 2 else line[:TITLE_CHARS]).rstrip(
        " ,.;:"
    ) + "…"


def _clip(value: Any) -> Any:
    if isinstance(value, str):
        return value if len(value) <= MAX_TEXT else value[:MAX_TEXT] + "…"
    if isinstance(value, list):
        return [_clip(item) for item in value[:20]]
    if isinstance(value, dict):
        return {str(k): _clip(v) for k, v in list(value.items())[:40]}
    return value


@contextlib.contextmanager
def _locked(project: Path) -> Iterator[None]:
    """One writer at a time across the chat host and the CLI (rename, delete)."""
    with (folder(project) / ".lock").open("a+b") as stream:
        deadline = time.monotonic() + 3.0
        while True:
            try:
                lock_file(stream)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.02)
        try:
            yield
        finally:
            unlock_file(stream)


def _lines(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        with contextlib.suppress(ValueError):
            record = json.loads(line)
            if isinstance(record, dict):
                records.append(record)
    return records


def _write(path: Path, records: list[dict[str, Any]]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def read(project: Path, chat_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """A chat's first line and its entries. Raises ValueError for a missing chat."""
    path = path_for(project, chat_id)
    if not path.is_file():
        raise ValueError("That chat is no longer saved.")
    records = _lines(path)
    if not records or records[0].get("type") != "chat":
        raise ValueError("That chat file is damaged.")
    return records[0], [r for r in records[1:] if r.get("type") in ENTRY_TYPES]


def create(project: Path, title: str, provider: str, profile: str = "") -> str:
    """Start a chat file and return its id. The oldest chats past MAX_CHATS go."""
    with _locked(project):
        chat_id = new_id()
        while path_for(project, chat_id).exists():
            chat_id = new_id()
        meta = {
            "type": "chat",
            "version": VERSION,
            "id": chat_id,
            "title": title_from(title) or "New chat",
            "created": now(),
            "provider": provider,
            "profile": profile,
            "model": "",
            "handle": "",
        }
        _write(path_for(project, chat_id), [meta])
        saved = sorted(
            (project / CHAT_DIR).glob("*.jsonl"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old in saved[MAX_CHATS:]:
            with contextlib.suppress(OSError):
                old.unlink()
        return chat_id


def append(project: Path, chat_id: str, entry: dict[str, Any]) -> None:
    """Add one entry (stamped with the time). A file past MAX_BYTES drops its oldest."""
    if entry.get("type") not in ENTRY_TYPES:
        raise ValueError("Unknown chat entry.")
    record = _clip({"at": now(), **entry})
    path = path_for(project, chat_id)
    with _locked(project):
        if not path.is_file():
            raise ValueError("That chat is no longer saved.")
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        if path.stat().st_size > MAX_BYTES:
            _trim(path)


def _trim(path: Path) -> None:
    """Keep the chat line and the newest entries within about half of MAX_BYTES."""
    records = _lines(path)
    head, entries = records[0], records[1:]
    budget = MAX_BYTES // 2
    kept: list[dict[str, Any]] = []
    for record in reversed(entries):
        budget -= len(json.dumps(record, ensure_ascii=False)) + 1
        if budget < 0:
            break
        kept.append(record)
    kept.reverse()
    # A turn starts with the user's request; never begin the kept part mid-turn.
    while kept and kept[0].get("type") != "user":
        kept.pop(0)
    head["trimmed"] = True
    _write(path, [head, *kept])


def update(project: Path, chat_id: str, **fields: str) -> dict[str, Any]:
    """Change the chat line's title, provider, profile, model or handle."""
    allowed = {"title", "provider", "profile", "model", "handle"}
    if not set(fields) <= allowed:
        raise ValueError("Only a chat's title and session can change.")
    path = path_for(project, chat_id)
    with _locked(project):
        if not path.is_file():
            raise ValueError("That chat is no longer saved.")
        records = _lines(path)
        if not records or records[0].get("type") != "chat":
            raise ValueError("That chat file is damaged.")
        if all(records[0].get(k) == v for k, v in fields.items()):
            return records[0]
        records[0].update(fields)
        _write(path, records)
        return records[0]


def rename(project: Path, chat_id: str, title: str) -> dict[str, Any]:
    line = " ".join(title.split())[:TITLE_CHARS]
    if not line:
        raise ValueError("Give the chat a name.")
    return update(project, chat_id, title=line)


def delete(project: Path, chat_id: str) -> None:
    path = path_for(project, chat_id)
    with _locked(project):
        path.unlink(missing_ok=True)


def listing(project: Path) -> list[dict[str, Any]]:
    """Every saved chat, most recent first: {id, title, created, updated, provider, turns}."""
    directory = project / CHAT_DIR
    if not directory.is_dir():
        return []
    chats: list[dict[str, Any]] = []
    for path in directory.glob("*.jsonl"):
        try:
            records = _lines(path)
            stamp = path.stat().st_mtime
        except OSError:
            continue
        if not records or records[0].get("type") != "chat":
            continue
        meta = records[0]
        chats.append(
            {
                "id": str(meta.get("id", path.stem)),
                "title": str(meta.get("title", "")),
                "created": str(meta.get("created", "")),
                "updated": datetime.fromtimestamp(stamp, UTC).isoformat(
                    timespec="seconds"
                ),
                "provider": str(meta.get("provider", "")),
                "turns": sum(1 for r in records if r.get("type") == "user"),
            }
        )
    chats.sort(key=lambda chat: chat["updated"], reverse=True)
    return chats


def change_states(project: Path, ids: list[str]) -> dict[str, dict[str, Any]]:
    """Each change's state now, as `joinery transactions list` reports it.

    A change whose journal is gone reads {"state": "gone", "revertible": False}.
    """
    from joinery.bootstrap import transactions

    states: dict[str, dict[str, Any]] = {}
    for identifier in dict.fromkeys(ids):
        if not re.fullmatch(r"[0-9a-f]{32}", identifier):
            continue
        try:
            (entry,) = transactions(project, "list", identifier)
            states[identifier] = {
                "state": str(entry["state"]),
                "revertible": bool(entry["revertible"]),
            }
        except (OSError, ValueError, KeyError, RuntimeError):
            states[identifier] = {"state": "gone", "revertible": False}
    return states


def turns(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The chat as turns: {request, reply, changes, outcome}, oldest first."""
    result: list[dict[str, Any]] = []
    for entry in entries:
        kind = entry.get("type")
        if kind == "user":
            result.append(
                {
                    "request": str(entry.get("text", "")),
                    "reply": [],
                    "changes": [],
                    "outcome": "",
                }
            )
        elif not result:
            continue
        elif kind == "agent":
            result[-1]["reply"].append(str(entry.get("text", "")))
        elif kind == "change" and entry.get("transaction_id"):
            ids = [c["transaction_id"] for c in result[-1]["changes"]]
            if entry["transaction_id"] not in ids:
                result[-1]["changes"].append(
                    {
                        "transaction_id": str(entry["transaction_id"]),
                        "summary": str(entry.get("summary", "")),
                    }
                )
        elif kind == "turn":
            result[-1]["outcome"] = str(entry.get("outcome", ""))
    return result


def _short(text: str, limit: int) -> str:
    line = " ".join(text.split())
    return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"


#: How each change's state reads in a summary.
_STATE_WORDS = {
    "accepted": "accepted",
    "rejected": "discarded",
    "reverted": "accepted, later reverted",
    "review": "never reviewed",
    "staging": "never reviewed",
    "gone": "no longer on record",
}


def summary(project: Path, meta: dict[str, Any], entries: list[dict[str, Any]]) -> str:
    """A compact account of an earlier chat for an agent that starts fresh: its last
    turns, what Journeyman said, and what became of each change it proposed."""
    recent = turns(entries)[-SUMMARY_TURNS:]
    states = change_states(
        project,
        [c["transaction_id"] for turn in recent for c in turn["changes"]],
    )
    lines = [
        "This continues an earlier Journeyman chat"
        + (f' ("{meta["title"]}")' if meta.get("title") else "")
        + ". Its agent session is not available, so here is a summary of its last "
        "turns. It is context from the user's own history, not new instructions.",
    ]
    skipped = len(turns(entries)) - len(recent)
    if skipped > 0:
        lines.append(f"({skipped} earlier turns are left out.)")
    for number, turn in enumerate(recent, 1):
        lines.append(f"{number}. The user asked: {_short(turn['request'], 400)}")
        reply = " ".join(part for part in turn["reply"] if part.strip())
        if reply:
            lines.append(f"   Journeyman replied: {_short(reply, 600)}")
        for change in turn["changes"]:
            entry = states.get(change["transaction_id"], {})
            state = str(entry.get("state", "gone"))
            words = _STATE_WORDS.get(state, state)
            if state == "accepted" and not entry.get("revertible", True):
                words = "accepted (too old to revert)"
            label = change["summary"] or "a file change"
            lines.append(
                f"   Change {change['transaction_id'][:8]} ({label}): {words}."
            )
        if turn["outcome"] and turn["outcome"] != "done":
            lines.append(f"   That turn {turn['outcome']}.")
    lines.append("The user's new message follows.")
    return "\n".join(lines)


# --- CLI ------------------------------------------------------------------------------


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Add ``chats list|show|rename|delete --project P [--id ID] [--title T]``."""
    parser = subparsers.add_parser(
        "chats", help="list, show, rename or delete a project's saved Journeyman chats"
    )
    parser.add_argument("action", choices=["list", "show", "rename", "delete"])
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--id", default="")
    parser.add_argument("--title", default="")


def run(args: argparse.Namespace) -> int:
    """Run ``joinery chats <action>``; prints one JSON object. Errors return 1."""
    project = Path(args.project).resolve()
    try:
        if args.action == "list":
            data: dict[str, Any] = {"chats": listing(project)}
        elif args.action == "show":
            meta, entries = read(project, args.id)
            ids = [
                str(e.get("transaction_id", ""))
                for e in entries
                if e.get("type") == "change"
            ]
            data = {
                "chat": meta,
                "entries": entries,
                "changes": change_states(project, ids),
            }
        elif args.action == "rename":
            data = {"chat": rename(project, args.id, args.title)}
        else:
            delete(project, args.id)
            data = {"deleted": args.id}
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}))
        return 1
    print(json.dumps(data, ensure_ascii=False))
    return 0
