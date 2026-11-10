"""Run the Project apps dialog and the app rail against a fixture Joinery CLI.

    uv run python tests/godot/run_setup_tests.py [--capture DIR]

The fixture answers `joinery status`, `tools`, `feature` and `bootstrap` the way the
real CLI does, from the real readiness core (`joinery.readiness`) fed by the fake host
the Python tests use, with a short delay so the test can see the editor keep drawing
frames while a command runs. Every call is logged, so the test can prove that a feature
change is planned before it is applied. Nothing touches Docker or the network.

With --capture, the editor runs windowed and saves dark and light screenshots of the
dialog and the rail's readiness card into DIR.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

MARKER = "project setup dialog: ok"
#: Feature names, as `joinery features --json` gives them.
FEATURES = [
    {"id": "controller", "name": "Controller", "summary": "Play with a gamepad.",
     "blueprints": ["*"], "requires": [], "conflicts": []},
    {"id": "online-play", "name": "Online play", "summary": "Play with friends over the internet.",
     "blueprints": ["*"], "requires": [], "conflicts": []},
    {"id": "save-load", "name": "Save and load", "summary": "Keep progress between sessions.",
     "blueprints": ["*"], "requires": [], "conflicts": []},
    {"id": "steam", "name": "Steam", "summary": "Achievements and friends through Steam.",
     "blueprints": ["*"], "requires": ["online-play"], "conflicts": []},
    {"id": "translations", "name": "Translations", "summary": "Play in more than one language.",
     "blueprints": ["*"], "requires": [], "conflicts": []},
]
PLAN = [
    "features/online-play/lobby.gd",
    "features/online-play/lobby.tscn",
    "features/online-play/net_session.gd",
    "features/online-play/peer_list.gd",
    "game/menus/online.tscn",
    "game/menus/online.gd",
    "joinery.toml",
    "project.godot",
    "tests/features/online-play/online_test.gd",
    "translations/online.csv",
]


# --- The fixture CLI ---------------------------------------------------------------


def _report(state: Path) -> dict[str, Any]:
    """A mixed readiness report from the real core and the tests' fake host."""
    os.environ["JOINERY_READINESS_CACHE"] = str(state / "cache")
    from joinery import readiness
    from tests.test_readiness import MCP_WORKER, FakeHost

    engine = state / "godot.macos.editor.arm64"
    engine.write_text("editor")
    host = FakeHost(engine=engine)
    host.setup_data = {"phase": "build", "image": MCP_WORKER, "pid": os.getpid()}
    host.outputs["inkscape --version"] = (1, "")
    host.outputs["JOINERY_GIMP_OK"] = (64, "batch command experienced a calling error\n")
    host.outputs.pop("FluidR3_GM", None)
    mixed = readiness.status(catalog=True, refresh=True, host=host)
    host.docker_reason = "Docker is not running."
    down = {t["id"]: t for t in readiness.status(catalog=True, refresh=True, host=host)["tools"]}
    tools = [down[t["id"]] if t["id"] in {"krita", "supercollider"} else t for t in mixed["tools"]]
    return {**mixed, "tools": tools}


def _count(state: Path, name: str) -> int:
    path = state / f"{name}.count"
    count = int(path.read_text()) if path.is_file() else 0
    path.write_text(str(count + 1))
    return count


