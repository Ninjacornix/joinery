"""The shape of an art bible: its sections, their tokens and when each matters.

This is the single source for the bible's headings, `{{token}}` placeholders,
which sections block approval while unset, and when an open section becomes
worth asking about. The template, the renderer, the presets, the upgrade and
the lint all read it from here, so a section cannot exist in one and be missing
from another.

A section is either *required* (approval is refused while it is unset; this is
exactly the first format's set, so no project gets stricter on upgrade) or
*open* (it can stay unset, and Journeyman suggests it when the work reaches it).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: First line after the title of a second-format bible. Absent means format 1.
VERSION = 2
VERSION_MARKER = f"<!-- joinery-bible: {VERSION} -->"
_VERSION_LINE = re.compile(r"<!--\s*joinery-bible:\s*(\d+)\s*-->")

#: Unset markers. Two, so approval and lint can tell them apart. The required
#: one is unchanged from the first format, which approval already refuses.
REQUIRED_MARKER = "_Not set yet. Journeyman will ask before it matters._"
OPEN_MARKER = "_Open. Journeyman will suggest this when it becomes relevant._"

#: Where an open section starts to matter. `any` is relevant to every game.
DIMENSIONS = ("3d", "2d", "ui", "audio", "any")

#: The title token sits in the first heading and is not a section of its own.
TITLE_TOKEN = "title"


@dataclass(frozen=True)
class Section:
    number: int
    heading: str
    #: Placeholders in the template. The first is the section's main token.
    tokens: tuple[str, ...]
    #: True blocks approval while unset, False may stay open, None is not a
    #: user-filled section (the checked rules block, the imports list).
    required: bool | None
    #: Taste is the user's alone: presets never fill these.
    taste: bool
    #: What a good answer contains, for the template and for Journeyman.
    hint: str
    #: Kinds of game in which the section matters (subset of DIMENSIONS).
    relevant_for: tuple[str, ...]
    #: When Journeyman should raise it, as the start of a sentence.
    moment: str
    #: Headings the first format used for the same content.
    aliases: tuple[str, ...] = ()

    @property
    def token(self) -> str:
        return self.tokens[0] if self.tokens else ""


SECTIONS: tuple[Section, ...] = (
    Section(
        1,
        "Vision",
        ("vision",),
        True,
        True,
        "2-4 sentences: what it feels like, and what it must never feel like.",
        ("any",),
        "Before approving the bible",
    ),
    Section(
        2,
        "Pillars",
        ("pillars",),
        False,
        True,
        "Three mood words, each with one line saying what it means in this game.",
        ("any",),
        "Before the first asset",
    ),
    Section(
        3,
        "References",
        ("references",),
        True,
        True,
        "A board of your own material: what to take from each, and what not to.",
        ("any",),
        "Before approving the bible",
    ),
    Section(
        4,
        "Camera and framing",
        ("camera",),
        False,
        False,
        "Projection, field of view or size, distance, pitch, framing, motion, safe area.",
        ("3d", "2d"),
        "Before the first level or camera work",
    ),
    Section(
        5,
        "Shape language",
        ("shape_language",),
        False,
        True,
        "A primary shape per role, examples from this game, a silhouette test.",
        ("3d", "2d"),
        "Before the first character model",
    ),
    Section(
        6,
        "Visual style",
        ("look",),
        True,
        True,
        "The overall look, and where detail goes versus where it stays simple.",
        ("any",),
        "Before approving the bible",
    ),
    Section(
        7,
        "Palette",
        ("palette",),
        True,
        True,
        "Roles with hex values, value structure, saturation ceiling outside accents.",
        ("any",),
        "Before approving the bible",
    ),
    Section(
        8,
        "Lighting recipes",
        ("lighting",),
        False,
        True,
        "Named recipes: key colour and angle, key to fill ratio, ambient, fog, sky.",
        ("3d",),
        "Before lighting the first level",
    ),
    Section(
        9,
        "Materials and texel density",
        ("materials",),
        False,
        False,
        "Roughness range, metallic only on metal, albedo bounds, texel density.",
        ("3d",),
        "Before the first material or texture",
    ),
    Section(
        10,
        "3D rules",
        ("models",),
        False,
        False,
        "Units, reference heights, triangle and bone budgets, pivots, naming.",
        ("3d",),
        "Before the first 3D model",
    ),
    Section(
        11,
        "Animation feel",
        ("animation",),
        False,
        False,
        "Snappy or floaty, frame rate, holds, anticipation and overshoot, a timing table.",
        ("3d", "2d"),
        "Before the first character model",
    ),
    Section(
        12,
        "VFX",
        ("vfx",),
        False,
        False,
        "Technique, shapes, colours, lifetimes, particle budgets, flash limits.",
        ("3d", "2d"),
        "Before the first effect",
    ),
    Section(
        13,
        "2D and sprites",
        ("sprites",),
        False,
        False,
        "Base resolution, pixel scale, colours per sprite, outlines, no sub-pixel rotation.",
        ("2d",),
        "Before the first sprite",
    ),
    Section(
        14,
        "UI look and motion",
        ("ui",),
        False,
        False,
        "Style, fonts and licence, sizes at 1080p, corner radius, focus, motion timing.",
        ("ui", "3d", "2d"),
        "Before the first menu or HUD",
    ),
    Section(
        15,
        "Writing tone",
        ("writing",),
        False,
        True,
        "Voice, person, reading level, UI sentence length, humour, banned words.",
        ("ui", "3d", "2d"),
        "Before the first player-facing text",
    ),
    Section(
        16,
        "Audio",
        ("sound",),
        True,
        True,
        "Overall sound, SFX character, music tempo and keys, patches, loudness.",
        ("any",),
        "Before approving the bible",
        ("Sound palette",),
    ),
    Section(
        17,
        "Accessibility",
        ("accessibility",),
        False,
        False,
        "Colour-blind safe roles, text size, contrast, subtitles, remapping, motion.",
        ("any",),
        "Before the first menu or HUD",
    ),
    Section(
        18,
        "Checked rules",
        ("checked_rules",),
        None,
        False,
        "The TOML block below is measured on every changed asset.",
        ("any",),
        "",
    ),
    Section(
        19,
        "Never",
        ("bans",),
        True,
        True,
        "Hard bans. Anything here is refused rather than argued about.",
        ("any",),
        "Before approving the bible",
    ),
    Section(
        20,
        "Allowed human imports",
        (),
        None,
        False,
        "Sources and licences you allow. Default none; reference boards always allowed.",
        ("any",),
        "",
    ),
)

_BY_TOKEN = {token: section for section in SECTIONS for token in section.tokens}

#: Sections whose numbers the checked rules measure; their headings say so.
CHECKED_TOKENS = frozenset(
    {"palette", "materials", "models", "sprites", "accessibility"}
)

#: Kinds a reference board row may name.
REFERENCE_KINDS = (
    "game",
    "film",
    "place",
    "photo",
    "sketch",
    "board",
    "painting",
    "music",
)

#: Where the user's own reference material lives, outside every check.
REFERENCE_DIR = "art/reference"


def heading_text(section: Section) -> str:
    """The heading as a second-format bible writes it, without its number."""
    suffix = " (checked)" if section.token in CHECKED_TOKENS else ""
    return section.heading + suffix


def tokens() -> list[str]:
    """Every placeholder a second-format template carries, title first."""
    return [TITLE_TOKEN, *(t for s in SECTIONS for t in s.tokens)]


def section_for(token: str) -> Section | None:
    """The section a token belongs to; None for the title or an unknown token."""
    return _BY_TOKEN.get(token)


def is_required(token: str) -> bool:
    """Whether an unset token blocks approval. The title does."""
    if token == TITLE_TOKEN:
        return True
    section = section_for(token)
    return bool(section and section.required)


def is_taste(token: str) -> bool:
    """Taste tokens are the user's alone: presets never fill them."""
    if token == TITLE_TOKEN:
        return True
    section = section_for(token)
    return section is None or section.taste


def unset_marker(token: str) -> str:
    """What an unanswered token renders as."""
    return REQUIRED_MARKER if is_required(token) else OPEN_MARKER


def version(text: str) -> int:
    """The bible's format: the version marker's number, or 1 without one."""
    for line in text.splitlines()[:6]:
        match = _VERSION_LINE.search(line)
        if match:
            return int(match.group(1))
    return 1


def normalise_heading(heading: str) -> str:
    """`## 7. Palette (checked)` -> `palette`, for matching headings loosely."""
    text = heading.lstrip("#").strip()
    text = re.sub(r"^\d+[a-z]?\.\s*", "", text)
    text = re.sub(r"\(.*?\)", "", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def find_section(heading: str) -> Section | None:
    """The section a markdown heading names, by heading or a first-format alias."""
    name = normalise_heading(heading)
    for section in SECTIONS:
        names = [section.heading, *section.aliases]
        if any(name == normalise_heading(n) for n in names):
            return section
    return None
