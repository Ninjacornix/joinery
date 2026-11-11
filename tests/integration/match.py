"""Make everything match, end to end through the real apps in the offline workers.

A temporary project gets one asset of each kind made with a loose art bible:
an off-palette Krita texture, an oversized Krita texture, a Blender crate with
unapplied scale, a raised pivot and too smooth a material, a loud SuperCollider
sound, and an Environment with the wrong tonemap, exposure and fog. Then the
bible gains checked rules (approved again, as a user would), and:

1. ``joinery match`` audits it: every planted mismatch is found and measured;
2. "Fix all" plans the changes and runs them as parallel scripted tasks (the
   real crew, lanes, claims and worker slots), each through its owning app:
   Krita palette map and resize, Blender apply scale / pivot / material value,
   ffmpeg loudness on the SuperCollider render, Godot's manage_resource;
3. every change passes the submit checks and is accepted;
4. the re-audit is clean, and every recipe replays.

Technical fixtures only; nothing here approves art in a user's game.
Run: ``uv run python tests/integration/match.py``.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from bridge.crew import IN_REVIEW, Crew
from bridge.match_runner import MatchRun
from bridge.protocol import Prompt
from joinery import match
from joinery.art_approval import approve
from joinery.authoring import run_offline
from joinery.cli import main as cli_main
from joinery.gateway import ProjectGateway, result_text
from joinery.submission import check_and_open

LOOSE = "# Art bible (test fixture)\n\nAutomated technical fixture; no game artwork.\n"
STRICT = """# Art bible (test fixture)

Automated technical fixture; no game artwork.

## Checked rules

```toml
schema = 2
[palette]
colours = ["#203040", "#c08040", "#e0d0b0"]
max_delta_e = 12.0
max_off_palette = 0.05
[textures]
power_of_two = true
[textures.max_size]
"art" = 128
[models]
applied_scale = true
pivot_tolerance_m = 0.02
[models.pivot]
"art/models" = "bottom-centre"
[materials]
roughness = [0.4, 0.95]
[audio]
true_peak_max_dbtp = -1.0
lufs_tolerance = 1.0
[audio.lufs]
"audio/sfx" = -18.0
[environment]
tonemap = "agx"
exposure = [0.9, 1.1]
fog = false
```
"""

KRITA = (
    "document = app.createDocument({size}, {size}, 'Fixture', 'RGBA', 'U8', '', 72.0)\n"
    "layer = document.createNode('Paint', 'paintlayer')\n"
    "document.rootNode().addChildNode(layer, None)\n"
    "layer.setPixelData(bytes([{b}, {g}, {r}, 255]) * ({size} * {size}), 0, 0, {size}, {size})\n"
    "document.refreshProjection()\n"
)
CRATE = (
    "bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 1.5))\n"
    "crate = bpy.context.object\n"
    "crate.name = 'Crate'\n"
    "crate.data.name = 'Crate'\n"
    "crate.scale = (2, 2, 2)\n"
    "wood = bpy.data.materials.new('Crate_Wood')\n"
    "wood.use_nodes = True\n"
    "wood.node_tree.nodes['Principled BSDF'].inputs['Roughness'].default_value = 0.2\n"
    "crate.data.materials.append(wood)\n"
)
TONE = (
    "(\nvar definition=SynthDef(\\tone,{ Out.ar(0,SinOsc.ar(440,0,0.9)"
    "*EnvGen.kr(Env.linen(0.01,0.9,0.05),doneAction:2)) });\n"
    "var score=Score([[0.0,[\\d_recv,definition.asBytes]],"
    "[0.0,[\\s_new,\\tone,1000,0,0]],[1.0,[\\c_set,0,0]]]);\n"
    'score.recordNRT(outputFilePath:"audio/sfx/tone.wav",sampleRate:48000,'
    'headerFormat:"WAV",sampleFormat:"int16",options:ServerOptions.new.'
    "numOutputBusChannels_(1),duration:1.0,action:{0.exit});\n)\n"
)
WORLD = """[gd_resource type="Environment" format=3]

