"""Exercise native controls and actual Godot/Python pipes in a disposable project.

Pass test file names to run only those (``run_widget_tests.py chatup_test.gd``).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from music_fixture import plant as plant_music
from run_startup_test import (
    crash,
    isolated_editor,
    isolated_environment,
    remove_when_released,
)

from joinery.catalog import ROOT
from joinery.distribution import engine_binary

CASES = {
    "command_job_test.gd": "command lifecycle: ok",
    "chat_transport_test.gd": "chat transport: ok",
    "chatup_test.gd": "sidebar chat: ok",
    "audio_preview_test.gd": "audio playback: ok",
    "scene_edits_test.gd": "scene edits: ok",
    "responsive_assistant_test.gd": "responsive assistant: ok",
    "ui_theme_test.gd": "ui theme: ok",
    "review_panel_test.gd": "review panel: ok",
    "review_tab_test.gd": "review tab: ok",
    "review_flow_test.gd": "review flow: ok",
    "review_glance_test.gd": "review glance: ok",
    "review_dialogue_test.gd": "review dialogue: ok",
    "review_stacked_test.gd": "review stacked: ok",
    "asset_workspace_test.gd": "asset workspace: ok",
    "kits_panel_test.gd": "kits panel: ok",
    "animation_browser_test.gd": "animation browser: ok",
    "review_clip_test.gd": "review clip: ok",
    "review_live_test.gd": "review live: ok",
    "review_variant_test.gd": "review variant: ok",
    "kit_share_test.gd": "kit share: ok",
    "sidebar_feature_test.gd": "sidebar features: ok",
    "art_review_test.gd": "art review: ok",
    "share_view_test.gd": "share view: ok",
    "chat_images_test.gd": "chat images: ok",
    "usage_meter_test.gd": "usage meter: ok",
    "chat_sessions_test.gd": "chat sessions: ok",
    "chat_rewind_test.gd": "chat rewind: ok",
    "provider_switch_test.gd": "provider switch: ok",
    "parallel_tasks_test.gd": "parallel tasks: ok",
    "agent_roles_test.gd": "agent roles: ok",
    "playtest_card_test.gd": "playtest card: ok",
    "todo_card_test.gd": "todo card: ok",
    "share_tab_test.gd": "share tab: ok",
    "match_card_test.gd": "match card: ok",
    "access_card_test.gd": "access card: ok",
    "perf_card_test.gd": "perf card: ok",
    "design_card_test.gd": "design card: ok",
    "tidy_card_test.gd": "tidy card: ok",
    "project_commands_test.gd": "project commands: ok",
    "variants_test.gd": "variants: ok",
    "complex_models_setting_test.gd": "complex models setting: ok",
    "fix_errors_setting_test.gd": "fix errors setting: ok",
    "mixer_test.gd": "mixer: ok",
    "sound_pass_card_test.gd": "sound pass card: ok",
    "sidebar_margins_test.gd": "sidebar margins: ok",
    "tour_test.gd": "tour: ok",
    "update_dialog_test.gd": "update dialog: ok",
    # Last: it puts the shared disposable project under git.
    "timeline_test.gd": "timeline: ok",
    # After the timeline: it shares the same project through a local bare remote.
    "team_test.gd": "team: ok",
}
#: Cases that need a game made from a blueprint (the real `joinery feature` CLI runs there).
GAME_CASES = {"sidebar_feature_test.gd"}
GAME_BLUEPRINT = "top-down"


def _project(path: Path) -> Path:
    path.mkdir()
    shutil.copytree(ROOT / "godot_addon/joinery", path / "addons/joinery")
    (path / "project.godot").write_text(
        'config_version=5\n[application]\nconfig/name="Native widget checks"\n'
        '[rendering]\nrenderer/rendering_method="gl_compatibility"\n'
    )
    (path / "joinery.toml").write_text('[tools]\nenabled=["godot"]\n')
    return path


def _game(path: Path, env: dict[str, str]) -> Path:
    """A real blueprint game, made by the same CLI the wizard uses."""
    _project(path)
    subprocess.run(
        [
            "uv",
            "run",
            "--frozen",
            "--quiet",
            "--project",
            str(ROOT),
            "joinery",
            "game",
            "apply",
            "--project",
            str(path),
            "--blueprint",
            GAME_BLUEPRINT,
            "--features",
            "",
            "--no-addon",
        ],
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return path


def main(selected: list[str]) -> int:
    temporary = Path(tempfile.mkdtemp(prefix="joinery native widgets "))
    try:
        return _run(selected, temporary)
    finally:
        remove_when_released(temporary)


def _run(selected: list[str], temporary: Path) -> int:
    cases = {case: CASES[case] for case in selected} if selected else CASES
    env = isolated_environment(temporary, ROOT, temporary / "captures")
    env["JOINERY_TEST_PYTHON"] = sys.executable
    project = _project(temporary / "project")
    audio = project / "audio/sfx/door_creak.wav"
    audio.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / "testbed/audio/sfx/door_creak.wav", audio)
    if "mixer_test.gd" in cases:
        plant_music(project)
    if "review_dialogue_test.gd" in cases:
        # The review plays a .dlg file through the game's own dialogue scripts.
        shutil.copytree(
            ROOT / "features/dialogue/overlay/features/dialogue",
            project / "features/dialogue",
        )
    game = _game(temporary / "game", env) if GAME_CASES & set(cases) else None
    binary = isolated_editor(engine_binary(), temporary)

    def base(path: Path) -> list[str]:
        return [
            str(binary),
            "--headless",
            "--audio-driver",
            "Dummy",
            "--path",
            str(path),
        ]

    commands = [(base(project) + ["--editor", "--import"], "")]
    if game is not None:
        commands.append((base(game) + ["--editor", "--import"], ""))
    commands += [
        (
            base(game if case in GAME_CASES and game else project)
            + ["--script", str(ROOT / "tests/godot" / case)],
            marker,
        )
        for case, marker in cases.items()
    ]
    failed: list[str] = []
    for argv, marker in commands:
        result = subprocess.run(
            argv,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
        )
        output = result.stdout + result.stderr
        print(output, flush=True)
        if reason := crash(result.returncode, output):
            print(f"{marker or ' '.join(argv[-2:])}: {reason}", flush=True)
        if result.returncode or marker not in output or "ERROR:" in output:
            failed.append(marker or " ".join(argv[-2:]))
    if failed:
        print("FAILED:", ", ".join(failed), flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
