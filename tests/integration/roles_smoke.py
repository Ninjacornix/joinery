"""A quick end-to-end check of the reviewer and debugger agents against a real provider.

    JOINERY_LIVE_AGENT=codex uv run python tests/integration/roles_smoke.py

Skipped (exit 0) unless ``JOINERY_LIVE_AGENT`` names a provider (codex or claude).
It makes an empty project in a temporary folder with one script that fails at
run time, starts the real chat host (``joinery chat``) as the editor does, and:

1. sends one request; its change is read by the reviewer agent before it opens
   for review, and the review's notes are printed, then accepted;
2. sends ``debug_start`` for the script's error, as the sidebar's Debug does: a
   debugger finds the cause, a worker stages the fix, the reviewer reads it, and
   the fix is accepted.

It prints when each milestone happened, in seconds from the start, and fails
when the host errors, the reviewer never ran, or the debugging task never ends.
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
    '"hello" in _ready, then submit it.'
)
BROKEN = 'extends Node\n\n\nfunc _ready() -> void:\n\tcall("jump")\n'
ERROR = "SCRIPT ERROR: Invalid call. Nonexistent function 'jump' in base 'Node (jumper.gd)'."
OUTPUT = ERROR + "\n          at: _ready (res://scripts/jumper.gd:5)\n"
FINAL = {"accepted", "discarded", "done", "failed", "stopped"}


class Smoke:
    def __init__(self, agent: str, project: Path) -> None:
        self.agent = agent
        self.project = project
        self.started = time.monotonic()
        self.events: list[dict[str, Any]] = []
        self.decided: set[str] = set()
        self.reviews: list[dict[str, Any]] = []
        self.reviewing = 0
        self.lead_done = False
        self.debug_sent = False
        self.debug_state = ""
        self.debug_role = ""

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

        def send(line: dict[str, Any]) -> None:
            writer.write((json.dumps(line) + "\n").encode())

        try:
            async with asyncio.timeout(TIMEOUT):
                while not (self.debug_sent and self.debug_state in FINAL):
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
        if not self.reviews or self.reviewing == 0:
            self.mark("the reviewer never ran")
            return 1
        self.mark("done")
        return 0 if self.debug_state == "accepted" else 1

    def handle(self, event: dict[str, Any], send: Any) -> bool:
        """One host event; True when the run failed."""
        self.events.append(event)
        kind = str(event.get("kind", ""))
        task = str(event.get("task_id", ""))
        who = f"debug task {task[:6]}" if task else "lead"
        if kind == "connection":
            if event.get("state") != "connected":
                self.mark("connection failed: " + str(event.get("message", "")))
                return True
            self.mark("connected")
            send({"kind": "prompt", "text": LEAD, "chat_id": "", "targets": []})
        elif kind == "progress":
            text = str(event.get("text", ""))
            if text == "Reviewing the change…":
                self.reviewing += 1
            self.mark(f"{who} step: {text}")
        elif kind == "task":
            state, role = str(event.get("state", "")), str(event.get("role", ""))
            if (state, role) != (self.debug_state, self.debug_role):
                self.debug_state, self.debug_role = state, role
                self.mark(
                    f"{who} {event.get('title')!r}: {state} as {role}"
                    + (f"; cause: {event['cause']}" if event.get("cause") else "")
                    + (f"; says: {event['message']}" if event.get("message") else "")
                )
        elif kind == "file_preview":
            identifier = str(event.get("transaction_id", ""))
            reviewer = event.get("reviewer", {})
            self.reviews.append(reviewer)
            self.mark(
                f"{who} in review: {event.get('summary', '')} | reviewer "
                f"{json.dumps(reviewer)} | notes {json.dumps(event.get('review_notes', []))}"
            )
            if identifier and identifier not in self.decided:
                self.decided.add(identifier)
                send({"kind": "decision", "transaction_id": identifier, "accept": True})
        elif kind == "file_decision":
            self.mark(f"{who} decided: accepted={event.get('accepted')}")
            if not task and not self.debug_sent:
                self.debug_sent = True
                self.mark("debug_start: " + ERROR)
                send({"kind": "debug_start", "error": ERROR, "output": OUTPUT, "log": ""})
        elif kind == "done" and not task:
            self.lead_done = True
            self.mark(f"lead done (ok={event.get('ok')})")
        elif kind == "message" and event.get("level") == "warn":
            self.mark(f"{who} warning: {event.get('text', '')}")
        elif kind == "notice":
            self.mark(f"{who} notice: {event.get('text', '')}")
        elif kind == "timing" and not task:
            self.mark("lead timing: " + json.dumps(event.get("phases_ms", {})))
        return False


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
        "Roles smoke",
        "--tools",
        "godot",
    )
    if await process.wait() != 0:
        raise RuntimeError("joinery new failed")
    (project / "scripts").mkdir(exist_ok=True)
    (project / "scripts/jumper.gd").write_text(BROKEN)
    return project


async def main() -> int:
    agent = os.environ.get("JOINERY_LIVE_AGENT", "")
    if not agent:
        print("roles smoke: skipped (set JOINERY_LIVE_AGENT=codex or claude)")
        return 0
    if agent not in AGENTS:
        print(f"roles smoke: JOINERY_LIVE_AGENT must be one of {sorted(AGENTS)}")
        return 2
    with tempfile.TemporaryDirectory(prefix="joinery-roles-smoke-") as folder:
        project = await make_project(Path(folder))
        smoke = Smoke(agent, project)
        code = await smoke.run()
        smoke.mark("jumper.gd now: " + (project / "scripts/jumper.gd").read_text().strip())
    print("roles smoke: " + ("ok" if code == 0 else "FAILED"), flush=True)
    return code


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
