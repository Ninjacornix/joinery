"""Working on one project together, over git and nothing else (ADR 0071).

There is no Joinery server. The project's own repository (the Timeline,
``timeline.py``) gets a shared remote, and people pull and push it with their
own git credentials: whatever ssh key or credential helper git already uses.
Joinery never stores, asks for or passes on a password or token.

What this module adds on top of plain git:

* ``connect`` sets the ``origin`` remote and checks Git LFS, so art and audio
  are shared as LFS objects instead of bloating everyone's history.
* ``pull`` brings teammates' commits in. Their accepted changes keep their own
  author, so the Timeline names who made each one. A pull never touches a
  change waiting for review: those follow the new state the way a change
  follows a parallel task's accepted change (``rebase.refresh_review``), or the
  user is told which files now conflict. A pull waits while Journeyman is
  still making a change.
* A file both sides changed is never settled silently. Binary assets (models,
  paintings, images, sounds) are never merged: the project keeps the user's
  version and the teammate's version waits in Review as an ordinary change,
  with pictures of both, so Accept means "use theirs" and Discard "keep mine".
  Text files merge as git merges them; where both changed the same lines the
  user's lines stay and the teammate's lines wait in Review the same way.
* ``push`` shares the user's commits, and waits while such a choice is open.
* A teammate's recent change to a file is a warning before an agent edits it
  ("Ana changed art/door.blend 10 min ago"). A teammate may also claim files
  for a few hours: claims live in the ``joinery-claims`` branch of the shared
  remote (one small JSON file, no server) and agents refuse to edit a file
  someone else claimed until the claim is released or expires.
* A teammate's change to the art bible or the game brief needs the local
  user's approval again before asset work continues: the art gate
  (``art_approval.approved``) stays closed until the user approves it.

The command line entry is :func:`register`, wired into ``joinery`` by cli.py.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import struct
import subprocess
import sys
import tempfile
import time
import zipfile
import zlib
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from joinery import timeline
from joinery.platform_support import lock_file, unlock_file
from joinery.timeline import TimelineError, _git, _split_z, _text
from joinery.transactions import (
    PENDING,
    FileTransaction,
    atomic_json,
    list_transactions,
)

REMOTE = "origin"
#: The branch of the shared remote that holds claims; never checked out.
CLAIMS_BRANCH = "joinery-claims"
CLAIMS_FILE = "claims.json"
CLAIMS_REF = f"refs/remotes/{REMOTE}/{CLAIMS_BRANCH}"
#: Local bookkeeping, ignored by git (``.joinery/*``).
TEAM_DIR = ".joinery/team"
STATE_FILE = f"{TEAM_DIR}/state.json"
DOCUMENTS_FILE = f"{TEAM_DIR}/documents.json"
RECENT_FILE = f"{TEAM_DIR}/recent.json"
PICTURES = f"{TEAM_DIR}/pictures"
_SYNC_LOCK = f"{TEAM_DIR}/sync.lock"
#: Written into a transaction's folder when it holds a teammate's version.
CONFLICT_FILE = "team.json"
#: Documents a teammate's change sends back for the local user's approval.
SHARED_DOCUMENTS = {"art/bible.md": "art bible", "design/brief.md": "game brief"}
BIBLE = "art/bible.md"
APPROVAL = ".joinery/art-approval.json"
#: How far back a teammate's change to a file is worth a warning.
RECENT_WINDOW = 3 * 24 * 3600
CLAIM_HOURS = 4.0
MAX_CLAIM_HOURS = 72.0
LOCK_FILE = "joinery.lock"
#: Never merged line by line: the user picks one whole file.
BINARY_SUFFIXES = frozenset(
    {
        "blend",
        "blend1",
        "kra",
        "aseprite",
        "psd",
        "png",
        "jpg",
        "jpeg",
        "webp",
        "bmp",
        "tga",
        "exr",
        "hdr",
        "wav",
        "ogg",
        "mp3",
        "flac",
        "glb",
        "fbx",
        "sf2",
        "ttf",
        "otf",
        "zip",
    }
)
IMAGE_SUFFIXES = frozenset({"png", "jpg", "jpeg", "webp", "bmp", "tga"})
NOT_SHARED = (
    "This project isn't shared yet. Connect a remote: joinery team connect <remote>."
)
WORKING = (
    "Journeyman is still making a change. Pull when it is waiting for your review."
)
_FETCH_TIMEOUT = 120.0


class TeamError(TimelineError):
    """A team action could not be done; the message is for people."""


# --- Small helpers ------------------------------------------------------------------


def _project(path: Path) -> Path:
    project = path.resolve()
    if not (project / "project.godot").is_file():
        raise TeamError("Expected a Godot project.")
    return project


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _state(project: Path) -> dict[str, Any]:
    return _read_json(project / STATE_FILE)


def _save_state(project: Path, **values: Any) -> None:
    data = _state(project)
    data.update(values)
    atomic_json(project / STATE_FILE, data)


def ago(seconds: float) -> str:
    """How long ago, for people: "just now", "10 min ago", "3 h ago", "2 days ago"."""
    seconds = max(0.0, seconds)
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)} h ago"
    days = int(seconds // 86400)
    return f"{days} day{'s' if days != 1 else ''} ago"


def first_name(name: str) -> str:
    """The first name ("Ana" for "Ana Lima"), or the whole name of one word."""
    parts = name.split()
    return parts[0] if parts else (name or "A teammate")


def _ok(result: Any) -> bool:
    return bool(result.returncode == 0)


def _rev(project: Path, ref: str) -> str:
    got = _git(project, "rev-parse", "--verify", "-q", ref + "^{commit}", check=False)
    return _text(got).strip() if _ok(got) else ""


def remote_url(project: Path) -> str:
    got = _git(project, "remote", "get-url", REMOTE, check=False)
    return _text(got).strip() if _ok(got) else ""


def _branch(project: Path) -> str:
    got = _git(project, "symbolic-ref", "--short", "-q", "HEAD", check=False)
    branch = _text(got).strip()
    if not branch:
        raise TeamError(
            "The project isn't on a branch right now, so there is nothing to sync."
        )
    return branch


def _ready(project: Path) -> None:
    current = timeline.state(project)
    if current["state"] != "ready":
        raise TeamError(current.get("message") or "This project has no timeline yet.")
    if not remote_url(project):
        raise TeamError(NOT_SHARED)


def _me(project: Path) -> tuple[str, str]:
    return timeline.identity(project)


@contextlib.contextmanager
def _sync_lock(project: Path) -> Iterator[None]:
    """One pull, push or claim at a time per project, across processes."""
    path = project / _SYNC_LOCK
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+b")
    try:
        try:
            lock_file(stream)
        except (BlockingIOError, PermissionError, OSError):
            raise TeamError(
                "Another pull or push is running. Try again in a moment."
            ) from None
        try:
            yield
        finally:
            with contextlib.suppress(OSError):
                unlock_file(stream)
    finally:
        stream.close()


# --- Connecting -----------------------------------------------------------------------


def lfs_state(project: Path) -> dict[str, Any]:
    """Whether Git LFS is installed and the project tracks art and audio with it."""
    installed = _ok(_git(project, "lfs", "version", check=False))
    attributes = project / ".gitattributes"
    text = attributes.read_text(encoding="utf-8") if attributes.is_file() else ""
    tracked = "filter=lfs" in text
    message = ""
    if not installed:
        message = (
            "Git LFS isn't installed. Install it from git-lfs.com before sharing art "
            "and audio, or teammates get placeholder files instead of them."
        )
    elif not tracked:
        message = "Art and audio aren't tracked with Git LFS in this project."
    return {"installed": installed, "tracked": tracked, "message": message}


def _into_lfs(project: Path) -> list[str]:
    """Committed files LFS should hold but that are still plain blobs.

    Their current version moves to LFS (history before it stays as it was), so
    a teammate's clone doesn't carry them in every later revision.
    """
    tracked = _git(project, "ls-files", "-z").stdout
    if not tracked:
        return []
    got = _git(project, "check-attr", "-z", "filter", "--stdin", stdin=tracked)
    fields = _split_z(got.stdout)
    wanted = [
        fields[index]
        for index in range(0, len(fields) - 2, 3)
        if fields[index + 2] == "lfs"
    ]
    moved = []
    for path in wanted:
        head = _git(project, "cat-file", "-p", f":{path}", check=False).stdout[:200]
        if not head.startswith(timeline.LFS_POINTER):
            moved.append(path)
    return moved


def connect(project: Path, remote: str) -> dict[str, Any]:
    """Share the project through ``remote`` (a URL or a path) and check Git LFS.

    Starts the Timeline first when the project has none: connecting is the
    user's own request to keep the project in git. Credentials are git's own.
    """
    project = _project(project)
    remote = remote.strip()
    if not remote or any(ch in remote for ch in "\n\r\0"):
        raise TeamError("Give the remote's address, like git@host:team/game.git.")
    local = Path(remote).expanduser()
    if "://" not in remote and ":" not in remote.split("/", 1)[0] and local.exists():
        remote = str(local.resolve())
    current = timeline.state(project)
    if current["state"] in {"no-git", "nested"}:
        raise TeamError(current["message"])
    if current["state"] in {"none", "empty"}:
        timeline.start(project)
    existing = remote_url(project)
    if existing != remote:
        action = "set-url" if existing else "add"
        _git(project, "remote", action, REMOTE, remote)
    from joinery import vcs  # Late: catalog reads the engine's own files.

    lfs = lfs_state(project)
    if lfs["installed"]:
        _git(project, "lfs", "install", "--local", check=False)
        changed = vcs.ensure_lfs(project)
        moved = _into_lfs(project)
        if changed or moved:
            _git(project, "add", "--", ".gitattributes")
            if moved:
                _git(project, "add", "--renormalize", "--", *moved)
            _git(
                project,
                "commit",
                "--no-verify",
                "-q",
                "-m",
                "Keep art and audio in Git LFS",
                "--",
                ".gitattributes",
                *moved,
                identity=_me(project),
            )
        lfs = lfs_state(project)
    probe = _git(project, "ls-remote", "--heads", REMOTE, check=False)
    reachable = _ok(probe)
    _save_state(project, connected=time.time())
    data = status(project)
    data["lfs"] = lfs
    data["reachable"] = reachable
    if not reachable:
        data["message"] = (
            "Connected, but the remote didn't answer: "
            + timeline._reason(probe)
            + ". Check the address and that your git login can reach it."
        )
    elif not _text(probe).strip():
        data["message"] = "Connected. The remote is empty: Push to share the project."
    else:
        data["message"] = "Connected. Pull to bring in your teammates' work."
    return data


def _lfs_pull(project: Path) -> None:
    """``git lfs pull``: it writes the index, so git's optional locks stay on."""
    program = timeline.git_executable()
    if program is None:
        return
    env = timeline._environment()
    env.pop("GIT_OPTIONAL_LOCKS", None)
    with contextlib.suppress(OSError, subprocess.TimeoutExpired):
        subprocess.run(
            [program, "lfs", "pull"],
            cwd=project,
            env=env,
            capture_output=True,
            timeout=_FETCH_TIMEOUT,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )


