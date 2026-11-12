"""`joinery access`: how accessible the game is, measured, with the fixes.

The game (not the editor) is played headless by the playtest bot with the probe on
(`joinery.check_runs`), which reads every visible text and focusable control on the HUD
during play and on every menu and settings page: text, font size and colour, what it sits
on, its accessible name, and where its colour and size come from. A second run in a
window records each frame's luminance for flashes. The project's files say the rest:
the input map, the scripts, the scenes, the features. Nine areas, each scored:

* text contrast (WCAG AA: 4.5:1, 3:1 for large text; the art bible's ``min_ui_contrast``
  where it says), and text over the game world with no backdrop or outline;
* colour: colours a script switches between that people with protanopia, deuteranopia or
  tritanopia can't tell apart, the bible's colour-blind pairs, and a colour filter option;
* text size at the target resolution (``[access] target_height``, 1080 by default; the
  bible's ``min_text_px``, else 18 px at 1080p), and a text size option;
* subtitles: a subtitle system, an on/off and size option, and voice lines that show one;
* controls: remapping, controller support, and toggles for actions that must be held;
* motion and flashes: shake and motion options honoured in code, a flash option, and
  flashes faster than three a second in the windowed run (WCAG 2.3.1);
* pause anywhere; difficulty and assists; screen readers (every focusable control has a
  name, every menu control can take focus).

Fixes: typed text edits of the scene or theme a colour, size or name comes from (a
recolour stays in the bible's palette); the Accessibility feature (or controller support)
for missing options, added by the user from its plan; an agent's change where code does it.
"""

from __future__ import annotations

import itertools
import re
from pathlib import Path
from typing import Any

from joinery import blueprints, check_runs
from joinery.bible_rules import (
    CVD_MATRICES,
    _lab,
    contrast_ratio,
    cvd_delta_e,
    delta_e_2000,
    hex_rgb,
    load_rules,
)
from joinery.checks_core import (
    CheckFix,
    Report,
    Severity,
    Tally,
    config,
    res,
    text_fix,
)
from joinery.godot_config import GodotConfig, action_events
from joinery.perf_static import all_files

AREAS: dict[str, str] = {
    "contrast": "Contrast",
    "colour": "Colour",
    "text": "Text size",
    "captions": "Subtitles",
    "controls": "Controls",
    "motion": "Motion",
    "pause": "Pause",
    "assists": "Assists",
    "menus": "Screen readers",
    "translation": "Translations",
}
#: Words for each way longer text fails (the probe's ``problem``) and how bad it is.
LONG_TEXT: dict[str, tuple[str, Severity]] = {
    "off_screen": ("runs off the screen", "high"),
    "cut_off": ("is cut off", "medium"),
    "cut_below": ("loses its last lines", "medium"),
    "past_box": ("runs past its box and is hidden", "medium"),
}
#: Label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART.
AUTOWRAP_WORD_SMART = 3
FEATURE = "accessibility"
#: Settings the Accessibility feature adds, by the control name its page gives them.
OPTIONS = {
    "text_scale": ("text", "Text size option", r"text_scale|font_scale|ui_scale"),
    "high_contrast": ("contrast", "High contrast option", r"high_contrast"),
    "colour_filter": (
        "colour",
        "Colour-blind filter",
        r"colou?r_?blind|daltoni|colou?r_filter",
    ),
    "subtitles": ("captions", "Subtitles option", r"subtitle|caption"),
    "screen_shake": ("motion", "Screen shake option", r"screen_shake|shake_strength"),
    "reduced_motion": ("motion", "Reduced motion option", r"reduced?_motion"),
    "reduce_flashes": ("motion", "Flash reduction option", r"reduce_flash|flashes"),
    "game_speed": ("assists", "Game speed assist", r"game_speed|difficulty|assist"),
}
HOLD_ACTIONS = ("sprint", "crouch", "aim", "block", "run", "walk", "zoom", "focus")
_HOLD = re.compile(r"is_action_pressed\(\s*&?\"(\w+)\"")
_COLOUR_SET = re.compile(
    r"\b(modulate|self_modulate|color|albedo_color|font_color)\s*=\s*(Color[.(][^\n]*)"
)
_NAMED = {
    "RED": "#ff0000",
    "GREEN": "#00ff00",
    "BLUE": "#0000ff",
    "YELLOW": "#ffff00",
    "ORANGE": "#ffa500",
    "WHITE": "#ffffff",
    "BLACK": "#000000",
    "LIME": "#00ff00",
    "LIME_GREEN": "#32cd32",
    "FOREST_GREEN": "#228b22",
    "CRIMSON": "#dc143c",
    "DARK_RED": "#8b0000",
    "DARK_GREEN": "#006400",
    "MAGENTA": "#ff00ff",
    "CYAN": "#00ffff",
    "PURPLE": "#a020f0",
    "GRAY": "#bebebe",
    "BROWN": "#a52a2a",
    "PINK": "#ffc0cb",
}
_RGB = re.compile(r"Color\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)")
_HEX = re.compile(r"Color\(\s*\"#?([0-9a-fA-F]{6})")
#: WCAG 2.3.1: no more than three flashes in any one second.
FLASHES_PER_SECOND = 3
FLASH_STEP = 0.1


