"""Turn an audit's fixes into small reviewed changes, one parallel task each.

Fixes are grouped by the file they change, so no two tasks ever want the same
file (claims would refuse the second), then by area and app, a few files per
task: "Remap 6 textures to the palette", "Normalise 9 sounds to −16 LUFS".
Several fixes on one file become one operation where the tool takes a list
(Krita layers, audio steps, resource properties, text patches); a Blender
model gets one typed operation per kind, in a fixed order. Each task is a plan
of typed calls that `bridge.crew` runs as a normal staged change: checks,
reviewer, the glanceable review. Nothing here writes a file.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from joinery import match_look
from joinery.match import AREAS, Audit, Finding, Fix

#: Files per task: small enough to review at a glance.
MAX_FILES = 6
#: The order a model's Blender operations run in: scale before pivots, geometry
#: before UVs, materials last.
_BLENDER_ORDER = [
    "apply_scale",
    "set_pivot",
    "reduce_triangles",
    "set_texel_density",
    "set_material_value",
]
_IMAGE_ORDER = ["resize", "palette_map"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Step(_Model):
    server: str
    tool: str
    arguments: dict[str, Any]
    label: str
    apps: list[str]


class FixTask(_Model):
    key: str
    title: str
    area: str
    #: The task's request in words, as its card and the reviewer read it.
    request: str
    #: Files the task claims before it starts.
    targets: list[str]
    apps: list[str]
    steps: list[Step]
    #: The findings it fixes.
    findings: list[str]
    #: A few plain words for the glanceable review ("6 textures on the palette").
    facts: list[str]


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def _merge(fixes: list[Fix], project: Path) -> list[Step]:
    """One file's fixes as the fewest typed calls, in a stable order."""
    steps: list[Step] = []
    if any(f.tool == "write_file" for f in fixes):
        # One file, one writer: a resource's own properties join the text patch,
        # so no call rewrites the file under another's feet.
        fixes = [
            f.model_copy(
                update={
                    "tool": "write_file",
                    "arguments": {
                        "filePath": f.source,
                        "patches": [
                            {
                                "section": ["resource", ""],
                                "props": f.arguments["properties"],
                            }
                        ],
                    },
                }
            )
            if f.tool == "manage_resource"
            else f
            for f in fixes
        ]
    by_tool: dict[tuple[str, str], list[Fix]] = {}
    for fix in fixes:
        key = (fix.tool, "")
        if fix.tool == "set_material_value":
            key = (
                fix.tool,
                f"{fix.arguments['material']}\x00{fix.arguments['socket']}",
            )
        by_tool.setdefault(key, []).append(fix)
    order = {name: index for index, name in enumerate(_BLENDER_ORDER)}
    for (tool, _detail), group in sorted(
        by_tool.items(), key=lambda item: (order.get(item[0][0], 99), item[0][1])
    ):
        first = group[0]
        arguments = dict(first.arguments)
        labels = list(dict.fromkeys(f.label for f in group))
        if tool == "edit_image":
            ops: dict[str, dict[str, Any]] = {}
            for fix in group:
                for op in fix.arguments["ops"]:
                    ops.setdefault(op["op"], op)
            arguments["ops"] = [ops[name] for name in _IMAGE_ORDER if name in ops]
        elif tool in {"apply_scale", "set_texel_density"}:
            arguments["objects"] = sorted(
                {o for fix in group for o in fix.arguments["objects"]}
            )
        elif tool == "reduce_triangles":
            arguments["max_triangles"] = min(
                int(f.arguments["max_triangles"]) for f in group
            )
        elif tool == "manage_resource":
            properties: dict[str, Any] = {}
            for fix in group:
                properties.update(fix.arguments["properties"])
            arguments["properties"] = properties
        elif tool == "process_audio":
            normalise = [
                f for f in group if f.arguments["steps"][0].get("op") == "normalise"
            ]
            arguments = dict((normalise or group)[0].arguments)
        elif tool == "write_file":
            arguments = {
                "projectPath": str(project),
                "filePath": first.source,
                "content": _patched(
                    project,
                    [p for f in group for p in f.arguments["patches"]],
                    first.source,
                ),
            }
            labels = ["Set " + ", ".join(_patched_keys(group)) + " in Godot"]
        steps.append(
            Step(
                server=first.server,
                tool=tool,
                arguments=arguments,
                label="; ".join(labels),
                apps=list(first.apps),
            )
        )
    return steps


def _patched(project: Path, patches: list[dict[str, Any]], path: str) -> str:
    text = (project / path).read_text(encoding="utf-8")
    for patch in patches:
        if "swap" in patch:
            old, new = patch["swap"]
            text = match_look.swap_ext_resource(text, old, new)
    for patch in patches:
        if "section" in patch:
            kind, identifier = patch["section"]
            text = match_look.set_properties(
                text, match_look.Section(kind, identifier), patch["props"]
            )
    return text


