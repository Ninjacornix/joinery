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


#: The named size exceptions (design direction v2 #62). Only ui_theme.gd defines them; screens
#: read them through UI_THEME (ICON, DOT, SEND, TRANSPORT, DIALOG and their *_px helpers).
NAMED = ("ICON", "DOT", "SEND", "TRANSPORT", "DIALOG")
#: Their values, as literals a screen might copy instead: a pixel size times the unit, or an
#: em size pair for a dialog.
NAMED_PIXELS = re.compile(
    r"\b(?:16|20|28|32|40)(?:\.0)?\s*\*\s*(?:UI_THEME\.unit\(|float\(m\[\"u\"\]\)|u\b)"
    r"|UI_THEME\.unit\([^)]*\)\s*\*\s*(?:16|20|28|32|40)\b"
)
NAMED_EM = re.compile(r"Vector2i?\(\s*(?:32|48)\s*,\s*36\s*\)")
#: Screens owned elsewhere that still carry a copy; each entry names what replaces it.
PENDING: dict[str, str] = {}


def test_named_size_exceptions_are_tokens() -> None:
    theme = (ADDON / "ui_theme.gd").read_text(encoding="utf-8")
    for name in NAMED:
        assert re.search(rf"^const {name} :=", theme, re.MULTILINE), name
    assert 'const ICON := {"sm": 16, "md": 20, "tile": 28}' in theme
    assert 'const DIALOG := {"connect": Vector2(32, 36), "doc": Vector2(48, 36)}' in theme


def test_no_copies_of_the_named_sizes() -> None:
    hits = []
    for path in sorted(ADDON.glob("*.gd")):
        if path.name == "ui_theme.gd" or path.name in PENDING:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if NAMED_PIXELS.search(line) or NAMED_EM.search(line):
                hits.append(f"{path.name}:{number} {line.strip()}")
    assert not hits, "Use UI_THEME.ICON, DOT, SEND, TRANSPORT or DIALOG:\n" + "\n".join(hits)


def test_the_named_size_guard_catches_copies() -> None:
    assert NAMED_PIXELS.search("var side := roundf(8 * UI_THEME.unit(self)) + 32 * u")
    assert NAMED_PIXELS.search('var pad := roundf(32 * float(m["u"]))')
    assert NAMED_EM.search("const CONNECT_EM := Vector2(32, 36)")
    assert not NAMED_PIXELS.search("var gap := roundf(3.0 * UI_THEME.unit(self))")