def _colour(expression: str) -> str | None:
    named = re.match(r"Color\.(\w+)", expression)
    if named:
        return _NAMED.get(named.group(1))
    found = _HEX.search(expression)
    if found:
        return "#" + found.group(1).lower()
    rgb = _RGB.search(expression)
    if rgb:
        values = [min(255, max(0, round(float(v) * 255))) for v in rgb.groups()]
        return "#" + "".join(f"{v:02x}" for v in values)
    return None


def godot_colour(colour: str) -> str:
    r, g, b = (round(c / 255, 4) for c in hex_rgb(colour))
    return f"Color({r:g}, {g:g}, {b:g}, 1)"


def recolour(
    text: str, background: str, needed: float, palette: list[str]
) -> tuple[str, str]:
    """The colour closest to ``text`` that reaches ``needed`` contrast on ``background``:
    from the palette when one does, else black or white. Returns (colour, why)."""
    lab = _lab(hex_rgb(text))
    passing = [c for c in palette if contrast_ratio(c, background) >= needed]
    if passing:
        best = min(passing, key=lambda c: delta_e_2000(lab, _lab(hex_rgb(c))))
        return best, "the nearest palette colour that reads on it"
    plain = max(("#000000", "#ffffff"), key=lambda c: contrast_ratio(c, background))
    return plain, "no palette colour reads on it, so plain " + (
        "black" if plain == "#000000" else "white"
    )


def _changes(series: list[tuple[int, float]]) -> list[int]:
    """Frames where the luminance turned by at least FLASH_STEP (with the darker side
    below 0.8): each one half of a flash."""
    if not series:
        return []
    changes: list[int] = []
    direction = 0
    extreme = series[0][1]
    for frame, value in series[1:]:
        if direction > 0:
            extreme = max(extreme, value)
        elif direction < 0:
            extreme = min(extreme, value)
        turned = (direction >= 0 and extreme - value >= FLASH_STEP) or (
            direction <= 0 and value - extreme >= FLASH_STEP
        )
        if turned and min(extreme, value) < 0.8:
            changes.append(frame)
            direction = 1 if value > extreme else -1
            extreme = value
    return changes


def flashes(trace: list[list[float]], fps: int = 60) -> tuple[float, int]:
    """The most flashes in any one second of a luminance trace, and the frame it starts.

    A flash is a pair of opposing changes of at least 10 % relative luminance where the
    darker side is below 0.8 (WCAG's general flash threshold), for the whole picture and
    each quarter of it."""
    worst = (0.0, -1)
    for column in range(1, 6):
        series = [
            (int(row[0]), float(row[column])) for row in trace if len(row) > column
        ]
        changes = _changes(series)
        for index, start in enumerate(changes):
            rate = sum(1 for c in changes[index:] if c < start + fps) / 2
            if rate > worst[0]:
                worst = (rate, start)
    return worst


