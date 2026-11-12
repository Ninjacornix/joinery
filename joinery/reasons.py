"""Why a change goes back to its author, as items the agent can work through.

Every check that can fail a submit (and the reviewer's blocking notes) reaches the
agent as :class:`Reason` items of one shape: a stable rule id, where it is (file,
node or part), what was measured, what is expected, and a one-line fix hint. The
plain ``message`` beside them stays what the user may read; the items are for the
agent and for counting which rules send changes back most (``joinery metrics
--rules``).

Rule ids are ``<check>.<what>`` and never change once released: metrics files
keep them, so a renamed id would split one rule's history in two.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Reason:
    """One thing a change fails, in a fixed shape."""

    #: Stable id, ``<check>.<what>`` ("art_bible.texture_size").
    rule: str
    #: The project file it is in ("" when it is not one file's).
    file: str = ""
    #: The node, object, material or part inside that file ("" for the whole file).
    node: str = ""
    #: What the check measured ("2048x2048"), in the check's own words.
    measured: str = ""
    #: What the rule expects ("at most 1024 px").
    expected: str = ""
    #: One line on how it gets fixed.
    fix: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "rule": self.rule,
            "file": self.file,
            "node": self.node,
            "measured": self.measured,
            "expected": self.expected,
            "fix": self.fix,
        }


def _clip(text: str, limit: int = 300) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _split(line: str) -> tuple[str, str]:
    """A ``"path: what is wrong"`` line as (path, what)."""
    if ":" not in line:
        return "", line.strip()
    path, _, rest = line.partition(":")
    return path.strip(), rest.strip()


# --- the art bible's measured rules ----------------------------------------------

#: (rule, pattern on the text after "path: ", fix). Named groups: m (measured),
#: e (expected), n (node or part). The first match wins.
_BIBLE: tuple[tuple[str, re.Pattern[str], str], ...] = tuple(
    (rule, re.compile(pattern), fix)
    for rule, pattern, fix in (
        (
            "art_bible.file_name",
            r"file name '(?P<m>[^']*)' does not follow the art bible's pattern (?P<e>.+)",
            "Rename the file (and its lock entry) to match the pattern.",
        ),
        (
            "art_bible.texture_size",
            r"(?P<m>\d+x\d+), over the art bible's (?P<e>\d+ px) limit",
            "Export the texture smaller from the tool that made it.",
        ),
        (
            "art_bible.power_of_two",
            r"(?P<m>\d+x\d+) is not a power of two on both sides",
            "Resize the canvas to a power of two on both sides.",
        ),
        (
            "art_bible.palette",
            (
                r"(?P<m>\d+%) of its colours are more than ΔE \S+ from the palette "
                r"\(allowed (?P<e>\d+%)\)"
            ),
            "Repaint or recolour with the art bible's palette.",
        ),
        (
            "art_bible.albedo",
            (
                r"(?P<m>\d+%) of the albedo is near pure black or white .*allowed "
                r"(?P<e>\d+%)\)"
            ),
            "Lift the darkest and lower the brightest albedo values.",
        ),
        (
            "art_bible.sprite_colours",
            r"(?P<m>\d+) distinct colours, over the art bible's (?P<e>\d+) per sprite",
            "Reduce the sprite to the bible's colour count.",
        ),
        (
            "art_bible.true_peak",
            r"true peak (?P<m>\S+ dBTP), above the art bible's (?P<e>\S+ dBTP)",
            "Lower the gain or add a limiter in the patch and render again.",
        ),
        (
            "art_bible.loudness",
            r"(?P<m>\S+ LUFS), the art bible asks for (?P<e>.+)",
            "Change the patch's output level and render again.",
        ),
        (
            "art_bible.audio_format",
            (
                r"(?P<m>\S+ file|\d+ Hz|\d+ channel\(s\)|\d+-bit), the art bible "
                r"(?:asks for|allows) (?P<e>.+)"
            ),
            "Render again in the format the art bible asks for.",
        ),
        (
            "art_bible.triangles",
            r"(?P<m>\d+ triangles), over the art bible's (?P<e>\d+) budget",
            "Reduce the model's triangles (decimate or simplify in Blender).",
        ),
        (
            "art_bible.bones",
            (
                r"(?:armature '(?P<n>[^']*)' has )?(?P<m>\d+ (?:deform )?bones)(?: in a "
                r"skin)?, over the art bible's (?P<e>\d+) budget"
            ),
            "Remove or merge deform bones in Blender.",
        ),
        (
            "art_bible.applied_scale",
            r"scale is not applied on (?P<n>.+); the art bible asks for (?P<e>scale 1)",
            "Apply scale on those objects in Blender.",
        ),
        (
            "art_bible.pivot",
            r"(?P<n>.+?) pivot is not (?P<e>.+?): (?P<m>.+)",
            "Move the object's origin as the art bible asks, in Blender.",
        ),
        (
            "art_bible.texel_density",
            (
                r"(?P<n>.+?) texel density (?P<m>\d+ px/m), the art bible asks for "
                r"(?P<e>.+)"
            ),
            "Rescale the UVs or the texture to the bible's texel density.",
        ),
        (
            "art_bible.naming",
            r"(?P<n>.+) do not follow the art bible's naming pattern (?P<e>.+)",
            "Rename those to match the pattern.",
        ),
        (
            "art_bible.roughness",
            (
                r"material '(?P<n>[^']*)' roughness (?P<m>\S+), the art bible allows "
                r"(?P<e>\S+)"
            ),
            "Set the material's roughness inside the allowed range.",
        ),
        (
            "art_bible.metallic",
            (
                r"material '(?P<n>[^']*)' (?:metallic (?P<m>\S+), the art bible allows "
                r"(?P<e>\S+)|is metallic \((?P<m2>\S+)\) but its name does not say metal)"
            ),
            "Make it non-metallic, or name it as metal if it is.",
        ),
        (
            "art_bible.base_colour",
            (
                r"material '(?P<n>[^']*)' base colour is near pure \w+ \(luminance "
                r"(?P<m>\S+); the art bible allows (?P<e>[^)]+)\)"
            ),
            "Move the base colour away from pure black or white.",
        ),
        (
            "art_bible.contrast",
            (
                r"(?P<n>.+?) text on its \w+ background has contrast (?P<m>\S+), below "
                r"the art bible's (?P<e>\S+)"
            ),
            "Raise the contrast between the text and its background.",
        ),
        (
            "art_bible.text_size",
            r"font size (?P<m>\d+) \((?P<n>[^)]*)\) is below the art bible's (?P<e>.+)",
            "Raise the font size to at least the bible's minimum.",
        ),
        (
            "art_bible.unmeasured",
            r"(?P<m>.*could not be (?:measured|read|counted).*)",
            "Check the file opens in its tool and is saved in a format it exports.",
        ),
    )
)


def art_bible(line: str) -> Reason:
    """One measured art-bible finding (``"path: what"``) as a reason."""
    path, text = _split(line)
    for rule, pattern, fix in _BIBLE:
        match = pattern.search(text)
        if match is None:
            continue
        groups = {k: v for k, v in match.groupdict().items() if v}
        measured = groups.get("m") or groups.get("m2") or text
        expected = groups.get("e", "")
        return Reason(
            rule,
            path,
            node=_clip(groups.get("n", "").strip("'"), 120),
            measured=_clip(measured, 120),
            expected=_clip(expected, 120),
            fix=fix,
        )
    return Reason(
        "art_bible.rule",
        path,
        measured=_clip(text),
        fix="Change the asset so it meets the art bible's rule.",
    )


# --- provenance, dependencies, references -----------------------------------------


def provenance(line: str) -> Reason:
    """One lock-gate failure (``joinery.lockfile.check_assets``) as a reason."""
    path, text = _split(line)
    if text.startswith("no ") and text.endswith(" entry"):
        return Reason(
            "provenance.no_lock_entry",
            path,
            measured="no joinery.lock entry",
            expected="an entry with origin and a replayable recipe",
            fix="Make the asset through a tool so Joinery records it, or remove it.",
        )
    if text == "missing":
        return Reason(
            "provenance.lock_missing",
            path,
            measured="joinery.lock missing",
            expected="joinery.lock at the project root",
            fix="Make assets through the tools; they write the lock.",
        )
    return Reason(
        "provenance.entry",
        path,
        measured=_clip(text),
        expected="a valid lock entry whose source and recipe exist",
        fix="Remake the asset with its tool so its lock entry is written again.",
    )


def dependency(line: str) -> Reason:
    path, text = _split(line)
    return Reason(
        "dependencies.denied",
        path,
        measured=_clip(text),
        expected="no package on Joinery's denylist",
        fix="Remove that package; do the work with the project's own tools.",
    )


def references(gaps: Mapping[str, Iterable[str]]) -> list[Reason]:
    """Scenes that load files nobody is adding, one reason per missing file."""
    return [
        Reason(
            "references.missing_file",
            scene,
            measured=f"loads {path}",
            expected="the file exists in the project or in this change",
            fix="Make that file in this change, or point the scene at one the "
            "project has.",
        )
        for scene, paths in sorted(gaps.items())
        for path in paths
    ]


# --- Godot and tests ------------------------------------------------------------

_GODOT_AT = re.compile(r"(res://[^\s:()]+)(?::(\d+))?")
_GODOT_ERROR = re.compile(r"(SCRIPT ERROR|Parse Error|ERROR)[^\n]*")


def godot_import(output: str) -> list[Reason]:
    """Godot's import errors, one reason per file it names (or one for the run)."""
    reasons: list[Reason] = []
    seen: set[tuple[str, str]] = set()
    lines = output.splitlines()
    for index, line in enumerate(lines):
        error = _GODOT_ERROR.search(line)
        if error is None:
            continue
        # Godot names the file on the error line or the "at:" line after it.
        nearby = " ".join(lines[index : index + 2])
        where = _GODOT_AT.search(nearby)
        file = where.group(1).removeprefix("res://") if where else ""
        node = f"line {where.group(2)}" if where and where.group(2) else ""
        key = (file, node)
        if key in seen:
            continue
        seen.add(key)
        reasons.append(
            Reason(
                "godot_import.parse_error"
                if "Parse Error" in error.group(0)
                else "godot_import.script_error",
                file,
                node=node,
                measured=_clip(error.group(0), 200),
                expected="Godot imports and parses it with no errors",
                fix="Fix the script or resource at that line.",
            )
        )
        if len(reasons) >= 10:
            break
    return reasons or [
        Reason(
            "godot_import.failed",
            measured=_clip(output[-300:] or "Godot exited with an error."),
            expected="Godot imports the project with no errors",
            fix="Read Godot's output and fix the file it names.",
        )
    ]


