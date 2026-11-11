"""A quick end-to-end check of parallel agents against a real, signed-in provider.

    JOINERY_LIVE_AGENT=codex uv run python tests/integration/parallel_smoke.py

Skipped (exit 0) unless ``JOINERY_LIVE_AGENT`` names a provider (codex or claude).
It makes an empty project in a temporary folder, starts the real chat host
(``joinery chat``) as the editor does, sends one request whose agent is asked to
hand a second, independent part to ``start_task``, and, once the lead is busy,
starts one more task with "Run alongside" (``task_start``). Every change that
reaches review is accepted. It prints when each milestone happened, in seconds
from the start, and fails when the host errors or a task never ends.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
AGENTS = {"codex", "claude"}
TIMEOUT = float(os.environ.get("JOINERY_SMOKE_TIMEOUT", "900"))
LEAD = (
    "Add a GDScript file scripts/greeter.gd that extends Node and prints "
    '"hello" in _ready. As a separate part, call start_task with the title '
    '"Farewell script" and the request "Add scripts/farewell.gd that extends '
    'Node and prints \\"bye\\" in _ready." Then submit your own part.'
)
ALONGSIDE = 'Add scripts/counter.gd that extends Node with "var count := 0".'


class Smoke:
    def __init__(self, agent: str, project: Path) -> None:
        self.agent = agent
        self.project = project
        self.started = time.monotonic()
        self.marks: list[tuple[float, str]] = []
        self.events: list[dict[str, Any]] = []
        self.tasks: dict[str, str] = {}
        self.decided: set[str] = set()
        self.lead_done = False
        self.alongside_sent = False

    def mark(self, text: str) -> None:
        moment = time.monotonic() - self.started
        self.marks.append((moment, text))
        print(f"{moment:7.1f}s  {text}", flush=True)

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
                    if isinstance(event, dict):
                        failure = self.handle(event, send)
                        if failure:
                            return failure
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
        self.mark("done")
        return 0

    def finished(self) -> bool:
        final = {"accepted", "discarded", "done", "failed", "stopped"}
        return (
            self.lead_done
            and self.alongside_sent
            and bool(self.tasks)
            and all(state in final for state in self.tasks.values())
        )

    def handle(self, event: dict[str, Any], send: Any) -> int:
        self.events.append(event)
        kind = str(event.get("kind", ""))
        task = str(event.get("task_id", ""))
        if kind == "connection":
            if event.get("state") != "connected":
                self.mark("connection failed: " + str(event.get("message", "")))
                return 1
            self.mark(f"connected ({event.get('parallel')} turns at once)")
            send({"kind": "prompt", "text": LEAD, "chat_id": "", "targets": []})
        elif kind == "progress" and not task:
            if not self.alongside_sent:
                self.alongside_sent = True
                self.mark("lead busy: " + str(event.get("text", "")))
                send({"kind": "task_start", "text": ALONGSIDE, "targets": []})
        elif kind == "progress":
            self.mark(f"task {task[:6]} step: {event.get('text', '')}")
        elif kind == "task":
            state = str(event.get("state", ""))
            if self.tasks.get(task) != state:
                self.tasks[task] = state
                if event.get("message"):
                    self.mark(f"task {task[:6]} says: {event['message']}")
                self.mark(
                    f"task {task[:6]} ({event.get('origin')}) "
                    f"{event.get('title')!r}: {state} {event.get('step', '')}".rstrip()
                )
        elif kind == "file_preview":
            identifier = str(event.get("transaction_id", ""))
            who = f"task {task[:6]}" if task else "lead"
            self.mark(f"{who} in review: {event.get('summary', '')}")
            if identifier and identifier not in self.decided:
                self.decided.add(identifier)
                send({"kind": "decision", "transaction_id": identifier, "accept": True})
        elif kind == "file_decision":
            who = f"task {task[:6]}" if task else "lead"
            self.mark(f"{who} decided: accepted={event.get('accepted')}")
        elif kind == "done" and not task:
            self.lead_done = True
            self.mark(f"lead done (ok={event.get('ok')})")
        elif kind == "message" and event.get("level") == "warn":
            self.mark(
                ("task " + task[:6] + " " if task else "")
                + "warning: "
                + str(event.get("text", ""))
            )
        elif kind == "timing" and not task:
            self.mark("lead timing: " + json.dumps(event.get("phases_ms", {})))
        return 0


async def make_project(folder: Path) -> Path:
    project = folder / "smoke"
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
        "Parallel smoke",
        "--tools",
        "godot",
    )
    if await process.wait() != 0:
        raise RuntimeError("joinery new failed")
    return project


async def main() -> int:
    agent = os.environ.get("JOINERY_LIVE_AGENT", "")
    if not agent:
        print("parallel smoke: skipped (set JOINERY_LIVE_AGENT=codex or claude)")
        return 0
    if agent not in AGENTS:
        print(f"parallel smoke: JOINERY_LIVE_AGENT must be one of {sorted(AGENTS)}")
        return 2
    with tempfile.TemporaryDirectory(prefix="joinery-parallel-smoke-") as folder:
        project = await make_project(Path(folder))
        smoke = Smoke(agent, project)
        code = await smoke.run()
        made = sorted(
            str(p.relative_to(project)) for p in (project / "scripts").glob("*.gd")
        )
        smoke.mark("scripts in the project: " + (", ".join(made) or "none"))
    print("parallel smoke: " + ("ok" if code == 0 else "FAILED"), flush=True)
    return code


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