class _State:
    def __init__(self, project: Path) -> None:
        self.project = project
        self.tally = Tally(AREAS)
        self.files = all_files(project)
        self.scripts = {
            p: (project / p).read_text(encoding="utf-8", errors="replace")
            for p in self.files
            if p.endswith(".gd")
        }
        self.config = GodotConfig.parse(
            (project / "project.godot").read_text(encoding="utf-8", errors="replace")
        )
        self.rules = load_rules(project)
        self.record = blueprints.read_record(project)
        self.features = list(self.record.features) if self.record else []
        self.palette = self.rules.palette.all_colours()
        table = config(project, "access")
        self.target_height = int(table.get("target_height", 1080) or 1080)
        minimum = table.get("min_text_px") or self.rules.accessibility.min_text_px or 18
        self.min_px = float(minimum)
        self.contrast = self.rules.accessibility.min_ui_contrast

    def code(self, pattern: str) -> list[str]:
        found = re.compile(pattern, re.IGNORECASE)
        return [p for p, text in self.scripts.items() if found.search(text)]

    def feature_fix(self, label: str, feature: str = FEATURE) -> CheckFix | None:
        if self.record is None or feature in self.features:
            return None
        return CheckFix(kind="feature", label=label, feature=feature)

    def option_fix(self, words: str) -> CheckFix:
        """Add an option: the feature where the game has one, else an agent's change."""
        offered = self.feature_fix("Add the Accessibility settings")
        if offered is not None:
            return offered
        return CheckFix(
            kind="agent",
            label="Add accessibility options",
            request=f"Add a {words.lower()} to the game's settings, saved with the "
            "other settings and applied at once, and make the game honour it.",
        )


def _controls(probe: dict[str, Any]) -> list[dict[str, Any]]:
    controls = [c for c in probe.get("hud", []) if isinstance(c, dict)]
    for page in probe.get("pages", []):
        if isinstance(page, dict):
            controls += [c for c in page.get("controls", []) if isinstance(c, dict)]
    seen: set[tuple[str, str]] = set()
    unique: list[dict[str, Any]] = []
    for control in controls:
        key = (str(control.get("page", "")), str(control.get("node", "")))
        if key not in seen:
            seen.add(key)
            unique.append(control)
    return unique


def _scene_edit(
    source: dict[str, Any], control: dict[str, Any]
) -> tuple[str, list[Any]] | None:
    """The scene file and node a value can be set on, when it came from a scene."""
    if source.get("kind") != "scene" or not source.get("file"):
        return None
    node = str(source.get("node", control.get("node", "")))
    if node in {"", "."}:
        return None
    parent, _, name = node.rpartition("/")
    return res(str(source["file"])), [name, parent or "."]


def _where(control: dict[str, Any], seed: int) -> dict[str, Any]:
    return {
        "scene": control.get("scene", ""),
        "node": control.get("node", ""),
        "page": control.get("page", ""),
        "seed": seed,
    }


def contrast_and_size(state: _State, probe: dict[str, Any], seed: int) -> None:
    viewport_h = float((probe.get("viewport") or [0, 648])[1] or 648)
    stretch = state.config.get("display", "window/stretch/mode") or '"disabled"'
    scale = state.target_height / viewport_h if "disabled" not in stretch else 1.0
    minimum = state.min_px * state.target_height / 1080
    for control in _controls(probe):
        text = str(control.get("text", "")).strip()
        if not text or control.get("disabled"):
            continue
        key = f"{control.get('page')}:{control.get('node')}"
        size = float(control.get("size", 16) or 16)
        shown = size * scale
        colour = str(control.get("colour", ""))
        background = str(control.get("background", ""))
        large = shown * 1080 / state.target_height >= 24
        needed = state.contrast or (3.0 if large else 4.5)
        name = str(control.get("name", ""))
        where = _where(control, seed)
        path = res(str(control.get("scene", ""))) or str(
            (control.get("colour_from") or {}).get("file", "")
        ).removeprefix("res://")
        if background and colour:
            ratio = contrast_ratio(colour, background)
            ok = ratio >= needed or int(control.get("outline", 0) or 0) > 0
            state.tally.check("contrast", key, ok)
            if not ok:
                state.tally.add(
                    "contrast",
                    path or "project.godot",
                    "contrast",
                    "high" if ratio < 3.0 else "medium",
                    f"{ratio:.1f}:1",
                    f"≥ {needed:g}:1",
                    f'"{text[:30]}" ({colour} on {background}) has contrast {ratio:.1f}:1.',
                    subject=key,
                    fix=_recolour_fix(state, control, background, needed),
                    evidence=where,
                    item=key,
                )
        elif control.get("page") == "hud":
            ok = int(control.get("outline", 0) or 0) > 0
            state.tally.check("contrast", key, ok)
            if not ok:
                target = _scene_edit(control.get("colour_from") or {}, control)
                state.tally.add(
                    "contrast",
                    path or "project.godot",
                    "backdrop",
                    "low",
                    "no backdrop",
                    "outline or panel",
                    f'"{text[:30]}" sits on the game world with no outline or panel, '
                    "so it can vanish against bright or busy scenes.",
                    subject=key,
                    fix=text_fix(
                        state.project,
                        target[0],
                        [
                            {
                                "node": target[1],
                                "props": {
                                    "theme_override_constants/outline_size": max(
                                        2, round(size / 8)
                                    ),
                                    "theme_override_colors/font_outline_color": "Color(0, 0, 0, 1)",
                                },
                            }
                        ],
                        "Outline HUD text in Godot",
                    )
                    if target
                    else CheckFix(
                        kind="agent",
                        label="Give HUD text an outline",
                        request=f'The HUD label {name} ("{text[:30]}") sits on the game '
                        "world with no backdrop: give it an outline "
                        "(theme_override_constants/outline_size and a dark "
                        "font_outline_color) or a translucent panel behind it.",
                        targets=[path] if path else [],
                    ),
                    evidence=where,
                    item=key,
                )
        ok_size = shown + 1e-6 >= minimum
        state.tally.check("text", key, ok_size)
        if not ok_size:
            state.tally.add(
                "text",
                path or "project.godot",
                "text_size",
                "high" if shown < 0.75 * minimum else "medium",
                f"{shown:.0f} px",
                f"≥ {minimum:.0f} px",
                f'"{text[:30]}" is {shown:.0f} px tall at {state.target_height}p.',
                subject=key,
                fix=_size_fix(state, control, size, minimum / scale),
                evidence=where,
                item=key,
            )


