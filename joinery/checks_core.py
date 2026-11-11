"""What the accessibility and performance checks share: findings, scores and fixes.

`joinery access` and `joinery perf` measure the game, not the editor, and each finding
says how bad it is, what was measured (with the scene, node, moment and seed where it
was seen), what it should be, and how it gets fixed:

* ``typed``: a known operation in the app that owns the file, as for `joinery match`
  (a text patch through the Godot MCP, a Krita resize, a Blender decimate). These run as
  scripted tasks of the crew, each a small reviewed change.
* ``agent``: code has to change. The check's read-only agent (the ``accessibility`` or
  ``performance`` role) reads the evidence and words the fix; a worker makes it as a
  normal reviewed change.
* ``feature``: a game feature adds what is missing (the Accessibility settings page,
  controller support). Adding a feature is the user's own step: the sidebar shows its
  plan, and Apply makes it.

Each area gets a score: the share of what it checked that passed.
"""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from joinery import match_plan
from joinery.match import AreaScore, Fix
from joinery.match_look import godot_value

Severity = Literal["high", "medium", "low"]
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
FixKind = Literal["typed", "agent", "feature"]
#: Where the last report of each check is kept, for the fix runner and the card.
FOLDER = Path(".joinery") / "checks"
#: Files per typed task, as for match.
MAX_FILES = match_plan.MAX_FILES
#: Findings one agent task takes on.
MAX_AGENT_FINDINGS = 6


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CheckFix(_Model):
    """How one finding gets fixed."""

    kind: FixKind
    #: What it does, in a few words ("Limit the texture to 1024 px in Godot").
    label: str
    #: typed: the operation, as `joinery.match.Fix`.
    server: str = ""
    tool: str = ""
    arguments: dict[str, Any] = {}
    apps: list[str] = []
    source: str = ""
    outputs: list[str] = []
    #: agent: what to change, complete on its own, and the files it touches.
    request: str = ""
    targets: list[str] = []
    #: feature: the game feature that adds what is missing.
    feature: str = ""

    def typed(self) -> Fix:
        return Fix(
            server=self.server,
            tool=self.tool,
            arguments=self.arguments,
            apps=self.apps,
            source=self.source,
            label=self.label,
            outputs=self.outputs,
        )


class Finding(_Model):
    id: str
    area: str
    path: str
    subject: str = ""
    check: str
    severity: Severity
    measured: str
    target: str
    message: str
    fix: CheckFix | None = None
    needs_you: str = ""
    #: Where it was seen: scene, node, t (game seconds), seed, page.
    evidence: dict[str, Any] = {}


class Report(_Model):
    kind: Literal["access", "perf"]
    project: str
    areas: list[AreaScore] = []
    summary: str = ""
    findings: list[Finding] = []
    errors: list[str] = []
    #: What the check measured, for the card and the agents (targets, numbers, runs).
    facts: dict[str, Any] = {}


class CheckTask(match_plan.FixTask):
    """One change a check's Fix starts: scripted, an agent's, or a feature to add."""

    kind: Literal["scripted", "agent", "feature"] = "scripted"
    feature: str = ""


def finding_id(*parts: str) -> str:
    return hashlib.sha1("\x00".join(parts).encode()).hexdigest()[:12]


class Tally:
    """The findings and what each area checked, as an audit goes."""

    def __init__(self, areas: dict[str, str]) -> None:
        self.areas = areas
        self.checked: dict[str, dict[str, bool]] = {area: {} for area in areas}
        self.findings: list[Finding] = []

    def check(self, area: str, item: str, ok: bool) -> None:
        """One thing ``area`` checked; a failure stays a failure if seen twice."""
        seen = self.checked[area]
        seen[item] = seen.get(item, True) and ok

    def add(
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
        fix: CheckFix | None = None,
        needs_you: str = "",
        evidence: dict[str, Any] | None = None,
        item: str = "",
    ) -> None:
        self.check(area, item or f"{path}#{subject}#{check}", False)
        self.findings.append(
            Finding(
                id=finding_id(area, path, subject, check),
                area=area,
                path=path,
                subject=subject,
                check=check,
                severity=severity,
                measured=measured,
                target=target,
                message=message,
                fix=fix,
                needs_you=""
                if fix is not None
                else (needs_you or "Change it by hand."),
                evidence=evidence or {},
            )
        )

    def scores(self) -> list[AreaScore]:
        rows: list[AreaScore] = []
        for area, label in self.areas.items():
            items = self.checked[area]
            if not items:
                continue
            failing = sum(1 for ok in items.values() if not ok)
            rows.append(
                AreaScore(
                    id=area,
                    label=label,
                    score=round(100 * (len(items) - failing) / len(items)),
                    checked=len(items),
                    failing=failing,
                    fixable=sum(
                        1 for f in self.findings if f.area == area and f.fix is not None
                    ),
                )
            )
        return rows

    def report(
        self, kind: Literal["access", "perf"], project: Path, errors: list[str]
    ) -> Report:
        order = list(self.areas)
        findings = sorted(
            self.findings,
            key=lambda f: (SEVERITY_ORDER[f.severity], order.index(f.area), f.path),
        )
        areas = self.scores()
        return Report(
            kind=kind,
            project=str(project),
            areas=areas,
            summary=" · ".join(f"{a.label} {a.score}%" for a in areas),
            findings=findings,
            errors=errors,
        )


