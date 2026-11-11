"""A quick end-to-end check of the sound pass against a real, signed-in provider.

    JOINERY_LIVE_AGENT=codex uv run python tests/integration/sound_smoke.py

Skipped (exit 0) unless ``JOINERY_LIVE_AGENT`` names a provider (codex or claude).
It makes a third-person game in a temporary folder with ``joinery new`` (SuperCollider,
Surge XT and Godot enabled), starts the real chat host (``joinery chat``) as the editor
does and sends ``sound_pass_start``, as "/sound-pass" does. The host listens to the
level, plans and makes every sound as a scripted task in the offline workers; the
provider runs as the read-only sound designer and writes its report, and its reviewer
agent reads each change before it opens. Every change that reaches review is
accepted, then the level is listened to again. It prints when each milestone
happened, in seconds from the start, and fails when the host errors, a task fails or
a sound is still missing.
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

from joinery import sound_pass

ROOT = Path(__file__).resolve().parents[2]
AGENTS = {"codex", "claude"}
TIMEOUT = float(os.environ.get("JOINERY_SMOKE_TIMEOUT", "1500"))
STUBS = Path(__file__).resolve().parent / "sound_pass.py"


class Smoke:
    def __init__(self, agent: str, project: Path) -> None:
        self.agent = agent
        self.project = project
        self.started = time.monotonic()
        self.tasks: dict[str, str] = {}
        self.decided: set[str] = set()
        self.run_state = ""
        self.summary = ""
        self.reviewers: list[str] = []
        self.milestones: dict[str, float] = {}

    def mark(self, text: str, milestone: str = "") -> None:
        now = time.monotonic() - self.started
        if milestone and milestone not in self.milestones:
            self.milestones[milestone] = round(now, 1)
        print(f"{now:7.1f}s  {text}", flush=True)

    def finished(self) -> bool:
        final = {"accepted", "discarded", "done", "failed", "stopped"}
        return self.run_state in {"done", "failed", "stopped"} and all(
            state in final for state in self.tasks.values()
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
        if failed or self.run_state != "done":
            self.mark(f"sound pass {self.run_state}; tasks not accepted: {failed}")
            return 1
        return 0

    def handle(self, event: dict[str, Any], send: Any) -> bool:
        kind = str(event.get("kind", ""))
        task = str(event.get("task_id", ""))
        if kind == "connection":
            if event.get("state") != "connected":
                self.mark("connection failed: " + str(event.get("message", "")))
                return True
            self.mark(
                f"connected ({event.get('parallel')} turns at once); /sound-pass",
                "connected",
            )
            send({"kind": "sound_pass_start", "focus": "", "scene": ""})
        elif kind == "sound_pass":
            self.run_state = str(event.get("state", ""))
            rows = ", ".join(
                f"{r['title']}={r['state']}" for r in event.get("tasks", [])
            )
            audit = event.get("audit") or {}
            headline = f" ({audit.get('headline')})" if audit else ""
            self.mark(f"sound pass {self.run_state}{headline}: {rows}", self.run_state)
            if event.get("summary") and not self.summary:
                self.summary = str(event["summary"])
                self.mark(
                    "sound designer: " + self.summary.replace("\n", " "), "report"
                )
            if event.get("message"):
                self.mark("sound pass says: " + str(event["message"]))
        elif kind == "task":
            state = str(event.get("state", ""))
            if self.tasks.get(task) != state:
                self.tasks[task] = state
                self.mark(
                    f"task {event.get('title')!r}: {state} {event.get('message', '')}"
                )
        elif kind == "progress" and task:
            self.mark(f"task step: {event.get('text', '')}")
        elif kind == "file_preview":
            identifier = str(event.get("transaction_id", ""))
            reviewer = event.get("reviewer", {})
            self.reviewers.append(str(reviewer.get("status", "")))
            self.mark(
                f"in review: {event.get('summary', '')}; facts {event.get('facts')}; "
                f"reviewer {reviewer.get('status')}",
                "first_review",
            )
            if identifier and identifier not in self.decided:
                self.decided.add(identifier)
                send({"kind": "decision", "transaction_id": identifier, "accept": True})
        elif kind == "file_decision":
            self.mark(f"decided: accepted={event.get('accepted')}")
        elif kind == "message" and event.get("level") == "warn":
            self.mark("warning: " + str(event.get("text", "")))
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
        "Sound smoke",
        "--tools",
        "godot,supercollider,surge-xt",
        "--blueprint",
        "third-person-3d",
        "--features",
        "",
    )
    if await process.wait() != 0:
        raise RuntimeError("joinery new failed")
    # A checkout whose game core has no sound nodes yet gets the contract's stubs.
    sys.path.insert(0, str(STUBS.parent))
    from sound_pass import core_sound_nodes

    core_sound_nodes(project)
    return project


async def main() -> int:
    agent = os.environ.get("JOINERY_LIVE_AGENT", "")
    if not agent:
        print("sound smoke: skipped (set JOINERY_LIVE_AGENT=codex or claude)")
        return 0
    if agent not in AGENTS:
        print(f"sound smoke: JOINERY_LIVE_AGENT must be one of {sorted(AGENTS)}")
        return 2
    with tempfile.TemporaryDirectory(prefix="joinery-sound-smoke-") as folder:
        project = await make_project(Path(folder))
        before = sound_pass.audit(project)
        smoke = Smoke(agent, project)
        smoke.mark("before: " + before.headline)
        code = await smoke.run()
        after = sound_pass.audit(project)
        smoke.mark("after: " + after.headline, "after")
        smoke.mark("reviewer: " + ", ".join(smoke.reviewers))
        if after.findings:
            smoke.mark("still missing: " + "; ".join(f.missing for f in after.findings))
            code = 1
        if not smoke.summary:
            smoke.mark("the sound designer wrote no report")
        print(
            json.dumps(
                {"milestones": smoke.milestones, "report": smoke.summary}, indent=1
            )
        )
    print("sound smoke: " + ("ok" if code == 0 else "FAILED"), flush=True)
    return code


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