def _recolour_fix(
    state: _State, control: dict[str, Any], background: str, needed: float
) -> CheckFix:
    colour, why = recolour(
        str(control.get("colour")), background, needed, state.palette
    )
    source = control.get("colour_from") or {}
    kind = "font_color" if control.get("type") != "RichTextLabel" else "default_color"
    target = _scene_edit(source, control)
    if target is not None:
        return text_fix(
            state.project,
            target[0],
            [
                {
                    "node": target[1],
                    "props": {f"theme_override_colors/{kind}": godot_colour(colour)},
                }
            ],
            f"Recolour text to {colour} in Godot",
        )
    if source.get("kind") == "theme" and source.get("file"):
        path = res(str(source["file"]))
        return text_fix(
            state.project,
            path,
            [
                {
                    "section": ["resource", ""],
                    "props": {
                        f"{control.get('type', 'Label')}/colors/{kind}": godot_colour(
                            colour
                        )
                    },
                }
            ],
            f"Recolour the theme's text to {colour} in Godot",
        )
    file = str(source.get("file", "")).removeprefix("res://")
    return CheckFix(
        kind="agent",
        label="Recolour low-contrast text",
        request=f'The text "{str(control.get("text", ""))[:30]}" ({control.get("node")}) '
        f"is {control.get('colour')} on {background}. Make it {colour} ({why}) where "
        f"its colour is set{' in ' + file if file else ''}.",
        targets=[file] if file else [],
    )


def _size_fix(
    state: _State, control: dict[str, Any], size: float, wanted: float
) -> CheckFix:
    source = control.get("size_from") or {}
    px = max(int(size) + 1, round(wanted + 0.49))
    target = _scene_edit(source, control)
    if target is not None:
        return text_fix(
            state.project,
            target[0],
            [{"node": target[1], "props": {"theme_override_font_sizes/font_size": px}}],
            f"Make text {px} px in Godot",
        )
    if source.get("kind") == "theme" and source.get("file"):
        return text_fix(
            state.project,
            res(str(source["file"])),
            [
                {
                    "section": ["resource", ""],
                    "props": {
                        f"{control.get('type', 'Label')}/font_sizes/font_size": px
                    },
                }
            ],
            f"Make the theme's text {px} px in Godot",
        )
    file = str(source.get("file", "")).removeprefix("res://")
    return CheckFix(
        kind="agent",
        label="Make small text bigger",
        request=f'The text "{str(control.get("text", ""))[:30]}" ({control.get("node")}) '
        f"is {size:.0f} px in the game's viewport; make it at least {px} px where its "
        f"size is set{' in ' + file if file else ''}.",
        targets=[file] if file else [],
    )


