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

Nothing here invents taste. An unanswered question is recorded as unanswered,
and the technical numbers the template proposes stay as the template's
suggestions, for the user to adjust.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from joinery.catalog import ROOT

TEMPLATE = ROOT / "templates" / "art_bible.md"
BIBLE_PATH = Path("art") / "bible.md"
ANSWERS_FILE = ".joinery-bible-answers.json"

#: The questions the interview asks, in order. `multiline` is a hint for the UI.
QUESTIONS: tuple[dict[str, object], ...] = (
    {
        "key": "vision",
        "label": "Vision",
        "hint": "What the game feels like, and what it must never feel like.",
        "multiline": True,
    },
    {
        "key": "references",
        "label": "References",
        "hint": "Games, films or places whose look you mean, and what to take from each.",
        "multiline": True,
    },
    {
        "key": "look",
        "label": "Overall look",
        "hint": "low-poly, stylised PBR, toon, pixel art, something else.",
        "multiline": False,
    },
    {
        "key": "palette",
        "label": "Palette",
        "hint": "In your words, or hex values if you have them.",
        "multiline": False,
    },
    {
        "key": "sound",
        "label": "Sound",
        "hint": "acoustic-ish, synthetic, lo-fi; and how SFX should feel.",
        "multiline": False,
    },
    {
        "key": "bans",
        "label": "Never",
        "hint": "Hard bans. Anything here is refused rather than argued about.",
        "multiline": True,
    },
)

_TOKEN = re.compile(r"\{\{(\w+)\}\}")
_UNANSWERED = "_Not set yet. Journeyman will ask before it matters._"


def questions() -> list[dict[str, object]]:
    return [dict(q) for q in QUESTIONS]


def render(answers: dict[str, str], template: Path = TEMPLATE) -> str:
    """Fill the template from the answers, leaving no token behind."""
    text = template.read_text()

    def substitute(match: re.Match[str]) -> str:
        key = match.group(1)
        value = (answers.get(key) or "").strip()
        return value if value else _UNANSWERED

    return _TOKEN.sub(substitute, text)


def write_bible(project: Path, answers: dict[str, str]) -> Path:
    """Write `<project>/art/bible.md`. Refuses to overwrite an existing bible."""
    destination = project / BIBLE_PATH
    if destination.exists():
        raise FileExistsError(
            f"{destination} already exists; changing a bible is a reviewed edit, "
            "not something project setup should overwrite"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(render(answers))
    return destination


def write_from_answers_file(project: Path) -> Path | None:
    """Read the answers a front end dropped in the project, then remove them.

    The Project Manager cannot pass multi-line text as command arguments
    comfortably, so it writes a small JSON file beside the project and this
    consumes it. Returns None when there is nothing to do.
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
    answers = {str(k): str(v) for k, v in raw.items() if isinstance(raw, dict)}
    return write_bible(project, answers)
