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

import pytest

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


# --- Contrast ---------------------------------------------------------------------------------
#: WCAG 2.2 AA: 4.5:1 for text (body and secondary), 3:1 for icons, borders that carry
#: meaning and focus indicators.
TEXT_AA = 4.5
NON_TEXT_AA = 3.0
#: Every role that is drawn as text, and the fills it is drawn on.
TEXT_ROLES = ("text", "text_muted", "text_faint", "success", "danger", "accent", "accent_hover")
TEXT_FILLS = ("surface", "surface_sunken", "surface_raised", "accent_tint", "danger_tint")
#: Icons (text_muted glyphs), field and stop-button borders (border_strong) and the focus ring
#: (accent), on every fill a control sits on.
NON_TEXT_ROLES = ("text_muted", "border_strong", "accent")
NON_TEXT_FILLS = ("surface", "surface_sunken", "surface_raised", "accent_tint")


def _palette_tokens() -> tuple[dict[str, dict[str, str]], dict[str, tuple[str, str]]]:
    theme = (ADDON / "ui_theme.gd").read_text(encoding="utf-8")
    themes: dict[str, dict[str, str]] = {}
    for name in ("DARK", "LIGHT"):
        block = re.search(rf"^const {name} := \{{(.*?)^\}}", theme, re.MULTILINE | re.DOTALL)
        assert block, name
        themes[name.lower()] = dict(
            re.findall(r'"(\w+)": Color\("([0-9a-f]{6})"\)', block.group(1))
        )
    block = re.search(r"^const ACCENTS := \{(.*?)^\}", theme, re.MULTILINE | re.DOTALL)
    assert block
    accents = {
        name: (dark, light)
        for name, dark, light in re.findall(
            r'"(\w+)": \[Color\("([0-9a-f]{6})"\), Color\("([0-9a-f]{6})"\)\]', block.group(1)
        )
    }
    return themes, accents


RGB = tuple[float, float, float]


def _rgb(hex_colour: str) -> RGB:
    return (
        int(hex_colour[0:2], 16) / 255,
        int(hex_colour[2:4], 16) / 255,
        int(hex_colour[4:6], 16) / 255,
    )


def _lerp(a: RGB, b: RGB, weight: float) -> RGB:
    return (
        a[0] + (b[0] - a[0]) * weight,
        a[1] + (b[1] - a[1]) * weight,
        a[2] + (b[2] - a[2]) * weight,
    )


def _palette(theme: str, accent_name: str) -> dict[str, RGB]:
    """ui_theme.palette(): the base roles plus the derived accent and tint roles."""
    themes, accents = _palette_tokens()
    dark = theme == "dark"
    colours = {role: _rgb(value) for role, value in themes[theme].items()}
    accent = _rgb(accents[accent_name][0 if dark else 1])
    white, black = (1.0, 1.0, 1.0), (0.0, 0.0, 0.0)
    colours["accent"] = accent
    # Color.lightened(a) moves toward white by a; Color.darkened(a) toward black.
    colours["accent_hover"] = _lerp(accent, white, 0.08) if dark else _lerp(accent, black, 0.08)
    colours["accent_pressed"] = _lerp(accent, black, 0.08 if dark else 0.16)
    colours["on_accent"] = _rgb("1a1a1a") if dark else white
    colours["accent_tint"] = _lerp(colours["surface_sunken"], accent, 0.14)
    colours["danger_tint"] = _lerp(colours["surface"], colours["danger"], 0.12)
    return colours


def _luminance(colour: RGB) -> float:
    channels = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in colour]
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast(a: RGB, b: RGB) -> float:
    high, low = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def _pairs() -> list[tuple[str, str, str, str, float, float]]:
    """(theme, accent, foreground, background, ratio, required) for every checked pair."""
    _themes, accents = _palette_tokens()
    pairs = []
    for theme in ("dark", "light"):
        for accent in accents:
            p = _palette(theme, accent)
            checks = [(fg, bg, TEXT_AA) for fg in TEXT_ROLES for bg in TEXT_FILLS]
            checks += [(fg, bg, NON_TEXT_AA) for fg in NON_TEXT_ROLES for bg in NON_TEXT_FILLS]
            checks += [
                ("on_accent", bg, TEXT_AA) for bg in ("accent", "accent_hover", "accent_pressed")
            ]
            for fg, bg, required in checks:
                pairs.append((theme, accent, fg, bg, contrast(p[fg], p[bg]), required))
            # Secondary buttons: text on their hover and pressed fills.
            raised, text = p["surface_raised"], p["text"]
            for weight in (0.07, 0.12):
                fill = _lerp(raised, text, weight)
                pairs.append(
                    (theme, accent, "text", f"button+{weight}", contrast(text, fill), TEXT_AA)
                )
    return pairs


