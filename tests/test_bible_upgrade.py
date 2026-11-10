"""Upgrading a first-format bible only adds, and keeps it approvable."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from joinery import bible_schema
from joinery.art_approval import approve
from joinery.bible_rules import parse_rules, rules_text
from joinery.bible_upgrade import upgrade_text

#: The first-format template, frozen here so these tests outlive its replacement.
V1_TEMPLATE = """# Art Bible — {{title}}

> One per game project, at `<project>/art/bible.md`. Written by you, the master (Journeyman can interview you and draft it). Journeyman checks every change in this project against it.
> Keep it short and concrete. "Gritty" is a mood; "palette below, roughness 0.6–0.9" is a rule.
> Fields marked (checked) are enforced automatically; the rest guide Journeyman's choices.

## 1. Vision (2–4 sentences)
What the game feels like, and what it must never feel like.

{{vision}}

## 2. References (your own words, no images pulled from the web)
- Games, films or places whose *look* you mean, and what exactly to take from each:
  - e.g. "<game>: the chunky silhouettes and flat lighting, not its colours"

{{references}}

## 3. Visual style
- Overall look: {{look}}
- Shape language: (round & friendly / angular & hostile / blocky & industrial) …
- Detail level: where detail goes (characters, interactables) vs where it stays simple (background) …
- Lighting mood: time of day, contrast, colour temperature …

## 4. Palette (checked)
| Role | Hex | Notes |
|---|---|---|
| Primary | #…… | |
| Secondary | #…… | |
| Accent (interactables only) | #…… | |
| Shadow tint | #…… | |
| UI base | #…… | |
- Palette, in your words: {{palette}}
- Max colour distance (ΔE) from the palette for surface colours: 12
- Saturation ceiling outside accents: …

## 5. 3D rules (checked)
- Units: metres; player height: 1.8 m; door height: 2.1 m
- Triangle budgets: hero character ≤ …, NPC ≤ …, prop small ≤ …, prop large ≤ …
- Texel density: … px/m (± 20 %)
- Pivots: characters/props bottom-centre; doors at hinge
- Naming: `Part_Material_Variant` (e.g. `Coat_Cloth_Dark`); bones per rigify
- Materials: roughness range …–…, metallic only on real metal, no pure black or white albedo

## 6. 2D / UI rules (checked where possible)
- UI style: flat / skeuomorphic / diegetic …
- Fonts: … (licence: …)
- Line weight: … px at 1080p; corner radius: …
- Pixel art (if used): resolution …, palette size ≤ …, no sub-pixel rotation

## 7. Sound palette
- Overall: {{sound}}
- SFX character: e.g. "dry, close, punchy; no cartoon boings"
- Music: tempo range … BPM, keys/modes …, instruments/patches …, loops (seamless, 60–120 s)
- Loudness (checked): music −16 LUFS, ambience −22 LUFS, SFX peaks ≤ −1 dBTP

## Checked rules
Measured on every changed asset before you review it. Change the numbers here;
an empty or commented line means "not checked".

```toml
[audio]
true_peak_max_dbtp = -1.0   # no audio file may peak above this
lufs_tolerance = 1.0        # allowed distance from a folder's loudness target

[audio.lufs]
"audio/music" = -16.0
"audio/ambience" = -22.0

[models.max_triangles]
# "art/characters" = 15000
# "art/props" = 3000

[palette]
colours = []                # e.g. ["#2b2d42", "#8d99ae", "#edf2f4", "#ef233c"]
max_delta_e = 12.0          # CIEDE2000 distance that still counts as on-palette
max_off_palette = 0.05      # share of image colours allowed beyond that
folders = ["art"]           # where images are checked
```

## 8. Never (hard bans)
- e.g. lens flares, chromatic aberration, neon outside UI, stock "whoosh" transitions …

{{bans}}

