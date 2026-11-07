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


def test_every_question_belongs_to_a_page() -> None:
    # A question in no group would never be shown by the paginated UI.
    group_keys = {key for key, _ in GROUPS}
    for question in QUESTIONS:
        assert question["group"] in group_keys, question["key"]
    paged = sum(len(page["questions"]) for page in grouped())
    assert paged == len(QUESTIONS)


def test_pages_are_small_enough_to_read() -> None:
    for page in grouped():
        assert 1 <= len(page["questions"]) <= 3, page["key"]


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
