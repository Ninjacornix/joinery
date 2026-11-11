"""What the performance check reads from the project's files, with the typed fixes.

No Godot needed: texture sizes and their import settings, model triangles and whether
Godot makes LODs for them on import, props repeated many times in one scene, lights
that cast shadows, and scripts that do heavy work every frame. Each finding carries the
typed fix where Joinery has one:

* a texture too big for the target: Krita resizes the painting that makes it (its
  recipe replays); a texture with no recipe is limited in its ``.import`` settings;
* an uncompressed big 3D texture: VRAM compression in its ``.import``;
* a model with LODs turned off: ``meshes/generate_lods`` back on in its ``.import``;
  a model far over the budget made in Blender: Blender's Decimate, applied;
* many copies of one prop in a scene: a visibility range on each copy, so far ones
  aren't drawn (turning them into one MultiMesh is code, an agent's task);
* more shadow-casting lights than the target affords: shadows off on the extra ones.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any

from joinery import match
from joinery.bible_rules import _folder_value, glb_triangles, image_size, load_rules
from joinery.checks_core import CheckFix, Severity, Tally, text_fix

_IMAGES = {".png", ".jpg", ".jpeg", ".webp"}
_HEADER = re.compile(r"^\[(\w+)([^\]]*)\]\s*$")
_ATTR = re.compile(r'(\w+)=("[^"]*"|\S+)')
_RES = re.compile(r'(?:Ext|Sub)Resource\(\s*"([^"]+)"\s*\)')
#: Copies of one mesh in a scene that are worth a visibility range or a MultiMesh.
REPEATS = 16
#: Metres beyond which a repeated prop isn't drawn, once ranged.
VISIBILITY_END = 60.0
_UI_FOLDERS = ("ui/", "hud/", "fonts/", "icons/", "sprites/")
_HOT_CALLS = re.compile(
    r"\b(get_nodes_in_group|find_child|find_children|get_tree\(\)\.get_nodes_in_group|"
    r"ResourceLoader\.load|load)\s*\("
)
_RANGE = re.compile(r"\bfor\s+\w+(?:\s*:\s*\w+)?\s+in\s+range\(\s*(\d+)")


def _import(project: Path, path: str) -> str:
    try:
        return (project / f"{path}.import").read_text(encoding="utf-8")
    except OSError:
        return ""


def _ini(text: str, section: str) -> dict[str, str]:
    values: dict[str, str] = {}
    current = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            current = stripped.strip("[]")
        elif current == section and "=" in stripped:
            key, _, value = stripped.partition("=")
            values[key.strip()] = value.strip()
    return values


def _three_d(project: Path, files: list[str]) -> bool:
    if any(f.endswith((".glb", ".gltf", ".blend")) for f in files):
        return True
    return any(
        'type="MeshInstance3D"' in _text(project, f)
        for f in files
        if f.endswith(".tscn")
    )


def _text(project: Path, path: str) -> str:
    try:
        return (project / path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def textures(
    tally: Tally, project: Path, files: list[str], max_size: int, owners: dict[str, Any]
) -> None:
    three_d = _three_d(project, files)
    for path in files:
        if PurePosixPath(path).suffix.lower() not in _IMAGES:
            continue
        try:
            size = image_size((project / path).read_bytes(), PurePosixPath(path).suffix)
        except OSError:
            continue
        if size is None:
            continue
        params = _ini(_import(project, path), "params")
        limit = int(float(params.get("process/size_limit", "0") or 0))
        shown = max(size) if limit <= 0 else min(max(size), limit)
        ok = shown <= max_size
        tally.check("textures", f"{path}#size", ok)
        if not ok:
            scale = max_size / max(size)
            new = (max(1, round(size[0] * scale)), max(1, round(size[1] * scale)))
            owner = owners.get(path)
            fix: CheckFix | None
            if (
                owner is not None
                and owner.tool == "krita"
                and (project / owner.source).is_file()
            ):
                fix = CheckFix(
                    kind="typed",
                    label="Resize the painting in Krita",
                    server="joinery-paint",
                    tool="edit_image",
                    arguments={
                        "source": owner.source,
                        "ops": [{"op": "resize", "width": new[0], "height": new[1]}],
                        "exports": owner.exports,
                    },
                    apps=["krita"],
                    source=owner.source,
                    outputs=owner.exports,
                )
            elif (project / f"{path}.import").is_file():
                fix = text_fix(
                    project,
                    f"{path}.import",
                    [{"ini": "params", "values": {"process/size_limit": max_size}}],
                    f"Limit textures to {max_size} px in Godot",
                )
            else:
                fix = None
            tally.add(
                "textures",
                path,
                "texture_size",
                "high" if max(size) >= 2 * max_size else "medium",
                f"{size[0]}×{size[1]}",
                f"≤ {max_size} px",
                f"is {size[0]}×{size[1]}, larger than the target's {max_size} px "
                f"(about {size[0] * size[1] * 4 * 1.33 / 1048576:.1f} MB of video memory).",
                fix=fix,
                item=f"{path}#size",
            )
        if not three_d or max(size) < 512 or path.startswith(_UI_FOLDERS):
            continue
        mode = params.get("compress/mode", "")
        tally.check("textures", f"{path}#compression", mode not in {"0", "4"})
        if mode in {"0", "4"} and (project / f"{path}.import").is_file():
            tally.add(
                "textures",
                path,
                "texture_compression",
                "medium",
                "lossless" if mode == "0" else "uncompressed",
                "VRAM compressed",
                "is a large texture kept lossless; on the GPU it takes four times the "
                "memory of a compressed one.",
                fix=text_fix(
                    project,
                    f"{path}.import",
                    [{"ini": "params", "values": {"compress/mode": 2}}],
                    "Compress textures for the GPU in Godot",
                ),
                item=f"{path}#compression",
            )


def models(
    tally: Tally, project: Path, files: list[str], budget: int, owners: dict[str, Any]
) -> None:
    rules = load_rules(project)
    for path in files:
        if not path.endswith(".glb"):
            continue
        try:
            triangles = glb_triangles((project / path).read_bytes())
        except (OSError, ValueError, KeyError, IndexError):
            continue
        settings = _ini(_import(project, path), "params")
        lods = settings.get("meshes/generate_lods", "true") != "false"
        tally.check("rendering", f"{path}#lods", lods or triangles < 500)
        if not lods and triangles >= 500:
            tally.add(
                "rendering",
                path,
                "lods",
                "medium",
                "no LODs",
                "LODs on import",
                f"has {triangles} triangles and Godot makes no LODs for it, so it is "
                "drawn in full however far away it is.",
                fix=text_fix(
                    project,
                    f"{path}.import",
                    [{"ini": "params", "values": {"meshes/generate_lods": True}}],
                    "Make LODs on import in Godot",
                ),
                item=f"{path}#lods",
            )
        limit = _folder_value(rules.models.max_triangles, path) or budget
        tally.check("rendering", f"{path}#triangles", triangles <= limit)
        if triangles <= limit:
            continue
        owner = owners.get(path)
        fix = None
        if (
            owner is not None
            and owner.tool == "blender"
            and (project / owner.source).is_file()
        ):
            fix = CheckFix(
                kind="typed",
                label="Reduce triangles in Blender",
                server="joinery-blender-ops",
                tool="reduce_triangles",
                arguments={"source": owner.source, "max_triangles": limit},
                apps=["blender"],
                source=owner.source,
                outputs=owner.exports,
            )
        tally.add(
            "rendering",
            path,
            "model_triangles",
            "high" if triangles > 2 * limit else "medium",
            f"{triangles} triangles",
            f"≤ {limit}",
            f"has {triangles} triangles, over the {limit} a model may have here.",
            fix=fix,
            needs_you="It has no Blender recipe (you imported it), so Joinery won't change it.",
            item=f"{path}#triangles",
        )


def _nodes(text: str) -> list[tuple[dict[str, str], dict[str, str]]]:
    found: list[tuple[dict[str, str], dict[str, str]]] = []
    for line in text.splitlines():
        header = _HEADER.match(line.strip())
        if header:
            if header.group(1) == "node":
                attrs = {k: v.strip('"') for k, v in _ATTR.findall(header.group(2))}
                found.append((attrs, {}))
            else:
                found.append(({"__other__": "1"}, {}))
            continue
        if found and "=" in line and not line.startswith((" ", "\t")):
            key, _, value = line.partition("=")
            found[-1][1][key.strip()] = value.strip()
    return [n for n in found if "__other__" not in n[0]]


def scenes(tally: Tally, project: Path, files: list[str], shadow_lights: int) -> None:
    for path in files:
        if not path.endswith(".tscn"):
            continue
        nodes = _nodes(_text(project, path))
        meshes: Counter[str] = Counter()
        holders: dict[str, list[tuple[dict[str, str], dict[str, str]]]] = {}
        for attrs, values in nodes:
            if attrs.get("type") == "MeshInstance3D" and "mesh" in values:
                ref = _RES.search(values["mesh"])
                if ref is not None:
                    meshes[ref.group(1)] += 1
                    holders.setdefault(ref.group(1), []).append((attrs, values))
        for mesh, count in meshes.items():
            if count < REPEATS:
                continue
            copies = holders[mesh]
            unranged = [c for c in copies if "visibility_range_end" not in c[1]]
            tally.check("rendering", f"{path}#{mesh}#range", not unranged)
            tally.check("rendering", f"{path}#{mesh}#multimesh", False)
            name = copies[0][0].get("name", "")
            tally.add(
                "rendering",
                path,
                "repeats",
                "medium",
                f"{count} copies",
                "one MultiMesh",
                f"draws one mesh {count} times as separate nodes ({name} and others): "
                "each is its own draw call.",
                subject=mesh,
                fix=CheckFix(
                    kind="agent",
                    label=f"Turn {count} copies into one MultiMesh",
                    request=(
                        f"In {path}, the {count} MeshInstance3D nodes that use the mesh "
                        f"{mesh} ({name} and the others) are drawn one by one. Replace them "
                        "with one MultiMeshInstance3D whose instances keep each copy's "
                        "transform, and keep their collisions working."
                    ),
                    targets=[path],
                ),
                evidence={"scene": f"res://{path}", "node": name},
                item=f"{path}#{mesh}#multimesh",
            )
            if unranged:
                tally.add(
                    "rendering",
                    path,
                    "visibility_range",
                    "low",
                    f"{len(unranged)} unranged",
                    f"hidden past {VISIBILITY_END:g} m",
                    f"draws all {len(unranged)} copies of one prop however far away they are.",
                    subject=f"{mesh}#range",
                    fix=text_fix(
                        project,
                        path,
                        [
                            {
                                "node": [a.get("name", ""), a.get("parent", "")],
                                "props": {
                                    "visibility_range_end": VISIBILITY_END,
                                    "visibility_range_end_margin": 5.0,
                                },
                            }
                            for a, _values in unranged
                        ],
                        "Hide far copies with a visibility range in Godot",
                    ),
                    evidence={"scene": f"res://{path}"},
                    item=f"{path}#{mesh}#range",
                )
        lights = [
            (a, v)
            for a, v in nodes
            if a.get("type") in {"OmniLight3D", "SpotLight3D"}
            and v.get("shadow_enabled") == "true"
        ]
        if not lights:
            continue
        ok = len(lights) <= shadow_lights
        tally.check("lighting", f"{path}#shadows", ok)
        if ok:
            continue
        extra = lights[shadow_lights:]
        tally.add(
            "lighting",
            path,
            "shadow_lights",
            "high" if len(lights) > 2 * max(shadow_lights, 1) else "medium",
            f"{len(lights)} shadowed lights",
            f"≤ {shadow_lights}",
            f"has {len(lights)} omni and spot lights casting shadows; each renders the "
            "scene again for its shadow map.",
            fix=text_fix(
                project,
                path,
                [
                    {
                        "node": [a.get("name", ""), a.get("parent", "")],
                        "props": {"shadow_enabled": False},
                    }
                    for a, _values in extra
                ],
                f"Turn off shadows on {len(extra)} lights in Godot",
            ),
            evidence={"scene": f"res://{path}", "node": extra[0][0].get("name", "")},
            item=f"{path}#shadows",
        )


def _frame_functions(text: str) -> list[tuple[str, int, list[str]]]:
    """Each ``_process`` / ``_physics_process`` body: (name, first line, its lines)."""
    lines = text.splitlines()
    found: list[tuple[str, int, list[str]]] = []
    for index, line in enumerate(lines):
        head = re.match(r"^func\s+(_process|_physics_process)\s*\(", line)
        if not head:
            continue
        body: list[str] = []
        for following in lines[index + 1 :]:
            if following.strip() and not following.startswith(("\t", " ")):
                break
            body.append(following)
        found.append((head.group(1), index + 1, body))
    return found


def hot_code(path: str, text: str) -> list[tuple[str, int, str]]:
    """Heavy work every frame: (function, line, what) for each pattern found."""
    found: list[tuple[str, int, str]] = []
    for name, start, body in _frame_functions(text):
        loops: list[int] = []
        for offset, line in enumerate(body, start=start + 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            indent = len(line) - len(line.lstrip("\t "))
            loops = [i for i in loops if i < indent]
            call = _HOT_CALLS.search(stripped)
            if call:
                found.append((name, offset, f"calls {call.group(1)}() every frame"))
            loop = _RANGE.search(stripped)
            if loop and int(loop.group(1)) >= 1000:
                found.append((name, offset, f"loops {loop.group(1)} times every frame"))
            if stripped.startswith(("for ", "while ")):
                if loops:
                    found.append((name, offset, "runs nested loops every frame"))
                loops.append(indent)
    return found[:6]


def scripts(
    tally: Tally,
    project: Path,
    files: list[str],
    measured: dict[str, float],
    budget_ms: float,
    factor: float,
) -> None:
    """Scripts that do heavy work every frame, and what each costs as measured."""
    for path in files:
        if not path.endswith(".gd"):
            continue
        cost = measured.get(f"res://{path}", 0.0) * factor
        hot = hot_code(path, _text(project, path))
        heavy = cost >= max(1.0, budget_ms * 0.1)
        if not hot and not heavy:
            if f"res://{path}" in measured:
                tally.check("scripts", path, True)
            continue
        tally.check("scripts", path, False)
        where = "; ".join(f"line {line} in {fn} {what}" for fn, line, what in hot)
        measured_text = f"{cost:.1f} ms a frame" if cost >= 0.05 else "work every frame"
        severity: Severity = ("high" if cost >= budget_ms * 0.3 else "medium") if heavy else "low"
        words = [f"Costs {cost:.1f} ms of every frame on the target."] if heavy else []
        if where:
            words.append(where[0].upper() + where[1:] + ".")
        tally.add(
            "scripts",
            path,
            "hot_script",
            severity,
            measured_text,
            f"≤ {budget_ms * 0.1:.1f} ms",
            " ".join(words),
            fix=CheckFix(
                kind="agent",
                label=f"Make {PurePosixPath(path).name} cheaper per frame",
                request=(
                    f"{path} "
                    + (f"costs {cost:.1f} ms of each frame. " if cost else "")
                    + (f"{where}. " if where else "")
                    + "Move the heavy work out of the per-frame functions: cache node "
                    "lookups and loaded resources in _ready, react to signals instead of "
                    "polling, and spread large loops over frames. Keep the behaviour."
                ),
                targets=[path],
            ),
            evidence={"script": f"res://{path}"},
            item=path,
        )


def all_files(project: Path) -> list[str]:
    return [
        p
        for p in match.project_files(project)
        if not p.startswith((".joinery/", "recipes/", "addons/"))
    ]


def owners(project: Path) -> dict[str, Any]:
    return dict(match.owners(project))
