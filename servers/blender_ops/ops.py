"""Typed part operations of joinery-blender-ops beyond materials and pixels.

* Maps as editable sources: `make_map_editable` starts a Krita document (or a
  Material Maker graph) from a map's pixels, `paint_map` adds typed Krita layers
  to it, `replace_map` puts any tool-made image in the map. Each ends with a
  Blender edit that loads the result into the map, so the model's recipe lists
  the image it read and `joinery rebuild` replays both steps.
* `split_material` gives some objects their own copy of a shared material.
* `edit_bone` and `edit_shape_key` change one bone or one shape key.

Every operation refuses names outside the prompt's part selection, and the
Blender side runs inside the scope guard, so nothing else in the model changes.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path, PurePosixPath
from typing import Any

from mcp.server import MCPServer

from joinery import parts
from joinery.authoring import confined, edit_document, edit_graph
from joinery.locking import claimed_outputs, write_lock
from joinery.recipe import HISTORY_DIR, RECIPE_DIR, store_preimage
from joinery.tools import blender_parts, krita_ops
from servers.authoring import Step, _exports, _name, run_step
from servers.errors import tool_errors

#: Where a model's editable maps live: art/sources/maps/<model>/<image>.kra.
MAPS_DIR = "art/sources/maps"
EDITORS = ("krita", "material-maker")
#: Material Maker material inputs by map role, and the export file each writes.
MM_PORTS = {
    "base_color": (0, "albedo"),
    "metallic": (1, "metallic"),
    "roughness": (2, "roughness"),
    "emission": (3, "emission"),
    "normal": (4, "normal"),
    "ao": (5, "ambient_occlusion"),
    "height": (6, "depth"),
}


def _clean(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_") or "map"


def map_paths(source: str, image: str, editor: str = "krita") -> tuple[str, str]:
    """(editable source, its PNG) for one map of a model."""
    folder = f"{MAPS_DIR}/{Path(source).stem}"
    stem = _clean(image)
    if editor == "material-maker":
        return f"{folder}/{stem}.ptex", f"{folder}/{stem}_export"
    return f"{folder}/{stem}.kra", f"{folder}/{stem}.png"


class _Undo:
    """Put the stage back as it was when a two-step map operation fails halfway."""

    def __init__(self, project: Path, files: tuple[str, ...]) -> None:
        self.project = project
        self.files = {f: _read(project / f) for f in files}
        self.existed = {f for f in files if (project / f).exists()}
        self.recipes = set(_listing(project / RECIPE_DIR))
        self.history = set(_listing(project / HISTORY_DIR))

    def restore(self) -> None:
        project = self.project
        for name in set(_listing(project / RECIPE_DIR)) - self.recipes:
            (project / RECIPE_DIR / name).unlink(missing_ok=True)
        for name in set(_listing(project / HISTORY_DIR)) - self.history:
            (project / HISTORY_DIR / name).rename(project / RECIPE_DIR / name)
        for relative, data in self.files.items():
            path = project / relative
            if data is not None:
                path.write_bytes(data)
            elif relative in self.existed:
                continue
            elif path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
        write_lock(project)


def _read(path: Path) -> bytes | None:
    return path.read_bytes() if path.is_file() else None


def _listing(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.glob("*.toml")) if folder.is_dir() else []


async def _map_part(
    project: Path, source: str, material: str, image: str
) -> parts.Part:
    """The map as Blender reports it, after checking it is in the selection."""
    _name(material, "material")
    _name(image, "image")
    parts.check_targets(
        parts.prompt_targets(project, source), material=material, image=image
    )
    report = await parts.report(project, source)
    return report.find({"part_kind": "texture", "material": material, "image": image})


async def _link(
    project: Path,
    source: str,
    material: str,
    image: str,
    picture: str,
    editable: str,
    exports: list[str] | None,
    note: str,
) -> dict[str, Any]:
    """The Blender edit that loads `picture` into the map and records it as a source."""
    return await edit_document(
        project,
        "blender",
        source,
        blender_parts.link_map_code(material, image, picture, editable),
        _exports(project, source, exports),
        note,
        inputs=(picture,),
    )


async def _two_steps(
    project: Path, files: tuple[str, ...], first: Any, second: Any
) -> dict[str, Any]:
    """Run the tool step then the Blender step; undo the first if the second fails."""
    undo = _Undo(project, files)
    try:
        made = await first()
        linked: dict[str, Any] = await second(made)
    except BaseException:
        undo.restore()
        raise
    return linked


async def make_map_editable(
    project: Path,
    source: str,
    material: str,
    image: str,
    editor: str,
    exports: list[str] | None,
) -> dict[str, Any]:
    if editor not in EDITORS:
        raise ValueError(f"editor is one of: {', '.join(EDITORS)}.")
    part = await _map_part(project, source, material, image)
    if part.editable:
        return {
            "status": "unchanged",
            "editable": part.editable,
            "note": f"{image} is already painted in {part.editable}. Use paint_map.",
        }
    if not part.texture or not (project / part.texture).is_file():
        raise ValueError(
            f"{image} has no exported pixels ({part.texture or 'no file'}). "
            "Export the model once, then try again."
        )
    seed = store_preimage(project, project / part.texture)
    document, picture = map_paths(source, image, editor)
    if editor == "material-maker":
        role = part.role if part.role in MM_PORTS else "base_color"
        port, suffix = MM_PORTS[role]
        stem = Path(document).stem
        result_png = f"{picture}/{stem}_{suffix}.png"
        graph = {
            "type": "graph",
            "name": stem,
            "nodes": [
                {
                    "type": "material",
                    "name": "Material",
                    "parameters": {"size": _mm_size(part, project)},
                },
                {"type": "image", "name": "Map", "parameters": {"image": seed}},
            ],
            "connections": [
                {"from": "Map", "from_port": 0, "to": "Material", "to_port": port}
            ],
        }

        async def first() -> dict[str, Any]:
            return await edit_graph(project, document, graph, picture)

        async def second(made: dict[str, Any]) -> dict[str, Any]:
            if result_png not in made["outputs"]:
                raise RuntimeError(
                    f"Material Maker wrote no {Path(result_png).name}: {made['outputs']}"
                )
            linked = await _link(
                project,
                source,
                material,
                image,
                result_png,
                document,
                exports,
                f"Map {image} of {material} from the Material Maker graph {document}",
            )
            return {**linked, "editable": document, "map": result_png, "graph": made}

        return await _two_steps(project, (document, picture), first, second)

    async def seed_krita() -> dict[str, Any]:
        return await edit_document(
            project,
            "krita",
            document,
            krita_ops.seed_code(seed),
            [picture],
            f"{image} of {material} as a Krita document, from the model's pixels",
            inputs=(seed,),
        )

    async def link_krita(made: dict[str, Any]) -> dict[str, Any]:
        linked = await _link(
            project,
            source,
            material,
            image,
            picture,
            document,
            exports,
            f"Map {image} of {material} from the Krita document {document}",
        )
        return {**linked, "editable": document, "map": picture, "painting": made}

    return await _two_steps(project, (document, picture), seed_krita, link_krita)


def _mm_size(part: parts.Part, project: Path) -> int:
    """Material Maker's size setting (2^n pixels) closest to the map's own size."""
    import struct

    data = (project / part.texture).read_bytes()[:24]
    width = struct.unpack(">I", data[16:20])[0] if data[:4] == b"\x89PNG" else 512
    return max(4, min(12, round(width.bit_length() - 1)))


async def paint_map(
    project: Path,
    source: str,
    material: str,
    image: str,
    ops: list[dict[str, Any]],
    exports: list[str] | None,
) -> dict[str, Any]:
    checked = krita_ops.check_ops(ops)
    part = await _map_part(project, source, material, image)
    document = part.editable
    if not document:
        raise ValueError(
            f"{image} has no Krita document yet. Call make_map_editable first."
        )
    if Path(document).suffix != ".kra":
        raise ValueError(
            f"{image} is a Material Maker graph ({document}). Change it with "
            "operate_graph, then call replace_map with its export."
        )
    if not (project / document).is_file():
        raise ValueError(f"{document} is missing. Call make_map_editable again.")
    picture = str(PurePosixPath(document).with_suffix(".png"))
    summary = ", ".join(row["op"] + " " + row["name"] for row in checked)

    async def paint() -> dict[str, Any]:
        return await edit_document(
            project,
            "krita",
            document,
            krita_ops.paint_code(checked),
            [picture],
            f"Paint {image} of {material}: {summary}",
        )

    async def link(made: dict[str, Any]) -> dict[str, Any]:
        linked = await _link(
            project,
            source,
            material,
            image,
            picture,
            document,
            exports,
            f"Map {image} of {material} from the Krita document {document}",
        )
        return {**linked, "editable": document, "map": picture, "painting": made}

    return await _two_steps(project, (document, picture), paint, link)


async def replace_map(
    project: Path,
    source: str,
    material: str,
    image: str,
    replacement: str,
    exports: list[str] | None,
) -> dict[str, Any]:
    relative = replacement.removeprefix("res://")
    path = confined(project, relative)
    if Path(relative).parts[0] != "art" or path.suffix.lower() not in {
        ".png",
        ".jpg",
        ".jpeg",
    }:
        raise ValueError("The replacement is a PNG or JPEG under art/ in this project.")
    if not path.is_file():
        raise ValueError(f"{relative} is not a file in this project.")
    textures = PurePosixPath(f"art/textures/{Path(source).stem}")
    if PurePosixPath(relative).is_relative_to(textures):
        raise ValueError(
            f"{relative} is one of this model's own exported maps. "
            "Use a Krita, Material Maker or GIMP image, or another model's map."
        )
    claims = claimed_outputs(project)
    if relative not in claims:
        raise ValueError(
            f"{relative} was not made by a project tool, so it cannot go in a model. "
            "Make it with Krita, Material Maker or GIMP first."
        )
    part = await _map_part(project, source, material, image)
    tool, _recipe = claims[relative]
    # The map's own Krita or Material Maker export keeps the link to its
    # source; any other image replaces it.
    editable = part.editable if relative in _editable_outputs(part.editable) else ""
    linked = await _link(
        project,
        source,
        material,
        image,
        relative,
        editable,
        exports,
        f"Map {image} of {material} replaced with {relative} ({tool})",
    )
    return {**linked, "map": relative, "made_by": tool}


def _editable_outputs(document: str) -> set[str]:
    """Images an editable map source exports: the Krita PNG, or the graph's files."""
    if not document:
        return set()
    path = PurePosixPath(document)
    if path.suffix == ".kra":
        return {str(path.with_suffix(".png"))}
    folder = f"{path.parent}/{path.stem}_export"
    return {f"{folder}/{path.stem}_{suffix}.png" for _port, suffix in MM_PORTS.values()}