## 9. Allowed human imports
- Sources you allow (your own work, bought packs, CC0 libraries) and their licences.
- Default: none.
"""

FULL = {
    "title": "Hollow Keep",
    "vision": "Quiet, heavy stone at dusk. Never cartoonish.",
    "references": "Hob: chunky silhouettes, not its colours.",
    "look": "low-poly with stylised PBR",
    "palette": "warm greys, brass on what you can touch",
    "sound": "dry and close, no reverb tails",
    "bans": "lens flares, chromatic aberration",
}


def v1_bible(answers: dict[str, str]) -> str:
    """Render the first-format template the way the first renderer did."""
    return re.sub(
        r"\{\{(\w+)\}\}",
        lambda m: answers.get(m.group(1), "").strip() or bible_schema.REQUIRED_MARKER,
        V1_TEMPLATE,
    )


def is_subsequence(old: list[str], new: list[str]) -> bool:
    remaining = iter(new)
    return all(any(line == candidate for candidate in remaining) for line in old)


def test_every_first_format_line_is_kept_in_order() -> None:
    old = v1_bible(FULL)
    new = upgrade_text(old)
    assert is_subsequence(old.splitlines(), new.splitlines())
    assert new.endswith("\n") == old.endswith("\n")
    assert bible_schema.version(new) == 2
    assert new.splitlines()[1] == bible_schema.VERSION_MARKER


def test_upgrade_adds_every_missing_section_as_open() -> None:
    new = upgrade_text(v1_bible(FULL))
    found = {
        section.token
        for line in new.splitlines()
        if line.startswith("## ")
        and (section := bible_schema.find_section(line)) is not None
    }
    assert {s.token for s in bible_schema.SECTIONS if s.token} <= found
    assert "| # | Reference | Kind | Take this | Not that | File |" in new
    assert bible_schema.REQUIRED_MARKER not in new
    # 3D rules existed in the first format, so it is kept, not duplicated.
    assert new.count("3D rules") == 1
    assert new.count(bible_schema.OPEN_MARKER) == 11


def test_rule_values_do_not_change() -> None:
    old = v1_bible(FULL)
    new = upgrade_text(old)
    before = parse_rules(rules_text(old) or "")
    after = parse_rules(rules_text(new) or "")
    assert after.schema_version == 2
    assert after.model_dump(exclude={"schema_version"}) == before.model_dump(
        exclude={"schema_version"}
    )
    block = rules_text(new) or ""
    assert "# [materials]" in block and "# [accessibility]" in block
    assert '# [audio.format."audio/sfx"]' in block
    assert "\n[audio]\n" in block  # the existing table is untouched


def test_upgrade_is_idempotent() -> None:
    once = upgrade_text(v1_bible(FULL))
    assert upgrade_text(once) == once


def test_an_approvable_first_format_bible_stays_approvable(tmp_path: Path) -> None:
    (tmp_path / "art").mkdir()
    (tmp_path / "art/bible.md").write_text(v1_bible(FULL))
    approve(tmp_path, "tester")
    (tmp_path / "art/bible.md").write_text(upgrade_text(v1_bible(FULL)))
    approve(tmp_path, "tester")


def test_an_unfinished_bible_stays_unfinished(tmp_path: Path) -> None:
    (tmp_path / "art").mkdir()
    (tmp_path / "art/bible.md").write_text(upgrade_text(v1_bible({"title": "Sparse"})))
    with pytest.raises(ValueError, match="Finish the art direction"):
        approve(tmp_path, "tester")


def test_a_bible_without_rules_gets_a_block_that_checks_nothing_new() -> None:
    old = "# Art Bible - Plain\n\n## 1. Vision\nStone.\n\n## Never\nFlares.\n"
    new = upgrade_text(old)
    assert is_subsequence(old.splitlines(), new.splitlines())
    rules = parse_rules(rules_text(new) or "")
    assert rules.schema_version == 2
    assert rules.models.max_triangles == {} and rules.audio.true_peak_max_dbtp is None
    assert new.index("## Checked rules") < new.index("## Never")


def test_an_invalid_rules_block_is_left_for_the_user() -> None:
    old = "# Bible\n\n## Checked rules\n\n```toml\n[audio\n```\n"
    new = upgrade_text(old)
    assert "```toml\n[audio\n```" in new
    assert bible_schema.version(new) == 2