# --- reading the project -----------------------------------------------------------------


def config(project: Path, table: str) -> dict[str, Any]:
    """One table of joinery.toml ({} when absent or unreadable)."""
    try:
        raw = tomllib.loads((project / "joinery.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    value = raw.get(table, {})
    return value if isinstance(value, dict) else {}


def every(project: Path, key: str) -> int:
    """``[agents] <key>``: run the check after this many accepted changes (0 = off)."""
    value = config(project, "agents").get(key, 0)
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and value > 0
        else 0
    )


def res(path: str) -> str:
    """A res:// path as a project path."""
    return path.removeprefix("res://").strip("/")


def save(project: Path, report: Report) -> Path:
    """Keep the report as the check's last (the fix runner and the agents read it)."""
    folder = project / FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{report.kind}.json"
    path.write_text(json.dumps(report.model_dump(), indent=1), encoding="utf-8")
    return path


def last(project: Path, kind: str) -> Report | None:
    try:
        data = json.loads(
            (project / FOLDER / f"{kind}.json").read_text(encoding="utf-8")
        )
        return Report.model_validate(data)
    except (OSError, ValueError):
        return None


# --- editing Godot text --------------------------------------------------------------------

_HEADER = re.compile(r"^\[(\w+)([^\]]*)\]\s*$")
_ATTR = re.compile(r'(\w+)=("[^"]*"|\S+)')


def split_node_path(path: str) -> tuple[str, str]:
    """A node path inside its scene ("Lights/Lamp", "." for the root) -> (name, parent)."""
    if path in {"", "."}:
        return "", ""
    parent, _, name = path.rpartition("/")
    return name, parent or "."


def set_node_properties(
    text: str, name: str, parent: str, props: dict[str, Any]
) -> str:
    """``text`` (a .tscn) with ``props`` set on one node, found by its name and parent.

    The scene's root has no parent attribute (``parent`` ""). Raises if it is gone.
    """
    lines = text.splitlines(keepends=True)
    start = -1
    for index, line in enumerate(lines):
        header = _HEADER.match(line.strip())
        if not header or header.group(1) != "node":
            continue
        attrs = {k: v.strip('"') for k, v in _ATTR.findall(header.group(2))}
        if attrs.get("name") == name and attrs.get("parent", "") == parent:
            start = index
            break
    if start < 0:
        raise ValueError(f"the node {parent}/{name} is no longer in the scene")
    end = next(
        (i for i in range(start + 1, len(lines)) if _HEADER.match(lines[i].strip())),
        len(lines),
    )
    remaining = dict(props)
    last_line = start
    for index in range(start + 1, end):
        line = lines[index]
        if "=" not in line or line.startswith((" ", "\t")):
            continue
        last_line = index
        key = line.partition("=")[0].strip()
        if key in remaining:
            newline = "\n" if line.endswith("\n") else ""
            lines[index] = f"{key} = {godot_value(remaining.pop(key))}{newline}"
    if remaining:
        if not lines[last_line].endswith("\n"):
            lines[last_line] += "\n"
        lines[last_line + 1 : last_line + 1] = [
            f"{key} = {godot_value(value)}\n" for key, value in remaining.items()
        ]
    return "".join(lines)


def set_ini_values(text: str, section: str, values: dict[str, Any]) -> str:
    """An ``.import`` (or any Godot ini) file with ``values`` set in ``[section]``."""
    lines = text.splitlines(keepends=True)
    start = next(
        (i for i, line in enumerate(lines) if line.strip() == f"[{section}]"), -1
    )
    if start < 0:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines += ["\n", f"[{section}]\n"]
        start = len(lines) - 1
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")),
        len(lines),
    )
    remaining = dict(values)
    for index in range(start + 1, end):
        key = lines[index].partition("=")[0].strip()
        if key in remaining:
            lines[index] = f"{key}={godot_value(remaining.pop(key))}\n"
    insert = end
    while insert > start + 1 and not lines[insert - 1].strip():
        insert -= 1
    lines[insert:insert] = [f"{k}={godot_value(v)}\n" for k, v in remaining.items()]
    return "".join(lines)