def clone(remote: str, target: Path) -> dict[str, Any]:
    """Make a teammate's copy of a shared project in ``target`` (a new folder)."""
    target = target.expanduser().resolve()
    if target.exists() and any(target.iterdir()):
        raise TeamError(f"{target} isn't empty. Choose a new folder.")
    target.parent.mkdir(parents=True, exist_ok=True)
    _git(target.parent, "clone", "-q", remote.strip(), str(target))
    if _ok(_git(target, "lfs", "install", "--local", check=False)):
        # Without a global LFS install the clone holds placeholders: fetch the files.
        _lfs_pull(target)
    _save_state(target, connected=time.time(), last_fetch=time.time())
    refresh_recent(target)
    return status(target)


# --- Status ------------------------------------------------------------------------


def _counts(project: Path, upstream: str) -> tuple[int, int]:
    got = _git(
        project,
        "rev-list",
        "--left-right",
        "--count",
        f"HEAD...{upstream}",
        check=False,
    )
    parts = _text(got).split()
    if not _ok(got) or len(parts) != 2:
        return 0, 0
    return int(parts[0]), int(parts[1])


def teammates(project: Path) -> list[dict[str, Any]]:
    """People other than the user who appear in the project's history, latest first."""
    _name, email = _me(project)
    got = _git(
        project,
        "log",
        "--no-merges",
        "--max-count=5000",
        "--format=%an%x1f%ae%x1f%at",
        f"--exclude={CLAIMS_BRANCH}",
        "--branches",
        f"--exclude={REMOTE}/{CLAIMS_BRANCH}",
        "--remotes",
        check=False,
    )
    people: dict[str, dict[str, Any]] = {}
    for line in _text(got).splitlines():
        fields = line.split("\x1f")
        if len(fields) != 3 or fields[1].lower() == email.lower():
            continue
        name, mail, when = fields
        key = mail.lower()
        entry = people.setdefault(
            key, {"name": name, "email": mail, "last": 0, "commits": 0}
        )
        entry["commits"] += 1
        stamp = int(when) if when.isdigit() else 0
        if stamp > entry["last"]:
            entry["last"] = stamp
            entry["name"] = name
    return sorted(people.values(), key=lambda person: -int(person["last"]))


