"""Joinery screens take sizes and colours from ui_theme.gd, never from literals.

Every spacing, font size, radius and colour in the addon comes from the design tokens in
``ui_theme.gd`` (built into a Theme by ``ui_theme_build.gd``), so a theme, accent or text
size change applies everywhere at once. This guard fails on the shortcuts that break that:
a numeric literal handed to a theme constant, font size, corner radius or ``panel()`` call,
and a palette colour copied into an override at build time.
"""

from __future__ import annotations

import re
from pathlib import Path

ADDON = Path(__file__).resolve().parents[1] / "godot_addon" / "joinery"
# The token owners themselves.
EXEMPT = {"ui_theme.gd", "ui_theme_build.gd"}
SIZED_CALLS = (
    "add_theme_constant_override",
    "add_theme_font_size_override",
    "set_corner_radius_all",
    "panel",
)
# A non-zero number standing alone as an argument: 8, 12.0, -4. Zero means "none" and is fine.
LITERAL = re.compile(r"^-?(?:[1-9]\d*(?:\.\d+)?|0?\.\d*[1-9]\d*)$")
PALETTE = re.compile(r"\bpalette\b|_THEME\.color\(|\bUI_THEME\.palette\(")


def _calls(text: str, name: str) -> list[tuple[int, str]]:
    """Each call of ``name(`` with its line number and full argument text."""
    found = []
    for match in re.finditer(
        r"(?<![\w.])(?:[\w.]*\.)?" + re.escape(name) + r"\(", text
    ):
        if text[: match.start()].rstrip().endswith("func"):
            continue  # A definition, not a call.
        depth, index = 1, match.end()
        while index < len(text) and depth:
            depth += {"(": 1, ")": -1}.get(text[index], 0)
            index += 1
        line = text.count("\n", 0, match.start()) + 1
        found.append((line, text[match.end() : index - 1]))
    return found


def _arguments(arguments: str) -> list[str]:
    parts, depth, current = [], 0, ""
    for char in arguments:
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        if char == "," and depth == 0:
            parts.append(current.strip())
            current = ""
        else:
            current += char
    if current.strip():
        parts.append(current.strip())
    return parts


def _sources() -> list[Path]:
    return sorted(path for path in ADDON.glob("*.gd") if path.name not in EXEMPT)


def test_the_addon_has_screens_to_check() -> None:
    assert len(_sources()) > 20


def test_no_size_literals_outside_the_theme() -> None:
    hits = []
    for path in _sources():
        text = path.read_text(encoding="utf-8")
        for name in SIZED_CALLS:
            for line, arguments in _calls(text, name):
                literals = [arg for arg in _arguments(arguments) if LITERAL.match(arg)]
                if literals:
                    hits.append(f"{path.name}:{line} {name}({arguments.strip()})")
    assert not hits, "Use ui_theme tokens instead of literals:\n" + "\n".join(hits)


def test_no_palette_colours_copied_into_overrides() -> None:
    hits = []
    for path in _sources():
        text = path.read_text(encoding="utf-8")
        for line, arguments in _calls(text, "add_theme_color_override"):
            if PALETTE.search(arguments):
                hits.append(
                    f"{path.name}:{line} add_theme_color_override({arguments.strip()})"
                )
    assert not hits, (
        "Use a theme type variation, or read the role inside _draw or update code:\n"
        + "\n".join(hits)
    )


def test_the_guard_catches_what_it_should() -> None:
    sample = (
        'box.add_theme_constant_override("separation", 8)\n'
        'label.add_theme_font_size_override("font_size", 12)\n'
        "style.set_corner_radius_all(6)\n"
        "var box := UI_THEME.panel(fill, edge, 12, 8)\n"
        'name.add_theme_color_override("font_color", UI_THEME.color(self, "text"))\n'
        'ok.add_theme_constant_override("separation", UI_THEME.space(self, 2))\n'
        'ok.add_theme_constant_override("separation", 0)\n'
    )
    sized = [
        (name, line)
        for name in SIZED_CALLS
        for line, arguments in _calls(sample, name)
        if any(LITERAL.match(arg) for arg in _arguments(arguments))
    ]
    assert sorted(line for _name, line in sized) == [1, 2, 3, 4]
    coloured = [
        line
        for line, arguments in _calls(sample, "add_theme_color_override")
        if PALETTE.search(arguments)
    ]
    assert coloured == [5]
