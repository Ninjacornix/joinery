"""The fork patches must add Joinery's behaviour without quietly deleting Godot's.

A patch that removes upstream code it never meant to touch is the worst kind of
fork bug: it compiles, and then the editor crashes somewhere unrelated. That
happened once here. A scripted edit anchored on text that did not match fell
back to a wrong anchor and deleted the Version Control Metadata widgets and a
file dialog from the project dialog's constructor, leaving pointers that
`ok_pressed` dereferences. The build was clean and the Project Manager died on
launch.

So the patches are checked as text: a deletion has to be one the series is
meant to make, and anything else fails here rather than at runtime.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from joinery.catalog import ROOT

PATCH_DIR = ROOT / "godot_patches" / "patches"

#: Upstream identifiers that must never disappear from a patched file. These are
#: constructed in a constructor and dereferenced elsewhere, so losing one is a
#: null dereference rather than a compile error.
MUST_SURVIVE = (
    "vcs_metadata_selection",
    "fdialog_install",
    "renderer_button_group",
    "project_name",
    "project_path",
    "edit_check_box",
)


def patches() -> list[Path]:
    return sorted(PATCH_DIR.glob("*.patch"))


def removed_lines(patch: Path) -> list[str]:
    """Lines the patch deletes, without the diff bookkeeping."""
    out: list[str] = []
    for line in patch.read_text().splitlines():
        if line.startswith(("---", "-- ")):
            continue
        if line.startswith("-"):
            out.append(line[1:])
    return out


def test_there_is_at_least_one_patch() -> None:
    assert patches(), "no patches exported; run godot_patches/fork.sh export"


@pytest.mark.parametrize("patch", patches(), ids=lambda p: p.name)
def test_patch_does_not_delete_load_bearing_upstream_code(patch: Path) -> None:
    deleted = "\n".join(removed_lines(patch))
    for symbol in MUST_SURVIVE:
        # A patch may *move* a line, so only flag a deletion that is not matched
        # by an addition of the same symbol.
        added = [
            line
            for line in patch.read_text().splitlines()
            if line.startswith("+") and symbol in line
        ]
        if symbol in deleted and not added:
            pytest.fail(
                f"{patch.name} deletes upstream code mentioning {symbol!r} "
                "without putting it back. That compiles and then crashes."
            )


# Patches that deliberately replace code an earlier Joinery patch added, by number.
DELETION_ALLOWANCE = {
    # The three-step wizard (worker threads, readiness rows, Look and sound)
    # replaces the questionnaire 0001 put into project_dialog.cpp. Of the lines
    # it removes, all but a few dozen generic ones ("}", "break;") are Joinery's.
    "0008": 500,
    # The start-screen and setup restyle rewrites the wizard and empty project
    # list that 0001, 0008 and 0009 added. Checked against 4.7.2-stable: the
    # only upstream lines removed are replaced in place (button icons, the
    # Import label, "Filter projects" casing, the main view panel style).
    "0012": 130,
    "0013": 70,
    "0014": 30,
    # The starting-point wizard replaces the 0008/0012 wizard's step 1 and 2
    # pages, its three-step creating list and its art-only step 3. Checked
    # against 4.7.2-stable: the one upstream line it changes is ok_pressed's
    # `if (mode == MODE_NEW) {`, which gains a condition.
    "0017": 175,
    # The classic-view switch wraps the layout changes of 0001, 0003-0006 and
    # 0012 in one setting. Every line it removes is one of those Joinery lines,
    # put back inside `if (EditorSettings::is_joinery_layout())`.
    "0019": 70,
}


@pytest.mark.parametrize("patch", patches(), ids=lambda p: p.name)
def test_patch_deletions_stay_small(patch: Path) -> None:
    """A Joinery patch adds; it should barely remove anything.

    The series reroutes stock flows, so a handful of replaced lines is normal.
    Wholesale deletion means an edit went wrong, which is what this catches.
    """
    deleted = [line for line in removed_lines(patch) if line.strip()]
    limit = DELETION_ALLOWANCE.get(patch.name.split("-", 1)[0], 20)
    assert len(deleted) <= limit, (
        f"{patch.name} removes {len(deleted)} upstream lines. If that is "
        "deliberate, raise this limit in the test and say why."
    )


@pytest.mark.parametrize("patch", patches(), ids=lambda p: p.name)
def test_patch_says_whether_it_is_for_upstream(patch: Path) -> None:
    # ADR 0001 keeps general fixes separable from Joinery-only reroutes, which
    # only works if each patch states which it is.
    text = patch.read_text().lower()
    assert "upstream" in text, (
        f"{patch.name} does not say whether it is meant for upstream. "
        "Keeping that explicit is what makes rebasing onto a new tag tractable."
    )


def test_patches_target_only_expected_areas() -> None:
    """The series should not sprawl across the engine without a decision."""
    allowed = ("editor/",)
    exceptions = {
        "0010-fix-windows-initialize-WGL-before-pixel-formats.patch": {
            "platform/windows/gl_manager_windows_native.cpp": "docs/adr/0018-windows-software-opengl.md",
        },
        "0011-fix-windows-treat-broken-output-pipes-as-EOF.patch": {
            "drivers/windows/file_access_windows_pipe.cpp": "docs/adr/0019-windows-process-pipe-eof.md",
        },
        "0021-test-platform-keep-test-editors-in-the-background.patch": {
            path: "docs/adr/0032-background-test-editors.md"
            for path in (
                "platform/joinery_background.h",
                "platform/macos/display_server_macos.mm",
                "platform/macos/godot_application.mm",
                "platform/macos/godot_window_delegate.mm",
                "platform/macos/os_macos.mm",
                "platform/windows/display_server_windows.cpp",
            )
        },
        "0022-test-windows-trace-fail-fast-exits-in-test-editors.patch": {
            "platform/windows/godot_windows.cpp": "docs/adr/0032-background-test-editors.md",
        },
    }
    for patch in patches():
        for match in re.findall(r"^\+\+\+ b/(.+)$", patch.read_text(), re.MULTILINE):
            if match.startswith(allowed):
                continue
            decision = exceptions.get(patch.name, {}).get(match)
            assert decision is not None and (ROOT / decision).is_file(), (
                f"{patch.name} touches {match}, outside {allowed}. "
                "An exact-file exception requires its own ADR."
            )


def test_the_new_project_wizard_patch_is_in_the_series() -> None:
    """The starting-point wizard ships as one patch that reads the CLI's data."""
    wizard = [p for p in patches() if "What are you making?" in p.read_text()]
    assert len(wizard) == 1, "exactly one patch adds the starting-point wizard"
    text = wizard[0].read_text()
    # Its data comes from the Joinery CLI, never from tables restated in C++.
    for command in (
        '"blueprints", "--json"',
        '"game", "apply"',
        '"brief", "--project"',
    ):
        assert command in text, f"the wizard runs joinery {command}"
    assert "not intended for upstream" in text


