"""A quick end-to-end check of "Make it match" against a real, signed-in provider.

    JOINERY_LIVE_AGENT=codex uv run python tests/integration/match_smoke.py

Skipped (exit 0) unless ``JOINERY_LIVE_AGENT`` names a provider (codex or claude).
It makes an empty project in a temporary folder, gives its art bible a look
(tonemap, exposure, fog) that the project's Environment doesn't have, starts the
real chat host (``joinery chat``) as the editor does and sends ``match_fix``, as
Fix all does. The host audits, plans and runs the fix as a scripted task through
the Godot MCP in the worker; the provider's reviewer agent reads the change
before it opens. Every change that reaches review is accepted, then the project
is audited again. It prints when each milestone happened, in seconds from the
start, and fails when the host errors, a task fails or the lighting still drifts.
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

from joinery import match
from joinery.art_approval import approve
from joinery.authoring import run_offline

ROOT = Path(__file__).resolve().parents[2]
AGENTS = {"codex", "claude"}
TIMEOUT = float(os.environ.get("JOINERY_SMOKE_TIMEOUT", "900"))
RULES = """

## Checked rules (match smoke)

```toml
schema = 2
[environment]
tonemap = "agx"
exposure = [0.9, 1.1]
fog = false
```
"""
WORLD = """[gd_resource type="Environment" format=3]

[resource]
background_mode = 1
tonemap_mode = 2
tonemap_exposure = 1.6
fog_enabled = true
"""


class Smoke:
    def __init__(self, agent: str, project: Path) -> None:
        self.agent = agent
        self.project = project
        self.started = time.monotonic()
        self.tasks: dict[str, str] = {}
        self.decided: set[str] = set()
        self.run_state = ""
        self.reviewers: list[str] = []

    def mark(self, text: str) -> None:
        print(f"{time.monotonic() - self.started:7.1f}s  {text}", flush=True)

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
            self.mark(f"run {self.run_state}; tasks not accepted: {failed}")
            return 1
        return 0

    def handle(self, event: dict[str, Any], send: Any) -> bool:
        kind = str(event.get("kind", ""))
        task = str(event.get("task_id", ""))
        if kind == "connection":
            if event.get("state") != "connected":
                self.mark("connection failed: " + str(event.get("message", "")))
                return True
            self.mark(f"connected ({event.get('parallel')} turns at once); Fix all")
            send({"kind": "match_fix", "areas": [], "findings": []})
        elif kind == "match":
            self.run_state = str(event.get("state", ""))
            rows = ", ".join(
                f"{r['title']}={r['state']}" for r in event.get("tasks", [])
            )
            audit = event.get("audit") or {}
            summary = f" ({audit.get('summary')})" if audit else ""
            self.mark(f"match {self.run_state}{summary}: {rows}")
            if event.get("message"):
                self.mark("match says: " + str(event["message"]))
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
                f"reviewer {reviewer.get('status')}"
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
        "Match smoke",
        "--tools",
        "godot",
    )
    if await process.wait() != 0:
        raise RuntimeError("joinery new failed")
    bible = project / "art/bible.md"
    bible.write_text(bible.read_text() + RULES)
    approve(project, "match smoke fixture")
    (project / "env").mkdir()
    (project / "env/world.tres").write_text(WORLD)
    return project


async def main() -> int:
    agent = os.environ.get("JOINERY_LIVE_AGENT", "")
    if not agent:
        print("match smoke: skipped (set JOINERY_LIVE_AGENT=codex or claude)")
        return 0
    if agent not in AGENTS:
        print(f"match smoke: JOINERY_LIVE_AGENT must be one of {sorted(AGENTS)}")
        return 2
    with tempfile.TemporaryDirectory(prefix="joinery-match-smoke-") as folder:
        project = await make_project(Path(folder))
        smoke = Smoke(agent, project)
        code = await smoke.run()
        after = await match.audit(
            project, lambda argv, root: run_offline(argv, root, 600, readonly=True)
        )
        smoke.mark("after: " + (after.summary or "nothing checked"))
        smoke.mark("reviewer: " + ", ".join(smoke.reviewers))
        lighting = [f for f in after.findings if f.area == "lighting"]
        if lighting:
            smoke.mark("still drifts: " + "; ".join(f.message for f in lighting))
            code = 1
    print("match smoke: " + ("ok" if code == 0 else "FAILED"), flush=True)
    return code


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
