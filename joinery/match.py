"""Make everything match: audit the whole project against the art bible.

`joinery match` measures every asset, scene and theme the bible's checked rules
cover, without a model: palette distance (CIEDE2000) of textures, texture sizes
and texel density, model scale, pivots and budgets, material ranges, the look
of each Environment, UI theme colours, fonts and text sizes, and sound
loudness. Each finding says how bad it is, what was measured, what the bible
asks for, and the typed operation that would bring it in line in the tool that
owns it (`joinery.match_plan` turns those into reviewed changes). Each area
gets one score: the share of what it checked that already matches.

What the user said not to touch is never measured: the bible's `exclude`,
`[match] exclude` and `[match] final` in joinery.toml.
"""

from __future__ import annotations

import hashlib
import os
import tomllib
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from joinery.art_approval import approved
from joinery.bible_rules import (
    BibleRules,
    Measure,
    _in_folders,
    load_rules,
)
from joinery.locking import claimed_outputs
from joinery.recipe import load_recipe

#: Areas in the order the card shows them, with their names.
AREAS: dict[str, str] = {
    "palette": "Palette",
    "textures": "Textures",
    "models": "Models",
    "materials": "Materials",
    "lighting": "Lighting",
    "ui": "UI",
    "sound": "Sound",
}
Severity = Literal["high", "medium", "low"]
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
#: Never audited: Godot's cache, Joinery's own state, plugins and recipes.
_SKIP_DIRS = {".godot", ".joinery", ".git", "addons", "recipes", "node_modules"}
_IMAGES = {".png", ".jpg", ".jpeg", ".webp"}
_AUDIO = {".wav", ".ogg"}
_GODOT_TEXT = {".tres", ".tscn"}
#: Apps that make each tool's sources, for the task card.
APP_OF = {"krita": "krita", "blender": "blender", "surge-xt": "surge-xt"}
SOUND_TOOLS = {"surge-xt", "supercollider"}


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Fix(_Model):
    """One typed operation that brings a finding in line."""

    server: str
    tool: str
    arguments: dict[str, Any]
    #: Apps it runs ("krita", "blender", "godot", …), for the task card.
    apps: list[str]
    #: The project file it changes; its task claims it.
    source: str
    #: What it does, in a few words ("Palette map in Krita").
    label: str
    #: Further files it rewrites (exports), claimed with the source.
    outputs: list[str] = []


class Finding(_Model):
    id: str
    area: str
    path: str
    #: The node, material, key or section inside the file ("" for the whole file).
    subject: str = ""
    check: str
    severity: Severity
    measured: str
    target: str
    message: str
    fix: Fix | None = None
    #: Why Joinery can't fix it itself (an imported file, a tool with no typed op).
    needs_you: str = ""


class AreaScore(_Model):
    id: str
    label: str
    score: int
    checked: int
    failing: int
    fixable: int


class Audit(_Model):
    project: str
    approved: bool
    #: The bible has checked rules at all.
    rules: bool
    areas: list[AreaScore] = []
    summary: str = ""
    findings: list[Finding] = []
    #: Files left alone because the user said so ([match] exclude / final).
    excluded: list[str] = []
    #: Measurements that could not run (no Docker, an unreadable file).
    errors: list[str] = []


class Settings(_Model):
    exclude: list[str] = []
    final: list[str] = []


def settings(project: Path) -> Settings:
    """``[match]`` in joinery.toml: ``exclude`` folders or files, ``final`` assets."""
    try:
        raw = tomllib.loads((project / "joinery.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Settings()
    table = raw.get("match", {})
    if not isinstance(table, dict):
        return Settings()

    def paths(key: str) -> list[str]:
        value = table.get(key, [])
        if not isinstance(value, list):
            return []
        return [
            str(v).replace("\\", "/").removeprefix("res://").strip("/")
            for v in value
            if isinstance(v, str) and v.strip()
        ]

    return Settings(exclude=paths("exclude"), final=paths("final"))


def _set_list(project: Path, key: str, path: str, add: bool) -> list[str]:
    """Add or remove one path in ``[match] <key>``, keeping the rest of the file."""
    config = project / "joinery.toml"
    text = config.read_text(encoding="utf-8") if config.is_file() else ""
    current = getattr(settings(project), key)
    clean = path.replace("\\", "/").removeprefix("res://").strip("/")
    wanted = (
        sorted(set(current) | {clean}) if add else [p for p in current if p != clean]
    )
    line = f"{key} = [" + ", ".join(f'"{p}"' for p in wanted) + "]"
    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines) if l.strip() == "[match]"), -1)
    if start < 0:
        lines += ["", "[match]", line]
    else:
        end = next(
            (
                i
                for i in range(start + 1, len(lines))
                if lines[i].lstrip().startswith("[")
            ),
            len(lines),
        )
        found = next(
            (i for i in range(start + 1, end) if lines[i].split("=")[0].strip() == key),
            -1,
        )
        if found >= 0:
            lines[found] = line
        else:
            lines.insert(start + 1, line)
    config.write_text("\n".join(lines).strip("\n") + "\n", encoding="utf-8")
    return wanted


def mark_final(project: Path, path: str, final: bool = True) -> list[str]:
    """The user marks an asset final (``[match] final``): match never changes it."""
    return _set_list(project, "final", path, final)


# --- the project -------------------------------------------------------------------


class Owner(_Model):
    tool: str
    #: The native document the typed operations edit (.kra, .blend, the audio itself).
    source: str
    exports: list[str]


