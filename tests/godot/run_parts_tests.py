"""Part selection in Godot: picking on the hammer fixture and the workspace parts list.

    uv run python tests/godot/run_parts_tests.py [--godot PATH] [--capture DIR]

Builds a throwaway project holding the committed hammer fixture the way a Joinery
edit leaves it (recipe, lock, Joinery's import settings, exported texture PNGs and a
cached parts report), imports it, then runs part_pick_test.gd and the parts section of
workspace_test.gd headless. With --capture it also renders the workspace in dark and
light and saves workspace-parts-<theme>.png, and asset_workspace_test.gd's and
kits_panel_test.gd's states as workspace-assets-<theme>-<state>.png, for a visual check
(the Kits panel shows a preview Blender draws in the worker). asset_workspace_test.gd also
saves a model page with many materials, wide and narrow (-model.png, -model-narrow.png).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from run_startup_test import crash, scratch_folder

from joinery import kit_preview, parts
from joinery.locking import GLB_IMPORT, write_lock
from joinery.workspace import index

FIXTURE = ROOT / "tests/fixtures/parts"
PATCHED = ROOT / "godot_patches/godot-src/bin/godot.macos.editor.arm64"
STOCK = ROOT / ".tools/Godot.app/Contents/MacOS/Godot"


def make_project(directory: Path) -> Path:
    project = directory / "parts-project"
    shutil.copytree(ROOT / "godot_addon/joinery", project / "addons/joinery")
    (project / "project.godot").write_text(
        'config_version=5\n[application]\nconfig/name="Parts check"\n'
        '[rendering]\nrenderer/rendering_method="gl_compatibility"\n'
    )
    (project / "joinery.toml").write_text('[tools]\nenabled=["godot", "blender"]\n')
    sources = project / "art/sources"
    sources.mkdir(parents=True)
    shutil.copyfile(FIXTURE / "hammer.blend", sources / "hammer.blend")
    (sources / ".gdignore").write_text("")
    models = project / "art/models"
    models.mkdir(parents=True)
    shutil.copyfile(FIXTURE / "hammer.glb", models / "hammer.glb")
    textures = project / "art/textures/hammer"
    shutil.copytree(FIXTURE / "textures", textures)
    written = [f"art/textures/hammer/{p.name}" for p in sorted(textures.glob("*.png"))]
    (project / "recipes").mkdir()
    (project / "recipes/hammer.py").write_text(
        (FIXTURE / "hammer_model.py").read_text()
    )
    outputs = ["art/sources/hammer.blend", "art/models/hammer.glb", *sorted(written)]
    (project / "recipes/hammer.toml").write_text(
        'version = 1\ntool = "blender"\nscript = "recipes/hammer.py"\n'
        f'outputs = {json.dumps(outputs)}\nsources = []\nnote = "Part fixture"\n'
    )
    problems = write_lock(project)
    assert not problems, problems
    assert (models / "hammer.glb.import").read_text() == GLB_IMPORT
    # A second model for the asset page's own check (asset_workspace_test.gd): the hammer's
    # shape under another name, whose many materials and maps that test makes up.
    shutil.copyfile(FIXTURE / "hammer.blend", sources / "crt_station.blend")
    shutil.copyfile(FIXTURE / "hammer.glb", models / "crt_station.glb")
    (models / "crt_station.glb.import").write_text(GLB_IMPORT)
    inspection = json.loads((FIXTURE / "parts.json").read_text())
    digest = parts._sha256(sources / "hammer.blend")
    cached = parts.cache_path(project, digest)
    cached.parent.mkdir(parents=True)
    cached.write_text(json.dumps({"sha256": digest, "inspection": inspection}))
    tests = project / "tests"
    tests.mkdir()
    for name in (
        "part_pick_test.gd",
        "workspace_test.gd",
        "asset_workspace_test.gd",
        "kits_panel_test.gd",
    ):
        shutil.copyfile(ROOT / "tests/godot" / name, tests / name)
    return project


def godot(binary: Path, project: Path, args: list[str], env: dict[str, str]) -> str:
    got = subprocess.run(
        [str(binary), "--path", str(project), *args],
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )
    output = got.stdout + got.stderr
    failed = ("SCRIPT ERROR", "Parse Error", "FAILED")
    if got.returncode != 0 or any(word in output for word in failed):
        print(output[-6000:])
        reason = crash(got.returncode, output) or f"exit code {got.returncode}"
        raise SystemExit(f"Godot failed ({reason}): {' '.join(args)}")
    return output


def keep(directory: Path) -> Path:
    """Build the project in [directory] and leave it there (for debugging)."""
    directory.mkdir(parents=True, exist_ok=True)
    return make_project(directory)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--godot", type=Path, default=PATCHED if PATCHED.is_file() else STOCK
    )
    parser.add_argument(
        "--capture", type=Path, help="save dark and light screenshots here"
    )
    args = parser.parse_args()
    with scratch_folder("joinery-parts-") as directory:
        project = make_project(Path(directory))
        env = os.environ.copy()
        env["JOINERY_HOME"] = str(ROOT)
        env["JOINERY_BACKGROUND"] = "1"  # never take focus (ADR 0032)
        godot(args.godot, project, ["--headless", "--editor", "--import"], env)
        extracted = sorted(p.name for p in (project / "art/models").glob("*.png"))
        assert not extracted, f"Textures were extracted beside the model: {extracted}"
        print(
            godot(
                args.godot,
                project,
                ["--headless", "--script", "res://tests/part_pick_test.gd"],
                env,
            )
            .strip()
            .splitlines()[-1]
        )
        report = Path(directory) / "index.json"
        report.write_text(json.dumps(index(project)))
        env["JOINERY_PARTS_INDEX"] = str(report)
        print(
            godot(
                args.godot,
                project,
                ["--headless", "--script", "res://tests/workspace_test.gd"],
                env,
            )
            .strip()
            .splitlines()[-1]
        )
        if args.capture:
            args.capture.mkdir(parents=True, exist_ok=True)
            env["JOINERY_TEST_PYTHON"] = sys.executable
            drawn = {
                "make": asyncio.run(
                    kit_preview.preview(project, "hut", {"windows": 4, "roof": "hip"}, seed=3)
                ),
                "edit": asyncio.run(
                    kit_preview.preview(project, "rock", {"moss": 0.4}, seed=11)
                ),
            }
            env["JOINERY_KITS_PREVIEW"] = json.dumps(drawn)
            for theme in ("dark", "light"):
                shot = args.capture.resolve() / f"workspace-parts-{theme}.png"
                env.update(JOINERY_PARTS_THEME=theme, JOINERY_PARTS_CAPTURE=str(shot))
                godot(
                    args.godot,
                    project,
                    ["--script", "res://tests/workspace_test.gd"],
                    env,
                )
                print("saved", shot)
                # Waiting-for-review rows and an app that is Ready but not installed.
                prefix = args.capture.resolve() / f"workspace-assets-{theme}"
                env["JOINERY_ASSET_CAPTURE"] = str(prefix)
                godot(
                    args.godot,
                    project,
                    ["--script", "res://tests/asset_workspace_test.gd"],
                    env,
                )
                godot(
                    args.godot,
                    project,
                    ["--script", "res://tests/kits_panel_test.gd"],
                    env,
                )
                print("saved", f"{prefix}-*.png")
    print("parts tests: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
