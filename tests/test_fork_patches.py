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

import re
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


@pytest.mark.parametrize("patch", patches(), ids=lambda p: p.name)
def test_patch_deletions_stay_small(patch: Path) -> None:
    """A Joinery patch adds; it should barely remove anything.

    The series reroutes stock flows, so a handful of replaced lines is normal.
    Wholesale deletion means an edit went wrong, which is what this catches.
    """
    deleted = [line for line in removed_lines(patch) if line.strip()]
    assert len(deleted) <= 20, (
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
    for patch in patches():
        for match in re.findall(r"^\+\+\+ b/(.+)$", patch.read_text(), re.MULTILINE):
            assert match.startswith(allowed), (
                f"{patch.name} touches {match}, outside {allowed}. "
                "Widening the fork's blast radius deserves an ADR."
            )
