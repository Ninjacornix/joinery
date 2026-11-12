"""The project timeline: every accepted change as a git commit.

A Joinery project already keeps restore data for its recent accepted changes
(``transactions.py``), but that is bounded and local. The timeline is the long
memory: each accepted Journeyman change, and each revert, becomes one plain git
commit in the project's own repository, so the editor's Timeline tab can show
how the game changed over months and put any file back as it was.

Rules this module keeps:

* Only the files of that change are committed (``git commit --only``), never
  the user's other edits, staged or not.
* The author and committer are the user's git identity, or a local fallback
  ("Joinery user"), never an agent's. Identity variables in the environment are
  replaced, so an agent process cannot lend its own.
* A project gets a repository only when it is being created, or when the user
  asks for one in the Timeline tab. An existing project is never put under git
  behind the user's back, and a project inside someone else's repository is
  left alone.
* Restoring a file never rewrites history: it stages an ordinary reviewed
  Joinery change with the old bytes, and the user accepts it like any other.
* Git missing, or a git command failing, never fails the change itself: the
  caller gets a short reason to show.

The command line entry is :func:`register`, wired into ``joinery`` by cli.py.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from collections.abc import Iterator, Sequence
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

#: Accepted and reverted changes say where they were decided, in plain text.
ACCEPTED_LINE = "Accepted in Joinery"
REVERTED_LINE = "Reverted in Joinery"
STARTED_MESSAGE = "Timeline starts here"
#: The author when the user has no git identity. Never an agent's name.
FALLBACK_NAME = "Joinery user"
FALLBACK_EMAIL = "joinery-user@localhost"
#: Entries per page in the Timeline tab.
PAGE = 50
#: The creation steps (setup, brief, art bible) fold into the "Start" commit
#: while this marker says it is the newest commit and it is this young.
START_MARKER = ".joinery/timeline-start.json"
START_WINDOW = 15 * 60
#: Cached before/after images of a file, for the Timeline tab to show.
CACHE = ".joinery/timeline"
#: The ignore rules a Joinery project needs: the editor's cache and Joinery's
#: restore data stay out; the game record and the art approval are kept.
IGNORE_MARKER = "# Joinery: restore data and caches stay out of git"
IGNORE_RULES = (
    ".godot/",
    ".joinery/*",
    "!.joinery/game-manifest.json",
    "!.joinery/art-approval.json",
    "!.joinery/commands/",
)
#: Text diffs longer than this are cut, with a note.
DIFF_LINES = 2000
DIFF_BYTES = 256 * 1024
#: Images larger than this are not copied out for a thumbnail.
IMAGE_BYTES = 32 * 1024 * 1024
IMAGE_SUFFIXES = frozenset({"png", "jpg", "jpeg", "webp", "bmp", "tga"})
LFS_POINTER = b"version https://git-lfs.github.com/spec/"
GIT_TIMEOUT = 60.0
NO_GIT = (
    "Git isn't installed, so this project has no timeline. Install Git from "
    "git-scm.com, then reopen the project."
)
#: Identity and location variables an agent's environment may carry.
_SCRUBBED = (
    "GIT_AUTHOR_NAME",
    "GIT_AUTHOR_EMAIL",
    "GIT_AUTHOR_DATE",
    "GIT_COMMITTER_NAME",
    "GIT_COMMITTER_EMAIL",
    "GIT_COMMITTER_DATE",
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_NAMESPACE",
)
_TIMELINE_TABLE = re.compile(
    r"^\[timeline\][ \t]*(?:#[^\n]*)?\n(?:(?!\[)[^\n]*(?:\n|$))*", re.MULTILINE
)
_COMMIT = re.compile(r"^[0-9a-fA-F]{4,64}$")


class TimelineError(ValueError):
    """A timeline action could not be done; the message is for people."""


# --- Settings -----------------------------------------------------------------


def auto_commit(project: Path) -> bool:
    """``[timeline] auto_commit`` in joinery.toml; on unless switched off."""
    path = project / "joinery.toml"
    if not path.is_file():
        return True
    try:
        table = tomllib.loads(path.read_text()).get("timeline", {})
    except tomllib.TOMLDecodeError:
        return True
    value = table.get("auto_commit", True) if isinstance(table, dict) else True
    return value is not False


def set_auto_commit(project: Path, on: bool) -> None:
    """Save ``[timeline] auto_commit``, keeping everything else in joinery.toml."""
    path = project / "joinery.toml"
    text = path.read_text() if path.is_file() else ""
    block = (
        "[timeline]\n"
        "# Each accepted change becomes a git commit, shown in the Timeline tab.\n"
        f"auto_commit = {'true' if on else 'false'}\n"
    )
    match = _TIMELINE_TABLE.search(text)
    if match:
        rest = text[match.end() :]
        updated = text[: match.start()] + block + ("\n" + rest if rest else "")
    else:
        lead = text.rstrip("\n") + "\n\n" if text.strip() else ""
        updated = lead + block
    try:
        saved = tomllib.loads(updated).get("timeline", {})
    except tomllib.TOMLDecodeError as exc:
        raise TimelineError(f"joinery.toml can't be read: {exc}") from None
    if saved.get("auto_commit") is not on:
        raise TimelineError(
            "joinery.toml keeps [timeline] in a form Joinery can't edit."
        )
    descriptor, temp = tempfile.mkstemp(prefix=".joinery-", dir=project)
    try:
        with os.fdopen(descriptor, "w") as handle:
            handle.write(updated)
        if path.is_file():
            os.chmod(temp, path.stat().st_mode & 0o777)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


# --- Running git ----------------------------------------------------------------


def git_executable() -> str | None:
    """The git program on PATH (git.exe on Windows), or None."""
    return shutil.which("git")


def to_git_path(path: str, windows: bool | None = None) -> str:
    """A project-relative path as git spells it: forward slashes, always.

    On Windows a path may arrive with backslashes; on other systems a backslash
    is an ordinary file name character and stays.
    """
    windows = os.name == "nt" if windows is None else windows
    if windows:
        return PureWindowsPath(path).as_posix()
    return PurePosixPath(path).as_posix()


def from_git_path(project: Path, path: str) -> Path:
    """The file in ``project`` for a path git printed (forward slashes)."""
    return project.joinpath(*PurePosixPath(path).parts)


def _environment(identity: tuple[str, str] | None = None) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key not in _SCRUBBED}
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_OPTIONAL_LOCKS"] = "0"
    # Plain English messages, so a failure reads the same everywhere.
    env["LC_ALL"] = "C"
    env["LANGUAGE"] = "C"
    if identity is not None:
        name, email = identity
        env["GIT_AUTHOR_NAME"] = env["GIT_COMMITTER_NAME"] = name
        env["GIT_AUTHOR_EMAIL"] = env["GIT_COMMITTER_EMAIL"] = email
    return env


def _git(
    project: Path,
    *args: str,
    check: bool = True,
    stdin: bytes | None = None,
    identity: tuple[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    program = git_executable()
    if program is None:
        raise TimelineError(NO_GIT)
    argv = [
        program,
        "--literal-pathspecs",
        "-c",
        "core.quotepath=off",
        "-c",
        "color.ui=false",
        "-c",
        "commit.gpgsign=false",
        "-c",
        "core.pager=cat",
        *args,
    ]
    try:
        result = subprocess.run(
            argv,
            cwd=project,
            input=stdin,
            capture_output=True,
            env=_environment(identity),
            timeout=GIT_TIMEOUT,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        raise TimelineError("Git took too long to answer.") from None
    except OSError as exc:
        raise TimelineError(f"Git couldn't start: {exc}") from None
    if check and result.returncode != 0:
        raise TimelineError(_reason(result))
    return result


def _reason(result: subprocess.CompletedProcess[bytes]) -> str:
    text = (result.stderr or result.stdout).decode("utf-8", "replace").strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in lines:
        if line.startswith(("fatal:", "error:")):
            return "Git: " + line.split(":", 1)[1].strip()
    return "Git: " + (lines[0] if lines else f"exit code {result.returncode}")


def _text(result: subprocess.CompletedProcess[bytes]) -> str:
    return result.stdout.decode("utf-8", "replace")


# --- The project's repository -----------------------------------------------------


def state(project: Path) -> dict[str, str]:
    """Whether ``project`` keeps a timeline.

    ``state`` is ``ready`` (its own repository with at least one commit),
    ``empty`` (its own repository, nothing committed yet), ``none`` (no
    repository), ``nested`` (inside another repository) or ``no-git``.
    """
    project = project.resolve()
    if git_executable() is None:
        return {"state": "no-git", "message": NO_GIT}
    found = _git(project, "rev-parse", "--show-toplevel", check=False)
    if found.returncode != 0:
        return {"state": "none", "message": "This project doesn't keep a timeline yet."}
    top = Path(_text(found).strip())
    if top.resolve() != project:
        return {
            "state": "nested",
            "message": f"This project is part of the git repository at {top}. "
            "The Timeline follows projects that have their own repository.",
            "repository": str(top),
        }
    head = _git(project, "rev-parse", "--verify", "-q", "HEAD", check=False)
    if head.returncode != 0:
        return {
            "state": "empty",
            "message": "Nothing is in this project's timeline yet.",
        }
    return {"state": "ready", "message": ""}


def identity(project: Path) -> tuple[str, str]:
    """The user's git name and email, or the local fallback."""

    def value(key: str) -> str:
        got = _git(project, "config", "--get", key, check=False)
        return _text(got).strip() if got.returncode == 0 else ""

    return value("user.name") or FALLBACK_NAME, value("user.email") or FALLBACK_EMAIL