def status(project: Path, fetch: bool = False) -> dict[str, Any]:
    """Where sharing stands: the remote, commits to pull and push, teammates.

    With ``fetch`` the remote is asked first (the user's git credentials); a
    fetch that fails is reported in ``fetch_error`` and the rest still answers.
    """
    project = _project(project)
    current = timeline.state(project)
    result: dict[str, Any] = {
        "state": current["state"],
        "message": current.get("message", ""),
        "connected": False,
        "remote": "",
        "branch": "",
        "ahead": 0,
        "behind": 0,
        "teammates": [],
        "conflicts": [],
        "documents": pending_documents(project),
    }
    if current["state"] != "ready":
        return result
    name, email = _me(project)
    result["me"] = {"name": name, "email": email}
    result["remote"] = remote_url(project)
    result["connected"] = bool(result["remote"])
    with contextlib.suppress(TeamError):
        result["branch"] = _branch(project)
    if result["connected"] and fetch:
        try:
            _fetch(project)
        except TeamError as exc:
            result["fetch_error"] = str(exc)
    if result["connected"] and result["branch"]:
        upstream = f"{REMOTE}/{result['branch']}"
        if _rev(project, upstream):
            result["ahead"], result["behind"] = _counts(project, upstream)
        else:
            result["ahead"] = int(
                _text(_git(project, "rev-list", "--count", "HEAD", check=False)).strip()
                or 0
            )
            result["unpublished"] = True
    result["teammates"] = teammates(project)
    result["conflicts"] = [info for info in _conflicts(project)]
    saved = _state(project)
    result["last_fetch"] = float(saved.get("last_fetch", 0) or 0)
    if isinstance(saved.get("last_pull"), dict):
        result["last_pull"] = saved["last_pull"]
    if not result["connected"]:
        result["message"] = NOT_SHARED
    return result


def sync_summary(project: Path) -> dict[str, Any]:
    """What the Timeline's sync bar shows, from what git already knows (no fetch)."""
    url = remote_url(project)
    if not url:
        return {"connected": False}
    summary: dict[str, Any] = {
        "connected": True,
        "remote": url,
        "ahead": 0,
        "behind": 0,
    }
    with contextlib.suppress(TeamError):
        upstream = f"{REMOTE}/{_branch(project)}"
        if _rev(project, upstream):
            summary["ahead"], summary["behind"] = _counts(project, upstream)
        else:
            summary["unpublished"] = True
    summary["conflicts"] = len(_conflicts(project))
    summary["last_fetch"] = float(_state(project).get("last_fetch", 0) or 0)
    return summary


def last_pulled(project: Path) -> set[str]:
    """The commits the last pull brought in."""
    last = _state(project).get("last_pull")
    if not isinstance(last, dict) or not last.get("from") or not last.get("to"):
        return set()
    got = _git(project, "rev-list", f"{last['from']}..{last['to']}", check=False)
    return set(_text(got).split()) if _ok(got) else set()


def _fetch(project: Path) -> None:
    got = _git(project, "fetch", "--prune", "--quiet", REMOTE, check=False)
    if not _ok(got):
        raise TeamError(
            "Couldn't reach the shared remote. "
            + timeline._reason(got)
            + ". Your git login is used as it is set up."
        )
    _save_state(project, last_fetch=time.time())
    refresh_recent(project)


# --- Recent changes by teammates -----------------------------------------------------


def refresh_recent(project: Path, now: float | None = None) -> dict[str, Any]:
    """Each file a teammate changed lately: the newest change wins.

    Read from the commits the project has and those fetched from the shared
    remote (a teammate's pushed branch counts too), and cached for agents.
    """
    now = time.time() if now is None else now
    _name, email = _me(project)
    since = int(now - RECENT_WINDOW)
    got = _git(
        project,
        "log",
        "--no-merges",
        # Never a parent before its children: commits made in the same second keep
        # their order, so the newest change to a file is the one seen first.
        "--date-order",
        "-z",
        "--name-only",
        f"--since={since}",
        "--format=%x1e%H%x1f%an%x1f%ae%x1f%at%x1f",
        "HEAD",
        f"--exclude={REMOTE}/{CLAIMS_BRANCH}",
        "--remotes",
        check=False,
    )
    changes: dict[str, dict[str, Any]] = {}
    for chunk in got.stdout.split(b"\x1e") if _ok(got) else []:
        fields = chunk.split(b"\x1f")
        if len(fields) < 5:
            continue
        commit, author, mail, when = (f.decode("utf-8", "replace") for f in fields[:4])
        if mail.lower() == email.lower():
            continue
        stamp = int(when) if when.isdigit() else 0
        for path in _split_z(b"\x1f".join(fields[4:])):
            path = path.strip("\n")
            if not path or (path in changes and changes[path]["time"] >= stamp):
                continue
            changes[path] = {
                "author": author,
                "email": mail,
                "time": stamp,
                "commit": commit,
            }
    pulled = set(
        _text(
            _git(project, "rev-list", f"--since={since}", "HEAD", check=False)
        ).split()
    )
    for entry in changes.values():
        entry["pulled"] = entry.pop("commit") in pulled
    data = {"computed": now, "changes": changes}
    atomic_json(project / RECENT_FILE, data)
    return data


def recent(
    project: Path, paths: Sequence[str], now: float | None = None
) -> list[dict[str, Any]]:
    """Teammates' recent changes to ``paths``, from the cache ``refresh_recent`` keeps."""
    now = time.time() if now is None else now
    cached = _read_json(project / RECENT_FILE).get("changes", {})
    found = []
    for path in sorted(set(paths)):
        entry = cached.get(path) if isinstance(cached, dict) else None
        if not isinstance(entry, dict):
            continue
        if now - float(entry.get("time", 0)) > RECENT_WINDOW:
            continue
        found.append({"path": path, **entry})
    return found


