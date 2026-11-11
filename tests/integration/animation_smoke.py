"""A live "give the hero a bouncier run" against a real, signed-in provider.

    JOINERY_LIVE_AGENT=codex uv run python tests/integration/animation_smoke.py

Skipped (exit 0) unless ``JOINERY_LIVE_AGENT`` names a provider (codex or claude).
It makes a third-person game with Blender enabled in a temporary folder, builds the
MPFB2 + Rigify hero with a run made by make_cycle (as the integration test does),
starts the real chat host (``joinery chat``) as the editor does, and sends the
request with the run picked as its target chip, as the Blender tab's animation list
does. The change that reaches review is accepted. It prints each milestone in
seconds from the start and fails when the host errors, nothing reaches review, or
the run is not bouncier afterwards.
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
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from blender_rig import BUILD

from joinery import parts
from joinery.authoring import edit_document, run_offline
from joinery.bible_rules import check_rules
from joinery.locking import write_lock
from joinery.tools import blender_anim
from servers.blender_ops import animation

AGENTS = {"codex", "claude"}
TIMEOUT = float(os.environ.get("JOINERY_SMOKE_TIMEOUT", "1500"))
REQUEST = "give the hero a bouncier run"
HERO, HERO_GLB = "art/sources/hero.blend", "art/models/hero.glb"


def mark(started: float, text: str) -> None:
    print(f"{time.monotonic() - started:7.1f}s  {text}", flush=True)


async def bounce(project: Path) -> float:
    got = await run_offline(
        [
            "blender",
            "--background",
            HERO,
            "--python-expr",
            blender_anim.measure_code(HERO, "", ["run"]),
        ],
        project,
        300,
        readonly=True,
    )
    rows = blender_anim.animation_rows(got.output)
    if not rows:
        raise RuntimeError("could not measure the run: " + got.output[-1500:])
    return float(rows[0]["bounce"])


async def make_project(folder: Path, started: float) -> tuple[Path, dict[str, str]]:
    project = folder / "smoke"
    await asyncio.to_thread(
        subprocess.run,
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
            "Animation smoke",
            "--tools",
            "godot,blender",
            "--blueprint",
            "third-person-3d",
        ],
        check=True,
        timeout=300,
    )
    mark(started, "project made")
    # The hero is made the way a reviewed change makes it: in a stage, by recipes.
    stage = folder / "build" / "stage"
    stage.mkdir(parents=True)
    (stage.parent / "journal.json").write_text(json.dumps({"state": "staging"}))
    shutil.copy(project / "joinery.toml", stage / "joinery.toml")
    (stage / "project.godot").write_text("config_version=5\n")
    await edit_document(stage, "blender", HERO, BUILD, [HERO_GLB], "the hero")
    await animation.make_cycle(stage, HERO, "run", None, "", None)
    # The hero stands in for one that passed review, so it must pass the
    # project's art bible as built; the request's checks then judge only the edit.
    shutil.copy(project / "art/bible.md", stage / "art/bible.md")
    found = await check_rules(
        stage,
        [HERO, HERO_GLB],
        lambda argv, root: run_offline(argv, root, 300, readonly=True),
    )
    for line in found:
        mark(started, "hero breaks the art bible: " + line)
    if found:
        raise RuntimeError("the hero as built does not pass the art bible's checks")
    for folder_name in ("art", "recipes"):
        shutil.copytree(stage / folder_name, project / folder_name, dirs_exist_ok=True)
    write_lock(project)
    await asyncio.to_thread(
        subprocess.run, ["git", "-C", str(project), "add", "-A"], check=False
    )
    await asyncio.to_thread(
        subprocess.run,
        ["git", "-C", str(project), "commit", "-qm", "The hero with a run"],
        check=False,
    )
    mark(started, "hero with a run built")
    report = await parts.report(project, HERO_GLB)
    run = next(
        p for p in report.parts if p.part_kind == "animation" and p.action == "run"
    )
    return project, run.target()


async def chat(
    agent: str, project: Path, target: dict[str, str], started: float
) -> int:
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
        agent,
        "--project",
        str(project),
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

    accepted = None
    rounds = 0
    try:
        async with asyncio.timeout(TIMEOUT):
            while accepted is None:
                raw = await process.stdout.readline()
                if not raw:
                    mark(started, "the chat host exited")
                    return 1
                try:
                    event = json.loads(raw)
                except ValueError:
                    continue
                kind = event.get("kind") if isinstance(event, dict) else None
                if kind == "connection":
                    if event.get("state") != "connected":
                        mark(started, "connection failed: " + str(event.get("message")))
                        return 1
                    mark(started, f"connected; {REQUEST!r} with {target['label']}")
                    send(
                        {
                            "kind": "prompt",
                            "text": REQUEST,
                            "chat_id": "",
                            "targets": [target],
                        }
                    )
                    await writer.drain()
                elif kind == "progress":
                    mark(started, "step: " + str(event.get("text", "")))
                elif kind == "file_preview":
                    mark(started, "in review: " + str(event.get("summary", "")))
                    mark(
                        started,
                        f"rounds: {rounds + 1} ({rounds} sent back by the checks)",
                    )
                    mark(started, "facts: " + json.dumps(event.get("facts", [])))
                    send(
                        {
                            "kind": "decision",
                            "transaction_id": event.get("transaction_id"),
                            "accept": True,
                        }
                    )
                    await writer.drain()
                elif kind == "file_decision":
                    accepted = bool(event.get("accepted"))
                    mark(started, f"decided: accepted={accepted}")
                elif kind == "done" and accepted is None:
                    mark(started, f"turn done before a review (ok={event.get('ok')})")
                    return 1
                elif kind == "check_problems":
                    rounds += 1
                    end = "final" if event.get("final") else "sent back to the agent"
                    mark(
                        started,
                        f"checks failed (attempt {event.get('attempt')}, {end}):",
                    )
                    for problem in event.get("problems", []):
                        mark(
                            started,
                            f"  [{problem.get('check')}] {problem.get('message')}",
                        )
                elif kind == "timing":
                    mark(started, "timing: " + json.dumps(event.get("phases_ms", {})))
                elif kind == "message" and event.get("level") == "warn":
                    mark(started, "warning: " + str(event.get("text", "")))
    except TimeoutError:
        mark(started, f"timed out after {TIMEOUT:.0f}s")
        return 1
    finally:
        writer.close()
        try:
            await asyncio.wait_for(process.wait(), 30)
        except TimeoutError:
            process.kill()
    return 0 if accepted else 1


async def main() -> int:
    agent = os.environ.get("JOINERY_LIVE_AGENT", "")
    if not agent:
        print("animation smoke: skipped (set JOINERY_LIVE_AGENT=codex or claude)")
        return 0
    if agent not in AGENTS:
        print(f"animation smoke: JOINERY_LIVE_AGENT must be one of {sorted(AGENTS)}")
        return 2
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="joinery-animation-smoke-") as folder:
        project, target = await make_project(Path(folder), started)
        before = await bounce(project)
        mark(started, f"run bounce before: {before * 100:.1f} cm")
        code = await chat(agent, project, target, started)
        after = await bounce(project)
        mark(
            started,
            f"run bounce after: {after * 100:.1f} cm ({(after / before - 1) * 100:+.0f}%)",
        )
        if after <= before * 1.05:
            mark(started, "FAILED: the run is not bouncier")
            code = 1
    print("animation smoke: " + ("ok" if code == 0 else "FAILED"), flush=True)
    return code


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