_GUT_FAILED = re.compile(r"(test_\w+)")


def tests(output: str) -> list[Reason]:
    names = list(dict.fromkeys(_GUT_FAILED.findall(output)))[:10]
    if not names:
        return [
            Reason(
                "tests.failed",
                measured=_clip(output[-300:] or "GUT exited with an error."),
                expected="every GUT test passes",
                fix="Fix the code the failing test checks (not the test).",
            )
        ]
    return [
        Reason(
            "tests.failed",
            node=name,
            measured="failed",
            expected="passes",
            fix="Fix the code the failing test checks (not the test).",
        )
        for name in names
    ]


# --- everything else -------------------------------------------------------------

#: For a problem that names only files: the rule and fix hint, by check.
GENERIC: dict[str, tuple[str, str, str]] = {
    "protected": (
        "protected.path",
        "not changed by an agent",
        "Leave project policy and MCP configuration as they are.",
    ),
    "live_conflict": (
        "live_conflict.scene",
        "a scene changes live or as a saved file in one change, not both",
        "Change that scene one way only.",
    ),
    "conflict": (
        "conflict.other_change",
        "the file as the project has it now",
        "Redo your part on top of the project's version.",
    ),
    "art_gate": (
        "art_gate.unapproved",
        "an approved art bible",
        "Stop; the user approves the art direction first (/art).",
    ),
    "checks": (
        "checks.unavailable",
        "Joinery's checks run",
        "Stop; nothing in the change can fix this.",
    ),
    "review": (
        "review.could_not_open",
        "the review opens",
        "Stop; nothing in the change can fix this.",
    ),
    "stopped": ("turn.stopped", "", "Stop."),
    "complex_models": (
        "complex_models.placeholder_shapes",
        "one model made in Blender (or a kit model)",
        "Make the object as a model in Blender and place that instead.",
    ),
    "references": (
        "references.missing_file",
        "every file a scene loads exists in the project or in this change",
        "Make the missing file in this change, or point the scene at one that exists.",
    ),
    "assets": (
        "provenance.entry",
        "a valid joinery.lock entry for every asset",
        "Make the asset through a tool so Joinery records it, or remove it.",
    ),
    "dependencies": (
        "dependencies.denied",
        "no package on Joinery's denylist",
        "Remove that package.",
    ),
    "art_bible": (
        "art_bible.rule",
        "the approved art bible's rules",
        "Change the asset so it meets the art bible's rule.",
    ),
    "godot_import": (
        "godot_import.failed",
        "Godot imports the project with no errors",
        "Fix the file Godot names.",
    ),
    "tests": (
        "tests.failed",
        "every GUT test passes",
        "Fix the code the failing test checks (not the test).",
    ),
    "reviewer": (
        "reviewer.other",
        "",
        "Fix it in the same change, or leave it and say why in one line.",
    ),
}


