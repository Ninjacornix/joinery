"""A shared kit's round trip in the real offline worker: made in one project, rebuilt in another.

In project A (a disposable project with an art bible):

* kit_batch makes three harbour rocks in Blender (joinery-blender-ops, as an
  agent calls it), SuperCollider renders a splash, and a prefab scene places
  the rocks;
* `joinery kit export` puts the prefab, the splash and the rock generator with
  a "Harbour rock" preset in a .joinerykit.

In a fresh project B:

* `joinery kit import --yes` adds it as one change under kits/harbour/;
* `joinery rebuild` replays every kit recipe to the fingerprints project A
  recorded (GLB and WAV bytes included);
* Godot imports the project and loads the prefab with its three rocks, no error;
* kit_list offers the imported generator, and kit_make makes a rock with it.

Prints the facts as JSON. Technical fixtures only.
Run: `uv run python tests/integration/shared_kits.py`.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from joinery.authoring import IMAGE, edit_document, run_offline
from joinery.catalog import load_catalog
from joinery.lockfile import KitAsset, check_assets, read_assets
from joinery.recipe import load_recipe, rebuild_one, recipe_paths
from joinery.runner import SandboxRunner
from joinery.transactions import FileTransaction

ROOT = Path(__file__).resolve().parents[2]
CLI = "import sys; from joinery.cli import main; sys.exit(main())"
BIBLE = """# Art bible

<!-- joinery-bible: 2 -->

## Checked rules

```toml
schema = 2

[models.max_triangles]
"art/environment" = 6000

[models.pivot]
"art/environment" = "bottom-centre"

[materials]
roughness = [0.6, 1.0]
metallic = [0.0, 1.0]

[palette]
colours = ["#7d7a75", "#c9c3b8", "#6b8f3a", "#4a6b2f", "#5a4632"]
```
"""
SPLASH = (
    "(\nvar definition=SynthDef(\\splash,{ Out.ar(0,SinOsc.ar(660,0,0.2)"
    "*EnvGen.kr(Env.perc(0.002,0.25),doneAction:2)) });\n"
    "var score=Score([[0.0,[\\d_recv,definition.asBytes]],"
    "[0.001,[\\s_new,\\splash,1000,0,0]],[0.3,[\\c_set,0,0]]]);\n"
    'score.recordNRT(outputFilePath:"audio/sfx/splash.wav",sampleRate:48000,'
    'headerFormat:"WAV",sampleFormat:"int16",options:ServerOptions.new.'
    "numOutputBusChannels_(1),duration:0.3,action:{0.exit});\n)\n"
)
CHECK_PREFAB = """extends SceneTree
func _init() -> void:
	var scene: PackedScene = load("res://kits/harbour/scenes/harbour.tscn")
	var harbour := scene.instantiate()
	var meshes := harbour.find_children("*", "MeshInstance3D", true, false).size()
	var sound: AudioStream = load("res://kits/harbour/audio/sfx/splash.wav")
	print("PREFAB_LOADED ", harbour.get_child_count(), " ", meshes, " ", sound != null)
	harbour.free()
	quit()
