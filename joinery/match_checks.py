"""The measurements behind `joinery match`, one family of checks per area.

Each check reads what it can in Python (image headers, `.glb`, Godot text) and
runs the rest in the offline worker through the audit's `Measure` (pixels,
loudness), then records findings with the typed fix for the tool that owns
the file. See `joinery.match` for the audit itself.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from pathlib import PurePosixPath
from typing import Any

from joinery import match_look
from joinery.bible_rules import (
    _MEASURE,
    _SAMPLE_IMAGES,
    _folder_value,
    _in_folders,
    _lab,
    delta_e_2000,
    hex_rgb,
    image_size,
    measure_glb,
    parse_loudness,
    parse_samples,
    viewport_height,
)
from joinery.match import (
    _AUDIO,
    _GODOT_TEXT,
    _IMAGES,
    SOUND_TOOLS,
    Fix,
    Owner,
    Severity,
    _Audit,
    _suffix,
)
from joinery.music_track import mix_measure_script, section_mixes

# --- palette and textures ------------------------------------------------------------


def _off_share(
    pixels: list[tuple[int, int, int]],
    palette: list[tuple[float, float, float]],
    limit: float,
) -> float:
    cache: dict[tuple[int, int, int], bool] = {}
    far = 0
    for rgb in pixels:
        if rgb not in cache:
            lab = _lab(rgb)
            cache[rgb] = min(delta_e_2000(lab, p) for p in palette) > limit
        far += cache[rgb]
    return far / len(pixels) if pixels else 0.0


def _pow2_at_most(value: int) -> int:
    return 1 << max(0, math.floor(math.log2(max(value, 1))))


def _pow2_near(value: int) -> int:
    return 1 << max(0, round(math.log2(max(value, 1))))


def target_size(
    width: int, height: int, limit: int | None, power_of_two: bool
) -> tuple[int, int]:
    """The size an oversized or odd-sized texture is resized to."""
    scale = 1.0
    if limit is not None and max(width, height) > limit:
        scale = limit / max(width, height)
    w = max(1, round(width * scale))
    h = max(1, round(height * scale))
    if power_of_two:
        w, h = _pow2_near(w), _pow2_near(h)
        if limit is not None:
            w, h = min(w, _pow2_at_most(limit)), min(h, _pow2_at_most(limit))
    return w, h


async def images(audit: _Audit, paths: list[str]) -> None:
    rules = audit.rules
    palette = rules.palette.all_colours()
    palette_images = [
        p
        for p in paths
        if palette and _suffix(p) in _IMAGES and _in_folders(rules.palette.folders, p)
    ]
    texture_rules = rules.textures
    for path in paths:
        if _suffix(path) not in _IMAGES or not _in_folders(texture_rules.folders, path):
            continue
        limit = _folder_value(texture_rules.max_size, path)
        if limit is None and not texture_rules.power_of_two:
            continue
        audit.checked["textures"].add(path)
        size = image_size((audit.project / path).read_bytes(), _suffix(path))
        if size is None:
            audit.errors.append(f"{path}: its size could not be read")
            continue
        width, height = size
        over = limit is not None and max(width, height) > limit
        odd = texture_rules.power_of_two and not all(v & (v - 1) == 0 for v in size)
        if not over and not odd:
            continue
        new = target_size(width, height, limit, texture_rules.power_of_two)
        owner, why = audit.owner(path, {"krita"})
        fix = None
        if owner is not None:
            fix = Fix(
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
                label=f"Resize to {new[0]}×{new[1]} in Krita",
            )
        audit.finding(
            "textures",
            path,
            "texture_size",
            "high"
            if over and limit is not None and max(size) > 2 * limit
            else ("medium" if over else "low"),
            f"{width}×{height}",
            f"{new[0]}×{new[1]}",
            (
                f"{width}×{height}, over the {limit} px limit"
                if over
                else f"{width}×{height} is not a power of two"
            ),
            fix=fix,
            needs_you=why,
        )
    if not palette_images:
        return
    for path in palette_images:
        audit.checked["palette"].add(path)
    try:
        got = await audit.measure(
            ["sh", "-c", _SAMPLE_IMAGES, "sh", *palette_images], audit.project
        )
        sampled = parse_samples(got.output) if got.ok else {}
    except (OSError, RuntimeError, TimeoutError) as error:
        audit.errors.append(f"Colours could not be measured: {error}")
        sampled = {}
    labs = [_lab(hex_rgb(c)) for c in palette]
    allowed = rules.palette.max_off_palette
    for path in palette_images:
        if path not in sampled:
            audit.errors.append(f"{path}: its colours could not be measured")
            continue
        share = _off_share(sampled[path], labs, rules.palette.max_delta_e)
        if share <= allowed:
            continue
        owner, why = audit.owner(path, {"krita"})
        fix = None
        if owner is not None:
            fix = Fix(
                server="joinery-paint",
                tool="edit_image",
                arguments={
                    "source": owner.source,
                    "ops": [
                        {
                            "op": "palette_map",
                            "colours": palette,
                            "max_delta_e": rules.palette.max_delta_e,
                        }
                    ],
                    "exports": owner.exports,
                },
                apps=["krita"],
                source=owner.source,
                outputs=owner.exports,
                label="Palette map layer in Krita",
            )
        audit.finding(
            "palette",
            path,
            "palette",
            "high" if share > max(3 * allowed, 0.25) else "medium",
            f"{share:.0%} off-palette",
            f"≤ {allowed:.0%}",
            f"{share:.0%} of its colours are more than ΔE {rules.palette.max_delta_e:g} "
            f"from the palette (allowed {allowed:.0%})",
            fix=fix,
            needs_you=why,
        )


# --- models and materials ------------------------------------------------------------


def _blender_fix(owner: Owner, tool: str, arguments: dict[str, Any], label: str) -> Fix:
    return Fix(
        server="joinery-blender-ops",
        tool=tool,
        arguments={"source": owner.source, **arguments},
        apps=["blender"],
        source=owner.source,
        outputs=owner.exports,
        label=label,
    )


def models(audit: _Audit, paths: list[str]) -> None:
    rules = audit.rules
    models = rules.models
    for path in paths:
        if _suffix(path) != ".glb":
            continue
        budget = _folder_value(models.max_triangles, path)
        bones = _folder_value(models.max_bones, path)
        pivot = _folder_value(models.pivot, path)
        texel = _folder_value(models.texel_density, path)
        material_rules = rules.materials
        materials_wanted = _in_folders(material_rules.folders, path) and (
            material_rules.roughness is not None
            or material_rules.metallic is not None
            or material_rules.metallic_requires_name is not None
        )
        model_wanted = (
            budget is not None
            or bones is not None
            or models.applied_scale
            or pivot not in (None, "any")
        )
        if not (model_wanted or texel is not None or materials_wanted):
            continue
        try:
            measured = measure_glb((audit.project / path).read_bytes())
        except (ValueError, KeyError, IndexError, TypeError) as error:
            audit.errors.append(f"{path}: the model could not be measured ({error})")
            continue
        owner, why = audit.owner(path, {"blender"})
        if model_wanted:
            audit.checked["models"].add(path)
        if models.applied_scale:
            scaled = [
                n
                for n in measured.nodes
                if n.mesh and any(abs(s - 1) > 0.001 for s in n.scale)
            ]
            if scaled:
                shown = ", ".join(
                    f"{n.name} {max(n.scale, key=lambda s: abs(s - 1)):g}"
                    for n in scaled[:3]
                )
                audit.finding(
                    "models",
                    path,
                    "scale",
                    "high",
                    f"scale {shown}",
                    "scale 1",
                    f"scale is not applied on {', '.join(n.name for n in scaled[:3])}",
                    fix=None
                    if owner is None
                    else _blender_fix(
                        owner,
                        "apply_scale",
                        {"objects": [n.name for n in scaled]},
                        "Apply scale in Blender",
                    ),
                    needs_you=why,
                )
        if (
            pivot in ("bottom-centre", "centre")
            and measured.bbox_min
            and measured.bbox_max
        ):
            low, high = measured.bbox_min, measured.bbox_max
            centre = [(a + b) / 2 for a, b in zip(low, high, strict=True)]
            off = (
                max(abs(low[1]), abs(centre[0]), abs(centre[2]))
                if pivot == "bottom-centre"
                else max(abs(c) for c in centre)
            )
            if off > models.pivot_tolerance_m:
                audit.finding(
                    "models",
                    path,
                    "pivot",
                    "medium",
                    f"{off:.2f} m off",
                    pivot,
                    f"the pivot is {off:.2f} m from its {pivot}",
                    fix=None
                    if owner is None
                    else _blender_fix(
                        owner,
                        "set_pivot",
                        {"pivot": pivot},
                        f"Pivot to {pivot} in Blender",
                    ),
                    needs_you=why,
                )
        if budget is not None and measured.triangles > budget:
            audit.finding(
                "models",
                path,
                "triangles",
                "high" if measured.triangles > 1.5 * budget else "medium",
                f"{measured.triangles} triangles",
                f"≤ {budget}",
                f"{measured.triangles} triangles, over the {budget} budget",
                fix=None
                if owner is None
                else _blender_fix(
                    owner,
                    "reduce_triangles",
                    {"max_triangles": budget},
                    "Decimate in Blender",
                ),
                needs_you=why,
            )
        if bones is not None and measured.bones > bones:
            audit.finding(
                "models",
                path,
                "bones",
                "medium",
                f"{measured.bones} bones",
                f"≤ {bones}",
                f"{measured.bones} bones in a skin, over the {bones} budget",
                needs_you="Removing bones changes the rig; do it in Blender with Journeyman.",
            )
        if texel is not None:
            audit.checked["textures"].add(path)
            wrong = {
                node: value
                for node, value in measured.texel_density.items()
                if abs(value - texel.px_per_m) > texel.tolerance * texel.px_per_m
            }
            if wrong:
                worst = max(
                    wrong.values(), key=lambda v: abs(math.log(v / texel.px_per_m))
                )
                audit.finding(
                    "textures",
                    path,
                    "texel_density",
                    "high"
                    if abs(worst - texel.px_per_m)
                    > 2 * texel.tolerance * texel.px_per_m
                    else "medium",
                    f"{worst:.0f} px/m",
                    f"{texel.px_per_m:g} px/m ± {texel.tolerance:.0%}",
                    f"texel density {worst:.0f} px/m on {', '.join(sorted(wrong)[:3])}",
                    fix=None
                    if owner is None
                    else _blender_fix(
                        owner,
                        "set_texel_density",
                        {
                            "objects": sorted(wrong),
                            "px_per_m": texel.px_per_m,
                            "power_of_two": rules.textures.power_of_two,
                        },
                        "Texel density in Blender",
                    ),
                    needs_you=why,
                )
        if materials_wanted:
            _materials(audit, path, measured.materials, owner, why)


def _materials(
    audit: _Audit, path: str, materials: Iterable[Any], owner: Owner | None, why: str
) -> None:
    rules = audit.rules.materials
    metal = (
        re.compile(rules.metallic_requires_name)
        if rules.metallic_requires_name
        else None
    )
    for material in materials:
        audit.checked["materials"].add(f"{path}#{material.name}")
        changes: list[tuple[str, float, float, str]] = []
        if rules.roughness is not None and material.roughness is not None:
            low, high = rules.roughness
            if not low - 1e-4 <= material.roughness <= high + 1e-4:
                changes.append(
                    (
                        "Roughness",
                        material.roughness,
                        min(max(material.roughness, low), high),
                        f"{low:g}–{high:g}",
                    )
                )
        if material.metallic is not None:
            low, high = rules.metallic or (0.0, 1.0)
            value = min(max(material.metallic, low), high)
            if metal is not None and value > 0.05 and not metal.search(material.name):
                value = max(low, 0.0)
                changes.append(
                    ("Metallic", material.metallic, value, "0 unless named metal")
                )
            elif value != material.metallic and abs(value - material.metallic) > 1e-4:
                changes.append(
                    ("Metallic", material.metallic, value, f"{low:g}–{high:g}")
                )
        for socket, found, value, target in changes:
            audit.finding(
                "materials",
                path,
                socket.lower(),
                "medium",
                f"{socket} {found:.2f}",
                target,
                f"{material.name}: {socket.lower()} {found:.2f}, the art bible allows {target}",
                subject=material.name,
                fix=None
                if owner is None
                else _blender_fix(
                    owner,
                    "set_material_value",
                    {
                        "material": material.name,
                        "socket": socket,
                        "value": round(value, 4),
                    },
                    f"{socket} {value:g} on {material.name} in Blender",
                ),
                needs_you=why,
            )


# --- environments and UI -------------------------------------------------------------


def _godot_fix(audit: _Audit, path: str, issue: match_look.Issue) -> Fix | None:
    """manage_resource for a resource file's own properties, else a text patch."""
    if issue.swap is not None:
        return Fix(
            server="godot",
            tool="write_file",
            arguments={"filePath": path, "patches": [{"swap": list(issue.swap)}]},
            apps=["godot"],
            source=path,
            label="Font in the theme",
        )
    if not issue.props:
        return None
    if issue.section.kind == "resource" and _suffix(path) == ".tres":
        return Fix(
            server="godot",
            tool="manage_resource",
            arguments={
                "projectPath": str(audit.project),
                "resourcePath": path,
                "action": "modify",
                "properties": dict(issue.props),
            },
            apps=["godot"],
            source=path,
            label="Set " + ", ".join(issue.props) + " in Godot",
        )
    return Fix(
        server="godot",
        tool="write_file",
        arguments={
            "filePath": path,
            "patches": [
                {
                    "section": [issue.section.kind, issue.section.id],
                    "props": dict(issue.props),
                }
            ],
        },
        apps=["godot"],
        source=path,
        label="Set " + ", ".join(issue.props) + " in Godot",
    )


