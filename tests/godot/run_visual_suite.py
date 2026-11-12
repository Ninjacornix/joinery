"""Capture every Joinery screen in light and dark, then check each one automatically.

    uv run python tests/godot/run_visual_suite.py [--report-only] [--out DIR]
        [--only NAME ...] [--skip-legacy]

All screenshots land in one directory (default ``out/visual``) together with
``report.json`` and ``report.md``. While each screen is shown, ``ui_checks.gd``
walks its visible controls and prints ``UI-CHECK <screen> <kind> <node> <detail>``
lines for clipped text, low contrast, overflow and small targets. Any finding
fails the run unless ``--report-only`` is given. A missing capture or a broken
harness always fails it.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

from music_fixture import plant as plant_music
from run_classic_view_test import capture_names, theme_pass
from run_startup_test import (
    crash,
    isolated_editor,
    isolated_environment,
    scratch_folder,
)

from joinery import mixer
from joinery.distribution import engine_binary

ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "tests/godot"
THEMES = ("dark", "light")
KINDS = ("clipped", "contrast", "overflow", "target")
FINDING = re.compile(r"^UI-CHECK (\S+) (\S+) (\S+) ?(.*)$")
CAPTURED = re.compile(r"^VISUAL-CAPTURE (\S+)$")
SIDEBAR_STATES = (
    "connecting",
    "no-agent",
    "empty",
    "chips",
    "shared-view",
    "attachments",
    "image-sent",
    "usage",
    "commands",
    "sending",
    "working",
    "submitting",
    "reviewing",
    "queued",
    "queued-paused",
    "parallel-starting",
    "parallel",
    "parallel-stop",
    "debugging",
    "playtest",
    "playtest-report",
    "todo",
    "todo-checked",
    "match",
    "match-fixing",
    "access",
    "access-fixing",
    "perf",
    "design",
    "design-reading",
    "design-clean",
    "variants-working",
    "variants",
    "variants-picked",
    "sound-pass",
    "review-waiting",
    "review-waiting-big",
    "accepted",
    "conversation",
    "compacted",
    "check-failed",
    "not-sent",
    "error",
    "feature-plan",
    "history",
    "art-gate",
    "settings",
    "settings-compact",
    "settings-policy",
    "settings-policy-custom",
    "settings-models",
    "chat-restored",
    "chats",
    "chats-rename",
    "chat-earlier",
)
# The sidebar's docked widths: its narrowest, its usual one and a wide dock.
SIDEBAR_WIDTHS = (340, 480, 1000)
# Captured at the wide width only: the art direction and game brief documents.
SIDEBAR_WIDE = ("art-direction", "art-board", "brief")
# Captured at the narrow width only: each Appearance accent, and Reduce motion on.
SIDEBAR_NARROW = (
    "accent-brass",
    "accent-blue",
    "accent-green",
    "accent-violet",
    "reduce-motion",
)
EDITOR_SCREENS = (
    "disconnected",
    "layout",
    "commands",
    "busy",
    "settings",
    "review-model",
    "review-model-live",
    "review-details",
    "review-applying",
    "review-error",
    "focus-review",
    "review-list",
    "review-decided",
    "review-task",
    "review-variants",
    "review-list-empty",
    "review-image",
    "review-rendering",
    "review-rendered",
    "review-image-before",
    "review-image-zoom",
    "review-audio",
    "review-audio-before",
    "review-music",
    "review-text",
    "review-done-when",
    "review-scene",
    "review-files",
    "review-blockout",
    "review-clip",
    "timeline-list",
    "timeline-entry",
    "timeline-restored",
    "timeline-start",
    "share-empty",
    "share-built",
    "share-publish",
    "mixer-empty",
    "mixer",
    "tools",
    "tools-blender",
    "tools-animation",
    "tools-supercollider",
    "tools-readiness",
    "bottom-panel",
    "focus",
    "setup",
    "art-review",
)
STARTUP_SCREENS = (
    "agent-connect",
    "agent-signin",
    "api-form",
    "projects",
    "setup-catalog-failed",
    "setup-1-details",
    "setup-2-tools",
    "setup-2-steam-error",
    "setup-2-steam-too-big",
    "setup-3-art",
    "setup-failed",
    "setup-creating",
)
LEGACY = {
    "startup": (
        "run_startup_test.py",
        (
            "startup-agent-connect.png",
            "startup-api-submenu.png",
            "startup-setup-details.png",
            "startup-setup-tools.png",
            "startup-setup-art.png",
        ),
    ),
    "assistant": (
        "run_assistant_ui_test.py",
        tuple(
            f"{prefix}assistant-{theme}-{width}.png"
            # Settings are captured by the sidebar harness (sidebar-settings-*).
            for prefix in ("", "working-")
            for theme in THEMES
            for width in (480, 1000)
        ),
    ),
    "shared-kits": (
        "run_kit_share_shots.py",
        tuple(
            f"shared-kits-{theme}-kits-{state}.png"
            for theme in THEMES
            for state in ("import", "export", "shared")
        ),
    ),
    "scene-edit": (
        "run_scene_edit_test.py",
        (
            # The command menu is captured by the sidebar and editor harnesses.
            "joinery-scroll-review.png",
            "joinery-model-review.png",
            "joinery-accepted-model.png",
        ),
    ),
}
LIGHT_SETTINGS = (
    '[gd_resource type="EditorSettings" format=3]\n\n'
    "[resource]\n"
    # The fork's own light preset, which the Appearance setting writes for Light.
    'interface/theme/color_preset = "Joinery Light"\n'
)
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


@dataclass
class Finding:
    screen: str
    kind: str
    node: str
    detail: str

    def line(self) -> str:
        return f"UI-CHECK {self.screen} {self.kind} {self.node} {self.detail}"


@dataclass
class Harness:
    name: str
    ok: bool = True
    seconds: float = 0.0
    errors: list[str] = field(default_factory=list)
    captures: list[str] = field(default_factory=list)
    expected: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)


def is_png(path: Path) -> bool:
    if not path.is_file():
        return False
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return False
    width, height = struct.unpack(">II", data[16:24])
    return width >= 100 and height >= 100 and len(data) >= 1000


def run_godot(
    harness: Harness,
    argv: list[str],
    env: dict[str, str],
    logs: Path,
    *,
    marker: str = "",
    cwd: Path | None = None,
    timeout: int = 180,
) -> str:
    """Run one Godot process, keep its log and collect captures and findings."""
    try:
        result = subprocess.run(
            argv,
            env=env,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        output = result.stdout + result.stderr
        code = result.returncode
    except subprocess.TimeoutExpired as exc:
        output = "\n".join(
            value.decode(errors="replace") if isinstance(value, bytes) else value or ""
            for value in (exc.stdout, exc.stderr)
        )
        output += f"\nTimed out after {timeout}s."
        code = -1
    with (logs / f"{harness.name}.log").open("a", encoding="utf-8") as log:
        log.write("$ " + " ".join(argv) + "\n" + output + "\n")
    for line in output.splitlines():
        if match := FINDING.match(line.strip()):
            harness.findings.append(Finding(*match.groups()))
        elif match := CAPTURED.match(line.strip()):
            harness.captures.append(match.group(1))
        elif "SCRIPT ERROR" in line or "Parse Error" in line:
            harness.errors.append(line.strip())
    # -1 stands for a timeout above, not a signal.
    reason = crash(code, output) if code != -1 else ""
    if reason:
        harness.errors.append(reason)
    elif code != 0:
        harness.errors.append(f"exit code {code}")
    if marker and marker not in output:
        harness.errors.append(f"missing completion marker: {marker!r}")
    return output


def startup(theme: str, capture: Path, logs: Path) -> Harness:
    harness = Harness(f"project-manager-{theme}")
    harness.expected = [f"pm-{name}-{theme}.png" for name in STARTUP_SCREENS]
    with scratch_folder("joinery-visual-pm-") as directory:
        temporary = Path(directory)
        env = isolated_environment(temporary, ROOT, capture)
        env["JOINERY_VISUAL_THEME"] = theme
        binary = isolated_editor(engine_binary(), temporary)
        seed_theme(binary, theme)
        argv = [
            str(binary),
            "--audio-driver",
            "Dummy",
            "--project-manager",
            "--script",
            str(TESTS / "visual_startup_test.gd"),
        ]
        argv += rendering(env)
        # The wizard loads the app list and creates a fixture project through the CLI.
        run_godot(
            harness,
            argv,
            env,
            logs,
            marker="visual startup: ok",
            cwd=temporary,
            timeout=300,
        )
    return harness


def sidebar(capture: Path, logs: Path) -> Harness:
    harness = Harness("sidebar")
    harness.expected = (
        [
            f"sidebar-{state}-{theme}-{width}.png"
            for state in SIDEBAR_STATES
            for theme in THEMES
            for width in SIDEBAR_WIDTHS
        ]
        + [
            f"sidebar-{state}-{theme}-480.png"
            for state in SIDEBAR_NARROW
            for theme in THEMES
        ]
        + [
            f"sidebar-{state}-{theme}-1000.png"
            for state in SIDEBAR_WIDE
            for theme in THEMES
        ]
    )
    with scratch_folder("joinery-visual-sidebar-") as directory:
        temporary = Path(directory)
        project = temporary / "project"
        project.mkdir()
        shutil.copytree(ROOT / "godot_addon/joinery", project / "addons/joinery")
        (project / "project.godot").write_text(
            'config_version=5\n[rendering]\nrenderer/rendering_method="gl_compatibility"\n'
        )
        env = isolated_environment(temporary, ROOT, capture)
        binary = isolated_editor(engine_binary(), temporary)
        base = [str(binary), "--path", str(project), "--audio-driver", "Dummy"]
        run_godot(harness, base + ["--headless", "--editor", "--import"], env, logs)
        selftest = ["--headless", "--script", str(TESTS / "ui_checks_selftest.gd")]
        run_godot(harness, base + selftest, env, logs, marker="ui checks selftest: ok")
        harness.findings.clear()  # The self-test's deliberate defects are not findings.
        script = ["--script", str(TESTS / "visual_sidebar_test.gd")]
        run_godot(
            harness,
            base + script + rendering(env),
            env,
            logs,
            marker="visual sidebar: ok",
        )
    return harness


def editor(theme: str, capture: Path, logs: Path) -> Harness:
    harness = Harness(f"editor-{theme}")
    harness.expected = [f"editor-{name}-{theme}.png" for name in EDITOR_SCREENS]
    with scratch_folder("joinery-visual-editor-") as directory:
        temporary = Path(directory)
        project = temporary / "project"
        shutil.copytree(
            ROOT / "testbed",
            project,
            symlinks=True,
            ignore=shutil.ignore_patterns(".godot", "addons", "*.uid"),
        )
        shutil.copytree(ROOT / "godot_addon/joinery", project / "addons/joinery")
        check = project / "addons/visual_check"
        check.mkdir()
        shutil.copyfile(TESTS / "visual_editor_test.gd", check / "test.gd")
        shutil.copyfile(TESTS / "ui_checks.gd", check / "ui_checks.gd")
        shutil.copyfile(TESTS / "visual_animation.gd", check / "visual_animation.gd")
        shutil.copyfile(TESTS / "visual_sound.gd", check / "visual_sound.gd")
        shutil.copyfile(TESTS / "visual_review.gd", check / "visual_review.gd")
        (check / "plugin.cfg").write_text(
            '[plugin]\nname="Visual check"\ndescription="Test"\nauthor="Joinery"\n'
            'version="1"\nscript="test.gd"\n'
        )
        (project / "visual.tscn").write_text(FIXTURE_SCENE)
        blockout_fixture(project / "blockout_fixture")
        # The Mixer's buses as the game core sets them up, and a track to mix.
        layout = mixer.to_tres(mixer.standard_layout())
        (project / "default_bus_layout.tres").write_text(layout)
        plant_music(project)
        settings = (
            "config_version=5\n[application]\n"
            'config/name="Harbour Keeper"\nconfig/features=PackedStringArray("4.7")\n'
            # Blender is not part of this suite; never prompt for its path.
            "[filesystem]\nimport/blender/enabled=false\n"
        )
        (project / "project.godot").write_text(settings)
        env = isolated_environment(temporary, ROOT, capture)
        env["JOINERY_VISUAL_THEME"] = theme
        binary = isolated_editor(engine_binary(), temporary)
        seed_theme(binary, theme)
        base = [str(binary), "--path", str(project), "--audio-driver", "Dummy"]
        run_godot(harness, base + ["--headless", "--editor", "--import"], env, logs)
        (project / "project.godot").write_text(
            settings
            + "[editor_plugins]\nenabled=PackedStringArray("
            + '"res://addons/joinery/plugin.cfg", "res://addons/visual_check/plugin.cfg")\n'
        )
        run_godot(
            harness,
            base + ["--editor", "res://visual.tscn"] + rendering(env),
            env,
            logs,
            marker="visual editor: ok",
            timeout=300,
        )
    return harness


def classic_view(theme: str, capture: Path, logs: Path) -> Harness:
    """Joinery's layout, Godot's classic one and back (run_classic_view_test.py)."""
    harness = Harness(f"classic-view-{theme}")
    harness.expected = capture_names(theme)
    lines: list[str] = []
    harness.errors = [
        problem
        for problem in theme_pass(theme, capture, logs=logs, findings=lines)
        if not problem.startswith("missing capture")
    ]
    for line in lines:
        if match := FINDING.match(line.strip()):
            harness.findings.append(Finding(*match.groups()))
    harness.captures = [name for name in harness.expected if (capture / name).is_file()]
    return harness


def legacy(name: str, capture: Path, logs: Path) -> Harness:
    script, expected = LEGACY[name]
    harness = Harness(f"legacy-{name}")
    harness.expected = list(expected)
    env = dict(os.environ, JOINERY_CAPTURE_DIR=str(capture))
    run_godot(harness, [sys.executable, str(TESTS / script)], env, logs, timeout=600)
    harness.captures = [name for name in expected if (capture / name).is_file()]
    return harness


def blockout_fixture(folder: Path) -> None:
    """A sample sketch and the plan Joinery draws of the level built from it."""
    from joinery import blockout

    sketches = ROOT / "tests/fixtures/blockout"
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(sketches / "keep.png", folder / "keep.png")
    built = blockout.make(folder, (sketches / "keep.toml").read_text(encoding="utf-8"))
    (folder / "keep-plan.png").write_bytes(built.plan_png)


def seed_theme(binary: Path, theme: str) -> None:
    """Start the isolated editor in the requested theme, as a user would set it."""
    if theme != "light":
        return
    data = binary.parent / "editor_data"
    data.mkdir(exist_ok=True)
    (data / "editor_settings-4.7.tres").write_text(LIGHT_SETTINGS)


def rendering(env: dict[str, str]) -> list[str]:
    driver = env.get("JOINERY_RENDERING_DRIVER", "")
    return ["--rendering-driver", driver] if driver else []


def plan(args: argparse.Namespace) -> list[tuple[str, Callable[[Path, Path], Harness]]]:
    jobs: list[tuple[str, Callable[[Path, Path], Harness]]] = []
    for theme in args.themes:
        jobs.append((f"project-manager-{theme}", partial(startup, theme)))
    jobs.append(("sidebar", sidebar))
    for theme in args.themes:
        jobs.append((f"editor-{theme}", partial(editor, theme)))
    for theme in args.themes:
        jobs.append((f"classic-view-{theme}", partial(classic_view, theme)))
    if not args.skip_legacy:
        for name in LEGACY:
            jobs.append((f"legacy-{name}", partial(legacy, name)))
    if args.only:
        jobs = [job for job in jobs if any(part in job[0] for part in args.only)]
    return jobs


def grouped(findings: list[Finding]) -> list[dict[str, object]]:
    """Merge one control's finding across screens, themes and widths."""
    groups: dict[tuple[str, str], list[Finding]] = defaultdict(list)
    for finding in findings:
        groups[(finding.kind, finding.node)].append(finding)
    rows: list[dict[str, object]] = [
        {
            "kind": kind,
            "node": node,
            "count": len(items),
            "screens": sorted({item.screen for item in items}),
            "example": items[0].detail,
        }
        for (kind, node), items in groups.items()
    ]
    rows.sort(
        key=lambda row: (
            -int(str(row["count"])),
            KINDS.index(str(row["kind"])),
            str(row["node"]),
        )
    )
    return rows