async def split_material(
    project: Path,
    source: str,
    material: str,
    objects: list[str],
    name: str,
    copy_maps: bool,
    exports: list[str] | None,
) -> dict[str, Any]:
    _name(material, "material")
    if not objects or len(objects) > 64:
        raise ValueError("Name the objects that get their own copy.")
    for obj in objects:
        _name(obj, "object")
    if name and (len(name) > 63 or not name.strip()):
        raise ValueError("The copy's name is 1 to 63 characters.")
    targets = parts.prompt_targets(project, source)
    if targets:
        parts.check_targets(targets, material=material)
        allowed: set[str] = set()
        for target in targets:
            allowed.add(target.get("object", ""))
            if target.get("material") == material or material in target.get(
                "materials", ""
            ).split(","):
                allowed |= set(target.get("used_by", "").split(","))
        outside = [obj for obj in objects if obj not in allowed - {""}]
        if outside:
            raise ValueError(
                f"Object {outside[0]!r} is not part of the selection. Pick it, "
                "or pick the material, first."
            )
    report = await parts.report(project, source)
    found = report.find({"part_kind": "material", "material": material})
    missing = [obj for obj in objects if obj not in found.used_by]
    if missing:
        raise ValueError(
            f"{missing[0]!r} does not use {material!r}. It is used by: "
            + ", ".join(found.used_by)
        )
    copy, images = blender_parts.split_names(material, objects, list(found.maps), name)
    taken = {p.material for p in report.parts} | {p.image for p in report.parts}
    clash = [n for n in (copy, *images.values()) if n in taken]
    if clash:
        raise ValueError(
            f"{clash[0]!r} is already a name in this model. Give the copy a name."
        )
    result = await edit_document(
        project,
        "blender",
        source,
        blender_parts.split_material_code(
            material, objects, copy, images if copy_maps else None
        ),
        _exports(project, source, exports),
        f"Copy {material} as {copy} for {', '.join(objects)}",
    )
    return {**result, "material": copy, "maps": images if copy_maps else {}}