def ensure_ignores(project: Path) -> bool:
    """Add Joinery's ignore rules to .gitignore. Returns True if it changed.

    Appends, like the LFS rules: Godot's own VCS metadata step may have written
    the file, and what it set up stays.
    """
    path = project / ".gitignore"
    existing = path.read_text() if path.is_file() else ""
    if IGNORE_MARKER in existing:
        return False
    present = {line.strip() for line in existing.splitlines()}
    missing = [rule for rule in IGNORE_RULES if rule not in present]
    merged = existing.rstrip("\n")
    merged = (merged + "\n\n" if merged else "") + "\n".join([IGNORE_MARKER, *missing])
    path.write_text(merged + "\n")
    return True


def _lfs(project: Path) -> None:
    """Turn Git LFS on for this repository when it is installed; skip it otherwise."""
    if _git(project, "lfs", "version", check=False).returncode == 0:
        _git(project, "lfs", "install", "--local", check=False)


def _head(project: Path) -> str:
    return _text(_git(project, "rev-parse", "HEAD")).strip()


def _commit_all(project: Path, message: str) -> str:
    _git(project, "add", "-A")
    _git(
        project,
        "commit",
        "--no-verify",
        "--allow-empty",
        "-q",
        "-m",
        message,
        identity=identity(project),
    )
    return _head(project)