def recent_note(entry: dict[str, Any], now: float | None = None) -> str:
    """The warning for one change, like "Ana changed art/door.blend 10 min ago."."""
    now = time.time() if now is None else now
    who = first_name(str(entry.get("author", "")))
    line = f"{who} changed {entry['path']} {ago(now - float(entry.get('time', 0)))}"
    if not entry.get("pulled", True):
        return line + ", and that change isn't pulled yet. Pull before changing it."
    return line + "."


# --- Shared documents ------------------------------------------------------------------


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""


def pending_documents(project: Path) -> list[dict[str, Any]]:
    """The art bible or brief a teammate changed, still waiting for the user's approval."""
    data = _read_json(project / DOCUMENTS_FILE)
    found = []
    for path, label in SHARED_DOCUMENTS.items():
        entry = data.get(path)
        if isinstance(entry, dict):
            found.append({"path": path, "label": label, **entry})
    return found


def documents_waiting(project: Path) -> bool:
    return bool(_read_json(project / DOCUMENTS_FILE))


def approve_document(project: Path, path: str) -> dict[str, Any]:
    """The user read a teammate's change to ``path`` and approves it.

    The art bible is approved where its rules are shown (/art, which calls
    ``art_approval.approve``); this approves the other shared documents.
    """
    project = _project(project)
    if path == BIBLE:
        raise TeamError("Approve the art bible in /art, where its rules are shown.")
    if path not in SHARED_DOCUMENTS:
        raise TeamError(f"{path} doesn't need an approval.")
    data = _read_json(project / DOCUMENTS_FILE)
    data.pop(path, None)
    atomic_json(project / DOCUMENTS_FILE, data)
    return {"approved": path, "documents": pending_documents(project)}


def clear_document(project: Path, path: str) -> None:
    """Called by ``art_approval.approve``: the user approved the bible as it is now."""
    data = _read_json(project / DOCUMENTS_FILE)
    if path in data:
        data.pop(path)
        atomic_json(project / DOCUMENTS_FILE, data)


def gate_message(project: Path) -> str:
    """Why asset work waits for a teammate's change to the bible or brief ("" if not)."""
    waiting = pending_documents(project)
    if not waiting:
        return ""
    first = waiting[0]
    who = first_name(str(first.get("author", "")))
    names = " and the ".join(str(item["label"]) for item in waiting)
    where = "/art" if any(item["path"] == BIBLE for item in waiting) else "Review"
    return (
        f"{who} changed the {names}. Read the change and approve it ({where}) "
        "before new art or audio is made."
    )


def _flag_documents(
    project: Path, before: dict[str, str], old: str, new: str
) -> list[str]:
    _name, email = _me(project)
    data = _read_json(project / DOCUMENTS_FILE)
    flagged = []
    for path in SHARED_DOCUMENTS:
        now = _digest(project / path)
        if not now or now == before.get(path, ""):
            continue
        got = _git(
            project,
            "log",
            "-1",
            "--format=%an%x1f%ae%x1f%at",
            f"{old}..{new}",
            "--",
            path,
            check=False,
        )
        fields = _text(got).strip().split("\x1f")
        if len(fields) != 3 or fields[1].lower() == email.lower():
            continue
        data[path] = {
            "sha256": now,
            "author": fields[0],
            "email": fields[1],
            "time": int(fields[2]) if fields[2].isdigit() else 0,
        }
        flagged.append(path)
    if flagged:
        atomic_json(project / DOCUMENTS_FILE, data)
    return flagged


# --- Pictures of a binary file ------------------------------------------------------------


def _png(width: int, height: int, rgba: bytes) -> bytes:
    def chunk(kind: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body))
            + kind
            + body
            + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
        )

    stride = width * 4
    rows = b"".join(
        b"\0" + rgba[row * stride : (row + 1) * stride] for row in range(height)
    )
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def blend_thumbnail(data: bytes) -> bytes | None:
    """The preview Blender saves inside a .blend (its TEST block) as a PNG, or None."""
    if data[:2] == b"\x1f\x8b":
        try:
            import gzip

            data = gzip.decompress(data)
        except (OSError, EOFError):
            return None
    if not data.startswith(b"BLENDER"):
        return None
    if data[7:9].isdigit():
        # Blender 5's header: BLENDER, header size, -, format version, v, version.
        size = int(data[7:9])
        little = data[size - 5 : size - 4] == b"v"
        order = "<" if little else ">"
        offset, head = size, 32
        layout = order + "4siQqq"
    else:
        wide = data[7:8] == b"-"
        order = "<" if data[8:9] == b"v" else ">"
        offset, head = 12, 24 if wide else 20
        layout = order + ("4siQii" if wide else "4siIii")
    while offset + head <= len(data):
        fields = struct.unpack_from(layout, data, offset)
        if data[7:9].isdigit():
            code, _sdna, _old, length, _count = fields
        else:
            code, length = fields[0], fields[1]
        body = offset + head
        if code == b"TEST":
            if length < 8:
                return None
            width, height = struct.unpack_from(order + "ii", data, body)
            pixels = data[body + 8 : body + 8 + width * height * 4]
            if width <= 0 or height <= 0 or len(pixels) != width * height * 4:
                return None
            stride = width * 4
            # Blender keeps the bottom row first.
            flipped = b"".join(
                pixels[row * stride : (row + 1) * stride]
                for row in reversed(range(height))
            )
            return _png(width, height, flipped)
        if code in {b"ENDB", b"DNA1"} or length < 0:
            return None
        offset = body + int(length)
    return None


def picture(data: bytes | None, path: str) -> tuple[bytes, str] | None:
    """A picture of one version of ``path``: the image itself, a Krita painting's
    merged image or a Blender file's saved preview. None when there is none."""
    if not data:
        return None
    suffix = PurePosixPath(path).suffix.lower().lstrip(".")
    if suffix in IMAGE_SUFFIXES:
        return data, suffix
    if suffix == "kra":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                for name in ("mergedimage.png", "preview.png"):
                    if name in archive.namelist():
                        return archive.read(name), "png"
        except (zipfile.BadZipFile, OSError, KeyError):
            return None
        return None
    if suffix == "blend":
        thumbnail = blend_thumbnail(data)
        return (thumbnail, "png") if thumbnail else None
    return None


# --- Pulling ----------------------------------------------------------------------------


