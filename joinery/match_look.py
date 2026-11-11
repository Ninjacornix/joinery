"""Godot text resources against the art bible's look: environments and UI themes.

`joinery match` reads `.tres` and `.tscn` text without Godot. An Environment is
either a resource file of its own or a sub-resource of a scene; a UI theme is a
Theme resource, a StyleBoxFlat, or a scene's `theme_override_*` lines. Each
issue carries the property values that would bring it back in line, so a fix
is plain data: the Godot MCP's `manage_resource` sets them on a resource file,
and a sub-resource is rewritten line by line (`set_properties`).
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from joinery.bible_rules import (
    TONEMAPS,
    EnvironmentRules,
    _godot_colour,
    _lab,
    _sections,
    delta_e_2000,
    hex_rgb,
)

_SECTION = re.compile(r"^\[(\w+)([^\]]*)\]\s*$")
_ATTR = re.compile(r'(\w+)=("[^"]*"|\S+)')
_COLOR = re.compile(
    r"Color\(\s*([-\d.e]+)\s*,\s*([-\d.e]+)\s*,\s*([-\d.e]+)\s*(?:,\s*([-\d.e]+)\s*)?\)"
)
_TONEMAP_NAMES = {value: name for name, value in TONEMAPS.items()}
#: Environment defaults when a property line is absent (Godot 4.7).
_DEFAULTS: dict[str, Any] = {
    "tonemap_mode": 0,
    "tonemap_exposure": 1.0,
    "fog_enabled": False,
    "glow_enabled": False,
    "adjustment_enabled": False,
    "adjustment_brightness": 1.0,
    "adjustment_contrast": 1.0,
    "adjustment_saturation": 1.0,
}
_FONT_TYPES = {"FontFile", "Font", "SystemFont", "FontVariation"}


@dataclass(frozen=True)
class Section:
    """Which section of a Godot text file: "resource", or a sub_resource by id."""

    kind: str
    id: str = ""

    def label(self) -> str:
        return "the resource" if self.kind == "resource" else f"sub-resource {self.id}"


@dataclass
class Issue:
    """One property group out of line with the bible, and the values that fix it."""

    check: str
    section: Section
    measured: str
    target: str
    message: str
    severity: str
    #: Property name -> value (Python types) that brings it in line; {} = no fix here.
    props: dict[str, Any] = field(default_factory=dict)
    #: An ext_resource path to replace: (old res:// path, new res:// path).
    swap: tuple[str, str] | None = None


# --- reading -------------------------------------------------------------------


def _value(raw: str) -> Any:
    text = raw.strip()
    if text in {"true", "false"}:
        return text == "true"
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


def environments(text: str) -> list[tuple[Section, dict[str, str]]]:
    """Every Environment in a `.tres` (the resource) or `.tscn` (sub-resources)."""
    found: list[tuple[Section, dict[str, str]]] = []
    sections = _sections(text)
    environment_file = any(
        kind == "gd_resource" and attrs.get("type", "").strip('"') == "Environment"
        for kind, attrs, _ in sections
    )
    for kind, attrs, values in sections:
        kind_type = attrs.get("type", "").strip('"')
        if kind == "resource" and environment_file:
            found.append((Section("resource"), values))
        elif kind == "sub_resource" and kind_type == "Environment":
            found.append(
                (Section("sub_resource", attrs.get("id", "").strip('"')), values)
            )
    return found


def _get(values: Mapping[str, str], key: str) -> Any:
    return _value(values[key]) if key in values else _DEFAULTS[key]


def _outside(value: float, bounds: tuple[float, float]) -> bool:
    return value < bounds[0] - 1e-6 or value > bounds[1] + 1e-6


def _nearest(value: float, bounds: tuple[float, float]) -> float:
    return round(min(max(value, bounds[0]), bounds[1]), 4)


def _range_text(bounds: tuple[float, float]) -> str:
    return f"{bounds[0]:g}–{bounds[1]:g}"


def environment_issues(
    section: Section, values: Mapping[str, str], rules: EnvironmentRules
) -> list[Issue]:
    """How one Environment differs from the bible's look."""
    issues: list[Issue] = []
    where = section.label()
    if rules.tonemap is not None:
        mode = int(_get(values, "tonemap_mode"))
        if mode != TONEMAPS[rules.tonemap]:
            name = _TONEMAP_NAMES.get(mode, str(mode))
            issues.append(
                Issue(
                    "tonemap",
                    section,
                    name,
                    rules.tonemap,
                    f"tonemap is {name} in {where}; the art bible uses {rules.tonemap}",
                    "high",
                    {"tonemap_mode": TONEMAPS[rules.tonemap]},
                )
            )
    if rules.exposure is not None:
        exposure = float(_get(values, "tonemap_exposure"))
        if _outside(exposure, rules.exposure):
            issues.append(
                Issue(
                    "exposure",
                    section,
                    f"{exposure:g}",
                    _range_text(rules.exposure),
                    f"exposure {exposure:g} in {where}; the art bible allows "
                    f"{_range_text(rules.exposure)}",
                    _severity(exposure, rules.exposure),
                    {"tonemap_exposure": _nearest(exposure, rules.exposure)},
                )
            )
    for check, key, wanted in (
        ("fog", "fog_enabled", rules.fog),
        ("glow", "glow_enabled", rules.glow),
    ):
        if wanted is None:
            continue
        found = bool(_get(values, key))
        if found != wanted:
            state = "on" if found else "off"
            issues.append(
                Issue(
                    check,
                    section,
                    state,
                    "on" if wanted else "off",
                    f"{check} is {state} in {where}; the art bible has it "
                    f"{'on' if wanted else 'off'}",
                    "medium",
                    {key: wanted},
                )
            )
    enabled = bool(_get(values, "adjustment_enabled"))
    for check, bounds in (
        ("brightness", rules.brightness),
        ("contrast", rules.contrast),
        ("saturation", rules.saturation),
    ):
        if bounds is None:
            continue
        key = "adjustment_" + check
        value = float(_get(values, key)) if enabled else 1.0
        if not _outside(value, bounds):
            continue
        props: dict[str, Any] = {key: _nearest(value, bounds)}
        if not enabled:
            props["adjustment_enabled"] = True
        issues.append(
            Issue(
                check,
                section,
                f"{value:g}",
                _range_text(bounds),
                f"{check} {value:g} in {where}; the art bible's grading allows "
                f"{_range_text(bounds)}",
                _severity(value, bounds),
                props,
            )
        )
    return issues