def options(state: _State, probe: dict[str, Any]) -> None:
    names = " ".join(
        str(c.get("name", "")) for c in _controls(probe) if c.get("page") not in {"hud"}
    )
    for key, (area, words, pattern) in OPTIONS.items():
        if key == "reduce_flashes" and not state.code(r"flash"):
            continue
        present = f"access_{key}" in names or bool(state.code(pattern))
        state.tally.check(area, f"option:{key}", present)
        if not present:
            state.tally.add(
                area,
                "project.godot",
                "option",
                "medium"
                if key in {"subtitles", "text_scale", "colour_filter"}
                else "low",
                "missing",
                words,
                f"The game's settings have no {words.lower()}.",
                subject=key,
                fix=state.option_fix(words),
                item=f"option:{key}",
            )


def colours(state: _State) -> None:
    limit = state.rules.accessibility.colour_blind_min_delta_e
    for path, text in state.scripts.items():
        by_property: dict[str, set[str]] = {}
        for prop, expression in _COLOUR_SET.findall(text):
            colour = _colour(expression)
            if colour is not None:
                by_property.setdefault(prop, set()).add(colour)
        for prop, found in by_property.items():
            for first, second in itertools.combinations(sorted(found), 2):
                if delta_e_2000(_lab(hex_rgb(first)), _lab(hex_rgb(second))) < limit:
                    continue  # the same to everyone: not a signal
                worst = min(CVD_MATRICES, key=lambda k: cvd_delta_e(first, second, k))
                distance = cvd_delta_e(first, second, worst)
                item = f"{path}:{prop}:{first}:{second}"
                state.tally.check("colour", item, distance >= limit)
                if distance < limit:
                    state.tally.add(
                        "colour",
                        path,
                        "colour_only",
                        "high",
                        f"ΔE {distance:.0f} ({worst})",
                        f"ΔE ≥ {limit:g}",
                        f"switches {prop} between {first} and {second}, which look alike "
                        f"with {worst}; colour alone carries the signal.",
                        subject=f"{prop}:{first}:{second}",
                        fix=CheckFix(
                            kind="agent",
                            label="Add a cue besides colour",
                            request=f"{path} signals a state by setting {prop} to {first} or "
                            f"{second}, which people with {worst} can't tell apart "
                            f"(ΔE {distance:.0f}). Keep the colours if you like, but add a "
                            "second cue: a shape, icon, pattern, text or a clear "
                            "difference in brightness.",
                            targets=[path],
                        ),
                        item=item,
                    )
    roles = state.rules.palette.roles
    for first, second in itertools.combinations(
        state.rules.accessibility.colour_blind_distinct, 2
    ):
        a, b = roles.get(first, first), roles.get(second, second)
        if not (a.startswith("#") and b.startswith("#")):
            continue
        worst = min(CVD_MATRICES, key=lambda k: cvd_delta_e(a, b, k))
        distance = cvd_delta_e(a, b, worst)
        state.tally.check("colour", f"bible:{first}:{second}", distance >= limit)
        if distance < limit:
            state.tally.add(
                "colour",
                "art/bible.md",
                "palette_pair",
                "high",
                f"ΔE {distance:.0f} ({worst})",
                f"ΔE ≥ {limit:g}",
                f"The palette's {first} and {second} look alike with {worst}.",
                subject=f"{first}:{second}",
                needs_you="Change one of the two colours in the art direction (/art).",
                item=f"bible:{first}:{second}",
            )


def captions(state: _State) -> None:
    voices = [
        p
        for p in state.files
        if re.search(r"(voice|dialog|vo_|speech|narrat)", p, re.IGNORECASE)
        and p.endswith((".wav", ".ogg", ".mp3", ".tscn"))
    ]
    voice_nodes = [
        p
        for p in state.files
        if p.endswith(".tscn")
        and re.search(
            r'\[node name="[^"]*(Voice|Dialog|Speech|Narrat)[^"]*" type="AudioStreamPlayer',
            (state.project / p).read_text(errors="replace"),
        )
    ]
    voiced = sorted(set(voices) | set(voice_nodes))
    calls = state.code(r"Subtitles\.say|subtitle|caption")
    if not voiced:
        return
    ok = bool(calls) and any(
        "Subtitles" in state.scripts[p] or "caption" in state.scripts[p] for p in calls
    )
    state.tally.check("captions", "voice", ok)
    if not ok:
        state.tally.add(
            "captions",
            voiced[0],
            "voice_subtitles",
            "high",
            f"{len(voiced)} voiced, 0 subtitled",
            "subtitles for every line",
            "The game has voice or dialogue sound but shows no subtitles for it.",
            fix=CheckFix(
                kind="agent",
                label="Show subtitles for voice lines",
                request="Show a subtitle for every voice or dialogue line: where each "
                "plays, call Subtitles.say(text, seconds, speaker) from the Accessibility "
                "feature (or the game's own subtitle display), with the line's words as an "
                f"English msgid. Voice found in: {', '.join(voiced[:6])}.",
                targets=voiced[:6],
            ),
            item="voice",
        )