def start(project: Path, message: str = STARTED_MESSAGE) -> dict[str, str]:
    """Start keeping a timeline: a repository, the ignore rules and a first commit.

    Only on request (the Timeline tab's button) or while a project is created.
    """
    project = project.resolve()
    current = state(project)
    if current["state"] in {"no-git", "nested"}:
        raise TimelineError(current["message"])
    if current["state"] == "ready":
        raise TimelineError("This project already keeps a timeline.")
    if current["state"] == "none":
        _git(project, "init", "-q")
    ensure_ignores(project)
    _lfs(project)
    commit = _commit_all(project, message)
    return {"state": "ready", "commit": commit, "message": ""}


def project_name(project: Path) -> str:
    """``config/name`` from project.godot, else the folder name."""
    path = project / "project.godot"
    if path.is_file():
        found = re.search(
            r'^config/name="((?:[^"\\]|\\.)*)"', path.read_text(), re.MULTILINE
        )
        if found and found.group(1).strip():
            return found.group(1).strip()
    return project.name


#: Files that show a project already has work in it.
_WORK_SUFFIXES = frozenset(
    {".tscn", ".scn", ".gd", ".cs", ".tres", ".res", ".gdshader"}
)
_NOT_WORK = frozenset({".git", ".godot", ".joinery", "addons"})


