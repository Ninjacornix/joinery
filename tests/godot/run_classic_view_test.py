"""Open the patched editor in Joinery's layout and Godot's classic one, and back.

    uv run python tests/godot/run_classic_view_test.py [--out DIR] [--themes dark light]

Per theme, one disposable project is opened three times with the editor layout
setting seeded: Joinery, then classic Godot, then Joinery again. The second and
third runs start from the layout the run before saved, so they cover the switch
itself: classic opens with Godot's stock docks rather than the Joinery
arrangement, and switching back restores the Joinery one as the first run left
it (with History opened), not Joinery's default. Each run checks the
docks, title bar, run bar, menu item, sidebar and settings page
(classic_view_test.gd) and captures the editor, the settings page and the
switch dialog to ``out/classic-view``.

``UI-CHECK`` findings from ui_checks.gd fail the run, as in the visual suite,
which runs this same pass as its ``classic-view`` harness. Stock Godot chrome
that draws into its dock margin on purpose is listed in ui_checks.gd's
``ALLOWED``.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

from run_startup_test import isolated_editor, isolated_environment, scratch_folder

from joinery.distribution import engine_binary

ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "tests/godot"
THEMES = ("dark", "light")
#: Joinery, then classic Godot from Joinery's saved layout, then back.
SEQUENCE = ("joinery", "classic", "joinery")
PRESETS = {"dark": "Joinery", "light": "Joinery Light"}
FIXTURE_SCENE = """[gd_scene format=3]

[sub_resource type="BoxMesh" id="crate"]
size = Vector3(1, 1, 1)

[sub_resource type="PlaneMesh" id="floor"]
size = Vector2(8, 8)

[node name="Harbour" type="Node3D"]

[node name="Floor" type="MeshInstance3D" parent="."]
mesh = SubResource("floor")

[node name="Crate" type="MeshInstance3D" parent="."]
transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0.5, 0)
mesh = SubResource("crate")
"""
SETTINGS = (
    "config_version=5\n[application]\n"
    'config/name="Harbour Keeper"\nconfig/features=PackedStringArray("4.7")\n'
    "[filesystem]\nimport/blender/enabled=false\n"
)


def seed(binary: Path, theme: str, layout: str) -> None:
    """Editor settings as a user would leave them: theme preset and editor layout."""
    data = binary.parent / "editor_data"
    data.mkdir(exist_ok=True)
    (data / "editor_settings-4.7.tres").write_text(
        '[gd_resource type="EditorSettings" format=3]\n\n[resource]\n'
        f'interface/theme/color_preset = "{PRESETS[theme]}"\n'
        f"interface/joinery/editor_layout = {1 if layout == 'classic' else 0}\n"
    )


def section(config: str, name: str) -> str:
    """The body of ``[name]`` in a Godot ConfigFile's text, or "" when it has none."""
    match = re.search(
        rf"^\[{re.escape(name)}\]\n(.*?)(?=^\[|\Z)", config, re.MULTILINE | re.DOTALL
    )
    return match.group(1).strip() if match else ""


