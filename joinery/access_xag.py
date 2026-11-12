"""The accessibility check's findings by Xbox Accessibility Guideline (XAG).

Microsoft's Xbox Accessibility Guidelines, version 3.2 (published 8 June 2023), number
23 guidelines, 101 to 123. The list below was read from Microsoft Learn's XAG version
history and guideline pages on 2026-11-12 (ADR 0067). Which Joinery checks speak to which
guideline is Joinery's own reading, not Microsoft's: a finding is filed under the
guideline it most plainly fails. Most guidelines need a person to judge (objective
clarity, haptics, customer support); the coverage view says which ones Joinery measures
and which it leaves to you.
"""

from __future__ import annotations

from typing import Any

VERSION = "3.2"
SOURCE = "https://learn.microsoft.com/en-us/gaming/accessibility/guidelines"
#: Number -> name, as Microsoft Learn titles them.
GUIDELINES: dict[str, str] = {
    "101": "Text display",
    "102": "Contrast",
    "103": "Additional channels for visual and audio cues",
    "104": "Subtitles and captions",
    "105": "Audio accessibility",
    "106": "Screen narration",
    "107": "Input",
    "108": "Game difficulty options",
    "109": "Objective clarity",
    "110": "Haptic feedback",
    "111": "Audio descriptions",
    "112": "UI navigation",
    "113": "UI focus handling",
    "114": "UI context",
    "115": "Error messages and destructive actions",
    "116": "Time limits",
    "117": "Visual distractions and motion settings",
    "118": "Photosensitivity",
    "119": "Speech-to-text and text-to-speech",
    "120": "Communication experiences",
    "121": "Accessible feature documentation",
    "122": "Accessible customer support",
    "123": "Mental health best practices",
}
#: What Joinery measures for each guideline it checks, in a few words.
CHECKS: dict[str, list[str]] = {
    "101": ["text size", "a text size option", "text 30% longer and right-to-left"],
    "102": ["text contrast", "HUD text backdrops", "a high contrast option"],
    "103": ["colour-only signals", "colour-blind palette pairs", "a colour filter"],
    "104": ["subtitles for voice", "a subtitles option"],
    "106": ["names screen readers say"],
    "107": ["remapping", "gamepad bindings", "hold or toggle"],
    "108": ["pause anywhere", "a difficulty or game speed option"],
    "117": ["shake that honours the setting", "shake and motion options"],
    "118": ["flashes per second", "flashes that honour the setting"],
}
#: A finding's check -> its guideline; options by the setting they name.
BY_CHECK: dict[str, str] = {
    "text_size": "101",
    "long_text": "101",
    "contrast": "102",
    "backdrop": "102",
    "colour_only": "103",
    "palette_pair": "103",
    "voice_subtitles": "104",
    "accessible_name": "106",
    "remap": "107",
    "controller": "107",
    "hold_toggle": "107",
    "pause": "108",
    "shake_setting": "117",
    "flash_setting": "118",
    "photosensitivity": "118",
}
BY_OPTION: dict[str, str] = {
    "text_scale": "101",
    "high_contrast": "102",
    "colour_filter": "103",
    "subtitles": "104",
    "game_speed": "108",
    "screen_shake": "117",
    "reduced_motion": "117",
    "reduce_flashes": "118",
}
#: The check's areas that measure each guideline: it passes when they ran clean.
BY_AREA: dict[str, list[str]] = {
    "101": ["text", "translation"],
    "102": ["contrast"],
    "103": ["colour"],
    "104": ["captions"],
    "106": ["menus"],
    "107": ["controls"],
    "108": ["pause", "assists"],
    "117": ["motion"],
    "118": ["motion"],
}


def guideline_of(finding: dict[str, Any]) -> str:
    """The guideline a finding is filed under ("" when none fits)."""
    check = str(finding.get("check", ""))
    if check == "option":
        return BY_OPTION.get(str(finding.get("subject", "")), "")
    return BY_CHECK.get(check, "")


def coverage(report: dict[str, Any]) -> dict[str, Any]:
    """Every guideline with what Joinery checks for it and how this report went.

    status: ``fails`` (findings filed under it), ``passes`` (its areas were measured and
    found nothing), ``not_run`` (Joinery checks it, but those areas weren't measured this
    time) or ``not_checked`` (a person judges it)."""
    measured = {str(a.get("id", "")) for a in report.get("areas", []) or []}
    filed: dict[str, list[str]] = {number: [] for number in GUIDELINES}
    for finding in report.get("findings", []) or []:
        number = guideline_of(finding)
        if number:
            filed[number].append(str(finding.get("id", "")))
    rows = []
    for number, name in GUIDELINES.items():
        checks = CHECKS.get(number, [])
        if filed[number]:
            status = "fails"
        elif not checks:
            status = "not_checked"
        elif any(area in measured for area in BY_AREA.get(number, [])):
            status = "passes"
        else:
            status = "not_run"
        rows.append(
            {
                "id": number,
                "name": name,
                "checks": checks,
                "status": status,
                "findings": filed[number],
            }
        )
    return {
        "version": VERSION,
        "source": SOURCE,
        "checked": sum(1 for number in GUIDELINES if CHECKS.get(number)),
        "total": len(GUIDELINES),
        "guidelines": rows,
    }