def _patched_keys(group: list[Fix]) -> list[str]:
    keys: list[str] = []
    for fix in group:
        for patch in fix.arguments["patches"]:
            keys += list(patch.get("props", {})) or ["the font"]
    return list(dict.fromkeys(keys))


def _title(area: str, tool: str, findings: list[Finding], files: int) -> str:
    checks = {f.check for f in findings}
    models = _plural(files, "model")
    if tool == "blender":
        for only, title in (
            ({"texel_density"}, f"Set texel density on {models}"),
            ({"scale"}, f"Apply scale on {models}"),
            ({"pivot"}, f"Set pivots on {models}"),
            ({"triangles"}, f"Reduce triangles on {models}"),
            ({"roughness", "metallic"}, f"Set material values on {models}"),
        ):
            if checks <= only:
                return title
        return f"Fix {models}"
    if area == "palette":
        return f"Remap {_plural(files, 'texture')} to the palette"
    if area == "textures":
        return f"Resize {_plural(files, 'texture')}"
    if area == "lighting":
        return "Match the lighting" + ("" if files == 1 else f" in {files} files")
    if area == "ui":
        return f"Match {_plural(files, 'UI theme')}"
    targets = {f.target for f in findings}
    if area == "sound" and len(targets) == 1 and "LUFS" in next(iter(targets)):
        loudness = next(iter(targets)).replace("-", "−")
        return f"Normalise {_plural(files, 'sound')} to {loudness}"
    return f"Fix the loudness of {_plural(files, 'sound')}"


def _facts(area: str, findings: list[Finding], files: int) -> list[str]:
    if area == "palette":
        return [f"{_plural(files, 'texture')} on the palette"]
    if area == "sound":
        return list(dict.fromkeys(f.target.replace("-", "−") for f in findings))[:3]
    facts: list[str] = []
    for finding in findings:
        fact = (
            f"{finding.check.replace('_', ' ')} {finding.measured} → {finding.target}"
        )
        if len(fact) > 40:
            fact = f"{finding.check.replace('_', ' ')} → {finding.target}"[:40]
        facts.append(fact[:1].upper() + fact[1:])
    return list(dict.fromkeys(facts))[:3]


def plan(
    project: Path,
    audit: Audit,
    *,
    areas: Iterable[str] | None = None,
    findings: Iterable[str] | None = None,
) -> list[FixTask]:
    """The tasks that fix what ``audit`` found, in ``areas`` (all by default)."""
    wanted = set(areas or AREAS)
    chosen = set(findings or ())
    fixable = [
        f
        for f in audit.findings
        if f.fix is not None and f.area in wanted and (not chosen or f.id in chosen)
    ]
    by_source: dict[str, list[Finding]] = {}
    for finding in fixable:
        assert finding.fix is not None
        by_source.setdefault(finding.fix.source, []).append(finding)
    area_order = list(AREAS)
    groups: dict[tuple[str, str], list[str]] = {}
    for source, on_source in sorted(by_source.items()):
        area = min((f.area for f in on_source), key=area_order.index)
        first = next(f for f in on_source if f.area == area)
        assert first.fix is not None
        kind = {"joinery-blender-ops": "blender", "godot": "godot"}.get(
            first.fix.server, first.fix.tool
        )
        groups.setdefault((area, kind), []).append(source)
    tasks: list[FixTask] = []
    for (area, tool), sources in sorted(
        groups.items(), key=lambda item: (area_order.index(item[0][0]), item[0][1])
    ):
        for start in range(0, len(sources), MAX_FILES):
            chunk = sources[start : start + MAX_FILES]
            steps: list[Step] = []
            targets: list[str] = []
            found: list[Finding] = []
            for source in chunk:
                fixes = [f.fix for f in by_source[source] if f.fix is not None]
                steps += _merge(fixes, project)
                targets += [source] + [o for fix in fixes for o in fix.outputs]
                found += by_source[source]
            title = _title(area, tool, found, len(chunk))
            lines = [
                f"- {f.path}{' (' + f.subject + ')' if f.subject and ':' not in f.subject else ''}: "
                f"{f.message}"
                for f in found
            ]
            request = (
                f"{title}, as the art bible asks. Joinery measured:\n"
                + "\n".join(lines)
                + "\nEach fix is a typed operation in the app that owns the file."
            )
            tasks.append(
                FixTask(
                    key=f"{area}-{tool}-{start // MAX_FILES}",
                    title=title,
                    area=area,
                    request=request,
                    targets=sorted(set(targets)),
                    apps=sorted({app for step in steps for app in step.apps}),
                    steps=steps,
                    findings=[f.id for f in found],
                    facts=_facts(area, found, len(chunk)),
                )
            )
    return tasks