def _severity(value: float, bounds: tuple[float, float]) -> str:
    """How far outside: past half the range's width again is high."""
    width = max(bounds[1] - bounds[0], 0.05)
    distance = bounds[0] - value if value < bounds[0] else value - bounds[1]
    return "high" if distance > width else "medium"


# --- UI themes -------------------------------------------------------------------


def _colour_key(key: str) -> bool:
    parts = key.split("/")
    return (len(parts) == 3 and parts[1] == "colors") or (
        len(parts) == 2 and parts[0] == "theme_override_colors"
    )


def nearest_palette(colour: str, palette: list[str]) -> tuple[str, float]:
    """The palette colour closest to ``colour`` and its CIEDE2000 distance."""
    lab = _lab(hex_rgb(colour))
    best = min(palette, key=lambda p: delta_e_2000(lab, _lab(hex_rgb(p))))
    return best, delta_e_2000(lab, _lab(hex_rgb(best)))


def _godot_colour_value(colour: str, alpha: float = 1.0) -> str:
    r, g, b = (round(c / 255, 4) for c in hex_rgb(colour))
    return f"Color({r:g}, {g:g}, {b:g}, {alpha:g})"


def ui_issues(
    text: str,
    *,
    palette: list[str],
    max_delta_e: float,
    fonts: list[str],
    min_px: float | None,
    palette_colours: bool,
) -> list[Issue]:
    """Theme colours off the palette, fonts the bible doesn't list, text too small."""
    issues: list[Issue] = []
    sections = _sections(text)
    if fonts:
        allowed = {"res://" + f.removeprefix("res://").strip("/") for f in fonts}
        for kind, attrs, _values in sections:
            if kind != "ext_resource":
                continue
            if attrs.get("type", "").strip('"') not in _FONT_TYPES:
                continue
            path = attrs.get("path", "").strip('"')
            if path and path not in allowed:
                first = min(allowed)
                issues.append(
                    Issue(
                        "font",
                        Section("ext_resource", attrs.get("id", "").strip('"')),
                        path.removeprefix("res://"),
                        ", ".join(sorted(a.removeprefix("res://") for a in allowed)),
                        f"uses the font {path.removeprefix('res://')}, which the art "
                        "bible doesn't list",
                        "medium",
                        swap=(path, first),
                    )
                )
    for kind, attrs, values in sections:
        if kind not in {"resource", "sub_resource", "node"}:
            continue
        section = (
            Section("resource")
            if kind == "resource"
            else Section(kind, (attrs.get("id") or attrs.get("name") or "").strip('"'))
        )
        kind_type = attrs.get("type", "").strip('"')
        if palette_colours and palette:
            for key, raw in values.items():
                if not (
                    _colour_key(key)
                    or (
                        kind_type == "StyleBoxFlat"
                        and key in {"bg_color", "border_color"}
                    )
                ):
                    continue
                colour = _godot_colour(raw)
                if colour is None:
                    continue
                best, distance = nearest_palette(colour, palette)
                if distance <= max_delta_e:
                    continue
                match = _COLOR.search(raw)
                alpha = float(match.group(4)) if match and match.group(4) else 1.0
                issues.append(
                    Issue(
                        "theme_colour",
                        section,
                        colour,
                        best,
                        f"{key} {colour} is ΔE {distance:.0f} from the palette "
                        f"(nearest {best}, allowed {max_delta_e:g})",
                        "high" if distance > 2 * max_delta_e else "medium",
                        {key: _godot_colour_value(best, alpha)},
                    )
                )
        if min_px is None:
            continue
        for key, raw in values.items():
            sized = (
                key == "default_font_size"
                or "/font_sizes/" in key
                or key.startswith("theme_override_font_sizes/")
                or (key == "font_size" and kind_type == "LabelSettings")
            )
            if not sized:
                continue
            try:
                size = int(float(raw))
            except ValueError:
                continue
            if size < min_px - 1e-9:
                wanted = math.ceil(min_px)
                issues.append(
                    Issue(
                        "font_size",
                        section,
                        f"{size} px",
                        f"{wanted} px",
                        f"{key} is {size} px, below the art bible's minimum ({wanted} px here)",
                        "high" if size < 0.75 * min_px else "medium",
                        {key: wanted},
                    )
                )
    return issues


