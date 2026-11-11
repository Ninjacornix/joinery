"""The performance check's typed fixes, end to end through the real apps in the workers.

A temporary game (the heavy scene of tests/fixtures/perf_game) gets two assets made with
real tools under an approved fixture bible: a 2048 px Krita texture and a Blender sphere
of about 65 000 triangles. Then, against the "low" target (1024 px textures, 20 000
triangles a model, one shadowed light):

1. `joinery perf` measures it: headless in stock Godot, plus the files;
2. Fix runs the typed fixes as scripted tasks of the real crew (lanes, claims, worker
   slots): Krita resizes the painting, Blender decimates the model, Godot's MCP turns
   off the extra shadows and ranges the repeated props;
3. every change passes the submit checks and is accepted;
4. measured again, those findings are gone and the scores rose; every recipe replays.

Technical fixtures only; nothing here approves art in a user's game.
Run: ``uv run python tests/integration/access_perf.py``.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from bridge.check_runner import Checks
from bridge.crew import IN_REVIEW, Crew
from bridge.protocol import Prompt
from joinery import checks_core, perf
from joinery.art_approval import approve
from joinery.cli import main as cli_main
from joinery.gateway import ProjectGateway, result_text
from joinery.submission import check_and_open

ROOT = Path(__file__).resolve().parents[2]
BIBLE = "# Art bible (test fixture)\n\nAutomated technical fixture; no game artwork.\n"
KRITA = (
    "document = app.createDocument(2048, 2048, 'Ground', 'RGBA', 'U8', '', 72.0)\n"
    "layer = document.createNode('Paint', 'paintlayer')\n"
    "document.rootNode().addChildNode(layer, None)\n"
    "layer.setPixelData(bytes([64, 96, 128, 255]) * (2048 * 2048), 0, 0, 2048, 2048)\n"
    "document.refreshProjection()\n"
)
ROCK = (
    "bpy.ops.mesh.primitive_uv_sphere_add(segments=256, ring_count=128, radius=1)\n"
    "rock = bpy.context.object\n"
    "rock.name = 'Rock'\n"
    "rock.data.name = 'Rock'\n"
)
TYPED = ("textures", "rendering", "lighting")


def stamp(start: float, text: str) -> None:
    print(f"{time.monotonic() - start:7.1f}s  {text}", flush=True)


async def plant(project: Path, start: float) -> None:
    gateway = ProjectGateway(project)
    try:
        for server, tool, source, code, export in (
            (
                "joinery-paint",
                "krita",
                "art/textures/ground.kra",
                KRITA,
                "art/textures/ground.png",
            ),
            (
                "joinery-blender-ops",
                "blender",
                "art/sources/rock.blend",
                ROCK,
                "art/models/rock.glb",
            ),
        ):
            result = await gateway.call(
                server,
                "operate_document",
                {"tool": tool, "source": source, "code": code, "exports": [export]},
            )
            assert not result.is_error, result_text(result)
        preview = await check_and_open(gateway)
        assert preview and "transaction_id" in preview, preview
        await gateway.decide(preview["transaction_id"], True)
        stamp(
            start,
            "planted: " + ", ".join(sorted(c["path"] for c in preview["changes"])),
        )
    finally:
        await gateway.close()


async def fix(
    project: Path, report: checks_core.Report, start: float
) -> list[dict[str, Any]]:
    gateway = ProjectGateway(project)
    events: list[dict[str, Any]] = []

    async def on_stop(reason: str) -> None:
        raise AssertionError("Rule Zero stop: " + reason)

    crew = Crew(
        gateway,
        events.append,
        agent="codex",
        profile="",
        limit=3,
        bridge=lambda _agent: None,  # type: ignore[arg-type,return-value]
        endpoint=lambda _lane, _stop: None,  # type: ignore[arg-type,return-value]
        on_stop=on_stop,
    )

    async def audit(_kind: str, _params: dict[str, Any]) -> checks_core.Report:
        return report

    checks = Checks(
        crew,
        project,
        lambda event: (events.append(event), stamp(start, _line(event))),  # type: ignore[func-returns-value]
        lambda: Prompt(text="", project_dir=str(project)),
        audit=audit,
    )
    typed = [
        f.id for f in report.findings if f.fix is not None and f.fix.kind == "typed"
    ]
    try:
        run = checks.runs["perf"]
        run.start(list(TYPED), typed, {"target": "low"})
        assert run.job is not None
        await run.job
        assert run.state == "done", run.rows
        assert run.rows and all(r["state"] == IN_REVIEW for r in run.rows), json.dumps(
            run.rows, indent=1
        )
        for task in list(crew.tasks.values()):
            decided = await crew.decide(task.transaction_id, True)
            assert decided["accepted"], decided
            stamp(start, f"accepted: {task.title}")
        return run.rows
    finally:
        await checks.close()
        await crew.close()
        await gateway.close()


def _line(event: dict[str, Any]) -> str:
    states = ", ".join(f"{r['title']}={r['state']}" for r in event.get("tasks", []))
    return f"perf {event['state']}" + (f": {states}" if states else "")


def _show(start: float, label: str, report: checks_core.Report) -> None:
    stamp(start, f"{label}: {report.summary}")
    for f in report.findings:
        how = f"{f.fix.kind}: {f.fix.label}" if f.fix else f.needs_you
        stamp(start, f"  {f.severity:<6} {f.path}: {f.measured} → {f.target} ({how})")


async def main() -> int:
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="joinery-perf-") as directory:
        project = Path(directory).resolve() / "game"
        shutil.copytree(ROOT / "tests/fixtures/perf_game", project)
        (project / "joinery.toml").write_text(
            '[tools]\nenabled = ["godot", "blender", "krita"]\n\n[performance]\ntarget = "low"\n'
        )
        (project / "joinery.lock").write_text("version = 1\n")
        (project / "art").mkdir()
        (project / "art/bible.md").write_text(BIBLE)
        approve(project, "perf fixture")
        await plant(project, start)

        before = perf.audit(project, "low", window=False)
        _show(start, "before", before)
        found = {(f.path, f.check) for f in before.findings}
        expected = {
            ("art/textures/ground.png", "texture_size"),
            ("art/models/rock.glb", "model_triangles"),
            ("main.tscn", "shadow_lights"),
            ("main.tscn", "visibility_range"),
        }
        assert expected <= found, sorted(found)
        rows = await fix(project, before, start)
        stamp(start, f"{len(rows)} changes accepted")

        after = perf.audit(project, "low", window=False)
        _show(start, "after", after)
        left = {(f.path, f.check) for f in after.findings}
        assert not expected & left, sorted(expected & left)
        scores = {a.id: a.score for a in after.areas}
        assert scores["lighting"] == 100 and scores["textures"] == 100, after.summary
        assert cli_main(["rebuild", "--project", str(project)]) == 0, (
            "a recipe did not replay"
        )
        stamp(start, "every recipe replays")
        print(
            json.dumps(
                {
                    "before": before.summary,
                    "after": after.summary,
                    "changes": [r["title"] for r in rows],
                }
            )
        )
        print("Performance fixes through the real apps, measured again: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