def _check_owner(
    project: Path, source: str, obj: str, field: str, value: str, new: bool
) -> None:
    """Refuse an object, bone or shape key outside the prompt's selection."""
    targets = parts.prompt_targets(project, source)
    if not targets:
        return
    for target in targets:
        if target.get("object") != obj:
            continue
        kind = target.get("part_kind")
        if kind == "object" or target.get(field) == value or (new and kind == field):
            return
    labels = ", ".join(t.get("label") or parts.chip_label(t) for t in targets)
    raise ValueError(
        f"{field.replace('_', ' ').capitalize()} {value!r} of {obj!r} is not part of "
        f"the selection ({labels}). Pick it in the parts list first."
    )


def plan_edit_bone(
    project: Path,
    source: str,
    armature: str,
    bone: str,
    op: str,
    params: dict[str, Any] | None = None,
    exports: list[str] | None = None,
) -> Step:
    _name(armature, "armature")
    _name(bone, "bone")
    checked = blender_parts.check_params(
        blender_parts.BONE_OPS, op, dict(params or {}), "bone"
    )
    _check_owner(project, source, armature, "bone", bone, False)
    return Step(
        source,
        blender_parts.edit_bone_code(armature, bone, op, checked),
        f"Bone {bone} of {armature}: {op} {checked}",
        exports,
    )