[resource]
background_mode = 1
tonemap_mode = 2
tonemap_exposure = 1.6
fog_enabled = true
"""


def stamp(start: float, text: str) -> None:
    print(f"{time.monotonic() - start:7.1f}s  {text}", flush=True)


async def made(
    gateway: ProjectGateway, server: str, tool: str, arguments: dict[str, Any]
) -> None:
    result = await gateway.call(server, tool, arguments)
    assert not result.is_error, result_text(result)


async def plant(project: Path, start: float) -> None:
    """One mismatch of each kind, made with real tools under the loose bible."""
    gateway = ProjectGateway(project)
    try:
        for name, size, colour in (
            ("wall", 64, (255, 0, 255)),
            ("floor", 512, (0x20, 0x30, 0x40)),
        ):
            r, g, b = colour
            await made(
                gateway,
                "joinery-paint",
                "operate_document",
                {
                    "tool": "krita",
                    "source": f"art/textures/{name}.kra",
                    "code": KRITA.format(size=size, r=r, g=g, b=b),
                    "exports": [f"art/textures/{name}.png"],
                },
            )
        await made(
            gateway,
            "joinery-blender-ops",
            "operate_document",
            {
                "tool": "blender",
                "source": "art/sources/crate.blend",
                "code": CRATE,
                "exports": ["art/models/crate.glb"],
            },
        )
        await made(
            gateway,
            "joinery-sound",
            "operate_document",
            {
                "tool": "supercollider",
                "source": "audio/sources/tone.scd",
                "code": TONE,
                "exports": ["audio/sfx/tone.wav"],
            },
        )
        await made(
            gateway,
            "godot",
            "write_file",
            {
                "projectPath": str(project),
                "filePath": "env/world.tres",
                "content": WORLD,
            },
        )
        preview = await check_and_open(gateway)
        assert preview and "transaction_id" in preview, preview
        await gateway.decide(preview["transaction_id"], True)
        stamp(
            start,
            "planted: "
            + ", ".join(sorted(c["path"] for c in preview["changes"]))[:300],
        )
    finally:
        await gateway.close()


def measure(argv: list[str], root: Path) -> Any:
    return run_offline(argv, root, 600, readonly=True)


async def fix_all(project: Path, start: float) -> list[dict[str, Any]]:
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
    run = MatchRun(
        crew,
        project,
        lambda event: (events.append(event), stamp(start, _line(event))),  # type: ignore[func-returns-value]
        measure,
        lambda: Prompt(text="", project_dir=str(project)),
    )
    try:
        run.start([], [])
        assert run.job is not None
        await run.job
        assert run.state == "done", run.rows
        rows = run.rows
        assert all(row["state"] == IN_REVIEW for row in rows), json.dumps(
            rows, indent=1
        )
        previews = [e for e in events if e.get("kind") == "file_preview"]
        assert all(p.get("facts") for p in previews), previews
        assert all(p.get("origin") == "joinery" for p in previews)
        for task in list(crew.tasks.values()):
            decided = await crew.decide(task.transaction_id, True)
            assert decided["accepted"], decided
            stamp(start, f"accepted: {task.title}")
        return rows
    finally:
        await crew.close()
        await gateway.close()


def _line(event: dict[str, Any]) -> str:
    states = ", ".join(f"{r['title']}={r['state']}" for r in event.get("tasks", []))
    return f"match {event['state']}" + (f": {states}" if states else "")


async def main() -> int:
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="joinery-match-") as directory:
        project = Path(directory).resolve()
        (project / "project.godot").write_text(
            'config_version=5\n[application]\nconfig/name="Match fixture"\n'
        )
        (project / "joinery.toml").write_text(
            '[tools]\nenabled = ["godot", "blender", "krita", "supercollider"]\n'
        )
        (project / "joinery.lock").write_text("version = 1\n")
        (project / "art").mkdir()
        (project / "art/bible.md").write_text(LOOSE)
        approve(project, "match fixture")
        await plant(project, start)

        (project / "art/bible.md").write_text(STRICT)
        approve(project, "match fixture")
        before = await match.audit(project, measure)
        stamp(start, "before: " + before.summary)
        found = {(f.area, f.path, f.check): f for f in before.findings}
        for finding in before.findings:
            stamp(
                start,
                f"  {finding.severity:<6} {finding.path}: {finding.measured} → {finding.target} ({finding.fix.label if finding.fix else finding.needs_you})",
            )
        expected = {
            ("palette", "art/textures/wall.png", "palette"),
            ("textures", "art/textures/floor.png", "texture_size"),
            ("models", "art/models/crate.glb", "scale"),
            ("models", "art/models/crate.glb", "pivot"),
            ("materials", "art/models/crate.glb", "roughness"),
            ("sound", "audio/sfx/tone.wav", "loudness"),
            ("lighting", "env/world.tres", "tonemap"),
            ("lighting", "env/world.tres", "exposure"),
            ("lighting", "env/world.tres", "fog"),
        }
        assert expected <= set(found), sorted(set(found))
        assert all(found[key].fix is not None for key in expected)
        assert not before.errors, before.errors

        rows = await fix_all(project, start)
        stamp(start, f"{len(rows)} changes accepted")

        after = await match.audit(project, measure)
        stamp(start, "after: " + after.summary)
        for finding in after.findings:
            stamp(start, f"  left: {finding.path}: {finding.message}")
        assert not after.findings, [f.message for f in after.findings]
        assert all(a.score == 100 for a in after.areas), after.summary
        assert not after.errors, after.errors

        rebuilt = cli_main(["rebuild", "--project", str(project)])
        assert rebuilt == 0, "a recipe did not replay"
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
        print("Make it match: audit, parallel fixes, re-audit, replay: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
