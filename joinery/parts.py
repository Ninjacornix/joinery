"""Parts of a model the user can pick: objects, materials and texture maps.

A part is one named piece inside a native source document: an object of a
Blender model, a material, an image a material uses (with the role it plays:
base colour, roughness, normal, height and so on), a bone or a shape key. The
owning tool reports them by name; Joinery never guesses them from the game's
copy, because Godot renames nodes (``Head.001`` becomes ``Head_001``) and glTF
cannot carry some maps at all (a height map).

Each tool that can list parts registers a provider here, keyed by its tool id
(``ToolModule.parts_provider``): Blender now; Krita layers or Surge parameters
can follow without touching the callers. Reports are cached under
``.joinery/parts/`` by the source document's sha256, so the editor's parts
panel and the registry read them without starting a worker again.

    joinery parts art/models/hammer.glb --project P --json
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qsl, quote

from pydantic import BaseModel, ConfigDict

from joinery import tools
from joinery.recipe import load_recipe, recipe_paths

PartKind = Literal["object", "material", "texture", "bone", "shape_key", "animation"]
#: Where the editor and gateway put a prompt's part targets for the tool servers.
TARGETS_FILE = ".joinery/part_targets.json"
CACHE_DIR = ".joinery/parts"
#: Part id fields, in the order they appear in an id.
ID_FIELDS = ("object", "material", "image", "bone", "shape_key", "action")
#: How each map role reads in the editor.
ROLE_NAMES = {
    "base_color": "Base colour",
    "roughness": "Roughness",
    "metallic": "Metallic",
    "normal": "Normal",
    "height": "Height",
    "emission": "Emission",
    "alpha": "Alpha",
    "ao": "Ambient occlusion",
}
#: The names that identify a part of each kind.
IDENTITY: dict[str, tuple[str, ...]] = {
    "object": ("object",),
    "material": ("material",),
    "texture": ("material", "image"),
    "bone": ("object", "bone"),
    "shape_key": ("object", "shape_key"),
    # Action names are unique in a model; a chip from Godot may not know the rig.
    "animation": ("action",),
}
#: Typed operations per part kind (maps depend on the map; see Part.ops).
PART_OPS: dict[str, tuple[str, ...]] = {
    "object": ("transform_parts",),
    "material": ("set_material_value", "split_material"),
    "bone": ("edit_bone",),
    "shape_key": ("edit_shape_key",),
    "animation": ("edit_action", "make_cycle"),
}
KIND_NAMES = {
    "object": "Object",
    "material": "Material",
    "texture": "Texture",
    "bone": "Bone",
    "shape_key": "Shape key",
    "animation": "Animation",
}


class Part(BaseModel):
    """One selectable part. Names are the owning tool's own, never Godot's."""

    model_config = ConfigDict(frozen=True)

    id: str
    part_kind: PartKind
    resource: str
    source: str
    owner: str
    object: str = ""
    material: str = ""
    slot: str = ""
    image: str = ""
    node: str = ""
    role: str = ""
    roles: tuple[str, ...] = ()
    socket: str = ""
    channel: str = ""
    bone: str = ""
    shape_key: str = ""
    #: Objects that use this material (a shared material changes on all of them).
    used_by: tuple[str, ...] = ()
    #: Materials on this object.
    materials: tuple[str, ...] = ()
    #: Images this material uses.
    maps: tuple[str, ...] = ()
    #: The PNG Joinery writes for this image, beside the model.
    texture: str = ""
    #: In the source only: the game's copy cannot carry it (a height map).
    source_only: bool = False
    #: The Krita document or Material Maker graph this map is painted in.
    editable: str = ""
    #: An animation: its action, frame range ("0,24"), frames per second,
    #: length in seconds, whether it loops, and the motion Joinery made it as.
    action: str = ""
    frames: str = ""
    fps: str = ""
    seconds: str = ""
    loop: bool = False
    animation_kind: str = ""

    @property
    def ops(self) -> tuple[str, ...]:
        """The typed operations that change this part (joinery-blender-ops)."""
        if self.part_kind == "texture":
            found = ["adjust_map", "make_map_editable", "replace_map"]
            if self.editable.endswith(".kra"):
                found[1] = "paint_map"
            if "height" in self.roles:
                found.append("bake_height_to_normal")
            return (*found, "split_material")
        return PART_OPS.get(self.part_kind, ())

    @property
    def label(self) -> str:
        return chip_label(self.target())

    def target(self) -> dict[str, str]:
        """The flat string target the sidebar sends with a prompt."""
        flat: dict[str, str] = {
            "id": self.id,
            "kind": KIND_NAMES[self.part_kind]
            if self.part_kind != "texture"
            else "Image",
            "part_kind": self.part_kind,
            "resource": self.resource,
            "source": "res://" + self.source,
            "owner": self.owner,
            "owner_source": self.source,
        }
        for key in (
            "object",
            "material",
            "slot",
            "image",
            "node",
            "role",
            "socket",
            "channel",
            "bone",
            "shape_key",
            "texture",
            "action",
            "frames",
            "fps",
            "seconds",
            "animation_kind",
        ):
            flat[key] = str(getattr(self, key))
        flat["loop"] = "true" if self.loop else ""
        flat["roles"] = ",".join(self.roles)
        flat["used_by"] = ",".join(self.used_by)
        flat["materials"] = ",".join(self.materials)
        flat["maps"] = ",".join(self.maps)
        flat["source_only"] = "true" if self.source_only else ""
        flat["editable"] = self.editable
        flat["ops"] = ",".join(self.ops)
        flat["label"] = chip_label(flat)
        return flat


class PartsReport(BaseModel):
    resource: str
    source: str
    owner: str
    sha256: str
    parts: list[Part]

    def counts(self) -> dict[str, int]:
        counted = dict.fromkeys(KIND_NAMES, 0)
        for part in self.parts:
            counted[part.part_kind] += 1
        return counted

    def find(self, wanted: Mapping[str, str]) -> Part:
        """The part matching a target's names, or a clear error."""
        kind = str(wanted.get("part_kind", "")) or _kind_from_fields(wanted)
        keys = IDENTITY.get(kind, ())
        obj = str(wanted.get("object", ""))
        for part in self.parts:
            if part.part_kind != kind or not keys:
                continue
            if not all(str(wanted.get(key, "")) == getattr(part, key) for key in keys):
                continue
            # A material or map picked on a surface names the object it was on.
            if obj and kind in {"material", "texture"} and obj not in part.used_by:
                continue
            return part
        names = ", ".join(
            f"{key} {wanted[key]!r}" for key in ID_FIELDS if wanted.get(key)
        )
        raise ValueError(
            f"{self.source} has no {KIND_NAMES.get(kind, 'part').lower()} with "
            f"{names or 'no name'}. List its parts with `joinery parts`."
        )


