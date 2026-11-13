"""Refresh tools: progress, a log, a readable failure and Cancel for `joinery bootstrap`.

A refresh builds the offline images the project's apps run in, which takes
minutes after an update and about an hour the first time. While it runs:

- every line Docker prints goes to `refresh.log` in the readiness cache
  folder, so Show log has the whole story;
- `refresh.json` beside it holds where it is (step, steps, words, fraction),
  for `--progress` callers and for a second caller that joins it;
- `--progress` prints one JSON line per change ({"progress", "step", "steps",
  "text", "log"}) and the result last;
- a failure says what went wrong in plain words ("Docker ran out of disk
  space."), never BuildKit's first line ("#0 building with desktop-linux");
- `cancel()` stops it: the running build is stopped and nothing is left half
  tagged (Docker keeps its cache, so the next refresh resumes quickly).
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import signal
import subprocess
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from joinery import readiness

Emit = Callable[[dict[str, Any]], None]

#: What each image is for, in words a person knows.
IMAGE_WORDS = {
    "joinery-sandbox-runtime": "the shared base",
    "joinery-mcp-worker": "Godot",
    "joinery-creative-worker": "the creative apps",
    "joinery-blender-worker": "Blender",
    "joinery-inkscape-worker": "Inkscape",
    "joinery-audacity-worker": "Audacity",
}
#: BuildKit's plain progress: "#7 [3/9] RUN apt-get …" (stage names may precede).
_STEP = re.compile(r"^#\d+ \[(?:[^\]]*? )?(\d+)/(\d+)\]")
#: Known failures and how to say them.
_REASONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"no space left on device", re.IGNORECASE),
        (
            "Docker ran out of disk space. Free some space in Docker Desktop "
            "(Settings, Resources), then try again."
        ),
    ),
    (
        re.compile(
            r"temporary failure (in name resolution|resolving)|could not resolve|"
            r"network is unreachable|connection (timed out|refused)|"
            r"TLS handshake timeout|i/o timeout",
            re.IGNORECASE,
        ),
        (
            "The refresh needs the internet to download the apps. Check the "
            "connection, then try again."
        ),
    ),
    (
        re.compile(
            r"cannot connect to the docker daemon|docker daemon is not running|"
            r"error during connect",
            re.IGNORECASE,
        ),
        "Docker stopped while the refresh ran. Start Docker Desktop, then try again.",
    ),
    (
        re.compile(r"killed|exit code: 137|out of memory", re.IGNORECASE),
        (
            "Docker ran out of memory. Give it more in Docker Desktop (Settings, "
            "Resources), then try again."
        ),
    ),
)
#: BuildKit lines that are progress, not a reason ("#0 building with …").
_NOISE = re.compile(
    r"^#\d+ (building with|\[internal\]|DONE|CACHED|load |transferring )"
)


class RefreshFailed(ValueError):
    """A refresh step failed; the message is the readable reason."""


class RefreshCancelled(Exception):
    """Cancel was pressed (or the refresh was told to stop)."""


def log_path() -> Path:
    return readiness.cache_dir() / "refresh.log"


def state_path() -> Path:
    return readiness.cache_dir() / "refresh.json"


def read_state() -> dict[str, Any]:
    try:
        data = json.loads(state_path().read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_state(data: dict[str, Any]) -> None:
    path = state_path()
    with contextlib.suppress(OSError):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f".{os.getpid()}.tmp")
        temporary.write_text(json.dumps(data) + "\n")
        os.replace(temporary, path)


def readable(lines: Sequence[str], fallback: str) -> str:
    """The reason a build failed, in plain words, from what Docker printed."""
    text = "\n".join(lines[-400:])
    for pattern, words in _REASONS:
        if pattern.search(text):
            return words
    for line in reversed(lines):
        stripped = line.strip()
        if "ERROR" in stripped or stripped.lower().startswith("error"):
            reason = re.sub(r"^#\d+ ", "", stripped)
            reason = re.sub(r"^ERROR:?\s*", "", reason)
            return f"{fallback}: {reason[:240]}"
    for line in reversed(lines):
        stripped = line.strip()
        if stripped and not _NOISE.match(stripped):
            return f"{fallback}: {re.sub(r'^#[0-9]+ ', '', stripped)[:240]}"
    return fallback + "."


class Progress:
    """Where a refresh is, written to refresh.json and sent to `emit`."""

    def __init__(self, steps: int, emit: Emit | None = None, *, first: bool = False):
        self.steps = max(steps, 1)
        self.step = 0
        self.fraction = 0.0
        self.words = ""
        self.first = first
        self.emit = emit
        self.started = time.time()
        self._last = 0.0
        log_path().parent.mkdir(parents=True, exist_ok=True)
        log_path().write_text("")

    def begin(self, image: str) -> None:
        self.step += 1
        self.fraction = 0.0
        self.words = IMAGE_WORDS.get(image, "the tools")
        self.log(f"--- step {self.step} of {self.steps}: {image}")
        self.send(force=True)

    def advance(self, fraction: float) -> None:
        if fraction > self.fraction:
            self.fraction = min(fraction, 1.0)
            self.send()

    def log(self, line: str) -> None:
        with (
            contextlib.suppress(OSError),
            log_path().open("a", encoding="utf-8") as log,
        ):
            log.write(line.rstrip("\n") + "\n")

    def event(self, state: str = "running") -> dict[str, Any]:
        done = (self.step - 1 + self.fraction) / self.steps if self.step else 0.0
        text = f"Refreshing {self.words} (step {self.step} of {self.steps})…"
        if self.first:
            text += " The first refresh takes about an hour."
        return {
            "state": state,
            "progress": round(max(done, 0.0), 3),
            "step": self.step,
            "steps": self.steps,
            "text": text,
            "log": str(log_path()),
            "pid": os.getpid(),
            "started": self.started,
        }

    def send(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last < 0.5:
            return
        self._last = now
        event = self.event()
        _write_state(event)
        if self.emit is not None:
            self.emit(event)

    def finish(self, state: str, **extra: Any) -> dict[str, Any]:
        event = {**self.event(state), **extra}
        if state == "done":
            event["progress"] = 1.0
        _write_state(event)
        return event


def run(
    argv: list[str],
    progress: Progress,
    *,
    cwd: Path,
    timeout: float,
    input_text: str | None = None,
    what: str = "",
) -> None:
    """Run one build step, logging every line and turning BuildKit steps into progress."""
    lines: list[str] = []
    child = subprocess.Popen(
        argv,
        cwd=cwd,
        stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        env={**os.environ, "BUILDKIT_PROGRESS": "plain"},
    )
    deadline = time.monotonic() + timeout
    try:
        if input_text is not None and child.stdin is not None:
            child.stdin.write(input_text)
            child.stdin.close()
        assert child.stdout is not None
        for line in child.stdout:
            progress.log(line)
            lines.append(line)
            del lines[:-400]
            found = _STEP.match(line)
            if found:
                done, total = int(found.group(1)), int(found.group(2))
                progress.advance((done - 1) / max(total, 1))
            if time.monotonic() > deadline:
                raise RefreshFailed(f"Refreshing {what or 'the tools'} took too long.")
        code = child.wait()
    except BaseException:
        _stop(child)
        raise
    if code:
        raise RefreshFailed(readable(lines, f"Couldn't refresh {what or 'the tools'}"))


def _stop(child: subprocess.Popen[str]) -> None:
    with contextlib.suppress(OSError):
        child.terminate()
    try:
        child.wait(timeout=10)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(OSError):
            child.kill()
        child.wait()


def stop_on_terminate() -> None:
    """Make Cancel (SIGTERM) raise RefreshCancelled, so the build is stopped cleanly."""

    def handler(_signum: int, _frame: Any) -> None:
        raise RefreshCancelled()

    with contextlib.suppress(ValueError, OSError):
        signal.signal(signal.SIGTERM, handler)


class RefreshBusy(Exception):
    """Another refresh runs now; join it (`join`) instead of starting a second."""


def lock_path() -> Path:
    return readiness.cache_dir() / "refresh.lock"


def _holder() -> int:
    try:
        return int(lock_path().read_text().strip() or 0)
    except (OSError, ValueError):
        return 0


def acquire() -> bool:
    """Take the one refresh slot; False while another live refresh holds it.

    Two refreshes at once (the rail and Project setup, two editors) would
    build the same images twice and fight over their tags.
    """
    path = lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    for _attempt in range(3):
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            holder = _holder()
            if holder and holder != os.getpid() and readiness._alive(holder):
                return False
            # Left behind by a refresh that ended without letting go.
            with contextlib.suppress(OSError):
                path.unlink()
            continue
        with os.fdopen(descriptor, "w") as lock:
            lock.write(str(os.getpid()))
        return True
    return False


def release() -> None:
    if _holder() == os.getpid():
        with contextlib.suppress(OSError):
            lock_path().unlink()


def join(
    emit: Emit | None, *, poll: float = 1.0, timeout: float = 4 * 3600
) -> dict[str, Any]:
    """Follow the refresh another process runs until it ends; its last state.

    Sends each new progress line to `emit`, like the refresh's own caller
    sees them. {"state": "idle"} when nothing runs.
    """
    holder = _holder()
    if not holder or not readiness._alive(holder):
        return {"state": "idle"}
    sent: dict[str, Any] = {}
    deadline = time.monotonic() + timeout
    while readiness._alive(holder) and time.monotonic() < deadline:
        state = read_state()
        if state.get("pid") == holder and state.get("state") == "running":
            if state != sent and emit is not None:
                emit({**state, "joined": True})
            sent = state
        time.sleep(poll)
    final = read_state()
    if final.get("pid") != holder:
        return {"state": "failed", "error": "The other refresh stopped before it began."}
    if final.get("state") == "running":
        return {"state": "failed", "error": "The other refresh stopped unexpectedly."}
    return final


def cancel() -> dict[str, Any]:
    """Stop the refresh that is running (Cancel in the sidebar)."""
    state = read_state()
    pid = int(state.get("pid", 0) or 0)
    if state.get("state") != "running" or not readiness._alive(pid):
        return {"state": "idle"}
    with contextlib.suppress(OSError):
        os.kill(pid, signal.SIGTERM)
    return {"state": "cancelling", "pid": pid}
