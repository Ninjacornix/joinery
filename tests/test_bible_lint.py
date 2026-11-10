"""Lint reads the bible itself: references, palette maths, gaps and suggestions."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from joinery import bible_lint, bible_presets
from joinery.catalog import ROOT
from tests.test_bible_upgrade import v1_bible

TEMPLATE = ROOT / "templates" / "art_bible.md"

BOARD = """
| R1 | Hob (game) | game | chunky silhouettes, flat lighting | its saturated greens | |
| R2 | my photo of the quarry | photo | warm stone, cold sky | the haze | art/reference/quarry.png |
| R3 | board: dusk | board | three-value lighting | lens flares | art/reference/dusk_board.kra |
| R4 | a song | music |  | the vocals | art/reference/song.ogg |
| R5 | Alien (film) | movie | the corridors | | art/props/door.blend |
"""


def write(project: Path, text: str) -> None:
    (project / "art").mkdir(parents=True, exist_ok=True)
    (project / "art/bible.md").write_text(text)


def rendered(answers: dict[str, str], preset: str | None = None) -> str:
    return bible_presets.fill(TEMPLATE.read_text(), answers, preset)


def with_board(text: str, rows: str = BOARD) -> str:
    header = "|---|---|---|---|---|---|\n"
    return text.replace(header, header + rows.strip() + "\n", 1)


def lock(project: Path, *paths: str) -> None:
    entries = []
    for path in paths:
        digest = hashlib.sha256((project / path).read_bytes()).hexdigest()
        entries.append(
            f'[[asset]]\npath = "{path}"\nsha256 = "{digest}"\norigin = "human-import"\n'
            'imported_by = "tester"\nlicence = "own work, reference only"\n'
        )
    (project / "joinery.lock").write_text("version = 1\n\n" + "\n".join(entries))


def test_reference_board_rows_are_parsed_with_file_state(tmp_path: Path) -> None:
    (tmp_path / "art/reference").mkdir(parents=True)
    (tmp_path / "art/reference/quarry.png").write_bytes(b"photo")
    (tmp_path / "art/reference/dusk_board.kra").write_bytes(b"krita")
    lock(tmp_path, "art/reference/quarry.png")
    rows = bible_lint.references(with_board(rendered({})), tmp_path)
    assert [r.id for r in rows] == ["R1", "R2", "R3", "R4", "R5"]
    assert rows[0].take == "chunky silhouettes, flat lighting"
    assert rows[0].avoid == "its saturated greens"
    assert (rows[1].file_exists, rows[1].file_locked) == (True, True)
    assert (rows[2].file_exists, rows[2].file_locked) == (True, False)
    assert (rows[3].file_exists, rows[3].file_locked) == (False, False)
    # Without a project, files are not looked up.
    assert not any(
        r.file_exists for r in bible_lint.references(with_board(rendered({})))
    )


def test_reference_problems_name_each_row(tmp_path: Path) -> None:
    (tmp_path / "art/reference").mkdir(parents=True)
    (tmp_path / "art/reference/quarry.png").write_bytes(b"photo")
    (tmp_path / "art/reference/dusk_board.kra").write_bytes(b"krita")
    lock(tmp_path, "art/reference/quarry.png")
    write(tmp_path, with_board(rendered({})))
    problems = bible_lint.lint(tmp_path).problems
    assert problems == [
        (
            "R3: art/reference/dusk_board.kra has no joinery.lock entry (your own "
            "import, or the Krita recipe that made it)"
        ),
        "R4: say what to take from it (Take this)",
        "R4: art/reference/song.ogg is missing",
        (
            "R5: kind 'movie' is not one of game, film, place, photo, sketch, board, "
            "painting, music"
        ),
        "R5: reference files live under art/reference/, not art/props/door.blend",
        "R5: art/props/door.blend is missing",
    ]


def test_ids_must_be_stable_and_unique() -> None:
    rows = bible_lint.references(
        with_board(
            rendered({}),
            "| R1 | one | game | a | b | |\n| R1 | two | game | a | b | |\n"
            "| x | three | game | a | b | |\n",
        )
    )
    assert bible_lint.reference_problems(rows, None) == [
        "R1 is used by more than one reference",
        "Reference 'three' needs an id like R1, so Journeyman can cite it",
    ]


def test_template_examples_are_not_parsed_as_references() -> None:
    assert bible_lint.references(rendered({})) == []


def test_palette_maths_in_the_rules_block_is_linted(tmp_path: Path) -> None:
    text = rendered({}).replace(
        "[palette.roles]\n# primary",
        '[palette.roles]\nui_base = "#2b2d42"\nui_text = "#3a3d55"\n'
        'danger = "#d62728"\nsafe = "#2ca02c"\n# primary',
    )
    text = text.replace("ui_pairs = []", 'ui_pairs = [["ui_text", "ui_base"]]').replace(
        "colour_blind_distinct = []", 'colour_blind_distinct = ["danger", "safe"]'
    )
    write(tmp_path, text)
    report = bible_lint.lint(tmp_path)
    assert report.rules is not None
    assert report.problems[0].startswith("ui_text on ui_base has contrast 1.")
    assert any(
        p.startswith("danger and safe merge under deuteranopia: ΔE ")
        and p.endswith("need 10")
        for p in report.problems
    )


def test_invalid_rules_are_a_problem_not_a_crash(tmp_path: Path) -> None:
    write(tmp_path, rendered({}).replace("schema = 2", "schema = 2\nbogus = 1"))
    report = bible_lint.lint(tmp_path)
    assert report.rules is None
    assert report.problems[0].startswith("Checked rules are invalid: bogus")


@pytest.mark.parametrize(
    ("old", "new", "named"),
    [
        (
            'metallic_requires_name = "(?i)metal"',
            'metallic_requires_name = "(metal"',
            "metallic_requires_name",
        ),
        ('"art" = "^[a-z0-9]+(_[a-z0-9]+)*$"', '"art" = "^[a-z"', "naming pattern"),
    ],
)
def test_a_broken_regex_in_the_rules_is_a_problem_not_a_crash(
    tmp_path: Path, old: str, new: str, named: str
) -> None:
    text = rendered({})
    assert old in text
    write(tmp_path, text.replace(old, new))
    report = bible_lint.lint(tmp_path)
    assert report.rules is None
    assert report.problems[0].startswith("Checked rules are invalid")
    assert named in report.problems[0]


def test_gaps_tell_required_from_open() -> None:
    report = bible_lint.lint_text(
        rendered({"title": "Keep", "vision": "Quiet."}, "lowpoly-3d")
    )
    required = [g.section for g in report.gaps if g.required]
    open_ = [g.section for g in report.gaps if not g.required]
    assert required == ["references", "look", "palette", "sound", "bans"]
    # The preset filled camera, models, materials, animation, vfx, ui, accessibility.
    assert open_ == ["pillars", "shape_language", "lighting", "sprites", "writing"]
    assert report.version == 2


def test_suggestions_put_required_first_then_the_projects_kind() -> None:
    report = bible_lint.lint_text(rendered({"title": "Keep", "vision": "Quiet."}))
    three_d = bible_lint.suggestions_for(report, "3d")
    assert three_d[0] == (
        "Before approving the bible: the references, visual style, palette, audio and "
        "never sections are not set yet. Ask about them."
    )
    assert three_d[1].startswith("The reference board is empty.")
    assert (
        "Before the first character model: the shape language and animation feel "
        "sections are open. Ask about them."
    ) in three_d
    assert not any("2D and sprites" in s for s in three_d)
    two_d = bible_lint.suggestions_for(report, "2d")
    assert (
        "Before the first sprite: the 2D and sprites section is open. Ask about it."
        in two_d
    )
    assert not any("3D rules" in s for s in two_d)
    # The project's own kind comes before sections that matter to any game.
    assert two_d.index(
        "Before the first sprite: the 2D and sprites section is open. Ask about it."
    ) < next(i for i, s in enumerate(two_d) if "pillars" in s)


def test_first_format_bible_is_offered_the_upgrade(tmp_path: Path) -> None:
    write(tmp_path, v1_bible({"title": "Old"}))
    assert bible_lint.lint(tmp_path).version == 1
    suggestions = bible_lint.suggestions(tmp_path)
    assert any(s.startswith("This bible uses the first format.") for s in suggestions)
    # Sections v1 never had are missing, not open: the upgrade covers them.
    assert not any("shape language" in s for s in suggestions)


def test_no_bible_suggests_drafting_one(tmp_path: Path) -> None:
    assert bible_lint.lint(tmp_path).version == 0
    assert bible_lint.suggestions(tmp_path)[0].startswith("There is no art bible yet.")


def test_project_dimension_comes_from_the_blueprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_root = tmp_path / "joinery-root"
    (fake_root / "blueprints/top-down").mkdir(parents=True)
    (fake_root / "blueprints/top-down/blueprint.toml").write_text('dimension = "2d"\n')
    monkeypatch.setattr(bible_lint, "ROOT", fake_root)
    project = tmp_path / "game"
    project.mkdir()
    assert bible_lint.project_dimension(project) is None
    (project / "joinery.toml").write_text('[game]\nblueprint = "top-down"\n')
    assert bible_lint.project_dimension(project) == "2d"
    (project / "joinery.toml").write_text('[game]\nblueprint = "../../etc"\n')
    assert bible_lint.project_dimension(project) is None


def test_summary_is_json_safe(tmp_path: Path) -> None:
    write(tmp_path, with_board(rendered({"title": "Keep"}, "pixel-2d")))
    summary = bible_lint.summary(tmp_path)
    assert set(summary) == {
        "version",
        "rules",
        "problems",
        "gaps",
        "references",
        "suggestions",
    }
    assert json.loads(json.dumps(summary)) == summary
    assert summary["rules"]["schema"] == 2
    assert summary["references"][0]["id"] == "R1"
    assert summary["gaps"][0]["section"] == "vision"
