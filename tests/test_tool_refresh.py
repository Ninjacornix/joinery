"""Refresh tools: progress from Docker's own steps, a log, and readable failures."""

from __future__ import annotations

import json
import sys
import threading
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


def test_one_refresh_at_a_time_and_a_second_caller_follows_it(
    tmp_path: Path, cache: Path
) -> None:
    import subprocess

    from joinery import bootstrap

    # Another process runs a refresh: it holds the slot and writes its progress.
    other = subprocess.Popen(
        _script(
            tmp_path,
            "import json, os, sys, time\n"
            "state = sys.argv[1]\n"
            "def put(**data):\n"
            "    data.update(pid=os.getpid(), log='refresh.log', steps=2)\n"
            "    open(state + '.tmp', 'w').write(json.dumps(data))\n"
            "    os.replace(state + '.tmp', state)\n"
            "put(state='running', progress=0.25, step=1, text='Refreshing Godot')\n"
            "time.sleep(1.0)\n"
            "put(state='failed', step=2, error='Docker ran out of disk space.')\n",
        )
        + [str(cache / "refresh.json")]
    )
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "refresh.lock").write_text(str(other.pid))
    # Reaped as it ends, so its pid stops answering (a real holder isn't our child).
    reaper = threading.Thread(target=other.wait)
    reaper.start()
    try:
        assert tool_refresh.acquire() is False
        with pytest.raises(tool_refresh.RefreshBusy):
            bootstrap.setup(tmp_path)
        seen: list[dict[str, Any]] = []
        final = tool_refresh.join(seen.append, poll=0.05, timeout=20)
    finally:
        reaper.join(timeout=10)
    assert seen and seen[0]["joined"] is True and seen[0]["text"] == "Refreshing Godot"
    assert final["state"] == "failed"
    assert final["error"] == "Docker ran out of disk space."
    # Its holder is gone: the slot is free again, and nothing runs to join.
    assert tool_refresh.join(None) == {"state": "idle"}
    assert tool_refresh.acquire() is True
    tool_refresh.release()
    assert not (cache / "refresh.lock").exists()
