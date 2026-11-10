"""The art bible is rendered from the template, and never invents taste."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from joinery.bible import (
    ANSWERS_FILE,
    GROUPS,
    QUESTIONS,
    grouped,
    render,
    write_bible,
    write_from_answers_file,
)
from joinery.catalog import ROOT

FULL = {
    "title": "Hollow Keep",
    "vision": "Quiet, heavy stone at dusk. Never cartoonish.",
    "references": "Hob: chunky silhouettes, not its colours.",
    "look": "low-poly with stylised PBR",
    "palette": "warm greys, brass on what you can touch",
    "sound": "dry and close, no reverb tails",
    "bans": "lens flares, chromatic aberration",
}


def test_every_question_has_a_token_in_the_template() -> None:
    # A question with nowhere to go would be silently discarded.
    template = (ROOT / "templates" / "art_bible.md").read_text()
    for question in QUESTIONS:
        token = "{{" + str(question["key"]) + "}}"
        assert token in template, question["key"]


def test_every_asked_question_belongs_to_a_page() -> None:
    # A question in an unknown group would never be shown by the paged UI.
    # An empty group is deliberate: the bible keeps the field, setup skips it.
    group_keys = {key for key, _ in GROUPS}
    asked = [q for q in QUESTIONS if q["group"]]
    for question in asked:
        assert question["group"] in group_keys, question["key"]
    paged = sum(len(page["questions"]) for page in grouped())
    assert paged == len(asked)


def test_setup_asks_one_short_look_and_sound_page() -> None:
    pages = grouped()
    assert [page["label"] for page in pages] == ["Look and sound"]
    questions = pages[0]["questions"]
    assert isinstance(questions, list)
    assert [q["key"] for q in questions] == [
        "vision",
        "look",
        "palette",
        "sound",
        "bans",
    ]
    assert [q["label"] for q in questions][-1] == "Never"
    assert all(str(q["placeholder"]).startswith("e.g. ") for q in questions)


def test_references_stay_in_the_bible_but_are_not_asked() -> None:
    keys = {str(q["key"]) for q in QUESTIONS}
    asked = {str(q["key"]) for page in grouped() for q in page["questions"]}  # type: ignore[attr-defined]
    assert "references" in keys
    assert "references" not in asked


def test_vision_hint_is_only_about_the_feel() -> None:
    vision = next(q for q in QUESTIONS if q["key"] == "vision")
    assert "never" not in str(vision["hint"]).lower()


def test_answers_reach_the_output() -> None:
    text = render(FULL)
    for value in FULL.values():
        assert value in text


def test_no_token_survives_rendering() -> None:
    assert "{{" not in render(FULL)


def test_unanswered_is_recorded_not_invented() -> None:
    text = render({"title": "Sparse"})
    assert "Sparse" in text
    assert "{{" not in text
    # The marker appears once per unanswered question.
    assert text.count("Not set yet") == len(QUESTIONS)


def test_write_bible_creates_the_file(tmp_path: Path) -> None:
    written = write_bible(tmp_path, FULL)
    assert written == tmp_path / "art" / "bible.md"
    assert "Hollow Keep" in written.read_text()


def test_write_bible_refuses_to_overwrite(tmp_path: Path) -> None:
    # Changing a bible is a reviewed edit, not something setup may clobber.
    write_bible(tmp_path, FULL)
    with pytest.raises(FileExistsError):
        write_bible(tmp_path, FULL)


def test_answers_file_is_consumed(tmp_path: Path) -> None:
    (tmp_path / ANSWERS_FILE).write_text(json.dumps(FULL))
    written = write_from_answers_file(tmp_path)
    assert written is not None and written.is_file()
    # Removed, so a stale answer set cannot reappear later.
    assert not (tmp_path / ANSWERS_FILE).exists()


def test_no_answers_file_is_not_an_error(tmp_path: Path) -> None:
    assert write_from_answers_file(tmp_path) is None


def test_malformed_answers_are_rejected_and_cleared(tmp_path: Path) -> None:
    (tmp_path / ANSWERS_FILE).write_text("{not json")
    with pytest.raises(ValueError, match="not valid JSON"):
        write_from_answers_file(tmp_path)
    assert not (tmp_path / ANSWERS_FILE).exists()


def test_bible_is_not_treated_as_an_asset(tmp_path: Path) -> None:
    # art/bible.md lives under art/ but is prose, not an asset needing a recipe.
    from joinery.lockfile import asset_files

    write_bible(tmp_path, FULL)
    assert asset_files(tmp_path) == set()
