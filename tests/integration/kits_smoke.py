"""A live kits request against a real, signed-in provider: mossy rocks scattered by a path.

    JOINERY_LIVE_AGENT=codex uv run python tests/integration/kits_smoke.py

Skipped (exit 0) unless ``JOINERY_LIVE_AGENT`` names a provider (codex or claude). It
makes a project with Blender in a temporary folder, with an approved art bible and a
saved level scene (a ground and a Path3D called Path), starts the real chat host
(``joinery chat``) as the editor does, and asks for "20 mossy rocks scattered by the
path". The agent should use kit_batch and scatter; the change that reaches review is
accepted. It prints when each milestone happened, in seconds from the start, and checks
that the accepted project has twenty kit-made rocks, the level places them beside the
path, and every rock has a lock entry. Fails when the host errors or nothing arrives.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import re
import sys
import tempfile
import time
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from joinery.art_approval import approve

AGENTS = {"codex", "claude"}
TIMEOUT = float(os.environ.get("JOINERY_SMOKE_TIMEOUT", "1500"))
REQUEST = (
    "Make 20 mossy rocks and scatter them by the path in scenes/level.tscn, "
    "on both sides of it, not on it."
)
LEVEL = """[gd_scene load_steps=4 format=3]

[sub_resource type="PlaneMesh" id="PlaneMesh_ground"]
size = Vector2(40, 40)

[sub_resource type="Curve3D" id="Curve3D_path"]
_data = {
"points": PackedVector3Array(0, 0, 0, 0, 0, 0, -18, 0, -3, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 18, 0, 4),
"tilts": PackedFloat32Array(0, 0, 0)
}
point_count = 3

[node name="Level" type="Node3D"]

[node name="Ground" type="MeshInstance3D" parent="."]
mesh = SubResource("PlaneMesh_ground")

[node name="Path" type="Path3D" parent="."]
curve = SubResource("Curve3D_path")
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
        self.marks: list[tuple[float, str]] = []
        self.decided: set[str] = set()
        self.accepted = False
        self.done = False
        self.tools: list[str] = []

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
                while not (self.done and self.accepted):
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
            self.mark("step: " + str(event.get("text", "")))
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
    """Twenty kit-made rocks in the lock, placed in the level beside the path."""
    lock = tomllib.loads((project / "joinery.lock").read_text())
    made = [
        a["path"]
        for a in lock.get("asset", [])
        if a["path"].endswith(".glb") and a.get("origin") == "tool-created"
    ]
    rocks = [p for p in made if "_lod" not in p]
    assert len(rocks) >= 20, rocks
    level = (project / "scenes/level.tscn").read_text()
    placed = re.findall(
        r"instance=ExtResource\([^)]*\)\]\ntransform = Transform3D\(([^)]*)\)", level
    )
    assert len(placed) >= 20, len(placed)
    on_path = 0
    for row in placed:
        x, _y, z = (float(v) for v in row.split(",")[9:12])
        # The path runs from (-18, -3) through (0, 0) to (18, 4).
        a, b = ((-18.0, -3.0), (0.0, 0.0)) if x < 0 else ((0.0, 0.0), (18.0, 4.0))
        dx, dz = b[0] - a[0], b[1] - a[1]
        t = max(
            0.0, min(1.0, ((x - a[0]) * dx + (z - a[1]) * dz) / (dx * dx + dz * dz))
        )
        gap = ((x - a[0] - t * dx) ** 2 + (z - a[1] - t * dz) ** 2) ** 0.5
        on_path += gap < 0.5
    assert on_path == 0, f"{on_path} rocks on the path"
    recipes = [
        p
        for p in (project / "recipes").glob("*.py")
        if "# joinery-kit: " in p.read_text()[:4000]
    ]
    assert recipes, "no kit recipe"
    return {"rocks": len(rocks), "placed": len(placed), "kit_recipes": len(recipes)}


async def main() -> int:
    agent = os.environ.get("JOINERY_LIVE_AGENT", "")
    if not agent:
        print("kits smoke: skipped (set JOINERY_LIVE_AGENT=codex or claude)")
        return 0
    if agent not in AGENTS:
        print(f"kits smoke: JOINERY_LIVE_AGENT must be one of {sorted(AGENTS)}")
        return 2
    with tempfile.TemporaryDirectory(prefix="joinery-kits-smoke-") as folder:
        project = Path(folder) / "smoke"
        made = await asyncio.create_subprocess_exec(
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
            "Kits smoke",
            "--tools",
            "godot,blender",
        )
        if await made.wait() != 0:
            print("kits smoke: joinery new failed")
            return 1
        (project / "art").mkdir(exist_ok=True)
        (project / "art/bible.md").write_text(bible_text())
        approve(project, "kits smoke (the user)")
        (project / "scenes").mkdir(exist_ok=True)
        (project / "scenes/level.tscn").write_text(LEVEL)
        smoke = Smoke(agent, project)
        code = await smoke.run()
        if code == 0:
            facts = check(project)
            smoke.mark("checked: " + json.dumps(facts))
        total = time.monotonic() - smoke.started
        print(json.dumps({"seconds": round(total, 1), "tools": smoke.tools}))
        print("KITS_SMOKE_OK" if code == 0 else "KITS_SMOKE_FAILED")
        return code


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