def write_reports(out: Path, harnesses: list[Harness], report_only: bool) -> int:
    findings = [finding for harness in harnesses for finding in harness.findings]
    captures = sorted(path.name for path in out.glob("*.png"))
    missing = sorted(
        {name for harness in harnesses for name in harness.expected}
        - {name for name in captures if is_png(out / name)}
    )
    broken = [harness.name for harness in harnesses if harness.errors]
    counts = Counter(finding.kind for finding in findings)
    by_screen = Counter(finding.screen for finding in findings)
    groups = grouped(findings)
    report = {
        "output": str(out),
        "report_only": report_only,
        "captures": captures,
        "missing": missing,
        "harnesses": [
            {
                "name": harness.name,
                "ok": not harness.errors,
                "seconds": round(harness.seconds, 1),
                "errors": harness.errors,
                "captures": harness.captures,
                "log": f"logs/{harness.name}.log",
            }
            for harness in harnesses
        ],
        "counts": {kind: counts.get(kind, 0) for kind in KINDS},
        "by_screen": dict(sorted(by_screen.items())),
        "grouped": groups,
        "findings": [finding.__dict__ for finding in findings],
    }
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    lines = [
        "# Joinery visual suite",
        "",
        (
            f"{len(captures)} screenshots in `{out}`. "
            f"{len(findings)} findings across {len(by_screen)} screens."
        ),
        "",
        "| Check | Findings |",
        "| --- | --- |",
        *(f"| {kind} | {counts.get(kind, 0)} |" for kind in KINDS),
        "",
    ]
    if missing:
        lines += ["## Missing captures", "", *(f"- {name}" for name in missing), ""]
    if broken:
        lines += ["## Harness failures", ""]
        for harness in harnesses:
            if harness.errors:
                lines.append(
                    f"- {harness.name}: {'; '.join(harness.errors[:3])} "
                    f"(logs/{harness.name}.log)"
                )
        lines.append("")
    lines += [
        "## Most widespread findings",
        "",
        "One row per control and check; *screens* counts where it occurs.",
        "",
        "| Check | Control | Screens | Example |",
        "| --- | --- | --- | --- |",
    ]
    for row in groups[:30]:
        example = str(row["example"]).replace("|", "\\|")
        lines.append(
            f"| {row['kind']} | `{row['node']}` | {row['count']} | {example} |"
        )
    lines += [
        "",
        "## Findings per screen",
        "",
        *(f"- {screen}: {count}" for screen, count in sorted(by_screen.items())),
        "",
        "Every finding is listed in `report.json`.",
        "",
    ]
    (out / "report.md").write_text("\n".join(lines))
    print("\n".join(lines))
    if missing or broken:
        return 1
    return 0 if report_only or not findings else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, default=ROOT / "out/visual")
    parser.add_argument(
        "--report-only", action="store_true", help="Report findings without failing"
    )
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        help="Run harnesses whose name contains this",
    )
    parser.add_argument(
        "--skip-legacy",
        action="store_true",
        help="Skip the older startup, assistant and scene-edit harnesses",
    )
    parser.add_argument("--themes", nargs="+", choices=THEMES, default=list(THEMES))
    args = parser.parse_args()
    if sys.prefix == sys.base_prefix:
        raise SystemExit("Run this suite with uv run python.")
    out: Path = args.out.resolve()
    if out.exists():
        for stale in [*out.glob("*.png"), out / "report.json", out / "report.md"]:
            stale.unlink(missing_ok=True)
        shutil.rmtree(out / "logs", ignore_errors=True)
    logs = out / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    harnesses: list[Harness] = []
    for name, job in plan(args):
        print(f"== {name}", flush=True)
        started = time.monotonic()
        harness = job(out, logs)
        harness.seconds = time.monotonic() - started
        status = (
            "ok" if not harness.errors else "FAILED: " + "; ".join(harness.errors[:3])
        )
        print(
            f"   {len(harness.captures)} captures, {len(harness.findings)} findings, "
            f"{harness.seconds:.0f}s, {status}",
            flush=True,
        )
        harnesses.append(harness)
    return write_reports(out, harnesses, args.report_only)


if __name__ == "__main__":
    raise SystemExit(main())