def looks_new(project: Path) -> bool:
    """True for a project being created: no joinery.toml and no scenes or scripts.

    An existing game brought into Joinery has its own work in it, and is never
    put under git without the user asking.
    """
    if (project / "joinery.toml").exists():
        return False
    for _folder, directories, files in os.walk(project):
        directories[:] = [name for name in directories if name not in _NOT_WORK]
        if any(Path(name).suffix.lower() in _WORK_SUFFIXES for name in files):
            return False
    return True


def begin_project(project: Path, fresh: bool) -> dict[str, str]:
    """A creation step finished: start the timeline, or fold into its first commit.

    ``fresh`` is True while the project is being created (no joinery.toml
    before this step). Then a project that is not in any repository gets one,
    with the first commit "Start <name>". Later creation steps (the apps, the
    brief, the art bible) fold their files into that commit while it is still
    the newest one and a few minutes old. Anything else is left alone.
    """
    project = project.resolve()
    current = state(project)
    if current["state"] == "no-git":
        return current
    if current["state"] == "none" and fresh:
        started = start(project, f"Start {project_name(project)}")
        _write_marker(project, started["commit"])
        return {**started, "started": "yes"}
    if current["state"] == "ready" and _marker_live(project):
        _git(project, "add", "-A")
        staged = _git(project, "diff", "--cached", "--quiet", check=False)
        if staged.returncode == 0:
            return {"state": "ready", "commit": _head(project), "message": ""}
        _git(
            project,
            "commit",
            "--amend",
            "--no-edit",
            "--no-verify",
            "-q",
            identity=identity(project),
        )
        commit = _head(project)
        _write_marker(project, commit)
        return {"state": "ready", "commit": commit, "message": ""}
    return current


def _write_marker(project: Path, commit: str) -> None:
    path = project / START_MARKER
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"commit": commit, "time": time.time()}) + "\n")


def _marker_live(project: Path) -> bool:
    path = project / START_MARKER
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return False
    if (
        not isinstance(data, dict)
        or time.time() - float(data.get("time", 0)) > START_WINDOW
    ):
        return False
    return str(data.get("commit", "")) == _head(project)


def _drop_marker(project: Path) -> None:
    (project / START_MARKER).unlink(missing_ok=True)


# --- Recording a change -----------------------------------------------------------


def message_for(summary: str, reverted: bool = False) -> str:
    """The commit message: the request summary, then where it was decided."""
    lines = [line.strip() for line in summary.strip().splitlines() if line.strip()]
    subject = lines[0] if lines else "Project changes"
    body = lines[1:]
    if len(subject) > 100:
        body = [subject, *body]
        subject = subject[:99].rstrip() + "…"
    if reverted:
        subject = "Revert: " + subject
    parts = [subject]
    if body:
        parts.append("\n".join(body))
    parts.append(REVERTED_LINE if reverted else ACCEPTED_LINE)
    return "\n\n".join(parts)


def commit_paths(project: Path, paths: Sequence[str], message: str) -> str:
    """Commit exactly ``paths`` as they are on disk. Returns the commit, or "".

    Created, changed and deleted files are all handled; paths git ignores are
    left out. Anything else the user changed or staged stays as it was.
    """
    project = project.resolve()
    wanted = sorted({to_git_path(path) for path in paths if path})
    if not wanted:
        return ""
    listed = "\0".join(wanted).encode() + b"\0"
    tracked = set(_split_z(_git(project, "ls-files", "-z", "--", *wanted).stdout))
    ignored = set(
        _split_z(
            _git(
                project, "check-ignore", "-z", "--stdin", stdin=listed, check=False
            ).stdout
        )
    )
    chosen = [
        path
        for path in wanted
        if path in tracked
        or (path not in ignored and from_git_path(project, path).is_file())
    ]
    if not chosen:
        return ""
    _git(project, "add", "-A", "--", *chosen)
    if (
        _git(
            project, "diff", "--cached", "--quiet", "HEAD", "--", *chosen, check=False
        ).returncode
        == 0
    ):
        return ""
    _git(
        project,
        "commit",
        "--no-verify",
        "-q",
        "--only",
        "-m",
        message,
        "--",
        *chosen,
        identity=identity(project),
    )
    return _head(project)