def look(audit: _Audit, paths: list[str]) -> None:
    rules = audit.rules
    environment = rules.environment
    env_wanted = any(
        getattr(environment, name) is not None
        for name in (
            "tonemap",
            "exposure",
            "fog",
            "glow",
            "brightness",
            "contrast",
            "saturation",
        )
    )
    access = rules.accessibility
    palette = rules.palette.all_colours()
    minimum = None
    if access.min_text_px is not None:
        minimum = access.min_text_px * viewport_height(audit.project) / 1080
    ui_wanted = (
        bool(rules.ui.fonts)
        or (rules.ui.palette_colours and palette)
        or minimum is not None
    )
    for path in paths:
        if _suffix(path) not in _GODOT_TEXT:
            continue
        text = (audit.project / path).read_text(encoding="utf-8", errors="replace")
        if env_wanted and _in_folders(environment.folders, path):
            for section, values in match_look.environments(text):
                audit.checked["lighting"].add(f"{path}#{section.id}")
                for issue in match_look.environment_issues(
                    section, values, environment
                ):
                    audit.finding(
                        "lighting",
                        path,
                        issue.check,
                        issue.severity,  # type: ignore[arg-type]
                        issue.measured,
                        issue.target,
                        issue.message,
                        subject=section.id,
                        fix=_godot_fix(audit, path, issue),
                    )
        if not ui_wanted or not _in_folders(access.ui_folders, path):
            continue
        issues = match_look.ui_issues(
            text,
            palette=palette,
            max_delta_e=rules.palette.max_delta_e,
            fonts=rules.ui.fonts,
            min_px=minimum,
            palette_colours=rules.ui.palette_colours,
        )
        themed = _themed(text)
        if themed or issues:
            audit.checked["ui"].add(path)
        for issue in issues:
            audit.finding(
                "ui",
                path,
                issue.check,
                issue.severity,  # type: ignore[arg-type]
                issue.measured,
                issue.target,
                issue.message,
                subject=f"{issue.section.id}:{next(iter(issue.props), '')}",
                fix=_godot_fix(audit, path, issue),
            )