def _slider_roles() -> dict[str, str]:
    """ui_theme_build.gd's SLIDER_ROLES: the roles a slider's track, fill and knob use."""
    build = (ADDON / "ui_theme_build.gd").read_text(encoding="utf-8")
    block = re.search(r"^const SLIDER_ROLES := \{(.*?)\}", build, re.MULTILINE)
    assert block, "SLIDER_ROLES"
    return dict(re.findall(r'"(\w+)": "(\w+)"', block.group(1)))


def test_slider_track_fill_and_knob_read_on_every_surface_in_both_themes() -> None:
    """A slider's knob and track are non-text controls: 3:1 on every surface a slider sits
    on, light and dark, every accent. A border_subtle track or a default white knob fails."""
    roles = _slider_roles()
    assert set(roles) == {"track", "fill", "knob"}
    _themes, accents = _palette_tokens()
    failures = []
    for theme in ("dark", "light"):
        for accent in accents:
            p = _palette(theme, accent)
            for part, role in roles.items():
                for bg in NON_TEXT_FILLS:
                    ratio = contrast(p[role], p[bg])
                    if ratio < NON_TEXT_AA:
                        failures.append(
                            f"{theme}/{accent}: slider {part} ({role}) on {bg} is "
                            f"{ratio:.2f}:1"
                        )
    assert not failures, "\n".join(failures)
    p = _palette("light", "brass")
    assert contrast(p["border_subtle"], p["surface"]) < NON_TEXT_AA  # the guard bites


def test_every_role_meets_wcag_aa_on_every_surface_in_both_themes() -> None:
    failures = [
        f"{theme}/{accent}: {fg} on {bg} is {ratio:.2f}:1, needs {required}:1"
        for theme, accent, fg, bg, ratio, required in _pairs()
        if ratio < required
    ]
    assert not failures, "\n".join(failures)


def test_the_contrast_check_covers_both_themes_and_every_accent() -> None:
    pairs = _pairs()
    assert {(theme, accent) for theme, accent, *_ in pairs} == {
        (theme, accent)
        for theme in ("dark", "light")
        for accent in ("brass", "blue", "green", "violet")
    }
    assert contrast((0.0, 0.0, 0.0), (1.0, 1.0, 1.0)) == pytest.approx(21.0)
    # Hairlines are not held to 3:1, but stay visible on every surface they divide.
    for theme in ("dark", "light"):
        p = _palette(theme, "brass")
        for bg in ("surface", "surface_sunken"):
            assert contrast(p["border_subtle"], p[bg]) >= 1.5, (theme, bg)


#: The Journeyman sidebar's rows, which share one gutter (ui_theme.GUTTER, held by the
#: dock's JoineryDock panel). A JoineryInset in one of them adds a second text edge.
SIDEBAR_ROWS = (
    "journeyman_dock.gd",
    "sidebar_header.gd",
    "sidebar_footer.gd",
    "sidebar_queue.gd",
    "sidebar_change_card.gd",
    "sidebar_review_row.gd",
    "sidebar_composer.gd",
    "chat_history.gd",
    "chat_sessions.gd",
)


def test_the_sidebar_rows_add_no_inset_of_their_own() -> None:
    hits = [
        f"{name}:{number} {line.strip()}"
        for name in SIDEBAR_ROWS
        for number, line in enumerate(
            (ADDON / name).read_text(encoding="utf-8").splitlines(), 1
        )
        if '"JoineryInset"' in line
    ]
    assert not hits, "Sidebar rows sit on the dock's gutter:\n" + "\n".join(hits)
    theme = (ADDON / "ui_theme.gd").read_text(encoding="utf-8")
    assert re.search(r"^const GUTTER := \d+$", theme, re.MULTILINE)