def text_fix(
    project: Path, path: str, edits: list[dict[str, Any]], label: str
) -> CheckFix:
    """A typed fix that rewrites a Godot text file through the Godot MCP in the worker.

    ``edits`` are applied to the file as it is when the change is planned, so several
    fixes of one file fold into one write: ``{"node": [name, parent], "props": {…}}``
    (a node of a .tscn), ``{"section": [kind, id], "props": {…}}`` (a resource or
    sub-resource) and ``{"ini": section, "values": {…}}`` (an .import file).
    """
    return CheckFix(
        kind="typed",
        label=label,
        server="godot",
        tool="write_file",
        arguments={"projectPath": str(project), "filePath": path, "edits": edits},
        apps=["godot"],
        source=path,
    )


def apply_edits(text: str, edits: Iterable[dict[str, Any]]) -> str:
    from joinery.match_look import Section, set_properties

    for edit in edits:
        if "node" in edit:
            name, parent = edit["node"]
            text = set_node_properties(text, name, parent, edit["props"])
        elif "section" in edit:
            kind, ident = edit["section"]
            text = set_properties(text, Section(kind, ident), edit["props"])
        elif "ini" in edit:
            text = set_ini_values(text, edit["ini"], edit["values"])
    return text


# --- planning the changes -------------------------------------------------------------------


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def _typed_steps(project: Path, fixes: list[CheckFix]) -> list[match_plan.Step]:
    """One file's fixes as the fewest typed calls: text edits fold into one write."""
    writes = [f for f in fixes if f.tool == "write_file" and "edits" in f.arguments]
    others = [f.typed() for f in fixes if f not in writes]
    steps = match_plan._merge(others, project) if others else []
    if writes:
        path = writes[0].source
        text = (project / path).read_text(encoding="utf-8")
        steps.append(
            match_plan.Step(
                server="godot",
                tool="write_file",
                arguments={
                    "projectPath": str(project),
                    "filePath": path,
                    "content": apply_edits(
                        text, (e for f in writes for e in f.arguments["edits"])
                    ),
                },
                label="; ".join(dict.fromkeys(f.label for f in writes)),
                apps=["godot"],
            )
        )
    return steps