def controls(state: _State, probe: dict[str, Any]) -> None:
    pages = {
        str(p.get("id", "")) for p in probe.get("pages", []) if isinstance(p, dict)
    }
    actions = [k for k in state.config.keys("input") if not k.startswith("ui_")]
    events = {k: action_events(state.config.get("input", k) or "") for k in actions}
    remap = "controls" in pages or bool(
        state.code(r"action_erase_events|action_add_event")
    )
    state.tally.check("controls", "remap", remap)
    if not remap and actions:
        state.tally.add(
            "controls",
            "project.godot",
            "remap",
            "high",
            "no remapping",
            "a Controls page",
            "Players can't change the game's keys or buttons.",
            fix=state.feature_fix(
                "Add controller support and a Controls page", "controller"
            )
            or CheckFix(
                kind="agent",
                label="Let players remap controls",
                request="Add a Controls settings page where each action of the input map "
                "can be rebound for keyboard and gamepad, saved with the settings.",
            ),
            item="remap",
        )
    pad = any("InputEventJoypad" in e for evs in events.values() for e in evs)
    pad = pad or "controller" in state.features
    state.tally.check("controls", "controller", pad or not actions)
    if not pad and actions:
        state.tally.add(
            "controls",
            "project.godot",
            "controller",
            "medium",
            "keyboard only",
            "gamepad bindings",
            f"None of the game's {len(actions)} actions has a gamepad binding.",
            fix=state.feature_fix(
                "Add controller support and a Controls page", "controller"
            )
            or CheckFix(
                kind="agent",
                label="Add gamepad bindings",
                request="Give every action in the input map a gamepad binding.",
            ),
            item="controller",
        )
    held = sorted(
        {
            m
            for text in state.scripts.values()
            for m in _HOLD.findall(text)
            if m in HOLD_ACTIONS
        }
    )
    if held:
        toggle = bool(
            state.code(r"toggle_actions|hold_to_toggle|toggle_sprint|toggle_crouch")
        )
        state.tally.check("controls", "toggle", toggle)
        if not toggle:
            state.tally.add(
                "controls",
                "project.godot",
                "hold_toggle",
                "medium",
                f"hold only: {', '.join(held)}",
                "a hold-or-toggle option",
                f"{', '.join(held).capitalize()} must be held down; some players can't hold a key.",
                fix=state.option_fix("Hold or toggle option"),
                item="toggle",
            )


def motion(state: _State, luma: list[list[float]], seed: int, measured: bool) -> None:
    for path in state.code(r"\bshake"):
        honoured = re.search(
            r"screen_shake|reduced_motion|reduce_motion", state.scripts[path]
        )
        state.tally.check("motion", f"{path}:shake", bool(honoured))
        if not honoured:
            state.tally.add(
                "motion",
                path,
                "shake_setting",
                "medium",
                "shake always on",
                "scaled by the setting",
                "shakes the camera without reading the screen shake setting.",
                fix=CheckFix(
                    kind="agent",
                    label="Honour the screen shake setting",
                    request=f"In {path}, multiply the camera shake by Settings.screen_shake() "
                    "(0 turns it off) and skip it when Settings.reduced_motion() is on.",
                    targets=[path],
                ),
                item=f"{path}:shake",
            )
    for path in state.code(r"\bflash"):
        honoured = re.search(
            r"reduce_flashes|flashes_reduced|reduced_motion", state.scripts[path]
        )
        state.tally.check("motion", f"{path}:flash", bool(honoured))
        if not honoured:
            state.tally.add(
                "motion",
                path,
                "flash_setting",
                "low",
                "flashes always on",
                "softened by the setting",
                "flashes without reading the flash or reduced motion setting.",
                fix=CheckFix(
                    kind="agent",
                    label="Honour the flash setting",
                    request=f"In {path}, soften or skip the flash when "
                    "Accessibility.flashes_reduced() (or Settings.reduced_motion()) is on.",
                    targets=[path],
                ),
                item=f"{path}:flash",
            )
    if not measured:
        return
    rate, frame = flashes(luma)
    state.tally.check("motion", "flash_rate", rate <= FLASHES_PER_SECOND)
    if rate > FLASHES_PER_SECOND:
        state.tally.add(
            "motion",
            "project.godot",
            "photosensitivity",
            "high",
            f"{rate:g} flashes/s",
            f"≤ {FLASHES_PER_SECOND} flashes/s",
            f"The screen flashed {rate:g} times in one second at {frame / 60:.1f} s; more "
            "than three can trigger seizures in people with photosensitive epilepsy.",
            fix=CheckFix(
                kind="agent",
                label="Slow down the flashing",
                request=f"With seed {seed}, the screen flashes {rate:g} times a second at "
                f"{frame / 60:.1f} s of play. Find the effect (a light, a modulate, a "
                "particle burst), keep it under three flashes a second or lower its "
                "brightness change below 10 %, and skip it when the flash setting is on.",
            ),
            evidence={"t": round(frame / 60, 2), "seed": seed},
            item="flash_rate",
        )


