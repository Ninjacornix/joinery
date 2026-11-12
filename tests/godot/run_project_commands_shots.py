"""Project commands screenshots: the command menu and a command's form, dark and light.

    uv run python tests/godot/run_project_commands_shots.py

Runs project_commands_test.gd in a window that never takes focus (``JOINERY_BACKGROUND=1``),
once per theme, in a disposable project with the addon, and saves
``project-commands-<theme>-menu.png``, ``-card.png`` and ``-card-error.png`` into
``JOINERY_CAPTURE_DIR`` (``out/project-commands-captures`` by default). Each capture is also
run through ui_checks.gd.
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
STATES = ("menu", "card", "card-error")


def main() -> int:
    capture = Path(
        os.environ.get("JOINERY_CAPTURE_DIR", ROOT / "out/project-commands-captures")
    ).resolve()
    capture.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="joinery project commands shots "))
    try:
        env = isolated_environment(temporary, ROOT, capture)
        env.update(JOINERY_TEST_PYTHON=sys.executable, JOINERY_BACKGROUND="1")
        project = _project(temporary / "project")
        binary = isolated_editor(engine_binary(), temporary)
        base = [str(binary), "--audio-driver", "Dummy", "--path", str(project)]
        runs = [(base[:1] + ["--headless"] + base[1:] + ["--editor", "--import"], {})]
        for theme in THEMES:
            runs.append(
                (
                    [
                        *base,
                        "--script",
                        str(ROOT / "tests/godot/project_commands_test.gd"),
                    ],
                    {
                        "JOINERY_PARTS_THEME": theme,
                        "JOINERY_ASSET_CAPTURE": str(
                            capture / f"project-commands-{theme}"
                        ),
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
            f"project-commands-{theme}-{state}.png"
            for theme in THEMES
            for state in STATES
            if not (capture / f"project-commands-{theme}-{state}.png").is_file()
        ]
        if missing:
            print("missing:", ", ".join(missing))
            return 1
        print("project commands shots: ok", capture)
        return 0
    finally:
        remove_when_released(temporary)


if __name__ == "__main__":
    raise SystemExit(main())
