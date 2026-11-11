"""The sound pass end to end through the real apps in the offline workers.

``sweep``: a temporary third-person game (the real blueprint, with SuperCollider,
Surge XT and Godot enabled) is listened to, and the sound pass's plan runs as
parallel scripted tasks of the real crew (lanes, claims, worker slots): every sound
is made by ``make_sound`` in SuperCollider or Surge XT in the creative worker and
every scene is patched through Godot's ``write_file``. Each change passes the submit
checks and is accepted. Then:

1. stock Godot loads every patched scene headless, with the Footsteps, SoundCues,
   AmbienceZone and UI sound settings in place and every stream loading;
2. every sound meets its loudness target within the bible's tolerance, under its
   true-peak ceiling;
3. ``rebuild_one`` on every recipe reproduces every file byte for byte;
4. a second sound pass finds nothing missing.

Sections are async functions in ``SECTIONS``; each prints its timings and returns a
summary. Technical fixtures only; nothing here approves art in a user's game.
Run: ``uv run python tests/integration/sound_pass.py [sweep|all]``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from bridge.crew import IN_REVIEW, Crew
from bridge.protocol import Prompt
from bridge.sound_pass import SoundPassRun
from joinery import scene_text, sound_pass
from joinery.authoring import IMAGE, run_offline
from joinery.bible_rules import _MEASURE, load_rules, parse_loudness
from joinery.catalog import ROOT
from joinery.cli import main as cli_main
from joinery.gateway import ProjectGateway
from joinery.playtest import STOCK_GODOT
from joinery.recipe import load_recipe, rebuild_one, recipe_paths
from joinery.runner import SandboxRunner
from joinery.tools.audio_chain import folder_value

TOOLS = '[tools]\nenabled = ["godot", "supercollider", "surge-xt"]\n'
#: The game core's sound nodes, when this checkout's blueprint core has none yet.
STUBS = {
    "footsteps.gd": (
        "extends Node\n@export var surfaces: Dictionary = {}\n@export var stride: float = 1.7\n"
        "@export var jump: AudioStream\n@export var land: AudioStream\n"
        '@export var bus: StringName = &"SFX"\n'
    ),
    "sound_cue.gd": (
        "extends Node\n@export var stream: AudioStream\n"
        '@export var bus: StringName = &"SFX"\n@export var trigger: StringName = &""\n'
        '@export var only_group: StringName = &"player"\n'
    ),
    "ambience_zone.gd": (
        "extends Area3D\n@export var stream: AudioStream\n"
        "@export var volume_db: float = -6.0\n"
    ),
    "ui_sounds.gd": "extends Node\n",
}
LOAD_CHECK = """extends SceneTree
func _initialize() -> void:
	var report := {}
	for path: String in %s:
		var packed: PackedScene = load(path)
		if packed == null:
			report[path] = "not loaded"
			continue
		var node: Node = packed.instantiate()
		var found := []
		for child: Node in node.find_children("*", "", true, false):
			for key: String in ["stream", "jump", "land"]:
				if child.get(key) is AudioStream:
					found.append("%%s.%%s" %% [node.get_path_to(child), key])
			if child.get("surfaces") is Dictionary:
				for surface: Variant in child.get("surfaces"):
					if child.get("surfaces")[surface] is AudioStream:
						found.append("%%s.surfaces.%%s" %% [node.get_path_to(child), surface])
		report[path] = found
		node.free()
	for key: String in ["audio/ui/click", "audio/ui/hover"]:
		var value := str(ProjectSettings.get_setting(key, ""))
		report[key] = value if load(value) is AudioStream else "missing"
	var loop: Variant = null
	for path: String in %s:
		var stream: Variant = load(path)
		loop = stream.loop if stream is AudioStreamOggVorbis else null
		report[path] = loop
	print("SOUND_LOAD " + JSON.stringify(report))
	quit()