def record(
    project: Path, paths: Sequence[str], summary: str, reverted: bool = False
) -> dict[str, str]:
    """Add one decided change to the timeline, if this project keeps one.

    Returns ``{"state": ...}``: ``committed`` (with ``commit``), ``unchanged``,
    ``off`` (switched off in joinery.toml), a :func:`state` that has no
    timeline, or ``failed`` with a ``message``. Never raises.
    """
    project = project.resolve()
    try:
        if not auto_commit(project):
            return {"state": "off"}
        current = state(project)
        if current["state"] != "ready":
            return current
        _drop_marker(project)
        commit = commit_paths(project, paths, message_for(summary, reverted))
    except (OSError, TimelineError) as exc:
        return {
            "state": "failed",
            "message": f"Couldn't add this to the Timeline. {exc}",
        }
    if not commit:
        return {"state": "unchanged"}
    return {"state": "committed", "commit": commit}


# --- Reading the timeline ---------------------------------------------------------


def _split_z(data: bytes) -> list[str]:
    return [part.decode("utf-8", "replace") for part in data.split(b"\0") if part]


def _check_commit(project: Path, commit: str) -> str:
    if not _COMMIT.match(commit):
        raise TimelineError("That isn't a timeline entry.")
    found = _git(
        project, "rev-parse", "--verify", "-q", commit + "^{commit}", check=False
    )
    if found.returncode != 0:
        raise TimelineError("That entry isn't in this project's timeline.")
    return _text(found).strip()


def _check_path(path: str) -> str:
    posix = to_git_path(path)
    parts = PurePosixPath(posix).parts
    if not parts or posix.startswith("/") or ".." in parts or ":" in parts[0]:
        raise TimelineError("Choose a file inside the project.")
    return posix


def _log(project: Path, *args: str) -> Iterator[dict[str, Any]]:
    out = _git(
        project,
        "log",
        "-z",
        "--name-only",
        "--format=%x1e%H%x1f%an%x1f%ae%x1f%at%x1f%s%x1f",
        *args,
    ).stdout
    for chunk in out.split(b"\x1e"):
        if not chunk.strip(b"\0\n"):
            continue
        fields = chunk.split(b"\x1f")
        if len(fields) < 6:
            continue
        commit, author, email, when, subject = (
            field.decode("utf-8", "replace") for field in fields[:5]
        )
        names = [name.strip("\n") for name in _split_z(b"\x1f".join(fields[5:]))]
        files = [name for name in names if name]
        yield {
            "commit": commit,
            "short": commit[:7],
            "subject": subject,
            "author": author,
            "email": email,
            "time": int(when) if when.isdigit() else 0,
            "files": len(files),
            "paths": files,
        }


def entries(
    project: Path, skip: int = 0, limit: int = PAGE, text: str = ""
) -> dict[str, Any]:
    """One page of the timeline, newest first.

    ``text`` keeps entries whose message, author or a changed file contains it
    (any case). ``more`` says whether an older page follows.
    """
    project = project.resolve()
    current = state(project)
    result: dict[str, Any] = {
        **current,
        "auto_commit": auto_commit(project),
        "entries": [],
        "more": False,
        "skip": max(0, skip),
    }
    if current["state"] != "ready":
        return result
    skip, limit = max(0, skip), max(1, limit)
    needle = text.strip().lower()
    if needle:
        found = [
            entry
            for entry in _log(project)
            if needle in entry["subject"].lower()
            or needle in entry["author"].lower()
            or any(needle in path.lower() for path in entry["paths"])
        ]
        page = found[skip : skip + limit + 1]
    else:
        page = list(_log(project, f"--skip={skip}", f"--max-count={limit + 1}"))
    result["more"] = len(page) > limit
    _name, email = identity(project)
    from joinery import team  # Late: team builds on this module.

    pulled = team.last_pulled(project)
    result["entries"] = [
        {
            **{key: value for key, value in entry.items() if key != "paths"},
            # A teammate's entry names them; the last pull's entries are new here.
            "teammate": entry["email"].lower() != email.lower(),
            "pulled": entry["commit"] in pulled,
        }
        for entry in page[:limit]
    ]
    result["team"] = team.sync_summary(project)
    return result