@dataclass
class Conflict:
    """One file both sides changed: the user's version stays, theirs waits in Review."""

    path: str
    kind: str  # "binary" or "text"
    mine: bytes | None
    theirs: bytes | None


def _stage_blob(project: Path, stage: int, path: str) -> bytes | None:
    spec = f":{stage}:{path}"
    if not _ok(_git(project, "cat-file", "-e", spec, check=False)):
        return None
    return bytes(_git(project, "cat-file", "--filters", spec).stdout)


def _binary(project: Path, path: str, *versions: bytes | None) -> bool:
    if PurePosixPath(path).suffix.lower().lstrip(".") in BINARY_SUFFIXES:
        return True
    attributes = _text(
        _git(project, "check-attr", "filter", "text", "--", path, check=False)
    )
    if "filter: lfs" in attributes or "text: unset" in attributes:
        return True
    return any(version is not None and b"\0" in version[:8000] for version in versions)


def _merge_file(
    project: Path, side: str, mine: bytes, base: bytes, theirs: bytes
) -> bytes:
    folder = project / TEAM_DIR / "merge"
    folder.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=folder) as temporary:
        root = Path(temporary)
        names = []
        for name, data in (("mine", mine), ("base", base), ("theirs", theirs)):
            (root / name).write_bytes(data)
            names.append(str(root / name))
        got = _git(project, "merge-file", "-p", f"--{side}", *names, check=False)
        if got.returncode < 0 or got.returncode > 127:
            raise TeamError(timeline._reason(got))
        return bytes(got.stdout)


def _write(project: Path, path: str, data: bytes | None) -> None:
    target = timeline.from_git_path(project, path)
    if data is None:
        target.unlink(missing_ok=True)
        _git(project, "rm", "-q", "--cached", "--ignore-unmatch", "--", path)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    _git(project, "add", "--", path)


def _settle(project: Path, path: str, mine_touched: set[str]) -> Conflict | None:
    """Leave the user's side of one conflicted file in place; None when merged."""
    base, mine, theirs = (_stage_blob(project, n, path) for n in (1, 2, 3))
    if path == LOCK_FILE and mine is not None and theirs is not None:
        from joinery.rebase import merge_lock  # Late: rebase imports transactions.

        try:
            merged = merge_lock(theirs.decode(), mine.decode(), mine_touched)
        except (UnicodeDecodeError, ValueError):
            pass
        else:
            _write(project, path, merged.encode())
            return None
    if (
        mine is not None
        and theirs is not None
        and not _binary(project, path, base, mine, theirs)
    ):
        ours = _merge_file(project, "ours", mine, base or b"", theirs)
        others = _merge_file(project, "theirs", mine, base or b"", theirs)
        _write(project, path, ours)
        if ours == others:
            return None
        return Conflict(path, "text", ours, others)
    _write(project, path, mine)
    return Conflict(path, "binary" if (mine or theirs) else "text", mine, theirs)


def _dirty(project: Path) -> set[str]:
    out = _git(project, "status", "--porcelain", "-z", "--untracked-files=all").stdout
    paths: set[str] = set()
    parts = _split_z(out)
    index = 0
    while index < len(parts):
        entry = parts[index]
        index += 1
        if len(entry) > 3:
            paths.add(entry[3:])
            if entry[:1] in {"R", "C"} and index < len(parts):
                index += 1
    return paths


def _changed(project: Path, old: str, new: str) -> set[str]:
    return set(
        _split_z(
            _git(project, "diff", "--name-only", "-z", old, new, check=False).stdout
        )
    )


def _authors(project: Path, old: str, new: str) -> list[str]:
    got = _git(
        project, "log", "--no-merges", "--format=%an", f"{old}..{new}", check=False
    )
    names: list[str] = []
    for name in _text(got).splitlines():
        if name and name not in names:
            names.append(name)
    return names


def _names(authors: Sequence[str]) -> str:
    firsts = [first_name(name) for name in authors] or ["your teammates"]
    if len(firsts) == 1:
        return firsts[0]
    return ", ".join(firsts[:-1]) + " and " + firsts[-1]


def _conflicts(project: Path) -> list[dict[str, Any]]:
    """Teammates' versions waiting in Review for the user's choice."""
    found = []
    for tx in list_transactions(project):
        if tx.state != "review":
            continue
        info = conflict_info(tx)
        if info:
            found.append({"id": tx.id, "summary": tx.summary, **info})
    return found


def conflict_info(tx: FileTransaction) -> dict[str, Any]:
    """The team facts of a change holding a teammate's version ({} for any other)."""
    data = _read_json(tx.directory / CONFLICT_FILE)
    return {"team": data} if data else {}


def is_conflict(directory: Path) -> bool:
    return (directory / CONFLICT_FILE).is_file()


