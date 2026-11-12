"""The dialogue feature: .dlg files parsed, played in a dialogue box, translated.

The Godot check runs `res://tests/features/dialogue/dialogue_test.gd` in stock
Godot on a 2D and a 3D blueprint: guard.dlg parses (and a broken file names its
problems by line), `load()` gives a DialogueFile, the box shows speakers and lines
in the menus' style, Accept and clicks advance, choices hide by condition and jump,
`set`/`do`/`if` run, variables last between conversations, the Accessibility
settings resize and recolour it, lines translate by their [ID:…] key, and the
playtester can read where a conversation is.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from joinery import blueprints
from tests.blueprints.godot_run import make_project, needs_godot, run_godot

FEATURE = "dialogue"
TEST = "res://tests/features/dialogue/dialogue_test.gd"
EXTRACT = "res://features/translations/tools/extract_pot.gd"


def test_the_feature_is_code_and_text_only_and_combines_with_every_blueprint() -> None:
    feature = blueprints.load_feature(blueprints.FEATURES_DIR / FEATURE)
    assert feature.merge.autoload == {"Dialogue": "res://features/dialogue/dialogue.gd"}
    assert set(feature.enhances) == {"accessibility", "translations"}
    overlay = blueprints.FEATURES_DIR / FEATURE / "overlay"
    suffixes = {p.suffix for p in overlay.rglob("*") if p.is_file()}
    assert suffixes <= {".gd", ".uid", ".dlg", ".md"}
    for blueprint in blueprints.list_blueprints():
        resolution = blueprints.resolve(blueprint.id, [FEATURE])
        assert resolution.problems == []


def test_the_guide_ships_into_the_game() -> None:
    guide = blueprints.FEATURES_DIR / FEATURE / "overlay/features/dialogue/GUIDE.md"
    text = guide.read_text(encoding="utf-8")
    for needle in ("~ ", "=> END", "[ID:", "[if ", "set ", "do ", "Dialogue.start("):
        assert needle in text


def test_apply_then_remove_leaves_the_project_as_it_was(tmp_path: Path) -> None:
    project = make_project(tmp_path, "top-down")
    before = (project / "project.godot").read_text(encoding="utf-8")
    blueprints.add_feature(project, FEATURE)
    assert (project / "features/dialogue/dialogue.gd").is_file()
    assert "Dialogue=" in (project / "project.godot").read_text(encoding="utf-8")
    blueprints.remove_feature(project, FEATURE)
    assert not (project / "features/dialogue").exists()
    assert (project / "project.godot").read_text(encoding="utf-8") == before


@needs_godot
@pytest.mark.parametrize("blueprint", ["top-down", "third-person-3d"])
def test_a_dialogue_plays_in_the_game(tmp_path: Path, blueprint: str) -> None:
    features = sorted({*blueprints.describe(blueprint)["default_features"], FEATURE, "accessibility"})
    project = make_project(tmp_path, blueprint, features)
    settings = f"--settings-path={tmp_path / 'settings.cfg'}"
    run_godot(project, TEST, ("--autostart", settings), timeout=240, marker=FEATURE)


@needs_godot
def test_the_translation_template_reads_dialogue_files(tmp_path: Path) -> None:
    project = make_project(tmp_path, "top-down", [FEATURE, "translations"])
    folder = project / "game/dialogue"
    folder.mkdir(parents=True)
    source = blueprints.FEATURES_DIR / FEATURE / "overlay/tests/features/dialogue/guard.dlg"
    (folder / "guard.dlg").write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    out = project / "locale/messages.pot"
    run_godot(project, EXTRACT, (f"--out={out}",), timeout=180, marker="extract_pot")
    pot = out.read_text(encoding="utf-8")
    assert 'msgctxt "guard_halt"\nmsgid "Halt! Who goes there?"' in pot
    assert 'msgid "You have {{gold}} gold."' in pot
    assert 'msgid "Ask about the castle"' in pot
    assert 'msgid "Guard"' in pot
    assert "#: res://game/dialogue/guard.dlg" in pot