def run(argv: list[str], env: dict[str, str], log: Path, timeout: int = 240) -> str:
    try:
        result = subprocess.run(
            argv,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        output = result.stdout + result.stderr
    except subprocess.TimeoutExpired as exc:
        output = "\n".join(
            value.decode(errors="replace") if isinstance(value, bytes) else value or ""
            for value in (exc.stdout, exc.stderr)
        )
        output += f"\nTimed out after {timeout}s."
    with log.open("a", encoding="utf-8") as handle:
        handle.write("$ " + " ".join(argv) + "\n" + output + "\n")
    return output


def capture_names(theme: str) -> list[str]:
    return [
        f"classic-view-{layout}-{shot}-{theme}.png"
        for layout in ("joinery", "classic")
        for shot in ("editor", "settings", "settings-pending", "switch")
    ]


def theme_pass(
    theme: str,
    out: Path,
    *,
    logs: Path | None = None,
    findings: list[str] | None = None,
) -> list[str]:
    """Run the three layouts in one theme; return problems, collect UI-CHECK lines."""
    problems: list[str] = []
    log = (logs or out) / f"classic-view-{theme}.log"
    log.unlink(missing_ok=True)
    with scratch_folder("joinery-classic-view-") as directory:
        temporary = Path(directory)
        project = temporary / "project"
        project.mkdir()
        shutil.copytree(ROOT / "godot_addon/joinery", project / "addons/joinery")
        check = project / "addons/classic_check"
        check.mkdir()
        shutil.copyfile(TESTS / "classic_view_test.gd", check / "test.gd")
        shutil.copyfile(TESTS / "ui_checks.gd", check / "ui_checks.gd")
        (check / "plugin.cfg").write_text(
            '[plugin]\nname="Classic view check"\ndescription="Test"\n'
            'author="Joinery"\nversion="1"\nscript="test.gd"\n'
        )
        (project / "visual.tscn").write_text(FIXTURE_SCENE)
        (project / "project.godot").write_text(SETTINGS)
        env = isolated_environment(temporary, ROOT, out)
        env["JOINERY_VISUAL_THEME"] = theme
        binary = isolated_editor(engine_binary(), temporary)
        base = [str(binary), "--path", str(project), "--audio-driver", "Dummy"]
        driver = env.get("JOINERY_RENDERING_DRIVER", "")
        rendering = ["--rendering-driver", driver] if driver else []
        seed(binary, theme, "joinery")
        run(base + ["--headless", "--editor", "--import"], env, log)
        (project / "project.godot").write_text(
            SETTINGS
            + "[editor_plugins]\nenabled=PackedStringArray("
            + '"res://addons/joinery/plugin.cfg", "res://addons/classic_check/plugin.cfg")\n'
        )
        layout_file = project / ".godot/editor/editor_layout.cfg"
        joinery_docks = ""
        for step, layout in enumerate(SEQUENCE, 1):
            seed(binary, theme, layout)
            env["JOINERY_EXPECT_LAYOUT"] = layout
            env["JOINERY_LAYOUT_STEP"] = {1: "first", 3: "restore"}.get(step, "")
            output = run(base + ["--editor", "res://visual.tscn"] + rendering, env, log)
            failed = [
                line for line in output.splitlines() if line.startswith("CHECK FAIL")
            ]
            for line in output.splitlines():
                if line.startswith("CHECK "):
                    print(f"  {theme} {step} {layout}: {line[6:]}")
            if "classic view: ok" not in output or failed:
                problems.append(f"{theme} run {step} ({layout}) failed; see {log.name}")
                if not any(line.startswith("CHECK ") for line in output.splitlines()):
                    # The editor never reached the checks: show how it ended.
                    tail = output.strip().splitlines()[-40:]
                    print(f"  {theme} {step} {layout}: no checks ran; last output:")
                    print("\n".join("    " + line for line in tail))
            if any(marker in output for marker in ("SCRIPT ERROR", "Parse Error")):
                problems.append(f"{theme} run {step} ({layout}) has script errors")
            found = [
                line for line in output.splitlines() if line.startswith("UI-CHECK ")
            ]
            if findings is not None:
                findings += found
            elif found:
                problems.append(
                    f"{theme} run {step} ({layout}): {len(found)} UI-CHECK findings"
                )
                problems += found
            saved = layout_file.read_text() if layout_file.is_file() else ""
            if f'joinery_view="{layout}"' not in saved:
                problems.append(f"{theme} run {step} did not save the {layout} layout")
            # Each layout keeps its own copy of the docks: the classic run adds its own and
            # leaves Joinery's as the first run saved it.
            if step == 1:
                joinery_docks = section(saved, "docks_joinery")
                if not joinery_docks:
                    problems.append(f"{theme} run 1 saved no copy of Joinery's docks")
            elif step == 2:
                if not section(saved, "docks_classic"):
                    problems.append(f"{theme} run 2 saved no copy of the classic docks")
                if section(saved, "docks_joinery") != joinery_docks:
                    problems.append(f"{theme} run 2 changed Joinery's saved docks")
        problems += [
            f"missing capture {name}"
            for name in capture_names(theme)
            if not (out / name).is_file()
        ]
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, default=ROOT / "out/classic-view")
    parser.add_argument("--themes", nargs="+", choices=THEMES, default=list(THEMES))
    args = parser.parse_args()
    if sys.prefix == sys.base_prefix:
        raise SystemExit("Run this harness with uv run python.")
    out: Path = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    for stale in out.glob("classic-view-*.png"):
        stale.unlink()
    problems = [problem for theme in args.themes for problem in theme_pass(theme, out)]
    for problem in problems:
        print(problem)
    print("Classic view: " + ("FAIL" if problems else "PASS") + f" ({out})")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
