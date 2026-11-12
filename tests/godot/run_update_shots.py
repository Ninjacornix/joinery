"""Joinery updates dialog screenshots, dark and light.

    uv run python tests/godot/run_update_shots.py

Runs update_dialog_test.gd in a window that never takes focus (``JOINERY_BACKGROUND=1``),
once per theme, in a disposable project with the addon, and saves
``updates-<theme>-available.png``, ``-ready.png`` and ``-current.png`` into
``JOINERY_CAPTURE_DIR`` (``out/update-captures`` by default).
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from run_startup_test import (
    crash,
    isolated_editor,
    isolated_environment,
    remove_when_released,
)
from run_widget_tests import _project

from joinery.catalog import ROOT
from joinery.distribution import engine_binary

THEMES = ("dark", "light")
STATES = ("available", "ready", "current")


def main() -> int:
    capture = Path(
        os.environ.get("JOINERY_CAPTURE_DIR", ROOT / "out/update-captures")
    ).resolve()
    capture.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="joinery update shots "))
    try:
        env = isolated_environment(temporary, ROOT, capture)
        env.update(JOINERY_TEST_PYTHON=sys.executable, JOINERY_BACKGROUND="1")
        project = _project(temporary / "project")
        binary = isolated_editor(engine_binary(), temporary)
        base = [str(binary), "--audio-driver", "Dummy", "--path", str(project)]
        runs: list[tuple[list[str], dict[str, str]]] = [
            (base[:1] + ["--headless"] + base[1:] + ["--editor", "--import"], {})
        ]
        for theme in THEMES:
            runs.append(
                (
                    [
                        *base,
                        "--script",
                        str(ROOT / "tests/godot/update_dialog_test.gd"),
                    ],
                    {
                        "JOINERY_PARTS_THEME": theme,
                        "JOINERY_ASSET_CAPTURE": str(capture / f"updates-{theme}"),
                    },
                )
            )
        for argv, extra in runs:
            got = subprocess.run(
                argv,
                env={**env, **extra},
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=300,
                check=False,
            )
            output = got.stdout + got.stderr
            if got.returncode or crash(got.returncode, output) or "FAILED" in output:
                print(output[-4000:])
                return 1
        missing = [
            f"updates-{theme}-{state}.png"
            for theme in THEMES
            for state in STATES
            if not (capture / f"updates-{theme}-{state}.png").is_file()
        ]
        if missing:
            print("missing:", ", ".join(missing))
            return 1
        print("update shots: ok", capture)
        return 0
    finally:
        remove_when_released(temporary)


if __name__ == "__main__":
    raise SystemExit(main())
