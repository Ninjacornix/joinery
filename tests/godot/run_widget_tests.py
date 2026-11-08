"""Exercise native controls and actual Godot/Python pipes in a disposable project."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from run_startup_test import isolated_editor, isolated_environment

from joinery.catalog import ROOT
from joinery.distribution import engine_binary

CASES = {
    "command_job_test.gd": "command lifecycle: ok",
    "chat_transport_test.gd": "chat transport: ok",
    "chatup_test.gd": "sidebar chat: ok",
    "audio_preview_test.gd": "audio playback: ok",
    "scene_edits_test.gd": "scene edits: ok",
}


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="joinery native widgets ") as directory:
        temporary = Path(directory)
        project = temporary / "project"
        project.mkdir()
        shutil.copytree(ROOT / "godot_addon/joinery", project / "addons/joinery")
        audio = project / "audio/sfx/door_creak.wav"
        audio.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / "testbed/audio/sfx/door_creak.wav", audio)
        (project / "project.godot").write_text(
            'config_version=5\n[application]\nconfig/name="Native widget checks"\n'
            '[rendering]\nrenderer/rendering_method="gl_compatibility"\n'
        )
        (project / "joinery.toml").write_text('[tools]\nenabled=["godot"]\n')
        env = isolated_environment(temporary, ROOT, temporary / "captures")
        env["JOINERY_TEST_PYTHON"] = sys.executable
        binary = isolated_editor(engine_binary(), temporary)
        base = [
            str(binary), "--headless", "--audio-driver", "Dummy",
            "--path", str(project),
        ]
        commands = [(base + ["--editor", "--import"], "")]
        commands += [
            (base + ["--script", str(ROOT / "tests/godot" / case)], marker)
            for case, marker in CASES.items()
        ]
        for argv, marker in commands:
            result = subprocess.run(
                argv, env=env, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=90, check=False,
            )
            output = result.stdout + result.stderr
            print(output, flush=True)
            if result.returncode or marker not in output or "ERROR:" in output:
                return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