def _kind_from_fields(fields: Mapping[str, str]) -> str:
    if fields.get("action"):
        return "animation"
    if fields.get("image"):
        return "texture"
    if fields.get("bone"):
        return "bone"
    if fields.get("shape_key"):
        return "shape_key"
    if fields.get("material"):
        return "material"
    return "object"


def part_id(resource: str, **fields: str) -> str:
    """A stable id: ``res://art/models/hammer.glb#material=Steel&image=steel_rough``."""
    resource = "res://" + resource.removeprefix("res://")
    query = "&".join(
        f"{key}={quote(fields[key], safe='')}" for key in ID_FIELDS if fields.get(key)
    )
    return resource + ("#" + query if query else "")


def parse_part_id(identifier: str) -> tuple[str, dict[str, str]]:
    """The resource and the named fields of a part id."""
    resource, _, query = identifier.partition("#")
    fields = dict(parse_qsl(query, keep_blank_values=False, strict_parsing=False))
    unknown = set(fields) - set(ID_FIELDS)
    if unknown:
        raise ValueError(f"Unknown part id fields: {', '.join(sorted(unknown))}")
    return resource, fields


def role_name(role: str) -> str:
    return ROLE_NAMES.get(role, role.replace("_", " ").capitalize())


def chip_label(target: Mapping[str, str]) -> str:
    """Leaf first: 'Roughness · Steel', 'Steel · Head', 'Head · Hammer'."""
    kind = str(target.get("part_kind", ""))
    model = Path(str(target.get("resource", "")).split("#")[0]).stem
    model = model.replace("_", " ").replace("-", " ").capitalize()
    obj = str(target.get("object", ""))
    material = str(target.get("material", ""))
    if kind == "texture":
        roles = [r for r in str(target.get("roles", "")).split(",") if r] or [
            str(target.get("role", ""))
        ]
        names = [role_name(r) for r in dict.fromkeys(roles) if r]
        leaf = (
            " and ".join([names[0], *[n.lower() for n in names[1:]]])
            if names
            else str(target.get("image", ""))
        )
        return f"{leaf} · {obj or material}"
    if kind == "material":
        return f"{material} · {obj or model}"
    if kind == "bone":
        return f"{target.get('bone', '')} · {obj or model}"
    if kind == "shape_key":
        return f"{target.get('shape_key', '')} · {obj or model}"
    if kind == "animation":
        return f"{target.get('action', '')} · {model}"
    return f"{obj} · {model}"


