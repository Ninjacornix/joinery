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
``music``: a 3-layer adaptive track (2 sections, a stinger) composed by rule and
rendered as stems: equal frame counts after decoding, seamless loops, the section
mix at the bible's loudness; stock Godot's core Music autoload plays it and switches
to the combat section on the bar with every layer in sync; edited (tempo, a layer
in, one out, the Mixer's fader kept); every recipe replays byte for byte.

Run: ``uv run python tests/integration/sound_pass.py [sweep|music|all]``.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import secrets
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import wave
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from bridge.crew import IN_REVIEW, Crew
from bridge.protocol import Prompt
from bridge.sound_pass import SoundPassRun
from joinery import scene_text, sound_pass
from joinery.authoring import IMAGE, run_offline
from joinery.bible_rules import _MEASURE, check_rules, load_rules, parse_loudness
from joinery.catalog import ROOT
from joinery.cli import main as cli_main
from joinery.gateway import ProjectGateway
from joinery.music_track import from_tres, to_tres
from joinery.playtest import STOCK_GODOT
from joinery.recipe import load_recipe, rebuild_one, recipe_paths
from joinery.runner import SandboxRunner
from joinery.tools.audio_chain import folder_value
from servers.sound import tracks

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


# --- Adaptive music ----------------------------------------------------------------

MUSIC_BIBLE = """# Art bible (test fixture)

## Checked rules

```toml
schema = 2

[audio]
true_peak_max_dbtp = -1.0
lufs_tolerance = 1.0

[audio.lufs]
"audio/music" = -16.0

[audio.format."audio/music"]
container = "ogg"
sample_rates = [48000]
channels = [2]
```
"""
#: Plays the composed track through the game core's Music autoload, asks for combat
#: (a section switch on the next bar) and reads every layer's position at once.
MUSIC_CHECK = """extends Node
var _start := 0
var _asked := -1.0


func _ready() -> void:
	print("MUSIC_PLAY ", Music.play("res://music/theme.tres", &"explore"))
	_start = Time.get_ticks_msec()


func _process(_delta: float) -> void:
	var elapsed := Time.get_ticks_msec() - _start
	if elapsed > 1000 and _asked < 0.0:
		# Mid-bar: the switch waits for the bar line at 2 s.
		_asked = Music.position_seconds()
		Music.set_intensity(&"combat", true)
	if elapsed < 3200:
		return
	AudioServer.lock()
	var positions := []
	for player: AudioStreamPlayer in Music.layer_players():
		positions.append(player.get_playback_position())
	AudioServer.unlock()
	var state := {
		"intensity": str(Music.intensity), "section": str(Music.current_section()),
		"positions": positions, "position": Music.position_seconds(), "asked_at": _asked,
	}
	print("MUSIC_STATE " + JSON.stringify(state))
	set_process(false)
	# Let go of the stems before quitting, as a game leaving its level does.
	Music.stop(0.0)
	# The audio thread lets go of stopped playbacks on its next mixes.
	await get_tree().create_timer(0.5).timeout
	get_tree().quit()
"""


async def decode(project: Path, path: str) -> list[float]:
    """A stem's left channel, decoded by ffmpeg in the worker."""
    out = ".decode.wav"
    got = await run_offline(
        [
            "ffmpeg",
            "-nostdin",
            "-loglevel",
            "error",
            "-y",
            "-i",
            path,
            "-c:a",
            "pcm_s16le",
            out,
        ],
        project,
    )
    assert got.ok, got.output
    with wave.open(str(project / out)) as audio:
        data = audio.readframes(audio.getnframes())
    (project / out).unlink()
    values = struct.unpack(f"<{len(data) // 2}h", data)
    return [values[i] / 32768 for i in range(0, len(values), 2)]


def seam(frames: list[float]) -> float:
    """The jump across the loop point against the 99th percentile jump inside it."""
    jumps = sorted(abs(frames[i] - frames[i - 1]) for i in range(1, len(frames)))
    return abs(frames[0] - frames[-1]) / (jumps[int(len(jumps) * 0.99)] or 1e-9)


async def measure_offline(argv: list[str], root: Path) -> Any:
    return await run_offline(argv, root, readonly=True)


def play_in_godot(project: Path, start: float) -> dict[str, Any]:
    binary = godot()
    (project / "music_check.gd").write_text(MUSIC_CHECK)
    (project / "music_check.tscn").write_text(
        '[gd_scene format=3]\n\n[ext_resource type="Script" path="res://music_check.gd"'
        ' id="1"]\n\n[node name="Check" type="Node"]\nscript = ExtResource("1")\n'
    )
    env = {"HOME": str(project.parent), "PATH": "/usr/bin:/bin"}
    base = [str(binary), "--headless", "--audio-driver", "Dummy", "--path", str(project)]
    imported = subprocess.run(
        [*base, "--editor", "--import"],
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
    )
    assert imported.returncode == 0, imported.stderr[-2000:]
    got = subprocess.run(
        [*base, "res://music_check.tscn"],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    for name in ("music_check.gd", "music_check.tscn"):
        (project / name).unlink()
    output = got.stdout + got.stderr
    errors = [
        line for line in output.splitlines() if line.startswith(("ERROR", "SCRIPT ERROR"))
    ]
    assert not errors, "\n".join(errors[:20])
    assert "MUSIC_PLAY true" in output, output[-2000:]
    line = next(x for x in output.splitlines() if x.startswith("MUSIC_STATE "))
    state: dict[str, Any] = json.loads(line.removeprefix("MUSIC_STATE "))
    positions = state["positions"]
    state["drift_ms"] = round((max(positions) - min(positions)) * 1000, 2)
    stamp(start, "stock Godot plays the track: " + json.dumps(state))
    assert state["section"] == "combat" and state["intensity"] == "combat", state
    assert len(positions) == 3 and state["drift_ms"] <= 5.0, state
    # Asked for mid-bar, the combat section started on the next bar line (2 s), not
    # at once: about 1.2 s of it has played when the check reads it.
    assert 1.0 < state["asked_at"] < 2.0 and 0.6 < state["position"] < 1.8, state
    return state


async def music() -> dict[str, Any]:
    """Compose a 3-layer track, check its stems, play it in Godot, edit it, rebuild it."""
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="joinery-music-") as directory:
        # Native authoring runs in a staged copy, as a change does.
        project = make_game(Path(directory)).rename(Path(directory) / "stage")
        (Path(directory) / "journal.json").write_text(json.dumps({"state": "staging"}))
        (project / "art").mkdir(exist_ok=True)
        (project / "art/bible.md").write_text(MUSIC_BIBLE)
        summary: dict[str, Any] = {}
        made = await tracks.compose_track(
            project,
            "theme",
            ["pads", "bass", "percussion"],
            mood="adventurous",
            key="D minor",
            tempo_bpm=120,
            bars=4,
            sections=["main", "combat"],
            stingers=["victory"],
            seed=7,
        )
        summary["compose_seconds"] = round(time.monotonic() - start, 1)
        stamp(start, "composed and rendered 3 layers x 2 sections + a stinger")
        frames = round(4 * 4 * 0.5 * 48000)
        summary["sections"] = {
            s["name"]: {"mix_lufs": s["mix_lufs"], "true_peak": s["mix_true_peak"]}
            for s in made["measured"]["sections"]
        }
        for section in made["measured"]["sections"]:
            assert section["frames"] == frames, section
            assert abs(section["mix_lufs"] + 16.0) <= 1.0, section
        track = from_tres((project / "music/theme.tres").read_text())
        seams: dict[str, float] = {}
        for part in track.sections:
            for layer, stem in part.stems.items():
                decoded = await decode(project, stem.removeprefix("res://"))
                assert len(decoded) == frames, (stem, len(decoded), frames)
                seams[f"{part.name}_{layer}"] = round(seam(decoded), 2)
        assert max(seams.values()) < 1.5, seams
        summary["seam_ratio_max"] = max(seams.values())
        stamp(start, f"every stem {frames} frames after decoding; seams {seams}")
        oggs = [o for o in made["outputs"] if o.endswith(".ogg")]
        problems = await check_rules(project, oggs, measure_offline)
        assert not problems, problems
        summary["godot"] = play_in_godot(project, start)

        mixed = from_tres((project / "music/theme.tres").read_text())
        mixed.layers[1].volume_db = -4.5
        (project / "music/theme.tres").write_text(to_tres(mixed))
        edit_start = time.monotonic()
        edited = await tracks.edit_track(
            project,
            "theme",
            {
                "tempo_bpm": 128,
                "add_layers": ["melody"],
                "remove_layers": ["percussion"],
                "variation": 2,
            },
        )
        summary["edit_seconds"] = round(time.monotonic() - edit_start, 1)
        track = from_tres((project / "music/theme.tres").read_text())
        assert [layer.name for layer in track.layers] == ["pads", "bass", "melody"]
        assert track.layers[1].volume_db == -4.5, "the Mixer's fader is kept"
        assert "audio/music/theme/main_percussion.ogg" in edited["removed"]
        stamp(start, "edited: 128 BPM, melody in, percussion out, fader kept")

        before = {
            o: hashlib.sha256((project / o).read_bytes()).hexdigest()
            for p in recipe_paths(project)
            for o in load_recipe(p).outputs
            if o.startswith("audio/music/")
        }
        rebuild_start = time.monotonic()
        for path in recipe_paths(project):
            replay = rebuild_one(path, project, SandboxRunner(image=IMAGE))
            assert replay.ok, replay
        summary["rebuild_seconds"] = round(time.monotonic() - rebuild_start, 1)
        after = {
            o: hashlib.sha256((project / o).read_bytes()).hexdigest() for o in before
        }
        assert before == after, [o for o in before if before[o] != after[o]]
        summary["replayed_files"] = len(before)
        stamp(start, f"{len(before)} music files replayed byte for byte")
        print(json.dumps(summary, indent=1))
        print("Adaptive music: compose, stems, Godot playback, edit, replay: PASS")
        return summary


SECTIONS: dict[str, Callable[[], Awaitable[dict[str, Any]]]] = {
    "sweep": sweep,
    "music": music,
}


async def main(which: str) -> int:
    for name, section in SECTIONS.items():
        if which in {"all", name}:
            await section()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("which", nargs="?", default="all", choices=["all", *SECTIONS])
    sys.exit(asyncio.run(main(parser.parse_args().which)))
