"""`joinery share page`: a short itch.io page drafted from what the project already says.

The draft takes only what the project states: the game's name, the brief's
pitch, core loop and player verbs (`design/brief.md`), the controls from the
input map, a few facts (session length, saving, gamepad, online, the builds
made), and credits from `joinery.lock` (the tools that made the assets, and the
licences of anything the user imported). Whatever the project doesn't say stays
a marked gap for the user to write; nothing is invented. Filler words found in
the text are reported with the same check the brief and the art bible use.

The page is saved to `share/page.md` for the user to edit. It is never
overwritten unless the user asks for a new draft.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from joinery import bible_writing, tools
from joinery.brief import UNSET, input_actions, load_brief, sections
from joinery.lockfile import read_assets
from joinery.share_build import SHARE, TARGETS, controls, game_name, last_report

PAGE = SHARE / "page.md"
GAP = "_Write this part._"
_TEMPLATE_LINES = {
    "Who the player gets to be, and what they get to feel.",
}
_FROM_STARTING_POINT = re.compile(r"^_From the .* starting point; change freely\._$")


def _clean(body: str) -> str:
    """A brief section's own words: no notes, gaps or starting-point remarks."""
    kept = []
    for line in body.strip().splitlines():
        text = line.strip()
        if (
            text.startswith(">")
            or UNSET in text
            or text in _TEMPLATE_LINES
            or _FROM_STARTING_POINT.match(text)
        ):
            continue
        kept.append(line.rstrip())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()


def _brief(project: Path) -> dict[str, str]:
    try:
        text = load_brief(project).text
    except FileNotFoundError:
        return {}
    return {heading: _clean(body) for heading, body in sections(text)}


def _verbs(table: str) -> list[str]:
    """`Verb | Input | Feedback` rows as 'Verb: feedback' bullets."""
    found = []
    for line in table.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3 or cells[0] in {"Verb", ""} or set(cells[0]) <= {"-"}:
            continue
        found.append(f"- {cells[0]}: {cells[2][:1].lower() + cells[2][1:]}")
    return found


def _features(project: Path, brief: dict[str, str]) -> list[str]:
    lines = _verbs(brief.get("Player verbs", ""))
    try:
        facts = load_brief(project).facts
    except FileNotFoundError:
        facts = None
    if facts is not None:
        if facts.session_minutes:
            lines.append(f"- A session lasts about {facts.session_minutes} minutes.")
        if facts.save != "none":
            lines.append("- Saves your progress.")
        if "gamepad" in facts.input:
            lines.append("- Plays with a gamepad.")
        if facts.online != "none" and facts.max_players > 1:
            lines.append(f"- Up to {facts.max_players} players online.")
        if len(facts.languages) > 1:
            lines.append(f"- In {len(facts.languages)} languages.")
    report = last_report(project) or {}
    built = [t for t, r in report.get("targets", {}).items() if r.get("ok")]
    if "web" in built:
        lines.append("- Plays in the browser.")
    desktops = [TARGETS[t].label for t in ("windows", "macos", "linux") if t in built]
    if desktops:
        lines.append("- Downloads for " + _and(desktops) + ".")
    return lines


def _and(names: Sequence[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _credits(project: Path) -> list[str]:
    entries = read_assets(project)
    made_with = sorted(
        {
            tools.REGISTRY[e.tool].name if e.tool in tools.REGISTRY else e.tool
            for e in entries
            if e.origin == "tool-created"
        }
    )
    lines = ["- Made with the [Godot Engine](https://godotengine.org) and Joinery."]
    if made_with:
        lines.append("- Art and sound made by hand in " + _and(made_with) + ".")
    for entry in entries:
        if entry.origin == "human-import":
            lines.append(f"- `{entry.path}`: {entry.licence}.")
    return lines


def draft(project: Path) -> str:
    """The page as Markdown: title, tagline, about, controls, features, credits."""
    name = game_name(project)
    brief = _brief(project)
    pitch = [p for p in brief.get("Pitch", "").split("\n\n") if p.strip()]
    tagline = pitch[0].strip() if pitch else GAP
    about = [*pitch[1:], brief.get("Player fantasy", ""), brief.get("Core loop", "")]
    about_text = "\n\n".join(p.strip() for p in about if p.strip()) or GAP
    rows = controls(input_actions(project))
    pads = any(pad for _, _, pad in rows)
    table = ["| Action | Keyboard and mouse |" + (" Gamepad |" if pads else "")]
    table.append("|---|---|" + ("---|" if pads else ""))
    for action, keys, pad in rows:
        table.append(
            f"| {action} | {keys or '-'} |" + (f" {pad or '-'} |" if pads else "")
        )
    features = _features(project, brief) or [GAP]
    parts = [
        (
            "<!-- Drafted by Joinery from design/brief.md, the input map and joinery.lock. "
            "Edit it freely; Joinery only replaces it when you ask for a new draft. -->"
        ),
        f"# {name}",
        tagline,
        "## About",
        about_text,
        "## Controls",
        "\n".join(table) if rows else GAP,
        "## Features",
        "\n".join(features),
        "## Credits",
        "\n".join(_credits(project)),
    ]
    return "\n\n".join(parts) + "\n"


def lint(text: str) -> list[str]:
    """One line naming any filler words in the page, as the brief's check does."""
    line = bible_writing.suggestion(
        "page",
        bible_writing.filler("## Tagline\n" + text),
        "what the player does, with a number where one exists",
    )
    return [] if line is None else [line]


def gaps(text: str) -> int:
    return text.count(GAP)


def write(project: Path, *, redraft: bool = False) -> dict[str, Any]:
    """Save the draft unless a page exists (or `redraft`); returns the page and checks."""
    project = project.resolve()
    path = project / PAGE
    created = False
    if redraft or not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(draft(project), encoding="utf-8")
        created = True
    text = path.read_text(encoding="utf-8")
    return {
        "path": PAGE.as_posix(),
        "created": created,
        "text": text,
        "gaps": gaps(text),
        "suggestions": lint(text),
    }