def pause(state: _State, report: dict[str, Any]) -> None:
    has = "pause" in state.config.keys("input")
    result = str((report.get("checks") or {}).get("pause", ""))
    ok = has and not result.startswith("failed")
    state.tally.check("pause", "pause", ok)
    if not ok:
        state.tally.add(
            "pause",
            "project.godot",
            "pause",
            "high",
            result if has else "no pause action",
            "pause anywhere",
            "The game can't be paused at any moment."
            if not has
            else f"Pausing didn't work in play ({result}).",
            fix=CheckFix(
                kind="agent",
                label="Let players pause anywhere",
                request="Add a pause action (Escape, P and the Start button) that pauses "
                "the game at any moment and shows a pause menu with Resume and Settings.",
            ),
            item="pause",
        )


def menus(state: _State, probe: dict[str, Any]) -> None:
    core = any(
        "core/menus.gd" in str((c.get("colour_from") or {}).get("file", ""))
        for c in _controls(probe)
    )
    for control in _controls(probe):
        if not control.get("focusable") or control.get("disabled"):
            continue
        name = (
            str(control.get("accessible", "")).strip()
            or str(control.get("text", "")).strip()
            or str(control.get("tooltip", "")).strip()
        )
        key = f"{control.get('page')}:{control.get('node')}"
        state.tally.check("menus", key, bool(name))
        if name:
            continue
        label = (
            str(control.get("label", "")) if control.get("page") != "hud" else ""
        ) or str(control.get("name", ""))
        target = (
            _scene_edit(
                {
                    "kind": "scene",
                    "file": control.get("scene"),
                    "node": control.get("node"),
                },
                control,
            )
            if control.get("scene")
            else None
        )
        fix: CheckFix | None
        if target is not None:
            fix = text_fix(
                state.project,
                target[0],
                [{"node": target[1], "props": {"accessibility_name": f'"{label}"'}}],
                "Name menu controls for screen readers in Godot",
            )
        elif core:
            fix = state.feature_fix("Add the Accessibility settings") or CheckFix(
                kind="agent",
                label="Name menu controls for screen readers",
                request="Give every focusable menu control an accessibility_name: the "
                "text of the label beside it.",
            )
        else:
            fix = CheckFix(
                kind="agent",
                label="Name menu controls for screen readers",
                request=f"The {control.get('type')} {control.get('node')} on the "
                f"{control.get('page')} screen has no text, accessibility_name or tooltip; "
                f'set accessibility_name (for example "{label}").',
            )
        state.tally.add(
            "menus",
            res(str(control.get("scene", "")))
            or str((control.get("colour_from") or {}).get("file", "")).removeprefix(
                "res://"
            )
            or "project.godot",
            "accessible_name",
            "medium",
            "no name",
            "a name screen readers say",
            f'The {control.get("type")} for "{label}" on the {control.get("page")} screen '
            "has no name a screen reader can say.",
            subject=key,
            fix=fix,
            evidence=_where(control, 0),
            item=key,
        )


