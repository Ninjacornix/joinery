"""Native integration: live editor scene previews, review, undo and empty scene."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from joinery.distribution import engine_binary

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--live",
        action="store_true",
        help="One real Codex inference in an isolated scene",
    )
    args = parser.parse_args()
    for empty in (False, True):
        with tempfile.TemporaryDirectory(prefix="joinery-scene-edit-") as directory:
            project = Path(directory)
            shutil.copytree(ROOT / "godot_addon/joinery", project / "addons/joinery")
            addon = project / "addons/edit_check"
            addon.mkdir()
            shutil.copyfile(
                ROOT / "tests/godot/scene_edit_editor_test.gd", addon / "test.gd"
            )
            (addon / "plugin.cfg").write_text(
                '[plugin]\nname="Edit Check"\ndescription="Test"\nauthor="Joinery"\nversion="1"\nscript="test.gd"\n'
            )
            (project / "project.godot").write_text(
                'config_version=5\n[application]\nconfig/name="Scene edit check"\n[rendering]\nrenderer/rendering_method="gl_compatibility"\n[editor_plugins]\nenabled=PackedStringArray("res://addons/joinery/plugin.cfg", "res://addons/edit_check/plugin.cfg")\n'
            )
            (project / "joinery.toml").write_text('[tools]\nenabled=["godot"]\n')
            from joinery.wiring import write_agent_wiring

            write_agent_wiring(project)
            (project / "main.tscn").write_text(
                '[gd_scene format=3]\n[node name="Scene" type="Node3D"]\n[node name="Existing" type="Node3D" parent="."]\nposition=Vector3(2,3,4)\n'
            )
            env = os.environ.copy()
            env.update(
                JOINERY_HOME=str(ROOT),
                JOINERY_AGENT="codex",
                JOINERY_EMPTY_SCENE="1" if empty else "0",
                JOINERY_SCENE_EDIT_LIVE="1" if args.live and empty else "0",
                JOINERY_SCENE_EDIT_LIVE_CRUD="1" if args.live and not empty else "0",
                JOINERY_STARTUP_PROMPT="create box 1 by 1 matre"
                if args.live and empty
                else "",
            )
            cmd = [
                str(engine_binary()),
                "--audio-driver",
                "Dummy",
                "--editor",
                "--path",
                str(project),
            ]
            if os.environ.get("JOINERY_RENDERING_DRIVER"):
                cmd += ["--rendering-driver", os.environ["JOINERY_RENDERING_DRIVER"]]
            if not empty:
                cmd.append("res://main.tscn")
            try:
                result = subprocess.run(
                    cmd,
                    check=False,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=180 if args.live else 120,
                )
            except subprocess.TimeoutExpired as exc:
                print(
                    (exc.stdout or b"").decode(errors="replace")
                    if isinstance(exc.stdout, bytes)
                    else exc.stdout
                )
                print(
                    (exc.stderr or b"").decode(errors="replace")
                    if isinstance(exc.stderr, bytes)
                    else exc.stderr
                )
                return 1
            print(result.stdout)
            print(result.stderr)
            if (
                result.returncode
                or "editor scene edits: ok" not in result.stdout
                or "SCRIPT ERROR" in result.stderr
                or "ERROR:" in result.stderr
            ):
                return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