def _upstream() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in (ROOT / "godot_patches" / "UPSTREAM").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def test_series_applies_to_the_pinned_tag() -> None:
    """Every patch applies, in order, to the pinned Godot tag.

    Uses the local fork checkout's object store and a throwaway index, so no
    files are checked out and nothing is fetched. Skips without a checkout.
    """
    source = ROOT / "godot_patches" / "godot-src"
    git = shutil.which("git")
    if git is None or not (source / ".git").exists():
        pytest.skip("no godot_patches/godot-src checkout; run fork.sh sync")
    commit = _upstream()["GODOT_COMMIT"]
    probe = subprocess.run(
        [git, "-C", str(source), "cat-file", "-e", commit + "^{commit}"],
        capture_output=True,
        check=False,
    )
    if probe.returncode != 0:
        pytest.skip("the pinned tag is not in the local checkout")
    with tempfile.TemporaryDirectory() as directory:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(directory) / "index"))
        subprocess.run(
            [git, "-C", str(source), "read-tree", commit],
            env=env,
            check=True,
            capture_output=True,
        )
        for patch in patches():
            applied = subprocess.run(
                [git, "-C", str(source), "apply", "--cached", str(patch)],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            assert applied.returncode == 0, (
                f"{patch.name} does not apply after the patches before it: "
                + applied.stderr.strip()
            )


def _series_text() -> str:
    return "\n".join(p.read_text() for p in patches())


def test_one_setting_switches_to_the_classic_layout() -> None:
    """Classic Godot's layout comes back through one setting, read in one place."""
    switch = [
        p for p in patches() if "interface/joinery/editor_layout" in p.read_text()
    ]
    assert len(switch) == 1, "exactly one patch adds the editor layout setting"
    text = switch[0].read_text()
    assert "not meant for upstream" in text
    # Restart-if-changed, offered in the Editor menu and the command palette.
    assert "PROPERTY_USAGE_RESTART_IF_CHANGED" in text
    assert 'TTRC("Switch to classic Godot view")' in text
    assert "command_palette->add_command(" in text
    # Each layout keeps its own dock arrangement.
    for key in ('"docks_joinery"', '"docks_classic"', '"joinery_view"'):
        assert key in text, key
    # Every Joinery dock move is guarded: the stock slots are restored by name.
    for stock in ("DOCK_SLOT_LEFT_UR", "DOCK_SLOT_LEFT_BR", "DOCK_SLOT_RIGHT_UL"):
        assert f": EditorDock::{stock}" in text or f"EditorDock::{stock});" in text, (
            stock
        )


def test_wizard_checks_the_home_path_and_the_steam_app_id() -> None:
    text = _series_text()
    # The background load refuses an unsafe home path, as _joinery_uv does.
    assert "!_joinery_shell_safe(home)" in text
    # Steam app IDs are unsigned 32-bit.
    assert "4294967295" in text
    assert "Too big for a Steam app ID." in text