def fake_cli(state: Path, argv: list[str]) -> int:
    """Answer one CLI call like the real `joinery`, logging it first."""
    with (state / "calls.jsonl").open("a") as log:
        log.write(json.dumps(argv) + "\n")
    command = argv[0] if argv else ""
    if command == "status":
        report = _report(state)
        if "--tool" in argv:
            time.sleep(1.0)  # A refresh takes a while; the editor must keep drawing.
            tool = argv[argv.index("--tool") + 1]
            entry = next(t for t in report["tools"] if t["id"] == tool)
            fixed = {**entry, "status": "ready", "state": "ready", "status_label": "Ready",
                     "status_reason": "", "action": None, "summary": entry["name"] + " ready."}
            print(json.dumps({**report, "tools": [fixed]}))
            return 0
        time.sleep(0.3)
        if _count(state, "status") == 0:
            # The first answer is still checking Surge XT; the dialog asks again.
            for entry in report["tools"]:
                if entry["id"] == "surge-xt":
                    entry["checking"] = True
            report["checking"] = True
        print(json.dumps(report))
        return 0
    if command == "tools":
        from joinery.catalog import ProjectTools, load_catalog
        from joinery.installer import tool_menu

        current = ProjectTools(enabled=("godot", "blender", "krita"))
        print(json.dumps(tool_menu(load_catalog(), current, report=_report(state))))
        return 0
    if command == "features":
        print(json.dumps({"features": FEATURES}))
        return 0
    if command == "feature" and argv[1] == "list":
        applied = ["save-load"]
        if (state / "applied").is_file():
            applied += (state / "applied").read_text().split()
        addable = [f["id"] for f in FEATURES if f["id"] not in applied and f["id"] != "steam"]
        game = {"blueprint": "platformer-2d", "features": applied, "can_add": addable}
        print(json.dumps({"game": game}))
        return 0
    if command == "feature":
        action, feature = argv[1], argv[2]
        if "--plan" in argv:
            time.sleep(0.3)
            print(json.dumps({"action": action, "feature": feature, "changes": PLAN,
                              "config_edits": [], "conflicts": [], "adds": [],
                              "notes": ["Players join with a code; no account needed."]}))
            return 0
        with (state / "applied").open("a") as handle:
            handle.write(feature + " ")
        print(json.dumps({"transaction_id": "fixture", "state": "accepted", "changes": []}))
        return 0
    if command == "bootstrap":
        time.sleep(0.5)
        print(json.dumps({"installed": [], "unavailable": []}))
        return 0
    if command == "setup":
        return 0
    print(json.dumps({"error": "unknown command " + command}))
    return 2


# --- The runner ------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fake-cli", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--capture", type=Path, help="save dark and light screenshots here")
    args, rest = parser.parse_known_args()
    if args.fake_cli is not None:
        return fake_cli(args.fake_cli, rest)

    from run_startup_test import (
        crash,
        isolated_editor,
        isolated_environment,
        scratch_folder,
    )

    from joinery.distribution import engine_binary

    with scratch_folder("joinery setup dialog ") as directory:
        temporary = Path(directory)
        project = temporary / "project"
        project.mkdir()
        shutil.copytree(ROOT / "godot_addon/joinery", project / "addons/joinery")
        (project / "project.godot").write_text(
            'config_version=5\n[application]\nconfig/name="Setup dialog checks"\n'
            '[rendering]\nrenderer/rendering_method="gl_compatibility"\n'
        )
        (project / "joinery.toml").write_text('[tools]\nenabled=["godot"]\n')
        state = temporary / "state"
        state.mkdir()
        capture = args.capture.resolve() if args.capture else temporary / "captures"
        capture.mkdir(parents=True, exist_ok=True)
        env = isolated_environment(temporary, ROOT, capture)
        env["JOINERY_SETUP_DIALOG_TEST"] = "1"
        env["JOINERY_SETUP_STATE"] = str(state)
        env["JOINERY_FAKE_CLI"] = json.dumps(
            [sys.executable, str(Path(__file__).resolve()), "--fake-cli", str(state)]
        )
        env["PYTHONPATH"] = str(ROOT)
        binary = isolated_editor(engine_binary(), temporary)
        base = [str(binary), "--audio-driver", "Dummy", "--path", str(project)]
        if args.capture is None:
            base.insert(1, "--headless")
            env.pop("JOINERY_CAPTURE_DIR", None)
        else:
            base += ["--resolution", "1280x900"]
        commands = [
            ([*base[:1], "--headless", *base[1:], "--editor", "--import"], ""),
            ([*base, "--script", str(ROOT / "tests/godot/project_setup_flow_test.gd")], MARKER),
        ]
        for argv, marker in commands:
            result = subprocess.run(
                argv, env=env, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=180, check=False,
            )
            output = result.stdout + result.stderr
            print(output, flush=True)
            if reason := crash(result.returncode, output):
                print(f"Setup dialog: {reason}.", flush=True)
            if result.returncode or marker not in output or "SCRIPT ERROR" in output:
                return 1
        if args.capture is not None:
            print("captures:", *sorted(p.name for p in capture.glob("*.png")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