def chip_tooltip(target: Mapping[str, str]) -> str:
    """The breadcrumb, who shares the material, and where it is edited."""
    model = Path(str(target.get("resource", "")).split("#")[0]).stem
    crumbs = [model.replace("_", " ").capitalize()]
    for key in ("object", "material"):
        if target.get(key):
            crumbs.append(str(target[key]))
    if target.get("part_kind") == "texture":
        crumbs.append(chip_label(target).split(" · ")[0])
    for key in ("bone", "shape_key", "action"):
        if target.get(key):
            crumbs.append(str(target[key]))
    lines = [" › ".join(crumbs)]
    users = [u for u in str(target.get("used_by", "")).split(",") if u]
    if len(users) > 1:
        lines.append("Used by " + ", ".join(users))
    owner = str(target.get("owner", ""))
    if owner:
        lines.append(f"In {owner.capitalize()}: {target.get('owner_source', '')}")
    return "\n".join(lines)


# --- providers -------------------------------------------------------------

Provider = Callable[[dict[str, Any], str, str], list[Part]]
PROVIDERS: dict[str, Provider] = {}


def provider(tool: str) -> Callable[[Provider], Provider]:
    """Register the parts provider for a tool id."""

    def register(function: Provider) -> Provider:
        PROVIDERS[tool] = function
        return function

    return register


@provider("blender")
def blender_parts(inspection: dict[str, Any], resource: str, source: str) -> list[Part]:
    """Objects, materials and texture maps from Blender's part inspection."""
    texture_dir = f"art/textures/{Path(source).stem}"
    found: list[Part] = []
    objects = [o for o in inspection.get("objects", []) if isinstance(o, dict)]
    for obj in objects:
        name = str(obj["name"])
        if obj.get("type", "MESH") not in {"MESH", "ARMATURE", "CURVE", "EMPTY"}:
            continue
        slots = tuple(
            dict.fromkeys(
                str(s["material"]) for s in obj.get("slots", []) if s.get("material")
            )
        )
        found.append(
            Part(
                id=part_id(resource, object=name),
                part_kind="object",
                object=name,
                materials=slots,
                resource=resource,
                source=source,
                owner="blender",
            )
        )
        for key in obj.get("shape_keys", [])[1:]:
            found.append(
                Part(
                    id=part_id(resource, object=name, shape_key=str(key)),
                    part_kind="shape_key",
                    object=name,
                    shape_key=str(key),
                    resource=resource,
                    source=source,
                    owner="blender",
                )
            )
        for bone in obj.get("bones", []):
            found.append(
                Part(
                    id=part_id(resource, object=name, bone=str(bone["name"])),
                    part_kind="bone",
                    object=name,
                    bone=str(bone["name"]),
                    resource=resource,
                    source=source,
                    owner="blender",
                )
            )
    found += _animation_parts(inspection, objects, resource, source)
    slot_of = {
        (str(o["name"]), str(s["material"])): str(s["index"])
        for o in objects
        for s in o.get("slots", [])
        if s.get("material")
    }
    for material in inspection.get("materials", []):
        if not isinstance(material, dict):
            continue
        name = str(material["name"])
        users = tuple(str(u) for u in material.get("users", []))
        images = [i for i in material.get("images", []) if isinstance(i, dict)]
        found.append(
            Part(
                id=part_id(resource, material=name),
                part_kind="material",
                material=name,
                used_by=users,
                maps=tuple(dict.fromkeys(str(i["image"]) for i in images)),
                slot=slot_of.get((users[0], name), "") if len(users) == 1 else "",
                resource=resource,
                source=source,
                owner="blender",
            )
        )
        for image in images:
            uses = [u for u in image.get("uses", []) if isinstance(u, dict)]
            roles = tuple(dict.fromkeys(str(u["role"]) for u in uses))
            first = uses[0] if uses else {}
            channels = [str(u.get("channel") or "") for u in uses]
            file = str(image.get("file", ""))
            found.append(
                Part(
                    id=part_id(resource, material=name, image=str(image["image"])),
                    part_kind="texture",
                    material=name,
                    image=str(image["image"]),
                    node=str(image.get("node", "")),
                    role=roles[0] if roles else "",
                    roles=roles,
                    socket=str(first.get("socket", "")),
                    channel=channels[0] if len(set(channels)) == 1 else "",
                    used_by=users,
                    texture=f"{texture_dir}/{file}" if file else "",
                    # glTF has no height map: it reaches the game only once baked.
                    source_only=bool(roles) and set(roles) <= {"height"},
                    editable=str(image.get("editable", "")),
                    resource=resource,
                    source=source,
                    owner="blender",
                )
            )
    return found


