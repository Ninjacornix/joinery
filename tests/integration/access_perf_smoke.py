"""A live check of the performance agent against a real, signed-in provider.

    JOINERY_LIVE_AGENT=codex uv run python tests/integration/access_perf_smoke.py

Skipped (exit 0) unless ``JOINERY_LIVE_AGENT`` names a provider (codex or claude). It
copies the heavy fixture game (tests/fixtures/perf_game, whose hot.gd loops 300 000 times
a frame), starts the real chat host (``joinery chat``) as the editor does and sends
``check_fix`` for the scripts area, as the Performance card's Fix does. The host measures,
the read-only performance agent reads the finding and hot.gd and words the change, a
worker makes it, the reviewer reads it, and the smoke accepts it. Then the game is
measured again: hot.gd must no longer be a finding. It prints when each milestone
happened, in seconds from the start.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from joinery import perf
from joinery.godot_config import GodotConfig

ROOT = Path(__file__).resolve().parents[2]
AGENTS = {"codex", "claude"}
TIMEOUT = float(os.environ.get("JOINERY_SMOKE_TIMEOUT", "1200"))
FINAL = {"accepted", "discarded", "done", "failed", "stopped"}


class Smoke:
    def __init__(self, agent: str, project: Path) -> None:
        self.agent = agent
        self.project = project
        self.started = time.monotonic()
        self.tasks: dict[str, str] = {}
        self.decided: set[str] = set()
        self.run_state = ""

    def mark(self, text: str) -> None:
        print(f"{time.monotonic() - self.started:7.1f}s  {text}", flush=True)

    def finished(self) -> bool:
        return self.run_state in {"done", "failed", "stopped"} and all(
            state in FINAL for state in self.tasks.values()
        )

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

        def send(line: dict[str, Any]) -> None:
            writer.write((json.dumps(line) + "\n").encode())

        try:
            async with asyncio.timeout(TIMEOUT):
                while not self.finished():
                    raw = await process.stdout.readline()
                    if not raw:
                        self.mark("the chat host exited")
                        return 1
                    try:
                        event = json.loads(raw)
                    except ValueError:
                        continue
                    if isinstance(event, dict) and self.handle(event, send):
                        return 1
                    await writer.drain()
        except TimeoutError:
            self.mark(f"timed out after {TIMEOUT:.0f}s")
            return 1
        finally:
            writer.close()
            try:
                await asyncio.wait_for(process.wait(), 30)
            except TimeoutError:
                process.kill()
        failed = [t for t, s in self.tasks.items() if s not in {"accepted", "done"}]
        if failed or self.run_state != "done" or not self.tasks:
            self.mark(f"run {self.run_state}; tasks not accepted: {failed}")
            return 1
        return 0

    def handle(self, event: dict[str, Any], send: Any) -> bool:
        kind = str(event.get("kind", ""))
        if kind == "connection":
            if event.get("state") != "connected":
                self.mark("connection failed: " + str(event.get("message", "")))
                return True
            self.mark(f"connected ({event.get('parallel')} turns at once); Fix scripts")
            send(
                {
                    "kind": "check_fix",
                    "check": "perf",
                    "areas": ["scripts"],
                    "target": "mid",
                }
            )
        elif kind == "perf":
            self.run_state = str(event.get("state", ""))
            rows = ", ".join(
                f"{r['title']}={r['state']}" for r in event.get("tasks", [])
            )
            self.mark(f"perf {self.run_state}: {rows} {event.get('message', '')}")
        elif kind == "task":
            state = str(event.get("state", ""))
            task = str(event.get("task_id", ""))
            if self.tasks.get(task) != state:
                self.tasks[task] = state
                self.mark(f"task {event.get('title')!r} ({event.get('role')}): {state}")
                if state == "working" and event.get("request"):
                    self.mark(
                        "  request: " + str(event["request"])[:300].replace("\n", " ")
                    )
        elif kind == "file_preview":
            identifier = str(event.get("transaction_id", ""))
            reviewer = event.get("reviewer", {})
            self.mark(
                f"in review: {event.get('summary', '')}; reviewer {reviewer.get('status')}"
            )
            if identifier and identifier not in self.decided:
                self.decided.add(identifier)
                send({"kind": "decision", "transaction_id": identifier, "accept": True})
        elif kind == "message" and event.get("level") == "warn":
            self.mark("warning: " + str(event.get("text", "")))
        return False


async def make_project(folder: Path) -> Path:
    """A project made as the editor makes one (hooks wired), with the heavy scene."""
    project = folder / "game"
    process = await asyncio.create_subprocess_exec(
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
        "Perf smoke",
        "--tools",
        "godot",
    )
    if await process.wait() != 0:
        raise RuntimeError("joinery new failed")
    fixture = ROOT / "tests/fixtures/perf_game"
    for name in ("main.tscn", "hot.gd"):
        shutil.copyfile(fixture / name, project / name)
    config = GodotConfig.parse((project / "project.godot").read_text(encoding="utf-8"))
    config.set("application", "run/main_scene", '"res://main.tscn"')
    (project / "project.godot").write_text(config.dump(), encoding="utf-8")
    toml = project / "joinery.toml"
    toml.write_text(toml.read_text(encoding="utf-8") + "\n[agents]\nparallel = 3\n")
    return project


async def main() -> int:
    agent = os.environ.get("JOINERY_LIVE_AGENT", "")
    if not agent:
        print("access/perf smoke: skipped (set JOINERY_LIVE_AGENT=codex or claude)")
        return 0
    if agent not in AGENTS:
        print(f"access/perf smoke: JOINERY_LIVE_AGENT must be one of {sorted(AGENTS)}")
        return 2
    with tempfile.TemporaryDirectory(prefix="joinery-perf-smoke-") as folder:
        project = await make_project(Path(folder))
        before = perf.audit(project, "mid", window=False)
        smoke = Smoke(agent, project)
        smoke.mark("before: " + before.summary)
        code = await smoke.run()
        after = perf.audit(project, "mid", window=False)
        smoke.mark("after: " + after.summary)
        hot = [f for f in after.findings if f.path == "hot.gd" and f.severity != "low"]
        if hot:
            smoke.mark("still hot: " + "; ".join(f.message for f in hot))
            code = 1
    print("access/perf smoke: " + ("ok" if code == 0 else "FAILED"), flush=True)
    return code


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