def _stage_conflicts(
    project: Path, conflicts: list[Conflict], theirs_name: str, commit: str
) -> FileTransaction | None:
    who = first_name(theirs_name)
    if len(conflicts) == 1:
        summary = f"{who}'s {PurePosixPath(conflicts[0].path).name} or yours"
    else:
        summary = f"{who}'s version of {len(conflicts)} files or yours"
    tx = FileTransaction(project)
    tx.note_tool("joinery")
    tx.begin(summary)
    try:
        for item in conflicts:
            target = timeline.from_git_path(tx.stage, item.path)
            if item.theirs is None:
                target.unlink(missing_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(item.theirs)
        try:
            tx.review()
        except ValueError:
            tx.reject()
            return None
        files = []
        for item in conflicts:
            entry: dict[str, Any] = {
                "path": item.path,
                "kind": item.kind,
                "mine_size": len(item.mine) if item.mine is not None else -1,
                "theirs_size": len(item.theirs) if item.theirs is not None else -1,
            }
            for side, data in (("mine", item.mine), ("theirs", item.theirs)):
                shown = picture(data, item.path)
                if shown is None:
                    continue
                image, suffix = shown
                target = (
                    project
                    / PICTURES
                    / tx.id
                    / side
                    / (PurePosixPath(item.path).stem + "." + suffix)
                )
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(image)
                entry[f"{side}_picture"] = str(target)
            files.append(entry)
        atomic_json(
            tx.directory / CONFLICT_FILE,
            {
                "kind": "conflict",
                "theirs": theirs_name,
                "commit": commit,
                "files": files,
            },
        )
    except BaseException:
        if tx.state in {"staging", "review"}:
            tx.reject()
        raise
    return tx


def _follow_reviews(project: Path, skip: set[str]) -> dict[str, list[dict[str, Any]]]:
    """Changes waiting for review follow the pulled state, or say what conflicts."""
    from joinery.rebase import refresh_review  # Late: rebase imports transactions.

    followed: list[dict[str, Any]] = []
    conflicting: list[dict[str, Any]] = []
    for tx in list_transactions(project):
        if tx.state != "review" or tx.id in skip:
            continue
        try:
            left = refresh_review(tx)
        except (OSError, ValueError) as exc:
            conflicting.append(
                {"id": tx.id, "summary": tx.summary, "paths": [], "why": str(exc)}
            )
            continue
        if left:
            conflicting.append({"id": tx.id, "summary": tx.summary, "paths": left})
        else:
            followed.append({"id": tx.id, "summary": tx.summary})
    return {"followed": followed, "conflicting": conflicting}


def _set_aside_approval(project: Path, incoming: Sequence[str]) -> bytes | None:
    """The user's own art approval, when a teammate's commit carries theirs too.

    The approval is each person's own: the committed file is put back so the
    pull can go ahead, and the user's approval returns afterwards while it still
    matches the bible (``_put_back_approval``).
    """
    if APPROVAL not in incoming:
        return None
    path = project / APPROVAL
    saved = path.read_bytes() if path.is_file() else None
    if _ok(_git(project, "cat-file", "-e", f"HEAD:{APPROVAL}", check=False)):
        _git(project, "checkout", "-q", "HEAD", "--", APPROVAL, check=False)
    else:
        path.unlink(missing_ok=True)
    return saved


def _put_back_approval(project: Path, saved: bytes | None) -> None:
    if saved is None:
        return
    try:
        data = json.loads(saved.decode("utf-8"))
    except ValueError:
        return
    if isinstance(data, dict) and data.get("bible_sha256") == _digest(project / BIBLE):
        (project / APPROVAL).write_bytes(saved)


def _still_working(project: Path) -> bool:
    """A change is being made right now (not one left behind by a closed session)."""
    if not any(tx.state in PENDING - {"review"} for tx in list_transactions(project)):
        return False
    from joinery.transactions import ProjectLease

    try:
        lease = ProjectLease(project, wait=0.0)
    except ValueError:
        return True  # A chat host holds the project: its change is still being made.
    lease.close()
    return False


def pull(project: Path) -> dict[str, Any]:
    """Bring in what teammates pushed. See the module notes for the rules it keeps."""
    project = _project(project)
    _ready(project)
    with _sync_lock(project):
        if _still_working(project):
            raise TeamError(WORKING)
        branch = _branch(project)
        _fetch(project)
        upstream = f"{REMOTE}/{branch}"
        theirs = _rev(project, upstream)
        old = _rev(project, "HEAD")
        result: dict[str, Any] = {
            "state": "current",
            "count": 0,
            "authors": [],
            "conflicts": [],
            "documents": [],
            "reviews": {"followed": [], "conflicting": []},
        }
        if not theirs:
            result["message"] = (
                "Nothing is shared on this branch yet. Push to share it."
            )
            return result
        incoming = _text(_git(project, "rev-list", f"HEAD..{theirs}")).split()
        made = _text(
            _git(project, "rev-list", "--no-merges", f"HEAD..{theirs}")
        ).split()
        if not incoming:
            result["message"] = "You have everything your teammates pushed."
            return result
        base = _text(_git(project, "merge-base", "HEAD", theirs, check=False)).strip()
        overlap = sorted(_dirty(project) & _changed(project, base or old, theirs))
        approval = _set_aside_approval(project, overlap)
        overlap = [path for path in overlap if path != APPROVAL]
        if overlap:
            _put_back_approval(project, approval)
            raise TeamError(
                f"Your own edits to {', '.join(overlap[:3])} aren't in the Timeline yet, "
                "and your teammates changed "
                + ("it" if len(overlap) == 1 else "them")
                + " too. Save or undo those edits, then pull."
            )
        documents = {path: _digest(project / path) for path in SHARED_DOCUMENTS}
        authors = _authors(project, old, theirs)
        conflicts: list[Conflict] = []
        try:
            if base == old:
                _git(project, "merge", "--ff-only", "-q", theirs)
            else:
                conflicts = _merge(project, theirs, authors, base)
        finally:
            _put_back_approval(project, approval)
        new = _rev(project, "HEAD")
        result.update(
            state="pulled",
            count=len(made) or len(incoming),
            authors=authors,
            commit=new,
        )
        _save_state(
            project,
            last_pull={"from": old, "to": new, "time": time.time(), "authors": authors},
        )
        refresh_recent(project)
        result["documents"] = _flag_documents(project, documents, old, new)
        review = (
            _stage_conflicts(project, conflicts, authors[0] if authors else "", theirs)
            if conflicts
            else None
        )
        if review is not None:
            result["review_id"] = review.id
            result["conflicts"] = [
                {"path": item.path, "kind": item.kind} for item in conflicts
            ]
        result["reviews"] = _follow_reviews(
            project, {review.id} if review is not None else set()
        )
        result["message"] = describe_pull(result)
        return result


def _merge(project: Path, theirs: str, authors: list[str], base: str) -> list[Conflict]:
    ident = _me(project)
    mine_touched = _changed(project, base, "HEAD") if base else set()
    got = _git(
        project,
        "merge",
        "--no-ff",
        "--no-commit",
        "--no-edit",
        "-q",
        theirs,
        check=False,
        identity=ident,
    )
    unmerged = sorted(
        set(
            _split_z(
                _git(project, "diff", "--name-only", "-z", "--diff-filter=U").stdout
            )
        )
    )
    if not _ok(got) and not unmerged:
        _git(project, "merge", "--abort", check=False)
        raise TeamError(timeline._reason(got))
    conflicts: list[Conflict] = []
    try:
        for path in unmerged:
            settled = _settle(project, path, mine_touched)
            if settled is not None:
                conflicts.append(settled)
        _git(
            project,
            "commit",
            "--no-verify",
            "-q",
            "-m",
            f"Bring in {_names(authors)}'s changes",
            identity=ident,
        )
    except BaseException:
        _git(project, "merge", "--abort", check=False)
        raise
    return conflicts


def describe_pull(data: dict[str, Any]) -> str:
    if data.get("state") != "pulled":
        return str(data.get("message", ""))
    count = int(data.get("count", 0))
    line = f"Pulled {count} change{'s' if count != 1 else ''} from {_names(data.get('authors', []))}."
    conflicts = data.get("conflicts", [])
    if conflicts:
        names = ", ".join(PurePosixPath(item["path"]).name for item in conflicts[:3])
        line += f" You both changed {names}: yours stays, theirs waits in Review."
    for item in data.get("reviews", {}).get("conflicting", []):
        paths = ", ".join(item.get("paths", [])[:3]) or "its files"
        line += (
            f" “{item['summary']}” waits for review and also changed {paths}: "
            "discard it or ask Journeyman to redo it."
        )
    if data.get("documents"):
        labels = " and the ".join(SHARED_DOCUMENTS[path] for path in data["documents"])
        line += f" The {labels} changed: approve it before new art or audio."
    return line


# --- Pushing ------------------------------------------------------------------------------


def push(project: Path) -> dict[str, Any]:
    """Share the user's commits. Waits while a teammate's version waits in Review."""
    project = _project(project)
    _ready(project)
    with _sync_lock(project):
        waiting = _conflicts(project)
        if waiting:
            raise TeamError(
                f"Choose between {waiting[0]['summary']} in Review first, then push."
            )
        branch = _branch(project)
        _fetch(project)
        upstream = f"{REMOTE}/{branch}"
        if _rev(project, upstream):
            ahead, behind = _counts(project, upstream)
            if behind:
                raise TeamError(
                    f"Your teammates pushed {behind} change{'s' if behind != 1 else ''} "
                    "since your last pull. Pull, then push."
                )
            if not ahead:
                return {
                    "state": "current",
                    "count": 0,
                    "message": "Nothing new to push.",
                }
        else:
            ahead = int(
                _text(_git(project, "rev-list", "--count", "HEAD")).strip() or 0
            )
        got = _git(
            project,
            "push",
            "-q",
            "-u",
            REMOTE,
            f"HEAD:refs/heads/{branch}",
            check=False,
        )
        if not _ok(got):
            raise TeamError(
                "Couldn't push. "
                + timeline._reason(got)
                + ". Your git login is used as it is set up."
            )
        _save_state(project, last_push=time.time())
        return {
            "state": "pushed",
            "count": ahead,
            "message": f"Pushed {ahead} change{'s' if ahead != 1 else ''}.",
        }


# --- Claims ---------------------------------------------------------------------------------


def _load_claims(project: Path, ref: str = CLAIMS_REF) -> list[dict[str, Any]]:
    got = _git(project, "cat-file", "blob", f"{ref}:{CLAIMS_FILE}", check=False)
    if not _ok(got):
        return []
    try:
        data = json.loads(got.stdout.decode("utf-8"))
    except ValueError:
        return []
    items = data.get("claims", []) if isinstance(data, dict) else []
    return [item for item in items if isinstance(item, dict) and item.get("path")]


def active_claims(project: Path, now: float | None = None) -> list[dict[str, Any]]:
    """Unexpired claims as last fetched from the shared remote."""
    now = time.time() if now is None else now
    return [c for c in _load_claims(project) if float(c.get("until", 0)) > now]


_CLAIM_CACHE: dict[str, tuple[float, list[dict[str, Any]], str]] = {}


def teammate_claim(project: Path, paths: set[str]) -> dict[str, Any] | None:
    """The first of ``paths`` a teammate claims, for agents (cached for a few seconds)."""
    if not paths:
        return None
    key = str(project)
    now = time.time()
    cached = _CLAIM_CACHE.get(key)
    if cached is None or now - cached[0] > 15:
        try:
            if not (project / ".git").exists() or not remote_url(project):
                claims, email = [], ""
            else:
                claims, email = active_claims(project, now), _me(project)[1]
        except TimelineError:
            claims, email = [], ""
        cached = (now, claims, email)
        _CLAIM_CACHE[key] = cached
    _stamp, claims, email = cached
    for claim in claims:
        if (
            claim.get("path") in paths
            and str(claim.get("email", "")).lower() != email.lower()
            and float(claim.get("until", 0)) > now
        ):
            return claim
    return None


def claim_refusal(claim: dict[str, Any]) -> str:
    who = first_name(str(claim.get("name", "")))
    until = datetime.fromtimestamp(float(claim.get("until", 0)))
    note = str(claim.get("note", "")).strip()
    return (
        f"{claim['path']} is claimed by {who} until {until:%H:%M}"
        + (f" ({note})" if note else "")
        + ". Work on something else, or ask them to release it."
    )


def _write_claims(project: Path, claims: list[dict[str, Any]], parent: str) -> bool:
    """Commit ``claims`` on top of ``parent`` and push it; False when someone was first."""
    body = json.dumps({"version": 1, "claims": claims}, indent=2, sort_keys=True) + "\n"
    blob = _text(
        _git(project, "hash-object", "-w", "--stdin", stdin=body.encode())
    ).strip()
    tree = _text(
        _git(project, "mktree", stdin=f"100644 blob {blob}\t{CLAIMS_FILE}\n".encode())
    ).strip()
    args = ["commit-tree", tree, "-m", "Update claims"]
    if parent:
        args += ["-p", parent]
    commit = _text(_git(project, *args, identity=_me(project))).strip()
    got = _git(
        project,
        "push",
        "-q",
        REMOTE,
        f"{commit}:refs/heads/{CLAIMS_BRANCH}",
        check=False,
    )
    if not _ok(got):
        text = (got.stderr or b"").decode("utf-8", "replace")
        if "rejected" in text or "fetch first" in text or "non-fast-forward" in text:
            return False
        raise TeamError("Couldn't share the claim. " + timeline._reason(got) + ".")
    _git(project, "update-ref", CLAIMS_REF, commit)
    _CLAIM_CACHE.pop(str(project), None)
    return True


def _fetch_claims(project: Path) -> str:
    got = _git(
        project,
        "fetch",
        "--quiet",
        REMOTE,
        f"+refs/heads/{CLAIMS_BRANCH}:{CLAIMS_REF}",
        check=False,
    )
    if not _ok(got):
        text = (got.stderr or b"").decode("utf-8", "replace")
        if "couldn't find remote ref" not in text:
            raise TeamError("Couldn't read claims. " + timeline._reason(got) + ".")
        _git(project, "update-ref", "-d", CLAIMS_REF, check=False)
    return _rev(project, CLAIMS_REF)


def _paths(project: Path, paths: Sequence[str]) -> list[str]:
    found = []
    for path in paths:
        relative = timeline._check_path(path.removeprefix("res://"))
        if relative not in found:
            found.append(relative)
    if not found:
        raise TeamError("Name the files with --path.")
    return found


def claim(
    project: Path, paths: Sequence[str], hours: float = CLAIM_HOURS, note: str = ""
) -> dict[str, Any]:
    """Claim ``paths`` for ``hours`` so teammates' agents leave them alone."""
    project = _project(project)
    _ready(project)
    if not 0 < hours <= MAX_CLAIM_HOURS:
        raise TeamError(f"Claim files for up to {int(MAX_CLAIM_HOURS)} hours.")
    wanted = _paths(project, paths)
    name, email = _me(project)
    with _sync_lock(project):
        for _attempt in range(4):
            parent = _fetch_claims(project)
            now = time.time()
            current = [
                c for c in _load_claims(project) if float(c.get("until", 0)) > now
            ]
            for item in current:
                if (
                    item["path"] in wanted
                    and str(item.get("email", "")).lower() != email.lower()
                ):
                    raise TeamError(claim_refusal(item))
            kept = [c for c in current if c["path"] not in wanted]
            until = now + hours * 3600
            mine = [
                {
                    "path": path,
                    "name": name,
                    "email": email,
                    "since": now,
                    "until": until,
                    **({"note": note.strip()[:200]} if note.strip() else {}),
                }
                for path in wanted
            ]
            if _write_claims(project, kept + mine, parent):
                return {"claimed": wanted, "until": until, "claims": kept + mine}
    raise TeamError("Teammates kept changing the claims. Try again in a moment.")


def release(project: Path, paths: Sequence[str] = ()) -> dict[str, Any]:
    """Release the user's claims on ``paths`` (all of them when none are named)."""
    project = _project(project)
    _ready(project)
    _name, email = _me(project)
    wanted = set(_paths(project, paths)) if paths else set()
    with _sync_lock(project):
        for _attempt in range(4):
            parent = _fetch_claims(project)
            if not parent:
                return {"released": [], "claims": []}
            now = time.time()
            current = [
                c for c in _load_claims(project) if float(c.get("until", 0)) > now
            ]
            mine = [
                c
                for c in current
                if str(c.get("email", "")).lower() == email.lower()
                and (not wanted or c["path"] in wanted)
            ]
            kept = [c for c in current if c not in mine]
            if not mine:
                return {"released": [], "claims": kept}
            if _write_claims(project, kept, parent):
                return {"released": [c["path"] for c in mine], "claims": kept}
    raise TeamError("Teammates kept changing the claims. Try again in a moment.")


def claims_list(project: Path, fetch: bool = True) -> dict[str, Any]:
    project = _project(project)
    _ready(project)
    if fetch:
        _fetch_claims(project)
    _CLAIM_CACHE.pop(str(project), None)
    return {"claims": active_claims(project)}


# --- Command line ---------------------------------------------------------------------------


def _describe(action: str, data: dict[str, Any]) -> str:
    if action == "status":
        if not data.get("connected"):
            return str(data.get("message", ""))
        lines = [f"Shared with {data['remote']} ({data.get('branch', '')})"]
        lines.append(f"To pull: {data['behind']}  To push: {data['ahead']}")
        people = [person["name"] for person in data.get("teammates", [])]
        lines.append("Teammates: " + (", ".join(people) or "none seen yet"))
        if data.get("fetch_error"):
            lines.append(str(data["fetch_error"]))
        return "\n".join(lines)
    if action in {"claim", "release", "claims"}:
        rows = [
            f"{c['path']}  {c.get('name', '')} until "
            f"{datetime.fromtimestamp(float(c.get('until', 0))):%H:%M}"
            for c in data.get("claims", [])
        ]
        return "\n".join(rows) or "No claims."
    if action == "recent":
        return "\n".join(recent_note(entry) for entry in data.get("changes", [])) or (
            "No teammate changed these lately."
        )
    return str(data.get("message", "")) or json.dumps(data)


def run(args: argparse.Namespace) -> int:
    """Run ``joinery team <action>``. Errors print as JSON and return 1."""
    try:
        data: dict[str, Any]
        if args.action == "clone":
            if not args.remote or args.target is None:
                raise TeamError(
                    "Give the remote and a new folder: team clone <remote> --target <folder>."
                )
            data = clone(args.remote, args.target)
        else:
            project = _project(args.project)
            if args.action == "connect":
                if not args.remote:
                    raise TeamError("Give the remote: joinery team connect <remote>.")
                data = connect(project, args.remote)
            elif args.action == "status":
                data = status(project, fetch=args.fetch)
            elif args.action == "pull":
                data = pull(project)
            elif args.action == "push":
                data = push(project)
            elif args.action == "claim":
                data = claim(project, args.path or [], args.hours, args.note)
            elif args.action == "release":
                data = release(project, args.path or [])
            elif args.action == "claims":
                data = claims_list(project)
            elif args.action == "recent":
                if args.fetch:
                    _fetch(project)
                else:
                    refresh_recent(project)
                data = {"changes": recent(project, args.path or [])}
            else:
                if not args.path:
                    raise TeamError("Name the document with --path.")
                data = approve_document(project, args.path[0])
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}))
        if not args.json:
            print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(data) if args.json else _describe(args.action, data))
    return 0


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Add ``team connect|status|pull|push|claim|release|claims|recent|approve|clone``."""
    parser = subparsers.add_parser(
        "team",
        help="work together through a shared git remote: connect, pull, push, claims",
    )
    parser.add_argument(
        "action",
        choices=[
            "connect",
            "status",
            "pull",
            "push",
            "claim",
            "release",
            "claims",
            "recent",
            "approve",
            "clone",
        ],
    )
    parser.add_argument(
        "remote", nargs="?", default="", help="connect, clone: the remote"
    )
    parser.add_argument("--project", type=Path, default=Path("."))
    parser.add_argument("--target", type=Path, help="clone: the new folder")
    parser.add_argument(
        "--path",
        action="append",
        default=None,
        help="claim, release, recent: a file (repeat); approve: the document",
    )
    parser.add_argument(
        "--hours", type=float, default=CLAIM_HOURS, help="claim: how long it lasts"
    )
    parser.add_argument("--note", default="", help="claim: what you're doing")
    parser.add_argument(
        "--fetch", action="store_true", help="status, recent: ask the remote first"
    )
    parser.add_argument("--json", action="store_true")