# --- writing ---------------------------------------------------------------------


def godot_value(value: Any) -> str:
    """A Python value in Godot's text resource syntax."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(round(value, 6))
    return str(value)


def _matcher(section: Section) -> Callable[[str, dict[str, str]], bool]:
    def matches(kind: str, attrs: dict[str, str]) -> bool:
        if kind != section.kind:
            return False
        if section.kind == "resource":
            return True
        own = (attrs.get("id") or attrs.get("name") or "").strip('"')
        return own == section.id

    return matches


def set_properties(text: str, section: Section, props: Mapping[str, Any]) -> str:
    """``text`` with ``props`` set in one section: existing lines replaced in place,
    new ones added after the section's last property. Raises if the section is gone."""
    lines = text.splitlines(keepends=True)
    matches = _matcher(section)
    start = -1
    for index, line in enumerate(lines):
        header = _SECTION.match(line.strip())
        if header and matches(header.group(1), dict(_ATTR.findall(header.group(2)))):
            start = index
            break
    if start < 0:
        raise ValueError(f"{section.label()} is no longer in the file")
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if _SECTION.match(lines[index].strip()):
            end = index
            break
    remaining = dict(props)
    last = start
    for index in range(start + 1, end):
        line = lines[index]
        if "=" not in line or line.startswith((" ", "\t")):
            continue
        last = index
        key = line.partition("=")[0].strip()
        if key in remaining:
            newline = "\n" if line.endswith("\n") else ""
            lines[index] = f"{key} = {godot_value(remaining.pop(key))}{newline}"
    if remaining:
        added = [f"{key} = {godot_value(value)}\n" for key, value in remaining.items()]
        if not lines[last].endswith("\n"):
            lines[last] += "\n"
        lines[last + 1 : last + 1] = added
    return "".join(lines)


def swap_ext_resource(text: str, old: str, new: str) -> str:
    """``text`` with one ext_resource pointed at another file (its uid dropped)."""
    out: list[str] = []
    for line in text.splitlines(keepends=True):
        header = _SECTION.match(line.strip())
        if header and header.group(1) == "ext_resource" and f'path="{old}"' in line:
            line = re.sub(r'\s+uid="[^"]*"', "", line).replace(
                f'path="{old}"', f'path="{new}"'
            )
        out.append(line)
    return "".join(out)
