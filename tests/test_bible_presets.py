"""Bible presets fill technical sections only, and the rules block always parses."""

from __future__ import annotations

from pathlib import Path

import pytest

from joinery import bible_presets, bible_schema
from joinery.art_approval import approve
from joinery.bible_rules import BibleRules, load_rules, parse_rules
from joinery.catalog import ROOT

TEMPLATE = ROOT / "templates" / "art_bible.md"
IDS = ["none", "cards-ui", "flat-2d", "lowpoly-3d", "pixel-2d", "stylised-pbr-3d"]
ANSWERS = {
    "title": "Hollow Keep",
    "vision": "Quiet, heavy stone at dusk. Never cartoonish.",
    "references": "| R1 | Hob (game) | game | chunky silhouettes | its greens | |",
    "look": "low-poly with stylised PBR",
    "palette": "warm greys, brass on what you can touch",
    "sound": "dry and close, no reverb tails",
    "bans": "lens flares, chromatic aberration",
}


def test_every_contract_preset_exists() -> None:
    assert [p["id"] for p in bible_presets.list_presets()] == IDS


@pytest.mark.parametrize("preset", IDS)
def test_every_preset_rules_block_parses(preset: str) -> None:
    text = bible_presets.rules_block(preset)
    rules = parse_rules(text)
    assert rules == BibleRules.model_validate(bible_presets.rules_dict(preset))
    assert rules.schema_version == 2
    assert rules.exclude == ["art/reference"]


@pytest.mark.parametrize("preset", IDS)
def test_presets_never_touch_taste(preset: str) -> None:
    chosen = bible_presets.get(preset)
    for token in chosen.sections:
        assert not bible_schema.is_taste(token), (preset, token)
        assert bible_schema.section_for(token) is not None


def test_none_preset_is_the_default_block() -> None:
    assert bible_presets.rules_block(None) == bible_presets.rules_block("none")
    assert bible_presets.apply_preset({}, None) == {}


def test_written_rules_round_trip_every_key() -> None:
    # Every key the model knows is written, so nothing set is silently dropped.
    rules = bible_presets.merge(
        bible_presets.DEFAULT_RULES,
        {
            "sprites": {"max_colours": {"art/sprites": 16}},
            "palette": {"roles": {"ui_text": "#edf2f4"}, "colours": ["#2b2d42"]},
            "accessibility": {"ui_pairs": [["ui_text", "#2b2d42"]]},
        },
    )
    parsed = parse_rules(bible_presets.write_rules(rules))
    assert parsed == BibleRules.model_validate(rules)
    assert parsed.accessibility.ui_pairs == [("ui_text", "#2b2d42")]


def test_unknown_rule_keys_fail_instead_of_vanishing() -> None:
    with pytest.raises(ValueError):
        bible_presets.write_rules({"models": {"max_triangle": {"art": 1}}})


def test_preset_rules_merge_over_the_default() -> None:
    rules = parse_rules(bible_presets.rules_block("pixel-2d"))
    assert rules.textures.power_of_two is False
    assert rules.sprites.max_colours == {"art/sprites": 32}
    assert rules.audio.lufs == {"audio/music": -16.0, "audio/ambience": -22.0}
    lowpoly = parse_rules(bible_presets.rules_block("lowpoly-3d"))
    assert lowpoly.models.max_triangles["art/characters"] == 3000
    assert lowpoly.models.max_triangles["art/props"] == 800


def test_answers_win_over_preset_suggestions() -> None:
    values = bible_presets.apply_preset(
        {"camera": "Fixed top-down.", "vision": ""}, "lowpoly-3d"
    )
    assert values["camera"] == "Fixed top-down."
    assert values["models"].startswith(
        "_Suggested by the Low-poly 3D starting point; change freely._"
    )
    assert "vision" not in values


def test_presets_that_fill_taste_are_refused(tmp_path: Path) -> None:
    bad = tmp_path / "loud.toml"
    bad.write_text('id = "loud"\nname = "Loud"\n[sections]\npalette = "neon"\n')
    with pytest.raises(ValueError, match="taste"):
        bible_presets._load(bad)
    unknown = tmp_path / "odd.toml"
    unknown.write_text('id = "odd"\n[sections]\nweather = "rain"\n')
    with pytest.raises(ValueError, match="not a bible section"):
        bible_presets._load(unknown)
    rules = tmp_path / "typo.toml"
    rules.write_text('id = "typo"\n[rules.models]\napplied_scal = true\n')
    with pytest.raises(ValueError, match="invalid"):
        bible_presets._load(rules)
    with pytest.raises(ValueError, match="Unknown bible preset"):
        bible_presets.get("photoreal")


@pytest.mark.parametrize("preset", IDS)
def test_rendered_v2_template_leaves_no_token_and_is_approvable(
    tmp_path: Path, preset: str
) -> None:
    text = bible_presets.fill(TEMPLATE.read_text(), ANSWERS, preset)
    assert "{{" not in text
    assert bible_schema.REQUIRED_MARKER not in text
    (tmp_path / "art").mkdir()
    (tmp_path / "art/bible.md").write_text(text)
    rules = load_rules(tmp_path)
    assert rules == BibleRules.model_validate(bible_presets.rules_dict(preset))
    approve(tmp_path, "tester")


def test_unanswered_required_sections_block_approval_and_open_ones_do_not(
    tmp_path: Path,
) -> None:
    text = bible_presets.fill(TEMPLATE.read_text(), {"title": "Sparse"}, None)
    # One required marker per interview question, as in the first format.
    assert text.count("Not set yet") == 6
    assert text.count(bible_schema.OPEN_MARKER) == 12
    (tmp_path / "art").mkdir()
    (tmp_path / "art/bible.md").write_text(text)
    with pytest.raises(ValueError, match="Finish the art direction"):
        approve(tmp_path, "tester")


def test_pixel_preset_names_krita_for_pixel_art() -> None:
    text = (ROOT / "templates" / "bible_presets" / "pixel-2d.toml").read_text()
    assert "Krita" in text
    assert "aseprite" not in text.lower()
