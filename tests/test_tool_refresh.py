"""Refresh tools: progress from Docker's own steps, a log, and readable failures."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from joinery import tool_refresh


@pytest.fixture(autouse=True)
def cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "cache"
    monkeypatch.setenv("JOINERY_READINESS_CACHE", str(folder))
    return folder


def _script(tmp_path: Path, body: str) -> list[str]:
    path = tmp_path / "fake_build.py"
    path.write_text(body)
    return [sys.executable, str(path)]


def test_buildkit_steps_become_progress_and_every_line_is_logged(
    tmp_path: Path, cache: Path
) -> None:
    events: list[dict[str, Any]] = []
    progress = tool_refresh.Progress(2, events.append)
    progress.begin("joinery-creative-worker")
    argv = _script(
        tmp_path,
        "import time\n"
        "print('#0 building with \"desktop-linux\" instance using docker driver')\n"
        "print('#5 [2/4] RUN apt-get install -y krita', flush=True)\n"
        "time.sleep(0.6)\n"
        "print('#6 [stage 4/4] COPY . /opt', flush=True)\n",
    )
    tool_refresh.run(argv, progress, cwd=tmp_path, timeout=60, what="Krita")
    assert events[0]["text"] == "Refreshing the creative apps (step 1 of 2)…"
    assert events[-1]["progress"] == pytest.approx(0.375)
    log = (cache / "refresh.log").read_text()
    assert "apt-get install -y krita" in log and "step 1 of 2" in log
    state = json.loads((cache / "refresh.json").read_text())
    assert state["state"] == "running" and state["log"] == str(cache / "refresh.log")


def test_a_failure_says_why_not_buildkits_first_line(tmp_path: Path) -> None:
    progress = tool_refresh.Progress(1)
    progress.begin("joinery-blender-worker")
    argv = _script(
        tmp_path,
        "import sys\n"
        "print('#0 building with \"desktop-linux\" instance using docker driver')\n"
        "print('#9 [3/7] RUN curl https://download.blender.org/x.tar.xz')\n"
        "print('#9 ERROR: process \"/bin/sh -c curl\" did not complete: exit code: 6')\n"
        "sys.exit(1)\n",
    )
    with pytest.raises(tool_refresh.RefreshFailed) as failed:
        tool_refresh.run(argv, progress, cwd=tmp_path, timeout=60, what="Blender")
    message = str(failed.value)
    assert message.startswith("Couldn't refresh Blender: process")
    assert "building with" not in message


@pytest.mark.parametrize(
    ("printed", "expected"),
    [
        ("write /var/lib/x: no space left on device", "Docker ran out of disk space."),
        ("Temporary failure resolving 'deb.debian.org'", "needs the internet"),
        ("Cannot connect to the Docker daemon at unix:///var/run", "Docker stopped"),
    ],
)
def test_known_failures_are_said_plainly(printed: str, expected: str) -> None:
    assert expected in tool_refresh.readable(["#0 building with x", printed], "x")


def test_finish_records_a_failure_for_the_next_screen(cache: Path) -> None:
    progress = tool_refresh.Progress(3)
    progress.begin("joinery-mcp-worker")
    progress.finish("failed", error="Docker ran out of disk space.")
    state = tool_refresh.read_state()
    assert state["state"] == "failed"
    assert state["error"] == "Docker ran out of disk space."
    assert tool_refresh.cancel() == {"state": "idle"}
