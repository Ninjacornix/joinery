"""Writing notes in the art bible and the game brief, and the filler-word check."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from joinery import bible, bible_lint, bible_schema, bible_writing, brief
from joinery.bible_upgrade import upgrade_text
from joinery.bible_writing import (
    AGENT_GUIDE,
    BIBLE_GUIDE,
    BRIEF_GUIDE,
    FILLER_WORDS,
    filler,
    suggestion,
)
from joinery.catalog import ROOT

BIBLE_TEMPLATE = ROOT / "templates" / "art_bible.md"
BRIEF_TEMPLATE = ROOT / "templates" / "game_brief.md"
GUIDES = (BIBLE_GUIDE, BRIEF_GUIDE, AGENT_GUIDE)


def _before_first_section(text: str) -> str:
    return text.split("\n## ", 1)[0]


# ---------------------------------------------------------------------------
# The notes themselves


@pytest.mark.parametrize("guide", GUIDES)
def test_every_guide_names_each_filler_word(guide: str) -> None:
    for word in FILLER_WORDS:
        assert word in guide


@pytest.mark.parametrize("guide", GUIDES)
def test_guides_follow_their_own_rules(guide: str) -> None:
    assert "—" not in guide and " -- " not in guide
    assert "!" not in guide.replace("<!--", "")
    assert "{{" not in guide
    # Only the list of banned words may contain them: the notes themselves do not.
    for line in guide.splitlines():
        if "filler" in line.lower():
            continue
        assert filler("## Any\n" + line) == [], line


def test_bible_guide_covers_the_art_direction_rules() -> None:
    text = BIBLE_GUIDE.lower()
    for needle in (
        "concrete nouns over adjectives",
        "hex",
        "metres",
        "triangles",
        "lufs",
        "name real references",
        "what to leave",
        "what to avoid",
        "open rather than guess",
        "em-dash",
        "not just x but y",
        "sentence case",
        "one rule per line",
    ):
        assert needle in text, needle
    assert BIBLE_GUIDE.startswith("<!--") and BIBLE_GUIDE.endswith("-->")
    assert BIBLE_GUIDE.count("-->") == 1


def test_agent_guide_never_invents_taste() -> None:
    assert "Never invent the user's taste" in AGENT_GUIDE
    assert "art bible" in AGENT_GUIDE and "game brief" in AGENT_GUIDE


# ---------------------------------------------------------------------------
# Templates and what they render


def test_bible_template_carries_the_notes_under_the_marker() -> None:
    lines = BIBLE_TEMPLATE.read_text().splitlines()
    assert lines[1] == bible_schema.VERSION_MARKER
    assert "\n".join(lines[2 : 2 + len(BIBLE_GUIDE.splitlines())]) == BIBLE_GUIDE


def test_brief_template_carries_the_note_above_the_sections() -> None:
    text = BRIEF_TEMPLATE.read_text()
    assert BRIEF_GUIDE in _before_first_section(text)


def test_rendered_bible_keeps_the_notes_out_of_every_section() -> None:
    text = bible.render({"vision": "Wet stone at dusk."}, preset="lowpoly-3d")
    assert BIBLE_GUIDE in _before_first_section(text)
    assert bible_schema.version(text) == 2
    _title, sections = bible_lint._split_sections(text)
    assert all("How to write" not in body for _heading, body in sections)
    report = bible_lint.lint_text(text)
    assert report.problems == [] and report.filler == []
    # The notes add no section and no gap of their own.
    assert {g.section for g in report.gaps} <= {
        *(s.token for s in bible_schema.SECTIONS),
        bible_schema.TITLE_TOKEN,
    }


@pytest.mark.parametrize(
    "preset",
    [
        None,
        *sorted(p.stem for p in (ROOT / "templates" / "bible_presets").glob("*.toml")),
    ],
)
def test_no_preset_writes_filler(preset: str | None) -> None:
    text = bible.render({}, preset=preset)
    assert filler(text) == []


def test_drafted_brief_keeps_its_parse_and_has_no_filler() -> None:
    text = brief.draft({"pitch": "A keeper fixes the harbour."}, None, [])
    assert BRIEF_GUIDE in _before_first_section(text)
    parsed = brief.parse(text)
    assert parsed.problems == []
    assert all(not heading.startswith("How to") for heading, _ in brief.sections(text))
    assert "How to write" not in "".join(body for _, body in brief.sections(text))
    assert brief._wording(text) == []


# ---------------------------------------------------------------------------
# Finding filler


def test_filler_finds_word_forms_by_section() -> None:
    text = (
        "# Title\nA stunning intro above the sections is not checked.\n"
        "## 1. Vision\nStunningly vibrant, immersive.\nDelving into a tapestry.\n"
        "## 7. Palette (checked)\nVibrant greens. Seamlessly unleashed. "
        "A cutting-edge, game-changing look; it elevates the scene.\n"
    )
    assert filler(text) == [
        ("stunning", "Vision"),
        ("vibrant", "Vision"),
        ("immersive", "Vision"),
        ("delve", "Vision"),
        ("tapestry", "Vision"),
        ("vibrant", "Palette (checked)"),
        ("seamless", "Palette (checked)"),
        ("unleash", "Palette (checked)"),
        ("cutting-edge", "Palette (checked)"),
        ("game-changer", "Palette (checked)"),
        ("elevate", "Palette (checked)"),
    ]


def test_filler_skips_notes_quotes_code_and_named_sections() -> None:
    text = (
        "## Vision\n"
        "<!-- stunning -->\n"
        "> e.g. not a vibrant mess\n"
        'Never write "immersive" or `seamless` or “stunning”.\n'
        "```toml\nnote = 'vibrant'\n```\n"
        "An elevated walkway; the blade's cutting edge.\n"
        "## Writing tone\nBanned words: epic, immersive.\n"
        "## 19. Never\nVibrant neon.\n"
    )
    assert filler(text, ("writing tone", "never")) == []
    assert filler(text) == [
        ("immersive", "Writing tone"),
        ("vibrant", "Never"),
    ]


def test_filler_ignores_words_that_only_contain_one() -> None:
    assert filler("## Look\nUnstunning? Vibrantine. Pre-immersive.\n") == []


def test_suggestion_lists_where_and_caps_the_list() -> None:
    assert suggestion("bible", [], "x") is None
    hits = [(w, "Vision") for w in FILLER_WORDS[:7]]
    line = suggestion("bible", hits, "a hex")
    assert line is not None
    assert line.startswith("Filler words in the bible: stunning (Vision), ")
    assert "and 2 more" in line and line.endswith("instead: a hex.")
    single = suggestion("brief", [("vibrant", "Palette (checked)")], "a number")
    assert single == (
        "Filler words in the brief: vibrant (Palette). "
        "Say what each means here instead: a number."
    )


# ---------------------------------------------------------------------------
# Lint and consistency: a suggestion, never a problem


def test_bible_lint_suggests_but_never_blocks(tmp_path: Path) -> None:
    clean = bible.render({"vision": "Wet stone at dusk.", "palette": "slate, moss"})
    sloppy = bible.render(
        {
            "vision": "A stunning, immersive world.",
            "palette": "Vibrant greens.",
            "bans": "immersive marketing copy",
        }
    )
    before, after = bible_lint.lint_text(clean), bible_lint.lint_text(sloppy)
    assert after.problems == before.problems
    assert [g.section for g in after.gaps] == [
        g.section for g in before.gaps if g.section not in {"bans"}
    ]
    assert after.filler == [
        ("stunning", "Vision"),
        ("immersive", "Vision"),
        ("vibrant", "Palette (checked)"),
    ]
    tips = bible_lint.suggestions_for(after, None)
    assert tips[-1] == (
        "Filler words in the bible: stunning (Vision), immersive (Vision), "
        "vibrant (Palette). Say what each means here instead: a hex, a size, "
        "a shape or a named reference."
    )
    assert not any("Filler" in tip for tip in bible_lint.suggestions_for(before, None))
    (tmp_path / "art").mkdir()
    (tmp_path / "art" / "bible.md").write_text(sloppy)
    summary = bible_lint.summary(tmp_path)
    assert summary["suggestions"][-1].startswith("Filler words in the bible: ")
    assert not any("Filler" in p for p in summary["problems"])


def test_brief_consistency_and_summary_suggest_plainer_words(tmp_path: Path) -> None:
    text = brief.draft({"pitch": "An immersive, seamless heist."}, None, [])
    (tmp_path / "design").mkdir()
    (tmp_path / "design" / "brief.md").write_text(text)
    expected = (
        "Filler words in the brief: immersive (Pitch), seamless (Pitch). Say what "
        "each means here instead: what the player does, with a number where one "
        "exists."
    )
    assert brief.consistency(tmp_path, None) == [expected]
    summary = brief.summary(tmp_path, None)
    assert summary["suggestions"][-1] == expected
    assert summary["problems"] == []


def test_brief_facts_block_is_not_checked_for_filler(tmp_path: Path) -> None:
    text = brief.draft({"pitch": "Fix the harbour."}, None, [])
    marked = text.replace("```toml\n", "```toml\n# seamless\n", 1)
    assert marked != text and "seamless" in brief.sections(marked)[-1][1]
    assert brief._wording(marked) == []


def test_filler_suggestion_is_not_a_sidebar_offer(tmp_path: Path) -> None:
    from bridge import stdio

    text = brief.draft({"pitch": "A stunning heist."}, None, [])
    (tmp_path / "design").mkdir()
    (tmp_path / "design" / "brief.md").write_text(text)
    assert brief.consistency(tmp_path, None)
    assert stdio.game_suggestions(tmp_path)["items"] == []


# ---------------------------------------------------------------------------
# Upgrade and agent instructions


def test_upgrade_adds_the_notes_once() -> None:
    old = "# Art Bible - Plain\n\n## 1. Vision\nStone.\n\n## Never\nFlares.\n"
    new = upgrade_text(old)
    lines = new.splitlines()
    assert lines[1] == bible_schema.VERSION_MARKER
    assert new.count("How to write in this file") == 1
    assert "\n".join(lines[2 : 2 + len(BIBLE_GUIDE.splitlines())]) == BIBLE_GUIDE
    assert upgrade_text(new) == new
    # A first-format bible that already has the notes does not get them twice.
    with_notes = old.replace("\n\n", "\n" + BIBLE_GUIDE + "\n\n", 1)
    assert upgrade_text(with_notes).count("How to write in this file") == 1


def test_every_agent_is_told_how_to_write_these_documents() -> None:
    from bridge.instructions import GAME_GUIDE
    from bridge.protocol import Prompt
    from bridge.provider_process import INSTRUCTIONS, prompt_text

    assert AGENT_GUIDE in GAME_GUIDE
    text = prompt_text(Prompt(text="hello", project_dir="/tmp/game"))
    for prompt in (INSTRUCTIONS, text):
        assert AGENT_GUIDE in prompt
        assert re.search(r"No filler words \(stunning, ", prompt)


def test_module_word_list_matches_its_patterns() -> None:
    for word in FILLER_WORDS:
        assert filler(f"## Look\n{word}\n") == [(word, "Look")], word
    assert bible_writing.FILLER_WORDS == FILLER_WORDS