def _animation_parts(
    inspection: dict[str, Any],
    objects: list[dict[str, Any]],
    resource: str,
    source: str,
) -> list[Part]:
    """Each action, on the armature that plays it (or the model's only armature)."""
    rigs = [str(o["name"]) for o in objects if o.get("type") == "ARMATURE"]
    found: list[Part] = []
    for row in inspection.get("actions", []):
        if not isinstance(row, dict) or not row.get("name"):
            continue
        name = str(row["name"])
        rig = str(row.get("armature") or (rigs[0] if len(rigs) == 1 else ""))
        frames = [float(f) for f in row.get("frames", [0, 0])][:2] or [0.0, 0.0]
        fps = float(row.get("fps") or 24.0)
        found.append(
            Part(
                id=part_id(resource, object=rig, action=name),
                part_kind="animation",
                object=rig,
                action=name,
                frames=f"{round(frames[0])},{round(frames[-1])}",
                fps=f"{fps:g}",
                seconds=f"{(frames[-1] - frames[0]) / fps:.3f}",
                loop=bool(row.get("loop", False)),
                animation_kind=str(row.get("kind", "")),
                resource=resource,
                source=source,
                owner="blender",
            )
        )
    return found


# --- resolving an asset to its source --------------------------------------


def owner_of(project: Path, asset: str) -> tuple[str, str, str]:
    """(tool, native source, game resource) for an asset or its source document."""
    relative = asset.removeprefix("res://").split("#", 1)[0].split("::", 1)[0]
    for path in recipe_paths(project):
        recipe = load_recipe(path)
        members = (*recipe.outputs, *recipe.sources)
        if relative not in members:
            continue
        module = tools.REGISTRY.get(recipe.tool)
        if module is None or not module.parts_provider:
            continue
        suffix = module.suffix
        sources = [p for p in recipe.outputs if Path(p).suffix == suffix]
        if not sources:
            continue
        resources = [p for p in recipe.outputs if Path(p).suffix in {".glb", ".gltf"}]
        resource = resources[0] if resources else sources[0]
        return module.parts_provider, sources[0], "res://" + resource
    raise ValueError(
        f"{relative} has no recorded source document that lists parts. "
        "Only models made in Blender list their parts so far."
    )


def _sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def cache_path(project: Path, digest: str) -> Path:
    return project / CACHE_DIR / f"{digest}.json"


def build_report(
    inspection: dict[str, Any], tool: str, source: str, resource: str, digest: str
) -> PartsReport:
    if tool not in PROVIDERS:
        raise ValueError(f"{tool} cannot list parts yet.")
    return PartsReport(
        resource=resource,
        source=source,
        owner=tool,
        sha256=digest,
        parts=PROVIDERS[tool](inspection, resource, source),
    )


def cached_report(project: Path, asset: str) -> PartsReport | None:
    """The cached report for an asset's current source, if there is one."""
    tool, source, resource = owner_of(project, asset)
    path = project / source
    if not path.is_file():
        return None
    cached = cache_path(project, _sha256(path))
    if not cached.is_file():
        return None
    data = json.loads(cached.read_text())
    return build_report(data["inspection"], tool, source, resource, data["sha256"])