def _base(project: Path, commit: str) -> str:
    parent = _git(project, "rev-parse", "--verify", "-q", commit + "^", check=False)
    if parent.returncode == 0:
        return _text(parent).strip()
    return _text(
        _git(project, "hash-object", "-t", "tree", "--stdin", stdin=b"")
    ).strip()


_CHANGE_WORDS = {"A": "created", "D": "deleted", "M": "modified", "T": "modified"}


def _kind(path: str, binary: bool) -> str:
    suffix = PurePosixPath(path).suffix.lower().lstrip(".")
    if suffix in IMAGE_SUFFIXES:
        return "image"
    return "binary" if binary else "text"


def show(project: Path, commit: str) -> dict[str, Any]:
    """One entry: its message and the files it changed, with their kind."""
    project = project.resolve()
    commit = _check_commit(project, commit)
    base = _base(project, commit)
    header = _text(
        _git(project, "show", "-s", "--format=%an%x1f%ae%x1f%at%x1f%B", commit)
    )
    author, email, when, message = (header.split("\x1f", 3) + ["", "", "", ""])[:4]
    lines = message.strip().splitlines()
    status = _split_z(
        _git(
            project, "diff-tree", "-z", "-r", "-M", "--name-status", base, commit
        ).stdout
    )
    numbers = _git(
        project, "diff-tree", "-z", "-r", "-M", "--numstat", base, commit
    ).stdout
    counts = _numstat(numbers)
    files: list[dict[str, Any]] = []
    index = 0
    while index < len(status):
        code = status[index]
        if code.startswith(("R", "C")) and index + 2 < len(status):
            old, path = status[index + 1], status[index + 2]
            index += 3
            change = "renamed"
        else:
            old, path = "", status[index + 1] if index + 1 < len(status) else ""
            index += 2
            change = _CHANGE_WORDS.get(code[:1], "modified")
        added, removed = counts.get(path, (-1, -1))
        entry: dict[str, Any] = {
            "path": path,
            "change": change,
            "kind": _kind(path, added < 0),
            "added": max(0, added),
            "removed": max(0, removed),
        }
        if old:
            entry["old_path"] = old
        files.append(entry)
    return {
        "commit": commit,
        "short": commit[:7],
        "subject": lines[0] if lines else "",
        "body": "\n".join(lines[1:]).strip(),
        "author": author,
        "email": email,
        "time": int(when) if when.strip().isdigit() else 0,
        "files": files,
    }


def _numstat(data: bytes) -> dict[str, tuple[int, int]]:
    """``--numstat -z`` as {path: (added, removed)}; binary files are (-1, -1)."""
    counts: dict[str, tuple[int, int]] = {}
    parts = data.split(b"\0")
    index = 0
    while index < len(parts):
        line = parts[index].decode("utf-8", "replace")
        index += 1
        fields = line.split("\t")
        if len(fields) < 3:
            continue
        added, removed, path = fields[0], fields[1], fields[2]
        if not path and index + 1 < len(parts):
            # A rename: the old and new names follow as their own fields.
            path = parts[index + 1].decode("utf-8", "replace")
            index += 2
        counts[path] = (
            int(added) if added.isdigit() else -1,
            int(removed) if removed.isdigit() else -1,
        )
    return counts