def generic(check: str, message: str, paths: Iterable[str]) -> list[Reason]:
    """Reasons for a problem with no finer ones: one per file it names."""
    rule, expected, fix = GENERIC.get(
        check,
        (
            f"{check}.failed" if "." not in check else check,
            "",
            "Fix what the message says, then submit again.",
        ),
    )
    files = list(paths) or [""]
    return [
        Reason(rule, file, measured=_clip(message), expected=expected, fix=fix)
        for file in files
    ]


def variant(line: str) -> Reason:
    """A scene variant that doesn't fit its base (``joinery.scene_variants.check``)."""
    file, what = _split(line)
    node = re.search(r"node (\S+) isn't in", what)
    if "doesn't exist" in what:
        return Reason(
            "variants.missing_base",
            file,
            measured=_clip(what),
            expected="a base scene in the project",
            fix="Point the variant at an existing scene, or add the base in this change.",
        )
    if "inherit from itself" in what:
        return Reason(
            "variants.cycle",
            file,
            measured=_clip(what),
            expected="a base that doesn't inherit the variant",
            fix="Inherit from another scene.",
        )
    return Reason(
        "variants.unknown_node",
        file,
        node=node.group(1) if node else "",
        measured=_clip(what),
        expected="a node the base scene has",
        fix="Name a node the base has, or give the new node a type.",
    )


def saves(save: str, kind: str, what: str, version: int) -> Reason:
    """A kept save that no longer loads (``load_failed``) or loses data (``data_lost``)
    in the changed game (``joinery.saves``)."""
    return Reason(
        "saves." + kind,
        save,
        measured=_clip(what),
        expected="the save loads with all its data",
        fix=(
            f"Raise save/data_version in project.godot to {version + 1} and add step "
            f"{version} in res://game/save_migrations.gd that turns the old save into "
            "the new shape, in this change."
        ),
    )


#: The reviewer's categories for a blocking note (``report_review``'s ``rule``).
REVIEW_RULES = ("request", "art_bible", "brief", "broken", "work_lost", "other")


def reviewer(text: str, file: str = "", category: str = "") -> Reason:
    return Reason(
        "reviewer." + (category if category in REVIEW_RULES else "other"),
        file,
        measured=_clip(text, 400),
        fix="Fix it in the same change, or leave it and say why in one line.",
    )


def rules_of(problems: Iterable[Any]) -> list[str]:
    """Every rule id in ``problems`` (each a ``joinery.turn.Problem``), one per item."""
    return [reason.rule for problem in problems for reason in problem.reasons()]