def plan(
    project: Path,
    report: Report,
    *,
    areas: Iterable[str] | None = None,
    findings: Iterable[str] | None = None,
    labels: dict[str, str] | None = None,
) -> list[CheckTask]:
    """The changes that fix what ``report`` found, in ``areas`` (all by default)."""
    wanted = set(areas or [f.area for f in report.findings])
    chosen = set(findings or ())
    names = labels or {}
    picked = [
        f
        for f in report.findings
        if f.fix is not None and f.area in wanted and (not chosen or f.id in chosen)
    ]
    order = [a.id for a in report.areas] + sorted({f.area for f in picked})
    tasks: list[CheckTask] = []
    for feature in dict.fromkeys(
        f.fix.feature for f in picked if f.fix is not None and f.fix.kind == "feature"
    ):
        offered = [f for f in picked if f.fix is not None and f.fix.feature == feature]
        label = offered[0].fix.label if offered[0].fix is not None else feature
        tasks.append(
            CheckTask(
                key=f"feature-{feature}",
                title=label,
                area=offered[0].area,
                request=f"/feature add {feature}",
                targets=[],
                apps=["godot"],
                steps=[],
                findings=[f.id for f in offered],
                facts=[],
                kind="feature",
                feature=feature,
            )
        )
    by_source: dict[str, list[Finding]] = {}
    for finding in picked:
        if finding.fix is not None and finding.fix.kind == "typed":
            by_source.setdefault(finding.fix.source, []).append(finding)
    groups: dict[tuple[str, str], list[str]] = {}
    for source, on_source in sorted(by_source.items()):
        area = min((f.area for f in on_source), key=order.index)
        first = next(f for f in on_source if f.area == area)
        assert first.fix is not None
        groups.setdefault((area, first.fix.server), []).append(source)
    for (area, server), sources in sorted(
        groups.items(), key=lambda item: (order.index(item[0][0]), item[0][1])
    ):
        for start in range(0, len(sources), MAX_FILES):
            chunk = sources[start : start + MAX_FILES]
            steps: list[match_plan.Step] = []
            targets: list[str] = []
            found: list[Finding] = []
            for source in chunk:
                fixes = [f.fix for f in by_source[source] if f.fix is not None]
                steps += _typed_steps(project, fixes)
                targets += [source] + [o for fix in fixes for o in fix.outputs]
                found += by_source[source]
            labels_seen = list(dict.fromkeys(f.fix.label for f in found if f.fix))
            title = (
                f"{labels_seen[0]} ({_plural(len(chunk), 'file')})"
                if len(labels_seen) == 1
                else f"Fix {names.get(area, area).lower()} in {_plural(len(chunk), 'file')}"
            )
            tasks.append(
                CheckTask(
                    key=f"{area}-{server}-{start // MAX_FILES}",
                    title=title[:80],
                    area=area,
                    request=_request(report.kind, title, found),
                    targets=sorted(set(targets)),
                    apps=sorted({app for step in steps for app in step.apps}),
                    steps=steps,
                    findings=[f.id for f in found],
                    facts=[f"{f.measured} → {f.target}"[:40] for f in found][:3],
                )
            )
    agent_fixes = [f for f in picked if f.fix is not None and f.fix.kind == "agent"]
    areas_of: dict[str, set[str]] = {}
    for finding in agent_fixes:
        assert finding.fix is not None
        areas_of.setdefault(finding.fix.label, set()).add(finding.area)
    agent: dict[str, list[Finding]] = {}
    for finding in agent_fixes:
        assert finding.fix is not None
        # One change for what several areas ask of the same kind ("Add accessibility
        # options"); otherwise one per area.
        shared = len(areas_of[finding.fix.label]) > 1
        agent.setdefault(finding.fix.label if shared else finding.area, []).append(
            finding
        )
    areas_of_group = set(order)
    for group in sorted(agent, key=lambda g: order.index(agent[g][0].area)):
        items = agent[group]
        area = items[0].area
        # A shared change (one label across areas) is one change however long.
        size = MAX_AGENT_FINDINGS if group in areas_of_group else len(items)
        for start in range(0, len(items), size):
            batch = items[start : start + size]
            what = names.get(area, area)
            first_fix = batch[0].fix
            assert first_fix is not None
            title = (
                first_fix.label
                if len(batch) == 1 or group not in names and group not in order
                else f"Fix {_plural(len(batch), what.lower() + ' finding')}"
            )
            tasks.append(
                CheckTask(
                    key=f"{area}-agent-{len(tasks)}",
                    title=title[:80],
                    area=area,
                    request=_request(report.kind, title, batch, agent=True),
                    targets=sorted(
                        {t for f in batch if f.fix is not None for t in f.fix.targets}
                    ),
                    apps=["godot"],
                    steps=[],
                    findings=[f.id for f in batch],
                    facts=[f"{f.measured} → {f.target}"[:40] for f in batch][:3],
                    kind="agent",
                )
            )
    return tasks


def _request(
    kind: str, title: str, found: list[Finding], *, agent: bool = False
) -> str:
    check = "accessibility" if kind == "access" else "performance"
    lines = []
    for f in found:
        where = ", ".join(
            f"{k} {v}"
            for k, v in f.evidence.items()
            if k in {"scene", "node", "t", "seed"}
        )
        detail = f.fix.request if agent and f.fix is not None and f.fix.request else ""
        lines.append(
            f"- {f.path}{' (' + f.subject + ')' if f.subject else ''}: {f.message}"
            f" Measured {f.measured}; wanted {f.target}."
            + (f" Seen at {where}." if where else "")
            + (f" {detail}" if detail else "")
        )
    tail = (
        "Change only what these need, keep the game working, then call "
        "submit_for_review with one short line naming the change."
        if agent
        else "Each fix is a typed operation in the app that owns the file."
    )
    return (
        f"{title}, from Joinery's {check} check. It measured:\n"
        + "\n".join(lines)
        + "\n"
        + tail
    )
