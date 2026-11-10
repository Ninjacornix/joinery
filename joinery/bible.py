"""Write a project's art bible from the interview answers.

Every game project has its own `art/bible.md`: the palette, shapes, budgets and
sound palette that each change in that project is checked against. It is written
when the project is created, because the alternative is making assets first and
deciding what they should have looked like afterwards.

The answers come from the questionnaire in the Project Manager. The rendering
lives here rather than in that C++ dialog, so `templates/art_bible.md` stays the
single source of the bible's shape and can be edited without rebuilding the
engine. The template carries `{{token}}` placeholders; a human filling it in by
hand sees them as placeholders, and anything left unanswered becomes a neutral
marker so a literal token never survives into a project.

Nothing here invents taste. An unanswered question is recorded as unanswered:
required sections with the marker approval refuses, open sections with the
marker Journeyman reads as "suggest this when it matters" (`bible_schema`). A
blueprint's bible preset may fill technical sections (camera numbers, budgets,
UI sizes), each marked as a suggestion, and never vision, references, look,
palette, sound or bans (`bible_presets`). The checked-rules block is the
default merged with the preset's rules.
"""

from __future__ import annotations

import json
from pathlib import Path

from joinery.bible_presets import fill
from joinery.bible_schema import TITLE_TOKEN
from joinery.brief import project_name
from joinery.catalog import ROOT

TEMPLATE = ROOT / "templates" / "art_bible.md"
BIBLE_PATH = Path("art") / "bible.md"
ANSWERS_FILE = ".joinery-bible-answers.json"
#: Key in the answers file naming the bible preset, consumed with the answers.
PRESET_KEY = "_preset"

#: The interview as pages. Setup asks six short, optional questions on one
#: page, References included; grouping lives here, not in the dialog, so it
#: can be re-cut without rebuilding the engine.
GROUPS: tuple[tuple[str, str], ...] = (("look_and_sound", "Look and sound"),)

#: Every answer the bible template takes, in order. `group` is the page that
#: asks it; an empty group means the bible keeps the field but setup does not
#: ask (Journeyman fills it in later). `multiline` and `placeholder` are hints
#: for the UI. `bans` keeps its key because the bible schema and rules read
#: that section by that name; the UI calls it "Never".
QUESTIONS: tuple[dict[str, object], ...] = (
    {
        "key": "vision",
        "group": "look_and_sound",
        "label": "What should it feel like?",
        "hint": "What the game feels like.",
        "placeholder": "e.g. cosy, hand-made, a little spooky",
        "multiline": True,
    },
    {
        "key": "references",
        "group": "look_and_sound",
        "label": "References",
        "hint": "Games, films or places whose look you mean, and what to take from each.",
        "placeholder": "e.g. art/reference/dusk_board.kra: the warm low light, not its colours",
        "multiline": True,
    },
    {
        "key": "look",
        "group": "look_and_sound",
        "label": "Look",
        "hint": "Low-poly, stylised PBR, toon, pixel art, something else.",
        "placeholder": "e.g. low-poly, flat colours",
        "multiline": False,
    },
    {
        "key": "palette",
        "group": "look_and_sound",
        "label": "Palette",
        "hint": "In your words, or hex values if you have them.",
        "placeholder": "e.g. warm earth tones, one bright accent",
        "multiline": False,
    },
    {
        "key": "sound",
        "group": "look_and_sound",
        "label": "Sound",
        "hint": "Acoustic, synthetic, lo-fi; and how effects should feel.",
        "placeholder": "e.g. soft synths, wooden foley",
        "multiline": False,
    },
    {
        "key": "bans",
        "group": "look_and_sound",
        "label": "Never",
        "hint": "Hard bans. Anything here is refused rather than argued about.",
        "placeholder": "e.g. gore, neon, photo-real faces",
        "multiline": False,
    },
)


def questions() -> list[dict[str, object]]:
    return [dict(q) for q in QUESTIONS]


def grouped() -> list[dict[str, object]]:
    """The interview as pages: one group per page, in order."""
    pages: list[dict[str, object]] = []
    for key, label in GROUPS:
        pages.append(
            {
                "key": key,
                "label": label,
                "questions": [dict(q) for q in QUESTIONS if q.get("group") == key],
            }
        )
    return pages


def render(
    answers: dict[str, str], template: Path = TEMPLATE, preset: str | None = None
) -> str:
    """Fill the template from the answers and preset, leaving no token behind."""
    return fill(template.read_text(), answers, preset)


def write_bible(
    project: Path, answers: dict[str, str], preset: str | None = None
) -> Path:
    """Write `<project>/art/bible.md`. Refuses to overwrite an existing bible."""
    destination = project / BIBLE_PATH
    if destination.exists():
        raise FileExistsError(
            f"{destination} already exists; changing a bible is a reviewed edit, "
            "not something project setup should overwrite"
        )
    filled = dict(answers)
    if not str(filled.get(TITLE_TOKEN) or "").strip():
        # The name the user typed for the game; setup asks nothing else for it.
        name = project_name(project)
        if name:
            filled[TITLE_TOKEN] = name
    text = render(filled, preset=preset)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text)
    return destination


def write_from_answers_file(project: Path, preset: str | None = None) -> Path | None:
    """Read the answers a front end dropped in the project, then remove them.

    The Project Manager cannot pass multi-line text as command arguments
    comfortably, so it writes a small JSON file beside the project and this
    consumes it. The file may name a preset under `_preset`: that is the
    user's choice in the wizard, so it wins; `preset` (usually the
    blueprint's) is used when the file names none. Returns None when there is
    nothing to do.
    """
    source = project / ANSWERS_FILE
    if not source.is_file():
        return None
    try:
        raw = json.loads(source.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"{source}: not valid JSON: {exc}") from exc
    finally:
        source.unlink(missing_ok=True)
    if not isinstance(raw, dict):
        # A ValueError like the JSON error above: both are a bad answers file.
        raise ValueError(f"{source}: answers must be a JSON object")  # noqa: TRY004
    # Only text and numbers are answers: `null` must stay unanswered rather than
    # become the word "None" and pass for a set section.
    answers = {
        str(k): str(v)
        for k, v in raw.items()
        if isinstance(v, str | int | float) and not isinstance(v, bool)
    }
    chosen = answers.pop(PRESET_KEY, "") or preset or None
    return write_bible(project, answers, chosen)