async def report(project: Path, asset: str, *, refresh: bool = False) -> PartsReport:
    """Parts of an asset, from the cache or a read-only inspection in the worker."""
    tool, source, resource = owner_of(project, asset)
    path = project / source
    if not path.is_file():
        raise ValueError(f"The source document {source} is missing.")
    digest = _sha256(path)
    cached = cache_path(project, digest)
    if cached.is_file() and not refresh:
        data = json.loads(cached.read_text())
        return build_report(data["inspection"], tool, source, resource, digest)
    from joinery.authoring import inspect_document

    inspection = await inspect_document(project, tool, source)
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text(json.dumps({"sha256": digest, "inspection": inspection}))
    return build_report(inspection, tool, source, resource, digest)


def as_json(found: PartsReport) -> dict[str, Any]:
    """The report the editor's parts panel reads."""
    return {
        "resource": found.resource,
        "source": found.source,
        "owner": found.owner,
        "sha256": found.sha256,
        "counts": found.counts(),
        "parts": [part.target() for part in found.parts],
    }


# --- prompt part targets ---------------------------------------------------


def write_targets(root: Path, targets: Iterable[Mapping[str, str]]) -> None:
    """Record a prompt's part targets where the tool servers of this stage read them."""
    path = root / TARGETS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    picked = [dict(t) for t in targets if t.get("part_kind")]
    path.write_text(json.dumps(picked))


def prompt_targets(project: Path, source: str) -> list[dict[str, str]]:
    """This prompt's part targets inside one source document."""
    path = project / TARGETS_FILE
    if not path.is_file():
        return []
    wanted = source.removeprefix("res://")
    rows = json.loads(path.read_text())
    return [
        {str(k): str(v) for k, v in row.items()}
        for row in rows
        if isinstance(row, dict)
        and wanted
        in {
            str(row.get("owner_source", "")),
            str(row.get("source", "")).removeprefix("res://"),
        }
    ]


def _listed(value: str) -> set[str]:
    return {item for item in value.split(",") if item}


def check_targets(
    targets: list[dict[str, str]],
    *,
    material: str = "",
    image: str = "",
    obj: str = "",
) -> None:
    """Refuse names outside the prompt's part targets.

    With no part picked in this source the whole model is in scope; the scope
    guard still keeps the edit to the part it names.
    """
    if not targets:
        return
    labels = ", ".join(t.get("label") or chip_label(t) for t in targets)
    hint = " Pick it in the parts list first, or clear the selection to edit the whole model."
    if material:
        allowed = set()
        for target in targets:
            allowed |= {target.get("material", "")} | _listed(
                target.get("materials", "")
            )
        if material not in allowed - {""}:
            raise ValueError(
                f"Material {material!r} is not part of the selection ({labels})." + hint
            )
    if image:
        allowed_images = set()
        for target in targets:
            if target.get("part_kind") == "texture":
                allowed_images.add(target.get("image", ""))
            elif material and material in (
                {target.get("material", "")} | _listed(target.get("materials", ""))
            ):
                allowed_images.add(image)
        if image not in allowed_images - {""}:
            raise ValueError(
                f"Image {image!r} is not part of the selection ({labels})." + hint
            )
    if obj:
        allowed_objects = {t.get("object", "") for t in targets} - {""}
        if obj not in allowed_objects:
            raise ValueError(
                f"Object {obj!r} is not part of the selection ({labels})." + hint
            )


# --- command line ----------------------------------------------------------


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Add ``parts <asset> --project P [--json] [--refresh]``; the handler is ``args.handler``."""
    parser = subparsers.add_parser(
        "parts", help="list the objects, materials and maps of a model"
    )
    parser.add_argument("asset", help="the model (GLB) or its source document")
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument(
        "--json", action="store_true", help="machine-readable, for the editor"
    )
    parser.add_argument(
        "--refresh", action="store_true", help="inspect again even when cached"
    )
    parser.set_defaults(handler=run)


def run(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve()
    try:
        found = asyncio.run(
            report(project, str(args.asset), refresh=bool(args.refresh))
        )
    except (ValueError, PermissionError, OSError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(as_json(found)))
        return 0
    print(f"{found.source} ({found.owner})")
    for part in found.parts:
        print(f"  {KIND_NAMES[part.part_kind]:<10} {part.label}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="joinery")
    register(parser.add_subparsers(dest="command", required=True))
    args = parser.parse_args(argv)
    result: int = args.handler(args)
    return result


if __name__ == "__main__":
    sys.exit(main())
