"""A live "add a chair" request against a real, signed-in provider ends as one model.

    JOINERY_LIVE_AGENT=codex uv run python tests/integration/complex_models_smoke.py

Skipped (exit 0) unless ``JOINERY_LIVE_AGENT`` names a provider (codex or claude). It
makes a project with Blender in a temporary folder, with an approved art bible and a
saved room scene holding a table (a model), starts the real chat host (``joinery
chat``) as the editor does, and asks for "add a wooden chair next to the table". With
complex models made in Blender (the default) the change that reaches review must place
the chair as one model, not a pile of placeholder shapes; it is accepted. It prints
when each milestone happened, in seconds from the start, including any send-back by
the complex models check. Fails when the host errors or nothing arrives.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import tempfile
import time
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from joinery.art_approval import approve
from joinery.complex_models import scene_findings

AGENTS = {"codex", "claude"}
TIMEOUT = float(os.environ.get("JOINERY_SMOKE_TIMEOUT", "1500"))
REQUEST = "Add a wooden chair next to the table in scenes/room.tscn."
ROOM = """[gd_scene load_steps=3 format=3]

[sub_resource type="PlaneMesh" id="PlaneMesh_floor"]
size = Vector2(8, 8)

[sub_resource type="BoxMesh" id="BoxMesh_table"]
size = Vector3(1.2, 0.75, 0.8)

[node name="Room" type="Node3D"]

[node name="Floor" type="MeshInstance3D" parent="."]
mesh = SubResource("PlaneMesh_floor")

[node name="Table" type="MeshInstance3D" parent="."]
transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0.375, 0)
mesh = SubResource("BoxMesh_table")
"""


def bible_text() -> str:
    """The art bible of the kits integration check (budgets, pivots, palette)."""
    spec = importlib.util.spec_from_file_location(
        "kits_integration", ROOT / "tests/integration/kits.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return str(module.BIBLE)


class Smoke:
    def __init__(self, agent: str, project: Path) -> None:
        self.agent = agent
        self.project = project
        self.started = time.monotonic()
        self.decided: set[str] = set()
        self.accepted = False
        self.done = False
        self.tools: list[str] = []
        self.sent_back = 0

    def mark(self, text: str) -> None:
        print(f"{time.monotonic() - self.started:7.1f}s  {text}", flush=True)

    async def run(self) -> int:
        process = await asyncio.create_subprocess_exec(
            *("uv", "run", "--frozen", "--quiet", "--project", str(ROOT), "joinery"),
            *("chat", "--agent", self.agent, "--project", str(self.project)),
            *("--addon-protocol", "2"),
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
                while not (self.done and self.accepted):
                    raw = await process.stdout.readline()
                    if not raw:
                        self.mark("the chat host exited")
                        return 1
                    try:
                        event = json.loads(raw)
                    except ValueError:
                        continue
                    if isinstance(event, dict) and (
                        failure := self.handle(event, send)
                    ):
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
        return 0

    def handle(self, event: dict[str, Any], send: Any) -> int:
        kind = str(event.get("kind", ""))
        if kind == "connection":
            if event.get("state") != "connected":
                self.mark("connection failed: " + str(event.get("message", "")))
                return 1
            self.mark("connected")
            send({"kind": "prompt", "text": REQUEST, "chat_id": "", "targets": []})
        elif kind == "progress":
            text = str(event.get("text", ""))
            self.sent_back += text == "Fixing what the checks found…"
            self.mark("step: " + text)
        elif kind == "tool":
            name = str(event.get("name") or event.get("tool") or "")
            if name:
                self.tools.append(name)
                self.mark("tool: " + name)
        elif kind == "file_preview":
            identifier = str(event.get("transaction_id", ""))
            self.mark("in review: " + str(event.get("summary", "")))
            if identifier and identifier not in self.decided:
                self.decided.add(identifier)
                send({"kind": "decision", "transaction_id": identifier, "accept": True})
        elif kind == "file_decision":
            self.accepted = bool(event.get("accepted"))
            self.mark(f"decided: accepted={self.accepted}")
            if not self.accepted:
                return 1
        elif kind == "done":
            self.done = True
            self.mark(f"done (ok={event.get('ok')})")
            if not self.decided:
                self.mark("the turn ended without a change to review")
                return 1
        elif kind == "message" and event.get("level") in {"warn", "error"}:
            self.mark("warning: " + str(event.get("text", "")))
        elif kind == "timing":
            self.mark("timing: " + json.dumps(event.get("phases_ms", {})))
        return 0


def check(project: Path) -> dict[str, Any]:
    """The room places a chair model and holds no pile of placeholder shapes."""
    room = (project / "scenes/room.tscn").read_text()
    assert scene_findings(room, "scenes/room.tscn") == [], "a placeholder pile"
    lock = tomllib.loads((project / "joinery.lock").read_text())
    models = [
        a["path"]
        for a in lock.get("asset", [])
        if a["path"].endswith(".glb") and a.get("origin") == "tool-created"
    ]
    assert models, "no tool-made model"
    assert any(Path(m).name in room for m in models), "the room doesn't place it"
    return {"models": models}


async def main() -> int:
    agent = os.environ.get("JOINERY_LIVE_AGENT", "")
    if not agent:
        print("complex models smoke: skipped (set JOINERY_LIVE_AGENT=codex or claude)")
        return 0
    if agent not in AGENTS:
        print(
            f"complex models smoke: JOINERY_LIVE_AGENT must be one of {sorted(AGENTS)}"
        )
        return 2
    with tempfile.TemporaryDirectory(prefix="joinery-complex-smoke-") as folder:
        project = Path(folder) / "smoke"
        made = await asyncio.create_subprocess_exec(
            *("uv", "run", "--frozen", "--quiet", "--project", str(ROOT), "joinery"),
            *("new", str(project), "--name", "Chair smoke", "--tools", "godot,blender"),
        )
        if await made.wait() != 0:
            print("complex models smoke: joinery new failed")
            return 1
        (project / "art").mkdir(exist_ok=True)
        (project / "art/bible.md").write_text(bible_text())
        approve(project, "complex models smoke (the user)")
        (project / "scenes").mkdir(exist_ok=True)
        (project / "scenes/room.tscn").write_text(ROOM)
        smoke = Smoke(agent, project)
        code = await smoke.run()
        if code == 0:
            smoke.mark("checked: " + json.dumps(check(project)))
        total = time.monotonic() - smoke.started
        print(
            json.dumps(
                {
                    "seconds": round(total, 1),
                    "sent_back": smoke.sent_back,
                    "tools": smoke.tools,
                }
            )
        )
        print("COMPLEX_MODELS_SMOKE_OK" if code == 0 else "COMPLEX_MODELS_SMOKE_FAILED")
        return code


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