"""


def stamp(start: float, text: str) -> None:
    print(f"{time.monotonic() - start:7.1f}s  {text}", flush=True)


def godot() -> Path:
    """Stock Godot 4.7: ``JOINERY_TEST_GODOT``, else the one Joinery ships in .tools."""
    for candidate in (os.environ.get("JOINERY_TEST_GODOT", ""), str(STOCK_GODOT)):
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise SystemExit(
        "Stock Godot 4.7 is needed: set JOINERY_TEST_GODOT or add .tools/Godot.app."
    )


def make_game(directory: Path) -> Path:
    project = directory / "game"
    project.mkdir()
    (project / "project.godot").write_text(
        'config_version=5\n[application]\nconfig/name="Sound pass fixture"\n'
    )
    (project / "joinery.toml").write_text(TOOLS)
    code = cli_main(
        [
            "game",
            "apply",
            "--project",
            str(project),
            "--blueprint",
            "third-person-3d",
            "--features",
            "",
            "--no-addon",
        ]
    )
    assert code == 0, "the blueprint did not apply"
    core_sound_nodes(project)
    # A git history, as a Joinery project has, so accepted changes are recorded.
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    return project


def core_sound_nodes(project: Path) -> None:
    """The game core's sound nodes, for a checkout whose blueprint core has none yet.

    Each script gets its ``.uid`` file, as the core ships them: otherwise every
    task's Godot import would write the same new ``.uid`` files and the parallel
    tasks would clash on them.
    """
    core = project / "core/audio"
    if (core / "sound_cue.gd").is_file():
        return
    core.mkdir(parents=True, exist_ok=True)
    source = ROOT / "blueprints/_core/overlay/core/audio"
    for name, text in STUBS.items():
        if (source / name).is_file():
            shutil.copy(source / name, core / name)
            if (source / (name + ".uid")).is_file():
                shutil.copy(source / (name + ".uid"), core / (name + ".uid"))
                continue
        else:
            (core / name).write_text(text)
        (core / (name + ".uid")).write_text("uid://" + _uid() + "\n")


def _uid() -> str:
    chars = "abcdefghijklmnopqrstuvwxy012345678"
    value = secrets.randbits(63)
    text = ""
    while True:
        text = chars[value % 34] + text
        value //= 34
        if not value:
            return text


async def run_pass(
    project: Path, start: float
) -> tuple[list[dict[str, Any]], dict[str, float]]:
    gateway = ProjectGateway(project)
    events: list[dict[str, Any]] = []
    started: dict[str, float] = {}
    took: dict[str, float] = {}

    async def on_stop(reason: str) -> None:
        raise AssertionError("Rule Zero stop: " + reason)

    def emit(event: dict[str, Any]) -> None:
        events.append(event)
        if event.get("kind") == "sound_pass":
            rows = ", ".join(
                f"{r['title']}={r['state']}" for r in event.get("tasks", [])
            )
            stamp(start, f"sound pass {event['state']}" + (f": {rows}" if rows else ""))
            for row in event.get("tasks", []):
                if row["state"] == "failed" and row["title"] not in took:
                    stamp(start, f"  {row['title']} failed: {row['message']}")
                if row["state"] == "working":
                    started.setdefault(row["title"], time.monotonic())
                elif row["title"] in started and row["title"] not in took:
                    took[row["title"]] = time.monotonic() - started[row["title"]]

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
    run = SoundPassRun(
        crew, project, emit, lambda: Prompt(text="", project_dir=str(project))
    )
    try:
        run.start()
        assert run.job is not None
        await run.job
        assert run.state == "done", (run.message, run.rows)
        assert run.rows and all(row["state"] == IN_REVIEW for row in run.rows), (
            json.dumps(run.rows, indent=1)
        )
        previews = [e for e in events if e.get("kind") == "file_preview"]
        assert previews and all(p.get("facts") for p in previews), previews
        for task in list(crew.tasks.values()):
            decided = await crew.decide(task.transaction_id, True)
            assert decided["accepted"], decided
            stamp(start, f"accepted: {task.title}")
        return run.rows, took
    finally:
        await crew.close()
        await gateway.close()


def load_in_godot(project: Path, start: float) -> dict[str, Any]:
    binary = godot()
    scenes = [
        "res://game/level.tscn",
        "res://game/player/player.tscn",
        "res://game/props/coin.tscn",
        "res://game/props/lever.tscn",
        "res://game/props/pressure_plate.tscn",
    ]
    loops = sorted(
        "res://" + p.relative_to(project).as_posix()
        for p in (project / "audio/ambience").glob("*.ogg")
    )
    script = project / "sound_load_check.gd"
    script.write_text(LOAD_CHECK % (json.dumps(scenes), json.dumps(loops)))
    env = {"HOME": str(project.parent), "PATH": "/usr/bin:/bin"}
    base = [
        str(binary),
        "--headless",
        "--audio-driver",
        "Dummy",
        "--path",
        str(project),
    ]
    imported = subprocess.run(
        [*base, "--editor", "--import"],
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
    )
    assert imported.returncode == 0, imported.stdout[-2000:] + imported.stderr[-2000:]
    got = subprocess.run(
        [*base, "--script", "res://sound_load_check.gd"],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    script.unlink()
    output = got.stdout + got.stderr
    errors = [
        line
        for line in output.splitlines()
        if line.startswith(("ERROR", "SCRIPT ERROR"))
    ]
    assert not errors, "\n".join(errors[:20])
    line = next((l for l in output.splitlines() if l.startswith("SOUND_LOAD ")), "")
    assert line, output[-3000:]
    report: dict[str, Any] = json.loads(line.removeprefix("SOUND_LOAD "))
    stamp(start, "stock Godot loaded every scene: " + json.dumps(report)[:600])
    level = report["res://game/level.tscn"]
    assert any(item.startswith("Ambience.stream") for item in level), level
    player = report["res://game/player/player.tscn"]
    assert {"Footsteps.jump", "Footsteps.land"} <= set(player), player
    assert {f"Footsteps.surfaces.{s}" for s in ("default", "grass", "stone")} <= set(
        player
    )
    for prop in ("coin", "lever", "pressure_plate"):
        assert any(
            i.endswith(".stream") for i in report[f"res://game/props/{prop}.tscn"]
        ), prop
    assert report["audio/ui/click"].endswith("ui_click.wav"), report
    assert report["audio/ui/hover"].endswith("ui_hover.wav"), report
    assert loops and all(report[path] is True for path in loops), report
    level_text = (project / "game/level.tscn").read_text()
    assert level_text.count("metadata/surface") == 10, "every floor is tagged"
    return report


async def loudness(project: Path, start: float) -> dict[str, dict[str, float]]:
    rules = load_rules(project)
    exports = sorted(
        p.relative_to(project).as_posix()
        for p in (project / "audio").rglob("*")
        if p.suffix in {".wav", ".ogg"} and "sources" not in p.parts
    )
    got = await run_offline(
        ["sh", "-c", _MEASURE, "sh", *exports], project, 600, readonly=True
    )
    measured = parse_loudness(got.output)
    ceiling = (
        rules.audio.true_peak_max_dbtp
        if rules.audio.true_peak_max_dbtp is not None
        else -1.0
    )
    results: dict[str, dict[str, float]] = {}
    for path in exports:
        values = measured[path]
        recipe = next(
            load_recipe(r)
            for r in recipe_paths(project)
            if path in load_recipe(r).outputs
        )
        stem = Path(path).stem
        # The kit's own target where the bible sets none, as make_sound recorded it.
        found = re.search(r"normalised to (-?[0-9.]+) LUFS", recipe.note)
        assert found, recipe.note
        target = float(found.group(1))
        bible = folder_value(rules.audio.lufs, path)
        assert bible is None or bible == target, (path, bible, target)
        assert abs(values["lufs"] - target) <= rules.audio.lufs_tolerance, (
            path,
            values,
            target,
        )
        assert values["true_peak"] <= ceiling + 0.05, (path, values)
        results[stem] = {
            "lufs": values["lufs"],
            "true_peak": values["true_peak"],
            "target": target,
        }
    stamp(
        start,
        "loudness: "
        + ", ".join(
            f"{k} {v['lufs']:.1f}/{v['target']:g}" for k, v in sorted(results.items())
        ),
    )
    return results


def rebuilt(project: Path, start: float) -> int:
    runner = SandboxRunner(image=IMAGE)
    paths = recipe_paths(project)
    replays = [rebuild_one(path, project, runner) for path in paths]
    failed = [r for r in replays if not r.ok]
    assert not failed, failed
    stamp(start, f"{len(replays)} recipes replay byte for byte")
    return len(replays)


async def sweep() -> dict[str, Any]:
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="joinery-sound-") as directory:
        project = make_game(Path(directory).resolve())
        stamp(start, "third-person game made")
        before = sound_pass.audit(project)
        stamp(start, "before: " + before.headline)
        assert before.headline == "11 sounds missing in level.tscn", before.headline
        assert before.approved and before.runtime, before
        rows, took = await run_pass(project, start)
        made = time.monotonic() - start
        report = load_in_godot(project, start)
        levels = await loudness(project, start)
        recipes = rebuilt(project, start)
        after = sound_pass.audit(project)
        stamp(start, "after: " + after.headline)
        assert not after.findings, [f.missing for f in after.findings]
        settings = (project / "project.godot").read_text()
        assert scene_text.get_setting(settings, "audio", "ui/click")
        summary = {
            "before": before.headline,
            "after": after.headline,
            "changes": [r["title"] for r in rows],
            "task_seconds": {k: round(v, 1) for k, v in took.items()},
            "made_and_accepted_seconds": round(made, 1),
            "total_seconds": round(time.monotonic() - start, 1),
            "loudness": levels,
            "recipes_replayed": recipes,
            "scenes_loaded": len([k for k in report if k.endswith(".tscn")]),
        }
        print(json.dumps(summary, indent=1))
        print(
            "Sound pass sweep: audit, parallel sounds, stock Godot, loudness, replay: PASS"
        )
        return summary


SECTIONS: dict[str, Callable[[], Awaitable[dict[str, Any]]]] = {"sweep": sweep}


async def main(which: str) -> int:
    for name, section in SECTIONS.items():
        if which in {"all", name}:
            await section()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("which", nargs="?", default="all", choices=["all", *SECTIONS])
    sys.exit(asyncio.run(main(parser.parse_args().which)))
