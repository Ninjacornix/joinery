"""Capture the native assistant in two widths and both themes, in isolation."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from run_startup_test import (
    crash,
    isolated_editor,
    isolated_environment,
    scratch_folder,
)

from joinery.catalog import ROOT
from joinery.distribution import engine_binary


def main() -> int:
    output = Path(
        os.environ.get("JOINERY_CAPTURE_DIR", ROOT / "out/assistant-captures")
    ).resolve()
    output.mkdir(parents=True, exist_ok=True)
    with scratch_folder("joinery-assistant-ui-") as directory:
        temporary = Path(directory)
        project = temporary / "project"
        project.mkdir()
        shutil.copytree(ROOT / "godot_addon/joinery", project / "addons/joinery")
        (project / "project.godot").write_text(
            'config_version=5\n[rendering]\nrenderer/rendering_method="gl_compatibility"\n'
        )
        environment = isolated_environment(temporary, ROOT, output)
        binary = isolated_editor(engine_binary(), temporary)
        base = [str(binary), "--path", str(project), "--audio-driver", "Dummy"]
        for arguments, marker in [
            (["--headless", "--editor", "--import"], ""),
            (
                ["--script", str(ROOT / "tests/godot/responsive_assistant_test.gd")],
                "responsive assistant: ok",
            ),
        ]:
            result = subprocess.run(
                base + arguments,
                env=environment,
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
            )
            text = result.stdout + result.stderr
            print(text, flush=True)
            if reason := crash(result.returncode, text):
                print(f"Assistant UI: {reason}.", flush=True)
            if result.returncode or marker not in text or "ERROR:" in text:
                return 1
        for theme in ("light", "dark"):
            for width in (480, 1000):
                capture = output / f"assistant-{theme}-{width}.png"
                if not capture.is_file() or capture.stat().st_size < 1000:
                    raise ValueError("A native assistant capture is missing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