def long_text(
    state: _State, probe: dict[str, Any], seed: int, drawn: dict[str, Any] | None = None
) -> None:
    """Text 30 % longer and in right-to-left marks (Godot's pseudo-locale): what is cut
    off or runs off the screen, by node, with the windowed run's outlined screenshot."""
    found = probe.get("long_text") or {}
    if not found.get("ran"):
        return
    shots = {
        str(s.get("page", "")): str(s.get("shot", ""))
        for s in ((drawn or {}).get("long_text") or {}).get("screens", [])
        if isinstance(s, dict) and s.get("shot")
    }
    expansion = float(found.get("expansion", 0.3) or 0.3)
    for screen in found.get("screens", []):
        if not isinstance(screen, dict):
            continue
        page = str(screen.get("page", ""))
        problems = [p for p in screen.get("problems", []) if isinstance(p, dict)]
        failing = {str(p.get("node", "")) for p in problems}
        for index in range(int(screen.get("checked", 0) or 0) - len(failing)):
            state.tally.check("translation", f"{page}:fits:{index}", True)
        for problem in problems:
            node = str(problem.get("node", ""))
            key = f"{page}:{node}"
            words, severity = LONG_TEXT.get(
                str(problem.get("problem", "")), ("doesn't fit", "medium")
            )
            text = str(problem.get("text", ""))
            where = {**_where(problem, seed), "rect": problem.get("rect", [])}
            if shots.get(page):
                where["shot"] = shots[page]
            path = res(str(problem.get("scene", ""))) or "project.godot"
            state.tally.add(
                "translation",
                path,
                "long_text",
                severity,
                str(problem.get("problem", "")),
                "fits 30% longer text",
                f'"{text[:30]}" {words} when text is {expansion:.0%} longer and '
                f"right-to-left ({problem.get('type', 'Label')} {node} on the {page} "
                "screen).",
                subject=key,
                fix=_long_text_fix(state, problem, page, words),
                evidence=where,
                item=key,
            )


def _long_text_fix(
    state: _State, problem: dict[str, Any], page: str, words: str
) -> CheckFix:
    """A label cut off on one line wraps instead; anything else is an agent's change."""
    target = (
        _scene_edit(
            {
                "kind": "scene",
                "file": problem.get("scene"),
                "node": problem.get("node"),
            },
            problem,
        )
        if problem.get("scene")
        else None
    )
    if (
        target is not None
        and problem.get("type") == "Label"
        and problem.get("problem") == "cut_off"
        and not problem.get("autowrap")
    ):
        return text_fix(
            state.project,
            target[0],
            [
                {
                    "node": target[1],
                    "props": {
                        "autowrap_mode": AUTOWRAP_WORD_SMART,
                        "text_overrun_behavior": 0,
                    },
                }
            ],
            "Let cut-off labels wrap in Godot",
        )
    scene = res(str(problem.get("scene", "")))
    return CheckFix(
        kind="agent",
        label="Make room for longer text",
        request=f"The {problem.get('type', 'Label')} {problem.get('node')} "
        f'("{str(problem.get("text", ""))[:30]}") on the {page} screen {words} when '
        "the text is 30% longer and right-to-left, as translations make it. Give it "
        "room: let it wrap, let its container grow or scroll, or shorten the layout's "
        "fixed sizes; keep it reading well in English too.",
        targets=[scene] if scene else [],
    )


def audit(
    project: Path, *, window: bool = True, seconds: float = check_runs.HEADLESS_SECONDS
) -> Report:
    """Play, measure and judge; never changes a file."""
    project = project.resolve()
    state = _State(project)
    errors: list[str] = []
    run = check_runs.headless(project, "access", seconds=seconds)
    if run.error:
        errors.append(run.error)
    drawn = (
        check_runs.windowed(project, "access", luma=True)
        if window and not run.error
        else check_runs.Run(error="")
    )
    if drawn.error:
        errors.append(drawn.error)
    probe = run.probe
    contrast_and_size(state, probe, run.seed)
    options(state, probe)
    colours(state)
    captions(state)
    controls(state, probe)
    motion(
        state,
        list(drawn.probe.get("luma", [])),
        run.seed,
        bool(drawn.probe.get("luma")),
    )
    if not run.error:
        pause(state, run.report)
        menus(state, probe)
        long_text(state, probe, run.seed, drawn.probe)
    report = state.tally.report("access", project, errors)
    report.facts = {
        "target_height": state.target_height,
        "min_text_px": state.min_px,
        "seed": run.seed,
        "features": state.features,
        "pages": [p.get("id") for p in probe.get("pages", []) if isinstance(p, dict)],
        "controls": len(_controls(probe)),
        "flashes_measured": bool(drawn.probe.get("luma")),
        "runs": {
            "headless": run.report.get("out", ""),
            "window": drawn.report.get("out", ""),
        },
    }
    return report
