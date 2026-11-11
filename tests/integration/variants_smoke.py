"""A quick end-to-end check of variants side by side against a real, signed-in provider.

    JOINERY_LIVE_AGENT=codex uv run python tests/integration/variants_smoke.py

Skipped (exit 0) unless ``JOINERY_LIVE_AGENT`` names a provider (codex or claude).
It makes an empty project with one small scene in a temporary folder, starts the real
chat host (``joinery chat``) as the editor does, and sends ``/variants 3`` for a tweak
of that scene, as the sidebar does. Once every way is in review (or finished without
one) it keeps the second way that reached review, as Use this does, and checks that
the others were discarded and the project has the kept way. It prints when each
milestone happened, in seconds from the start.
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
TIMEOUT = float(os.environ.get("JOINERY_SMOKE_TIMEOUT", "1200"))
SCENE = """[gd_scene format=3]

[node name="Sign" type="Node2D"]

[node name="Greeting" type="Label" parent="."]
offset_right = 200.0
offset_bottom = 40.0
text = "Hello"
"""
REQUEST = (
    "/variants 3 Restyle the Greeting label in scenes/sign.tscn: change its text to "
    "welcome the player to the cabin and set a font colour (theme_override_colors/"
    "font_color) and size (theme_override_font_sizes/font_size) that suit your direction."
)
FINAL = {"accepted", "discarded", "done", "failed", "stopped"}


class Smoke:
    def __init__(self, agent: str, project: Path) -> None:
        self.agent = agent
        self.project = project
        self.started = time.monotonic()
        self.group: dict[str, Any] = {}
        self.picked = ""
        self.kept_text = ""
        self.ready_at: dict[str, float] = {}

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

        code = 1
        try:
            async with asyncio.timeout(TIMEOUT):
                while True:
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
                    result = self.handle(event, send)
                    await writer.drain()
                    if result is not None:
                        code = result
                        break
        except TimeoutError:
            self.mark(f"timed out after {TIMEOUT:.0f}s")
            return 1
        finally:
            writer.close()
            try:
                await asyncio.wait_for(process.wait(), 30)
            except TimeoutError:
                process.kill()
        return code

    def handle(self, event: dict[str, Any], send: Any) -> int | None:
        kind = str(event.get("kind", ""))
        task = str(event.get("task_id", ""))[:6]
        if kind == "connection":
            if event.get("state") != "connected":
                self.mark("connection failed: " + str(event.get("message", "")))
                return 1
            self.mark(f"connected ({event.get('parallel')} turns at once)")
            send(
                {
                    "kind": "variants_start",
                    "text": REQUEST,
                    "targets": [],
                    "chat_id": "",
                }
            )
        elif kind == "task":
            self.mark(
                f"way {event.get('variant')} {task} ({event.get('direction')}): "
                f"{event.get('state')} {event.get('step', '')}".rstrip()
            )
            if event.get("state") == "in_review" and task not in self.ready_at:
                self.ready_at[task] = time.monotonic() - self.started
        elif kind == "file_preview" and task:
            self.mark(f"way {task} in review: {event.get('summary', '')}")
        elif kind == "variants":
            self.group = event
            ways = event.get("ways", [])
            states = [str(w.get("state")) for w in ways]
            self.mark(f"group {event.get('state')}: " + ", ".join(states))
            if event.get("state") in {"picked", "discarded"}:
                return self.check(event)
            ready = [w for w in ways if w.get("state") == "in_review"]
            if (
                not self.picked
                and ways
                and all(s in FINAL | {"in_review"} for s in states)
                and ready
            ):
                chosen = ready[1] if len(ready) > 1 else ready[0]
                self.picked = str(chosen["task_id"])
                self.mark(
                    f"keeping way {chosen.get('variant')}: {chosen.get('direction')}"
                )
                send(
                    {
                        "kind": "variants_pick",
                        "group_id": event["group_id"],
                        "task_id": self.picked,
                    }
                )
            elif ways and all(s in FINAL for s in states):
                self.mark("no way reached review")
                return 1
        elif kind == "file_decision":
            self.mark(f"way {task} decided: accepted={event.get('accepted')}")
        elif kind in {"message", "notice"} and event.get("level") == "warn":
            self.mark(
                ("way " + task + " " if task else "")
                + "warning: "
                + str(event.get("text"))
            )
        return None

    def check(self, event: dict[str, Any]) -> int:
        if event.get("state") != "picked" or event.get("picked") != self.picked:
            self.mark("the pick didn't hold")
            return 1
        others = [w for w in event.get("ways", []) if w.get("task_id") != self.picked]
        if any(
            w.get("state") not in {"discarded", "stopped", "failed", "done"}
            for w in others
        ):
            self.mark("another way was left open")
            return 1
        scene = (self.project / "scenes/sign.tscn").read_text(encoding="utf-8")
        self.mark(
            "kept scene text: "
            + " ".join(line for line in scene.splitlines() if "text" in line)
        )
        return 0 if 'text = "Hello"' not in scene else 1


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
        "Variants smoke",
        "--tools",
        "godot",
    )
    if await process.wait() != 0:
        raise RuntimeError("joinery new failed")
    (project / "scenes").mkdir(exist_ok=True)
    (project / "scenes/sign.tscn").write_text(SCENE, encoding="utf-8")
    return project


async def main() -> int:
    agent = os.environ.get("JOINERY_LIVE_AGENT", "")
    if not agent:
        print("variants smoke: skipped (set JOINERY_LIVE_AGENT=codex or claude)")
        return 0
    if agent not in AGENTS:
        print(f"variants smoke: JOINERY_LIVE_AGENT must be one of {sorted(AGENTS)}")
        return 2
    with tempfile.TemporaryDirectory(prefix="joinery-variants-smoke-") as folder:
        project = await make_project(Path(folder))
        smoke = Smoke(agent, project)
        code = await smoke.run()
        for task, seconds in sorted(smoke.ready_at.items(), key=lambda item: item[1]):
            smoke.mark(f"way {task} was ready after {seconds:.1f}s")
    print("variants smoke: " + ("ok" if code == 0 else "FAILED"), flush=True)
    return code


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
