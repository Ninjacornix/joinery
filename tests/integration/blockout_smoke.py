"""A live /blockout against a real, signed-in provider, on a fresh third-person game.

    JOINERY_LIVE_AGENT=codex uv run python tests/integration/blockout_smoke.py [name]

Skipped (exit 0) unless ``JOINERY_LIVE_AGENT`` names a provider (codex or claude). It
makes a game from the starting point that fits the sample (default ``keep``,
third-person), copies the sample sketch from tests/fixtures/blockout into the chat's
attachment folder as the sidebar does, starts the real chat host (``joinery chat``) and
sends ``/blockout`` with the sketch attached. The agent must call ``blockout``, fix what
it reports, and submit; the review must lead with the walk's fact and show the plan
beside the sketch. The change is accepted, and the layout the agent wrote is compared
with the one the sketch was drawn from (``joinery.blockout.accuracy``). It prints each
milestone in seconds from the start, the facts, and the accuracy, and fails when the
host errors, nothing reaches review or the accepted scene is missing.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/fixtures/blockout"
AGENTS = {"codex", "claude"}
TIMEOUT = float(os.environ.get("JOINERY_SMOKE_TIMEOUT", "900"))
BLUEPRINTS = {
    "keep": "third-person-3d",
    "house": "first-person-3d",
    "cliffs": "platformer-2d",
    "dungeon": "top-down",
}


class Smoke:
    def __init__(self, agent: str, project: Path, sketch: Path) -> None:
        self.agent = agent
        self.project = project
        self.sketch = sketch
        self.started = time.monotonic()
        self.preview: dict[str, Any] = {}
        self.decision: dict[str, Any] = {}
        self.calls: list[str] = []

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

        async def send(line: dict[str, Any]) -> None:
            writer.write((json.dumps(line) + "\n").encode())
            await writer.drain()

        try:
            async with asyncio.timeout(TIMEOUT):
                while not self.decision:
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
                        self.mark("connected; /blockout with the sketch attached")
                        await send(
                            {
                                "kind": "prompt",
                                "text": "/blockout",
                                "chat_id": "",
                                "attachments": [str(self.sketch)],
                            }
                        )
                    elif kind == "progress":
                        self.mark("  " + str(event.get("text", "")))
                    elif kind == "tool_call" or kind == "step":
                        self.calls.append(json.dumps(event)[:160])
                    elif kind == "file_preview":
                        self.preview = event
                        self.mark("in review: " + " · ".join(event.get("facts", [])))
                        await send(
                            {
                                "kind": "decision",
                                "transaction_id": event["transaction_id"],
                                "accept": True,
                            }
                        )
                    elif kind == "file_decision":
                        self.decision = event
                        self.mark(
                            "accepted" if event.get("accepted") else "not accepted"
                        )
                    elif kind == "done" and not self.preview:
                        self.mark("the turn ended without a review")
                        return 1
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
        return 0


def make_project(folder: Path, blueprint: str) -> Path:
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
            "Blockout smoke",
            "--tools",
            "godot",
            "--blueprint",
            blueprint,
        ],
        check=True,
        timeout=300,
    )
    return project


def main() -> int:
    agent = os.environ.get("JOINERY_LIVE_AGENT", "")
    name = sys.argv[1] if len(sys.argv) > 1 else "keep"
    if not agent:
        print("blockout smoke: skipped (set JOINERY_LIVE_AGENT=codex or claude)")
        return 0
    if agent not in AGENTS or name not in BLUEPRINTS:
        print(
            f"blockout smoke: agent in {sorted(AGENTS)}, sample in {sorted(BLUEPRINTS)}"
        )
        return 2
    sys.path.insert(0, str(ROOT))
    from joinery.blockout.accuracy import compare
    from joinery.blockout.layout import parse

    with tempfile.TemporaryDirectory(prefix="joinery-blockout-smoke-") as folder:
        project = make_project(Path(folder), BLUEPRINTS[name])
        attachments = project / ".joinery/attachments"
        attachments.mkdir(parents=True, exist_ok=True)
        sketch = attachments / f"{name}-sketch.png"
        shutil.copyfile(FIXTURES / f"{name}.png", sketch)
        smoke = Smoke(agent, project, sketch)
        code = asyncio.run(smoke.run())
        layouts = sorted((project / "design/blockouts").glob("*.toml"))
        problems: list[str] = []
        if code:
            problems.append("the run did not finish")
        if not smoke.preview:
            problems.append("nothing reached review")
        else:
            facts = smoke.preview.get("facts", [])
            if not facts or not str(facts[0]).startswith("Walkable:"):
                problems.append(f"the review doesn't lead with the walk: {facts}")
            item = (smoke.preview.get("media") or [{}])[0]
            if "reference" not in item:
                problems.append("the review doesn't show the sketch")
        if not layouts:
            problems.append("no layout was accepted")
        else:
            got = parse(layouts[0].read_text(encoding="utf-8"))
            want = parse((FIXTURES / f"{name}.toml").read_text(encoding="utf-8"))
            scene = project / f"game/{got.name}_blockout.tscn"
            if not scene.is_file():
                problems.append(f"{scene.name} is missing")
            smoke.mark("accuracy: " + json.dumps(compare(want, got)))
        smoke.mark(f"total {time.monotonic() - smoke.started:.0f}s")
        for problem in problems:
            smoke.mark("FAILED: " + problem)
        code = 1 if problems else 0
    print("blockout smoke: " + ("ok" if code == 0 else "FAILED"), flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