def _themed(text: str) -> bool:
    """The file says something about how UI looks: a theme, styles, fonts or sizes."""
    return any(
        marker in text
        for marker in (
            'type="Theme"',
            "StyleBoxFlat",
            "/colors/",
            "font_size",
            "FontFile",
        )
    )


# --- sound ---------------------------------------------------------------------------


def _music_mix(
    audit: _Audit, label: str, stems: list[str], values: dict[str, float]
) -> None:
    """A track section's mix against the music loudness; fixed by rendering again."""
    rules = audit.rules.audio
    description, _, rest = label.partition(" (")
    subject = rest.rstrip(")")
    if "lufs" not in values:
        audit.errors.append(f"{label}: its loudness could not be measured")
        return
    target = _folder_value(rules.lufs, stems[0])
    if target is None or abs(values["lufs"] - target) <= rules.lufs_tolerance:
        return
    name = PurePosixPath(description).stem
    owner, why = audit.owner(stems[0], SOUND_TOOLS)
    fix = (
        None
        if owner is None
        else Fix(
            server="joinery-sound",
            tool="edit_track",
            arguments={"name": name, "changes": {}, "lufs": target},
            apps=[owner.tool],
            source=description,
            label=f"Render {name} again at {target:g} LUFS",
            outputs=stems,
        )
    )
    gap = abs(values["lufs"] - target)
    audit.finding(
        "sound",
        description,
        "loudness",
        "high" if gap > 3 * max(rules.lufs_tolerance, 1) else "medium",
        f"{values['lufs']:.1f} LUFS",
        f"{target:g} LUFS",
        f"the {subject} is {values['lufs']:.1f} LUFS, the art bible asks for "
        f"{target:g} ± {rules.lufs_tolerance:g}",
        fix=fix,
        needs_you=why,
        subject=subject,
    )


