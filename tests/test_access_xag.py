"""The accessibility check's findings by Xbox Accessibility Guideline."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bridge.check_runner import for_agent
from joinery import access, access_xag, checks_core


def _finding(area: str, check: str, subject: str = "") -> dict[str, Any]:
    return {
        "id": f"{area}-{check}-{subject}",
        "area": area,
        "check": check,
        "subject": subject,
    }


def test_the_list_is_xag_3_2_numbered_101_to_123() -> None:
    assert list(access_xag.GUIDELINES) == [str(n) for n in range(101, 124)]
    assert access_xag.GUIDELINES["118"] == "Photosensitivity"
    assert access_xag.VERSION == "3.2"


def test_every_check_and_option_of_the_access_check_has_a_guideline() -> None:
    checks = {
        "contrast",
        "backdrop",
        "text_size",
        "colour_only",
        "palette_pair",
        "voice_subtitles",
        "remap",
        "controller",
        "hold_toggle",
        "shake_setting",
        "flash_setting",
        "photosensitivity",
        "pause",
        "accessible_name",
        "long_text",
    }
    assert checks == set(access_xag.BY_CHECK)
    assert set(access.OPTIONS) == set(access_xag.BY_OPTION)
    # Every guideline a finding can land on says what Joinery checks for it.
    landed = set(access_xag.BY_CHECK.values()) | set(access_xag.BY_OPTION.values())
    assert landed == set(access_xag.CHECKS) == set(access_xag.BY_AREA)
    areas = {a for names in access_xag.BY_AREA.values() for a in names}
    assert areas == set(access.AREAS)


def test_findings_are_grouped_and_coverage_says_what_ran() -> None:
    report = {
        "areas": [
            {"id": "contrast"},
            {"id": "colour"},
            {"id": "text"},
            {"id": "motion"},
        ],
        "findings": [
            _finding("contrast", "contrast"),
            _finding("contrast", "option", "high_contrast"),
            _finding("motion", "photosensitivity"),
            _finding("colour", "option", "colour_filter"),
        ],
    }
    xag = access_xag.coverage(report)
    assert (xag["checked"], xag["total"]) == (9, 23)
    rows = {g["id"]: g for g in xag["guidelines"]}
    assert rows["102"]["status"] == "fails"
    assert rows["102"]["findings"] == [
        "contrast-contrast-",
        "contrast-option-high_contrast",
    ]
    assert rows["103"]["status"] == "fails" and rows["118"]["status"] == "fails"
    # Measured and clean.
    assert rows["101"]["status"] == "passes" and rows["117"]["status"] == "passes"
    # Joinery checks it, but the area wasn't measured this time.
    assert rows["104"]["status"] == "not_run"
    # A person judges it.
    assert rows["109"]["status"] == "not_checked" and rows["109"]["checks"] == []


def test_a_real_report_carries_it_and_the_agent_gets_the_failing_ones() -> None:
    tally = checks_core.Tally(access.AREAS)
    tally.check("text", "a", True)
    tally.add("pause", "project.godot", "pause", "high", "no pause", "pause", "x")
    report = tally.report("access", Path("/game"), [])
    report.facts["xag"] = access_xag.coverage(report.model_dump())
    data = for_agent(report)
    assert data["facts"]["xag"]["failing"] == [
        {
            "id": "108",
            "name": "Game difficulty options",
            "findings": [report.findings[0].id],
        }
    ]
    assert data["facts"]["xag"]["checked"] == 9