def owners(project: Path) -> dict[str, Owner]:
    """Each recipe output -> the tool and native document that make it."""
    from joinery import tools

    found: dict[str, Owner] = {}
    for output, (tool, recipe_path) in claimed_outputs(project).items():
        try:
            recipe = load_recipe(project / recipe_path)
        except (OSError, ValueError):
            continue
        suffix = tools.REGISTRY[tool].suffix if tool in tools.REGISTRY else ""
        native = next((o for o in recipe.outputs if suffix and o.endswith(suffix)), "")
        if not native:
            native = next(
                (s for s in recipe.sources if suffix and s.endswith(suffix)), output
            )
        found[output] = Owner(
            tool=tool,
            source=native,
            exports=[o for o in recipe.outputs if o != native],
        )
    return found


def project_files(project: Path) -> list[str]:
    files: list[str] = []
    for root, dirs, names in os.walk(project):
        dirs[:] = sorted(
            d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")
        )
        base = Path(root)
        for name in sorted(names):
            if name.startswith(".") or name.endswith((".import", ".uid")):
                continue
            files.append((base / name).relative_to(project).as_posix())
    return files


def _suffix(path: str) -> str:
    return PurePosixPath(path).suffix.lower()


def _id(*parts: str) -> str:
    return hashlib.sha1("\x00".join(parts).encode()).hexdigest()[:12]


class _Audit:
    """One audit's state: rules, owners, and the items each area checked."""

    def __init__(self, project: Path, rules: BibleRules, measure: Measure) -> None:
        self.project = project
        self.rules = rules
        self.measure = measure
        self.owners = owners(project)
        self.checked: dict[str, set[str]] = {area: set() for area in AREAS}
        self.findings: list[Finding] = []
        self.errors: list[str] = []

    def finding(
        self,
        area: str,
        path: str,
        check: str,
        severity: Severity,
        measured: str,
        target: str,
        message: str,
        *,
        subject: str = "",
        fix: Fix | None = None,
        needs_you: str = "",
    ) -> None:
        if fix is None and not needs_you:
            needs_you = "Joinery has no typed operation for this yet."
        self.findings.append(
            Finding(
                id=_id(area, path, subject, check),
                area=area,
                path=path,
                subject=subject,
                check=check,
                severity=severity,
                measured=measured,
                target=target,
                message=message,
                fix=fix,
                needs_you="" if fix is not None else needs_you,
            )
        )

    def owner(self, path: str, tools: set[str]) -> tuple[Owner | None, str]:
        """The owner when one of ``tools`` made it, else why it can't be fixed here."""
        owner = self.owners.get(path)
        if owner is None:
            return (
                None,
                "It has no recipe (you imported it), so Joinery won't change it.",
            )
        if owner.tool not in tools:
            return None, (
                f"It was made in {owner.tool}, which has no typed operation for this; "
                "change it there."
            )
        if not (self.project / owner.source).is_file():
            return None, f"Its source {owner.source} is missing."
        return owner, ""


# --- the audit -----------------------------------------------------------------------


def _scores(audit: _Audit) -> list[AreaScore]:
    rows: list[AreaScore] = []
    for area, label in AREAS.items():
        checked = len(audit.checked[area])
        if checked == 0:
            continue
        failing_items = {
            f.path if area not in {"materials", "lighting"} else f"{f.path}#{f.subject}"
            for f in audit.findings
            if f.area == area
        }
        failing = min(len(failing_items), checked)
        rows.append(
            AreaScore(
                id=area,
                label=label,
                score=round(100 * (checked - failing) / checked),
                checked=checked,
                failing=failing,
                fixable=sum(
                    1 for f in audit.findings if f.area == area and f.fix is not None
                ),
            )
        )
    return rows


def summary_line(areas: list[AreaScore]) -> str:
    return " · ".join(f"{a.label} {a.score}%" for a in areas)


def is_excluded(path: str, rules: BibleRules, chosen: Settings) -> bool:
    folders = [*rules.exclude, *chosen.exclude]
    return (
        _in_folders(folders, path)
        or path in chosen.final
        or any(path.startswith(f.rstrip("/") + "/") for f in chosen.final)
    )


async def audit(
    project: Path, measure: Measure, *, areas: Iterable[str] | None = None
) -> Audit:
    """Measure the whole project against the bible; never changes a file."""
    project = project.resolve()
    result = Audit(project=str(project), approved=approved(project), rules=False)
    try:
        rules = load_rules(project)
    except ValueError as error:
        result.errors.append(str(error))
        return result
    bible = project / "art/bible.md"
    result.rules = bible.is_file() and "```toml" in bible.read_text(errors="replace")
    chosen = settings(project)
    state = _Audit(project, rules, measure)
    paths: list[str] = []
    no_rules = BibleRules(exclude=[])
    for path in project_files(project):
        if path == "art/bible.md":
            continue
        owner = state.owners.get(path)
        # A file whose native source is final is left alone too.
        sources = [path] if owner is None else [path, owner.source]
        if any(is_excluded(p, no_rules, chosen) for p in sources):
            result.excluded.append(path)  # the user's own "don't touch"
            continue
        if is_excluded(path, rules, Settings()):
            continue  # the bible's exclude: reference boards
        paths.append(path)
    from joinery import match_checks

    await match_checks.images(state, paths)
    match_checks.models(state, paths)
    match_checks.look(state, paths)
    await match_checks.sound(state, paths)
    wanted = set(areas or AREAS)
    state.findings = [f for f in state.findings if f.area in wanted]
    state.findings.sort(
        key=lambda f: (SEVERITY_ORDER[f.severity], list(AREAS).index(f.area), f.path)
    )
    result.findings = state.findings
    result.areas = [a for a in _scores(state) if a.id in wanted]
    result.summary = summary_line(result.areas)
    result.errors = state.errors
    return result