async def sound(audit: _Audit, paths: list[str]) -> None:
    rules = audit.rules.audio
    audio = [
        p
        for p in paths
        if _suffix(p) in _AUDIO
        and p.startswith("audio/")
        and (
            rules.true_peak_max_dbtp is not None
            or _folder_value(rules.lufs, p) is not None
        )
    ]
    if not audio:
        return
    for path in audio:
        audit.checked["sound"].add(path)
    # A music track's stems are judged as the section mix they play in.
    mixes = {
        label: group
        for label, group in section_mixes(audit.project).items()
        if set(group) <= set(audio)
    }
    stems = {stem for group in mixes.values() for stem in group}
    try:
        got = await audit.measure(["sh", "-c", _MEASURE, "sh", *audio], audit.project)
        loudness = parse_loudness(got.output) if got.ok else {}
        if mixes:
            got = await audit.measure(
                ["sh", "-c", mix_measure_script(mixes)], audit.project
            )
            loudness.update(parse_loudness(got.output) if got.ok else {})
    except (OSError, RuntimeError, TimeoutError) as error:
        audit.errors.append(f"Loudness could not be measured: {error}")
        loudness = {}
    for label, group in mixes.items():
        _music_mix(audit, label, group, loudness.get(label, {}))
    for path in audio:
        values = loudness.get(path, {})
        if "lufs" not in values or "true_peak" not in values:
            audit.errors.append(f"{path}: its loudness could not be measured")
            continue
        target = None if path in stems else _folder_value(rules.lufs, path)
        ceiling = rules.true_peak_max_dbtp
        loud_off = (
            target is not None and abs(values["lufs"] - target) > rules.lufs_tolerance
        )
        peak_off = ceiling is not None and values["true_peak"] > ceiling
        if not loud_off and not peak_off:
            continue
        owner, why = audit.owner(path, SOUND_TOOLS)
        step: dict[str, Any]
        if target is not None:
            step = {"op": "normalise", "lufs": target}
            if ceiling is not None:
                step["true_peak"] = ceiling
            label = f"Normalise to {target:g} LUFS"
        else:
            assert ceiling is not None
            step = {"op": "gain", "db": round(ceiling - values["true_peak"] - 0.5, 2)}
            label = f"Lower the peak below {ceiling:g} dBTP"
        fix = (
            None
            if owner is None
            else Fix(
                server="joinery-sound",
                tool="process_audio",
                arguments={"source": path, "steps": [step]},
                apps=[owner.tool],
                source=path,
                label=label,
            )
        )
        if loud_off:
            assert target is not None
            gap = abs(values["lufs"] - target)
            measured, wanted = f"{values['lufs']:.1f} LUFS", f"{target:g} LUFS"
            message = f"{values['lufs']:.1f} LUFS, the art bible asks for {target:g} ± {rules.lufs_tolerance:g}"
            severity: Severity = (
                "high" if gap > 3 * max(rules.lufs_tolerance, 1) else "medium"
            )
        else:
            assert ceiling is not None
            measured, wanted = f"{values['true_peak']:.1f} dBTP", f"≤ {ceiling:g} dBTP"
            message = (
                f"true peak {values['true_peak']:.1f} dBTP, above {ceiling:g} dBTP"
            )
            severity = "high"
        audit.finding(
            "sound",
            path,
            "loudness",
            severity,
            measured,
            wanted,
            message,
            fix=fix,
            needs_you=why,
        )