def _blob(project: Path, revision: str, path: str) -> bytes | None:
    """The file at ``revision`` as checked out (LFS and line endings applied)."""
    spec = f"{revision}:{path}"
    if _git(project, "cat-file", "-e", spec, check=False).returncode != 0:
        return None
    return _git(project, "cat-file", "--filters", spec).stdout


def _size(project: Path, revision: str, path: str) -> int:
    spec = f"{revision}:{path}"
    got = _git(project, "cat-file", "-s", spec, check=False)
    if got.returncode != 0:
        return -1
    size = int(_text(got).strip() or 0)
    if size < 512:
        head = _git(project, "cat-file", "blob", spec).stdout
        if head.startswith(LFS_POINTER):
            found = re.search(rb"^size (\d+)", head, re.MULTILINE)
            if found:
                return int(found.group(1))
    return size


def file_detail(project: Path, commit: str, path: str) -> dict[str, Any]:
    """What one entry did to one file: a text diff, two images, or two sizes."""
    project = project.resolve()
    commit = _check_commit(project, commit)
    path = _check_path(path)
    listed = {entry["path"]: entry for entry in show(project, commit)["files"]}
    if path not in listed:
        raise TimelineError(f"{path} didn't change in this entry.")
    entry = dict(listed[path])
    base = _base(project, commit)
    old = str(entry.get("old_path", path))
    entry["before_size"] = _size(project, base, old)
    entry["after_size"] = _size(project, commit, path)
    if entry["kind"] == "text":
        diff = _text(
            _git(
                project,
                "diff",
                "--no-color",
                "--no-ext-diff",
                "-M",
                "-U3",
                base,
                commit,
                "--",
                *sorted({old, path}),
            )
        )
        lines = diff.splitlines()
        start_at = next(
            (i for i, line in enumerate(lines) if line.startswith("@@")), len(lines)
        )
        body = lines[start_at:]
        cut = len(body) > DIFF_LINES or sum(len(line) + 1 for line in body) > DIFF_BYTES
        kept: list[str] = []
        size = 0
        for line in body[:DIFF_LINES]:
            size += len(line) + 1
            if size > DIFF_BYTES:
                break
            kept.append(line)
        entry["diff"] = kept
        entry["truncated"] = cut
    elif entry["kind"] == "image":
        folder = project / CACHE / commit[:16]
        for side, revision, name in (("before", base, old), ("after", commit, path)):
            entry[side] = ""
            if entry[f"{side}_size"] < 0 or entry[f"{side}_size"] > IMAGE_BYTES:
                continue
            data = _blob(project, revision, name)
            if data is None:
                continue
            if data.startswith(LFS_POINTER):
                entry["note"] = "Stored with Git LFS. Install Git LFS to see it."
                continue
            target = folder / side / PurePosixPath(name).name
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.is_file() or target.stat().st_size != len(data):
                target.write_bytes(data)
            entry[side] = str(target)
    return entry