async def edit_bone(
    project: Path,
    source: str,
    armature: str,
    bone: str,
    op: str,
    params: dict[str, Any] | None,
    exports: list[str] | None,
) -> dict[str, Any]:
    return await run_step(
        project, plan_edit_bone(project, source, armature, bone, op, params, exports)
    )


def plan_edit_shape_key(
    project: Path,
    source: str,
    object: str,
    shape_key: str,
    op: str,
    params: dict[str, Any] | None = None,
    exports: list[str] | None = None,
) -> Step:
    obj = object
    _name(obj, "object")
    _name(shape_key, "shape key")
    checked = blender_parts.check_params(
        blender_parts.SHAPE_KEY_OPS, op, dict(params or {}), "shape key"
    )
    new = op in {"new_from_mix", "new_from_pose"}
    if new and (len(shape_key) > 63 or not shape_key.strip()):
        raise ValueError("The new shape key's name is 1 to 63 characters.")
    _check_owner(project, source, obj, "shape_key", shape_key, new)
    return Step(
        source,
        blender_parts.edit_shape_key_code(obj, shape_key, op, checked),
        f"Shape key {shape_key} of {obj}: {op} {checked}",
        exports,
    )


async def edit_shape_key(
    project: Path,
    source: str,
    obj: str,
    shape_key: str,
    op: str,
    params: dict[str, Any] | None,
    exports: list[str] | None,
) -> dict[str, Any]:
    return await run_step(
        project,
        plan_edit_shape_key(project, source, obj, shape_key, op, params, exports),
    )


