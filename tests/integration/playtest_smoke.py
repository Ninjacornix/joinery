"""A live /playtest against a real, signed-in provider, on a fresh third-person game.

    JOINERY_LIVE_AGENT=codex uv run python tests/integration/playtest_smoke.py

Skipped (exit 0) unless ``JOINERY_LIVE_AGENT`` names a provider (codex or claude). It
makes a third-person game in a temporary folder, imports it, starts the real chat host
(``joinery chat``) as the editor does, and sends ``playtest_start`` as the sidebar's
``/playtest`` does. The playtester must call ``playtest`` itself (the run carries the
focus it was given), and the run must end with a report and the playtester's own words.
It prints each milestone in seconds from the start, the report's counts and the written
report, and fails when the host errors, nothing is played or nothing is written. Set
``JOINERY_PLAYTEST_GODOT`` to the stock Godot to play in.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
AGENTS = {"codex", "claude"}
TIMEOUT = float(os.environ.get("JOINERY_SMOKE_TIMEOUT", "900"))
FOCUS = "the coins and the lever"


class Smoke:
    def __init__(self, agent: str, project: Path) -> None:
        self.agent = agent
        self.project = project
        self.started = time.monotonic()
        self.states: list[str] = []
        self.done: dict[str, Any] = {}

    def mark(self, text: str) -> None:
        print(f"{time.monotonic() - self.started:7.1f}s  {text}", flush=True)

    async def run(self) -> int:
        process = await asyncio.create_subprocess_exec(
            "uv",
            "run",
            "--frozen",
            "--quiet",
            "--project",
            str(ROOT),
            "joinery",
            "chat",
            "--agent",
            self.agent,
            "--project",
            str(self.project),
            "--addon-protocol",
            "2",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            limit=8_000_000,
        )
        assert process.stdin is not None and process.stdout is not None
        writer = process.stdin
        try:
            async with asyncio.timeout(TIMEOUT):
                while not self.done:
                    raw = await process.stdout.readline()
                    if not raw:
                        self.mark("the chat host exited")
                        return 1
                    try:
                        event = json.loads(raw)
                    except ValueError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    kind = event.get("kind")
                    if kind == "connection":
                        if event.get("state") != "connected":
                            self.mark("connection failed: " + str(event.get("message")))
                            return 1
                        self.mark("connected; /playtest " + FOCUS)
                        line = {"kind": "playtest_start", "focus": FOCUS}
                        writer.write((json.dumps(line) + "\n").encode())
                        await writer.drain()
                    elif kind == "playtest":
                        self.playtest(event)
                    elif kind == "message" and event.get("level") == "warn":
                        self.mark("warning: " + str(event.get("text", "")))
        except TimeoutError:
            self.mark(f"timed out after {TIMEOUT:.0f}s")
            return 1
        finally:
            writer.close()
            try:
                await asyncio.wait_for(process.wait(), 30)
            except TimeoutError:
                process.kill()
        return self.verdict()

    def playtest(self, event: dict[str, Any]) -> None:
        state = str(event.get("state"))
        if not self.states or self.states[-1] != state:
            self.states.append(state)
            self.mark(f"playtest {state} (origin {event.get('origin')})")
        if state == "playing" and int(event.get("elapsed", 0)) % 20 == 0:
            self.mark(f"  playing {event.get('elapsed')} / {event.get('seconds')} s")
        if state in {"done", "failed", "stopped"}:
            self.done = event

    def verdict(self) -> int:
        report = self.done.get("report") or {}
        self.mark("states: " + " -> ".join(self.states))
        self.mark("report: " + str(report.get("summary", "none")))
        for item in report.get("items", []):
            self.mark(f"  [{item['severity']}] {item['title']}: {item['detail']}")
        self.mark("checks: " + json.dumps(report.get("checks", {})))
        self.mark("goals: " + json.dumps(report.get("goals", {})))
        self.mark("screenshots: " + str(len(report.get("screenshots", []))))
        print("--- the playtester's report ---\n" + str(self.done.get("summary", "")))
        for followup in self.done.get("followups", []):
            print("Fix: " + followup)
        problems = []
        if self.done.get("state") != "done":
            problems.append(
                f"ended {self.done.get('state')}: {self.done.get('message', '')}"
            )
        if "playing" not in self.states:
            problems.append("nothing was played")
        if not self.done.get("summary"):
            problems.append("the playtester wrote nothing")
        if self.done.get("message"):
            problems.append(str(self.done["message"]))
        for problem in problems:
            self.mark("FAILED: " + problem)
        return 1 if problems else 0


def make_project(folder: Path) -> Path:
    project = folder / "smoke"
    subprocess.run(
        [
            "uv",
            "run",
            "--frozen",
            "--quiet",
            "--project",
            str(ROOT),
            "joinery",
            "new",
            str(project),
            "--name",
            "Playtest smoke",
            "--tools",
            "godot",
            "--blueprint",
            "third-person-3d",
        ],
        check=True,
        timeout=300,
    )
    sys.path.insert(0, str(ROOT))
    from joinery import playtest

    subprocess.run(
        [
            str(playtest.godot_binary()),
            "--headless",
            "--path",
            str(project),
            "--import",
        ],
        capture_output=True,
        timeout=300,
        check=False,
    )
    return project


def _status(project: Path) -> list[str]:
    """Changed files outside Joinery's own folder (the run's report lives in it)."""
    lines = subprocess.run(
        ["git", "-C", str(project), "status", "--porcelain", "--untracked-files=all"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.splitlines()
    return [line for line in lines if ".joinery/" not in line]


def main() -> int:
    agent = os.environ.get("JOINERY_LIVE_AGENT", "")
    if not agent:
        print("playtest smoke: skipped (set JOINERY_LIVE_AGENT=codex or claude)")
        return 0
    if agent not in AGENTS:
        print(f"playtest smoke: JOINERY_LIVE_AGENT must be one of {sorted(AGENTS)}")
        return 2
    with tempfile.TemporaryDirectory(prefix="joinery-playtest-smoke-") as folder:
        project = make_project(Path(folder))
        before = _status(project)
        code = asyncio.run(Smoke(agent, project).run())
        changed = sorted(set(_status(project)) - set(before))
        # The playtest is read-only: only its own folder under .joinery/ may appear.
        print("files the playtest changed:", ", ".join(changed) or "none")
        if changed:
            code = 1
    print("playtest smoke: " + ("ok" if code == 0 else "FAILED"), flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