def restore(project: Path, commit: str, path: str) -> dict[str, Any]:
    """Stage a reviewed change that puts ``path`` back as it was at ``commit``.

    A file that entry deleted comes back as it was just before. Nothing in the
    project changes until the user accepts the change in the Review tab; the
    timeline itself is never rewritten.
    """
    from joinery import game_setup  # Late: game_setup imports transactions.

    project = project.resolve()
    commit = _check_commit(project, commit)
    path = _check_path(path)
    revision = commit
    data = _blob(project, commit, path)
    if data is None:
        revision = _base(project, commit)
        data = _blob(project, revision, path)
    if data is None:
        raise TimelineError(f"{path} isn't in this entry.")
    if data.startswith(LFS_POINTER):
        raise TimelineError(
            f"{path} is stored with Git LFS. Install Git LFS, then try again."
        )
    when = _text(_git(project, "show", "-s", "--format=%at", revision)).strip()
    date = datetime.fromtimestamp(int(when)) if when.isdigit() else datetime.now()
    summary = f"Restore {PurePosixPath(path).name} as it was on {date.day} {date:%b %Y}"

    def edit(stage: Path) -> None:
        target = from_git_path(stage, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    try:
        tx = game_setup.stage_change(project, summary, edit)
    except ValueError as exc:
        if "no project changes" in str(exc):
            raise TimelineError(f"{path} is already as it was then.") from None
        raise
    return {
        "transaction_id": tx.id,
        "summary": tx.summary,
        "state": tx.state,
        "changes": tx.changes,
    }


# --- Command line -----------------------------------------------------------------


def _describe(action: str, data: dict[str, Any]) -> str:
    if action == "list":
        if data["state"] != "ready":
            return str(data.get("message", ""))
        rows = [
            f"{entry['short']}  {entry['subject']}  ({entry['author']}, {entry['files']} files)"
            for entry in data["entries"]
        ]
        return "\n".join(rows) or "Nothing matches."
    if action == "show":
        if "files" in data and "diff" not in data:
            lines = [f"{data.get('short', '')}  {data.get('subject', '')}"]
            lines += [f"  {item['change']:<9} {item['path']}" for item in data["files"]]
            return "\n".join(lines)
        if "diff" in data:
            return "\n".join(data["diff"])
        return json.dumps(data, indent=2)
    if action == "restore":
        return (
            f"{data['summary']}. Waiting for your review: joinery transactions accept "
            f"--project <project> --id {data['transaction_id']}"
        )
    if action == "settings":
        return (
            "Accepted changes join the timeline."
            if data["auto_commit"]
            else ("Accepted changes don't join the timeline.")
        )
    return str(data.get("message") or data.get("state", ""))


def run(args: argparse.Namespace) -> int:
    """Run ``joinery timeline <action>``. Errors print as JSON and return 1."""
    project = args.project.resolve()
    paths: list[str] = list(args.path or [])
    try:
        if not (project / "project.godot").is_file():
            raise TimelineError("Expected a Godot project.")
        if len(paths) > 1 and args.action in {"show", "restore"}:
            raise TimelineError("Choose one file with --path.")
        data: dict[str, Any]
        if args.action == "status":
            data = {**state(project), "auto_commit": auto_commit(project)}
        elif args.action == "list":
            data = entries(project, args.skip, args.limit, args.filter or "")
        elif args.action == "show":
            if not args.commit:
                raise TimelineError("Choose the entry with --commit.")
            data = (
                file_detail(project, args.commit, paths[0])
                if paths
                else show(project, args.commit)
            )
        elif args.action == "restore":
            if not args.commit or not paths:
                raise TimelineError(
                    "Choose the entry with --commit and the file with --path."
                )
            data = restore(project, args.commit, paths[0])
        elif args.action == "start":
            data = start(project)
        elif args.action == "record":
            if not paths:
                raise TimelineError("Name the files with --path.")
            data = record(project, paths, args.message or "Project changes")
        else:
            if args.auto_commit is not None:
                set_auto_commit(project, args.auto_commit == "on")
            data = {"auto_commit": auto_commit(project)}
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}))
        if not args.json:
            print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(data) if args.json else _describe(args.action, data))
    return 1 if data.get("state") == "failed" else 0


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Add ``timeline status|list|show|restore|start|record|settings``."""
    parser = subparsers.add_parser(
        "timeline",
        help="the project's git timeline of accepted changes: list, show, restore a file",
    )
    parser.add_argument(
        "action",
        choices=["status", "list", "show", "restore", "start", "record", "settings"],
    )
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--commit", default="", help="show, restore: the entry")
    parser.add_argument(
        "--path",
        action="append",
        default=None,
        help="show, restore: the file; record: each file to commit",
    )
    parser.add_argument("--skip", type=int, default=0, help="list: entries to skip")
    parser.add_argument("--limit", type=int, default=PAGE, help="list: page size")
    parser.add_argument("--filter", default="", help="list: text to look for")
    parser.add_argument("--message", default="", help="record: what the change did")
    parser.add_argument(
        "--auto-commit",
        choices=["on", "off"],
        help="settings: add each accepted change to the timeline",
    )
    parser.add_argument("--json", action="store_true")
