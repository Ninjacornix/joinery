"""The bible's sections come from one schema, and the template carries all of them."""

from __future__ import annotations

import re

from joinery import bible_schema
from joinery.bible import QUESTIONS
from joinery.catalog import ROOT

TEMPLATE = ROOT / "templates" / "art_bible.md"


def test_tokens_are_unique_and_title_first() -> None:
    tokens = bible_schema.tokens()
    assert tokens[0] == "title"
    assert len(tokens) == len(set(tokens))


def test_sections_are_numbered_in_order() -> None:
    assert [s.number for s in bible_schema.SECTIONS] == list(range(1, 21))


def test_v2_template_carries_every_token_once() -> None:
    text = TEMPLATE.read_text()
    found = re.findall(r"\{\{(\w+)\}\}", text)
    assert sorted(found) == sorted(bible_schema.tokens())


def test_v2_template_headings_follow_the_schema() -> None:
    text = TEMPLATE.read_text()
    headings = [line for line in text.splitlines() if line.startswith("## ")]
    sections = [bible_schema.find_section(h) for h in headings]
    assert [s.number for s in sections if s] == [
        s.number for s in bible_schema.SECTIONS
    ]
    for heading, section in zip(headings, sections, strict=True):
        assert section is not None
        assert (
            heading == f"## {section.number}. {bible_schema.heading_text(section)}"
            or (section.token == "bans" and heading == "## 19. Never (hard bans)")
        )


def test_v2_template_declares_its_version() -> None:
    text = TEMPLATE.read_text()
    assert bible_schema.version(text) == 2
    assert text.splitlines()[1] == bible_schema.VERSION_MARKER
    assert bible_schema.version("# Art Bible\n\nNo marker.\n") == 1


def test_required_sections_are_exactly_the_first_formats() -> None:
    # Upgrading must never make a project stricter: the required set is v1's.
    required = {s.token for s in bible_schema.SECTIONS if s.required and s.token}
    assert required == {str(q["key"]) for q in QUESTIONS}
    assert bible_schema.is_required("title")


def test_markers_tell_required_from_open() -> None:
    assert bible_schema.unset_marker("vision") == bible_schema.REQUIRED_MARKER
    assert bible_schema.unset_marker("camera") == bible_schema.OPEN_MARKER
    assert "Not set yet" in bible_schema.REQUIRED_MARKER
    # Approval refuses "Not set yet" and "{{"; the open marker has neither.
    assert "Not set yet" not in bible_schema.OPEN_MARKER
    assert "{{" not in bible_schema.OPEN_MARKER


def test_taste_is_never_technical() -> None:
    for token in ("title", "vision", "references", "look", "palette", "sound", "bans"):
        assert bible_schema.is_taste(token), token
    for token in ("camera", "models", "materials", "animation", "ui", "accessibility"):
        assert not bible_schema.is_taste(token), token


def test_first_format_headings_are_recognised() -> None:
    v1 = (ROOT / "templates" / "art_bible.md").read_text()
    names = {
        section.token
        for line in v1.splitlines()
        if line.startswith("## ")
        and (section := bible_schema.find_section(line)) is not None
    }
    assert {"vision", "references", "look", "palette", "sound", "bans"} <= names


def test_relevance_uses_known_dimensions() -> None:
    for section in bible_schema.SECTIONS:
        assert set(section.relevant_for) <= set(bible_schema.DIMENSIONS)
        if section.required is not None:
            assert section.moment