"""


def project(path: Path) -> Path:
    path.mkdir(parents=True)
    (path / "project.godot").write_text("config_version=5\n")
    (path / "joinery.toml").write_text(
        '[tools]\nenabled=["godot", "blender", "supercollider"]\n'
    )
    (path / "joinery.lock").write_text("version = 1\n")
    (path / "art").mkdir()
    (path / "art/bible.md").write_text(BIBLE)
    return path


async def op(root: Path, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    from mcp import Client

    from servers.authoring import make_server

    async with Client(make_server(root, "joinery-blender-ops")) as client:
        result = await client.call_tool(tool, arguments)
    text = result.content[0].text
    assert not result.is_error, f"{tool}: {text}"
    found: dict[str, Any] = json.loads(text)
    return found


def joinery(*argv: str) -> dict[str, Any]:
    got = subprocess.run(
        [sys.executable, "-c", CLI, *argv, "--json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert got.returncode == 0, got.stdout + got.stderr
    data: dict[str, Any] = json.loads(got.stdout)
    return data


def prefab(glbs: list[str]) -> str:
    lines = [f"[gd_scene load_steps={len(glbs) + 1} format=3]", ""]
    for index, glb in enumerate(glbs, 1):
        lines.append(
            f'[ext_resource type="PackedScene" path="res://{glb}" id="{index}_rock"]'
        )
    lines += ["", '[node name="Harbour" type="Node3D"]']
    for index, _glb in enumerate(glbs, 1):
        lines += [
            "",
            f'[node name="Rock{index}" parent="." instance=ExtResource("{index}_rock")]',
            f"transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, {index * 2.5}, 0, 0)",
        ]
    return "\n".join(lines) + "\n"


async def make_a(project_a: Path, facts: dict[str, Any]) -> dict[str, str]:
    started = time.monotonic()
    # An agent's change: made in a staged copy, then accepted by the user.
    tx = FileTransaction(project_a)
    tx.begin("Harbour rocks, a splash and a prefab")
    a = tx.stage
    made = await op(
        a,
        "kit_batch",
        {
            "kit": "rock",
            "count": 3,
            "params": {"moss": 0.5},
            "seed": 7,
            "name": "harbour_rocks",
        },
    )
    glbs = [m["glb"] for m in made["models"]]
    await edit_document(
        a,
        "supercollider",
        "audio/sfx/splash.scd",
        SPLASH,
        ["audio/sfx/splash.wav"],
        "Splash",
    )
    (a / "scenes").mkdir()
    (a / "scenes/harbour.tscn").write_text(prefab(glbs))
    tx.review()
    tx.accept()
    facts["made_in_a_seconds"] = round(time.monotonic() - started, 1)
    expected: dict[str, str] = {}
    for path in recipe_paths(project_a):
        expected.update(load_recipe(path).expect)
    return expected


async def main() -> dict[str, Any]:
    facts: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="joinery-shared-kits-") as directory:
        root = Path(directory)
        a, b = project(root / "a"), project(root / "b")
        expected = await make_a(a, facts)
        kit = root / "harbour.joinerykit"
        exported = joinery(
            "kit", "export", "--project", str(a), "--name", "Harbour",
            "--include", "scenes/harbour.tscn", "--include", "audio/sfx/splash.wav",
            "--generator", "rock", "--preset", 'Harbour rock=rock:{"moss": 0.7}',
            "--licence", "CC-BY-4.0", "--out", str(kit),
        )  # fmt: skip
        facts["kit"] = {
            "bytes": exported["bytes"],
            "counts": exported["counts"],
            "presets": [p["name"] for p in exported["presets"]],
        }
        imported = joinery("kit", "import", "--project", str(b), str(kit), "--yes")
        assert imported["state"] == "accepted", imported
        assert imported["folder"] == "kits/harbour", imported
        assert check_assets(b, load_catalog()) == [], check_assets(b, load_catalog())
        kit_entries = [e for e in read_assets(b) if isinstance(e, KitAsset)]
        assert {e.kit for e in kit_entries} == {"Harbour"}, kit_entries
        moved = {f"kits/harbour/{path}": value for path, value in expected.items()}
        started = time.monotonic()
        replays = [
            await asyncio.to_thread(rebuild_one, path, b, SandboxRunner(image=IMAGE))
            for path in recipe_paths(b)
        ]
        failed = [(r.recipe, r.problems) for r in replays if not r.ok]
        assert not failed, failed
        same = 0
        for replay in replays:
            for path, value in replay.measured.items():
                if path.endswith(".scd"):
                    continue  # the patch text names its new path
                assert moved[path] == value, (path, moved[path], value)
                same += 1
        facts["rebuild"] = {
            "recipes": len(replays),
            "fingerprints_same_as_a": same,
            "seconds": round(time.monotonic() - started, 1),
        }
        got = await run_offline(
            ["godot", "--headless", "--path", "/work", "--editor", "--import"], b, 600
        )
        assert got.ok and "ERROR" not in got.output, got.output[-3000:]
        (b / "check_prefab.gd").write_text(CHECK_PREFAB)
        loaded = await run_offline(
            [
                "godot",
                "--headless",
                "--path",
                "/work",
                "--script",
                "res://check_prefab.gd",
            ],
            b,
            300,
        )
        (b / "check_prefab.gd").unlink()
        line = next(
            (r for r in loaded.output.splitlines() if r.startswith("PREFAB_LOADED")), ""
        )
        assert line == "PREFAB_LOADED 3 3 true", loaded.output[-3000:]
        assert "ERROR" not in loaded.output, loaded.output[-3000:]
        facts["godot"] = line
        tx = FileTransaction(b)
        tx.begin("A pier rock from the Harbour kit")
        listed = await op(tx.stage, "kit_list", {})
        shared = next(k for k in listed["kits"] if k["id"] == "harbour_rock")
        assert shared["from"].startswith("Harbour 1.0.0 by "), shared
        made = await op(
            tx.stage,
            "kit_make",
            {"kit": "harbour_rock", "params": {"moss": 0.7}, "name": "pier_rock"},
        )
        assert made["models"][0]["glb"] == "art/environment/pier_rock.glb", made
        facts["agent_made_with_imported_generator"] = made["models"][0]["triangles"]
        facts["presets"] = [p["name"] for p in shared["presets"]]
        tx.reject()
    return facts


if __name__ == "__main__":
    print(json.dumps(asyncio.run(main()), indent=1))
    print("SHARED_KITS_OK")