def register(server: MCPServer[Any], project: Path) -> None:
    """Add the map, material copy, bone, shape key, whole-model, kit and animation operations."""
    from servers.blender_ops import animation, batch, kits, model_ops

    model_ops.register(server, project)
    kits.register(server, project)
    animation.register(server, project)
    batch.register(server, project)

    @server.tool(name="make_map_editable")
    @tool_errors
    async def make_map_editable_tool(
        source: str,
        material: str,
        image: str,
        editor: str = "krita",
        exports: list[str] | None = None,
    ) -> dict[str, Any]:
        """Make one texture map of a Blender model an editable source: a Krita document (editor "krita", for painting) or a Material Maker graph (editor "material-maker", for procedural changes) that starts from the map's own pixels, under art/sources/maps/<model>/. The model's map is then loaded from that source, so later paint_map edits replay. Run it once per map; the parts list shows maps that have a source. Exports default to the model's existing GLB."""
        return await make_map_editable(
            project, source, material, image, editor, exports
        )

    @server.tool(name="paint_map")
    @tool_errors
    async def paint_map_tool(
        source: str,
        material: str,
        image: str,
        ops: list[dict[str, Any]],
        exports: list[str] | None = None,
    ) -> dict[str, Any]:
        """Paint a map made editable in Krita with typed layers, then load it into the model. Each op adds a layer on top: {"op": "fill", "color": [r, g, b(, a)], "rect"?: [x, y, w, h], "opacity"?, "name"?}; {"op": "stroke", "points": [[x, y], ...], "color", "width" (pixels), "opacity"?, "name"?}; {"op": "adjust", "filter": "invert" | "levels" {in_low, in_high, gamma, out_low, out_high} | "hue_saturation" {hue -0.5..0.5, saturation -1..1, value -1..1} | "blur" {radius} | "desaturate", "params"?, "opacity"?}. Colours are sRGB 0-1; pixels count from the top left of the map."""
        return await paint_map(project, source, material, image, ops, exports)

    @server.tool(name="replace_map")
    @tool_errors
    async def replace_map_tool(
        source: str,
        material: str,
        image: str,
        replacement: str,
        exports: list[str] | None = None,
    ) -> dict[str, Any]:
        """Put a tool-made image (a PNG or JPEG under art/ that a project recipe makes, such as a Krita, GIMP or Material Maker export) into one map of a Blender model. The model's recipe lists the image, so a rebuild reads it again. Also use it after changing a map's Material Maker graph."""
        return await replace_map(project, source, material, image, replacement, exports)

    @server.tool(name="split_material")
    @tool_errors
    async def split_material_tool(
        source: str,
        material: str,
        objects: list[str],
        name: str = "",
        copy_maps: bool = True,
        exports: list[str] | None = None,
    ) -> dict[str, Any]:
        """Give some objects their own copy of a shared Blender material ("make a copy for Head only"), so per-part edits change only them. The copy is named <material>_<first object> unless named; with copy_maps (default) its maps are copied too (<image>_<copy>), so map edits stay separate. Other objects keep the original."""
        return await split_material(
            project, source, material, objects, name, copy_maps, exports
        )

    @server.tool(name="edit_bone")
    @tool_errors
    async def edit_bone_tool(
        source: str,
        armature: str,
        bone: str,
        op: str,
        params: dict[str, Any] | None = None,
        exports: list[str] | None = None,
    ) -> dict[str, Any]:
        """Change one bone of a Blender armature. op: rename {name} (vertex groups, constraints and animation follow); set_rest {head?, tail? [x, y, z] metres in armature space, roll_degrees?}; set_parent {parent: bone name or null, connected?}; scale_chain {factor} (the bone and all its children, about the bone's head). The skinned mesh follows the new rest pose."""
        return await edit_bone(project, source, armature, bone, op, params, exports)

    @server.tool(name="edit_shape_key")
    @tool_errors
    async def edit_shape_key_tool(
        source: str,
        object: str,
        shape_key: str,
        op: str,
        params: dict[str, Any] | None = None,
        exports: list[str] | None = None,
    ) -> dict[str, Any]:
        """Change one shape key of a Blender mesh. op: set_value {value}; rename {name}; delete; new_from_mix (a new key named shape_key from the current mix of keys); new_from_pose (a new key from the armature's current pose). The basis cannot be deleted or set."""
        return await edit_shape_key(
            project, source, object, shape_key, op, params, exports
        )
