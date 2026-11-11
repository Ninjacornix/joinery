"""Shared read-only asset index for every desktop tool workspace."""

from __future__ import annotations

import hashlib
import tomllib
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from joinery import kits, readiness, references
from joinery.catalog import load_catalog, load_project_tools, selection_ids
from joinery.desktop import TOOLS, launch, launcher, overrides, project_file
from joinery.lockfile import (
    ASSET_DIRS,
    NOT_ASSET_PATHS,
    NOT_ASSETS,
    AssetEntry,
    asset_files,
)
from joinery.recipe import IMAGE_SUFFIXES, Recipe, load_recipe, recipe_paths
from joinery.transactions import DIRECTORY, TRANSACTIONS, list_transactions

#: What an asset is, for grouping in the workspace: by file type first.
KIND_SUFFIXES: dict[str, frozenset[str]] = {
    "model": frozenset({".blend", ".glb", ".gltf", ".fbx", ".obj", ".dae", ".vox"}),
    "scene": frozenset({".tscn", ".scn"}),
    "material": frozenset({".ptex", ".mmg"}),
    "image": frozenset(
        {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tga", ".tif", ".tiff", ".exr"}
        | {".hdr", ".svg", ".svgz", ".gif", ".kra", ".ora", ".psd", ".xcf"}
        | {".ase", ".aseprite"}
    ),
    "sound": frozenset(
        {".wav", ".ogg", ".mp3", ".flac", ".aiff", ".aif", ".aup3", ".aup"}
        | {".rpp", ".mid", ".midi", ".mscz", ".mscx", ".scd", ".sc", ".fxp"}
    ),
}
#: Fallback kind from the creating tool's domain when the file type says nothing.
DOMAIN_KINDS = {
    "3d": "model",
    "2d": "image",
    "audio": "sound",
    "materials": "material",
    "engine": "scene",
}
#: Baked texture-map names: a source whose parts are these is a material.
MAP_NAMES = frozenset(
    {"albedo", "basecolor", "base_color", "diffuse", "roughness", "metallic"}
    | {"metalness", "normal", "height", "displacement", "ao", "occlusion"}
    | {"emission", "specular", "opacity"}
)


def watch_stamp(project: Path) -> str:
    """Cheap change detection for automatic refresh; never a provenance check."""
    paths = asset_files(project) | {"joinery.toml", "joinery.lock"}
    # A change going to review, or decided, rewrites its journal.
    paths.update(
        p.relative_to(project).as_posix()
        for p in (project / TRANSACTIONS).glob("*/journal.json")
    )
    paths.update(_scene_models(project))
    paths.update(
        p.relative_to(project).as_posix()
        for p in (project / "recipes").rglob("*")
        if p.is_file()
    )
    for path in recipe_paths(project):
        try:
            paths.update(load_recipe(path).sources)
        except (ValueError, OSError):
            pass
    digest = hashlib.sha256()
    for relative in sorted(paths):
        try:
            stat = project_file(project, relative).stat()
            state = f"{relative}:{stat.st_mtime_ns}:{stat.st_size}"
        except (ValueError, OSError):
            state = f"{relative}:missing"
        digest.update(state.encode())
        digest.update(b"\0")
    return digest.hexdigest()


@dataclass
class OpenOption:
    tool: str
    name: str
    path: str
    available: bool
    reason: str = ""


@dataclass
class Asset:
    path: str
    tool: str = ""
    origin: str = "unrecorded"
    integrity: str = "unrecorded"
    recipe: str = ""
    sources: list[str] = field(default_factory=list)
    previews: list[str] = field(default_factory=list)
    verification: str = "Not checked by replay"
    open_options: list[OpenOption] = field(default_factory=list)
    #: model, sound, image, material, scene or other.
    kind: str = "other"
    #: Derived files (baked maps, exports, renders) folded under this source asset.
    parts: list[str] = field(default_factory=list)
    #: For a part, the source asset it is folded under; empty for top-level assets.
    part_of: str = ""
    #: The change waiting for review that adds or replaces this file; empty otherwise.
    pending: str = ""
    #: Where that change keeps the new file until it is accepted (project-relative).
    staged: str = ""
    #: For a model made from a parametric kit: the kit, its values and its seed.
    kit: dict[str, Any] = field(default_factory=dict)


#: Model files a scene can load; ones outside art/ are still listed when a scene uses them.
SCENE_MODELS = frozenset({".glb", ".gltf", ".fbx", ".obj", ".dae", ".blend"})


def _scene_models(project: Path) -> set[str]:
    """Model files the project's scenes load that live outside art/ and audio/."""
    found: set[str] = set()
    for scene in references.scenes(project):
        try:
            text = scene.read_text(errors="replace")
        except OSError:
            continue
        for path, _uid in references.references(text):
            if (
                Path(path).suffix.lower() in SCENE_MODELS
                and Path(path).parts[0] not in ASSET_DIRS
                and (project / path).is_file()
            ):
                found.add(path)
    return found


def _is_asset(path: str) -> bool:
    """The asset gate's own rule (lockfile.asset_files) for one project path."""
    pure = Path(path)
    return (
        pure.parts[0] in ASSET_DIRS
        and pure.suffix != ".import"
        and pure.name not in NOT_ASSETS
        and path not in NOT_ASSET_PATHS
    )


def _pending(
    project: Path,
) -> tuple[dict[str, tuple[str, str]], dict[str, Recipe], dict[str, Path]]:
    """Assets that changes waiting for review add or replace, and those changes' recipes.

    Returns ``{path: (transaction id, app)}``, ``{recipe path: recipe}`` read from
    each change's stage, so a new model folds its maps and exports like an accepted one,
    and ``{recipe path: stage}``.
    """
    found: dict[str, tuple[str, str]] = {}
    recipes: dict[str, Recipe] = {}
    stages: dict[str, Path] = {}
    for tx in list_transactions(project):
        if tx.state != "review":
            continue
        tool = next((t for t in tx.tools if t not in {"godot", "joinery"}), "")
        for change in tx.changes:
            path = change["path"]
            if change["after"] in {"", DIRECTORY}:
                continue
            if _is_asset(path):
                found[path] = (tx.id, tool)
            elif path.startswith("recipes/") and path.endswith(".toml"):
                try:
                    recipes[path] = load_recipe(tx.stage / path)
                    stages[path] = tx.stage
                except (ValueError, OSError):
                    continue
    # The recipe names the app that made each file; the change only lists its apps.
    for recipe in recipes.values():
        for path in (*recipe.outputs, *recipe.sources):
            if path in found:
                found[path] = (found[path][0], recipe.tool)
    return found, recipes, stages


def _suffix_kind(path: str) -> str:
    suffix = Path(path).suffix.lower()
    return next((k for k, s in KIND_SUFFIXES.items() if suffix in s), "")


def _is_map(owner: str, part: str) -> bool:
    stem = Path(part).stem.lower()
    base = Path(owner).stem.lower()
    name = stem.removeprefix(base).strip("_-. ")
    return name in MAP_NAMES or any(stem.endswith("_" + m) for m in MAP_NAMES)


def _same_model(owner: str, part: str) -> bool:
    """rock_03.blend owns rock_03.glb and rock_03_lod1.glb, not rock_13.glb."""
    stem, other = Path(owner).stem, Path(part).stem
    return other == stem or other.startswith(stem + "_lod")


def _fold(
    assets: list[Asset], recipes: dict[str, Recipe], domains: Mapping[str, str]
) -> None:
    """Fold each recipe's derived outputs under its native source asset and set kinds.

    The owner is the first recipe output or source that is a native document of the
    recipe's tool (a .blend for Blender, a .ptex for Material Maker) and is itself an
    asset. Other native documents stay separate assets; everything else the recipe
    produced becomes a part. Sources that are not native documents (image inputs)
    are never parts. Every asset stays in the index, so parts still open and
    preview on their own.
    """
    by_path = {asset.path: asset for asset in assets}
    for recipe in recipes.values():
        spec = TOOLS.get(recipe.tool)
        native = spec.native if spec else ()
        # A source that is not a native document (an image a Material Maker graph
        # reads) is an input, not something the recipe made: it stays its own asset.
        sources = [p for p in recipe.sources if Path(p).suffix.lower() in native]
        members = [
            p for p in dict.fromkeys((*recipe.outputs, *sources)) if p in by_path
        ]
        owners = [p for p in members if Path(p).suffix.lower() in native]
        if not owners:
            continue
        for path in members:
            part = by_path[path]
            if path in owners or part.part_of or part.parts:
                continue
            # A recipe that makes several sources (a kit set) folds each export
            # under the source of the same name.
            owner = by_path[
                next((o for o in owners if _same_model(o, path)), owners[0])
            ]
            part.part_of = owner.path
            owner.parts.append(path)
        for path in owners:
            owner = by_path[path]
            if owner.parts and all(_suffix_kind(p) == "image" for p in owner.parts):
                if any(_is_map(owner.path, p) for p in owner.parts):
                    owner.kind = "material"
                elif all(p in recipe.renders for p in owner.parts):
                    owner.kind = "image"
    for asset in assets:
        if asset.kind == "other":
            asset.kind = (
                _suffix_kind(asset.path)
                or DOMAIN_KINDS.get(domains.get(asset.tool, ""))
                or "other"
            )


def _readiness(entry: Mapping[str, Any] | None) -> dict[str, Any]:
    """The readiness fields of a tool row, or none when nothing is known yet."""
    if entry is None or "status" not in entry:
        return {}
    return {
        key: entry[key]
        for key in ("status", "status_label", "status_reason", "action", "checked_at")
        if key in entry
    }


def index(project: Path) -> dict[str, Any]:
    catalog = load_catalog()
    config = project / "joinery.toml"
    selected = load_project_tools(config) if config.is_file() else None
    enabled = selection_ids(catalog, selected) if selected else []
    names = {t.id: t.name for t in catalog.tool}
    domains: dict[str, str] = {t.id: t.domain for t in catalog.tool}
    if selected:
        names.update({t.id: t.name for t in selected.custom})
        domains.update({t.id: t.domain for t in selected.custom})
    # Desktop apps Joinery no longer drives (catalog.REMOVED_TOOLS) still open
    # documents when they are installed; they are never offered as missing apps
    # except for assets they made.
    kind_domains = {kind: domain for domain, kind in DOMAIN_KINDS.items()}
    desktop_only = {tool for tool in TOOLS if tool not in names}
    for tool in sorted(desktop_only):
        names[tool] = TOOLS[tool].app
        kind = _suffix_kind(TOOLS[tool].native[0]) if TOOLS[tool].native else ""
        domains[tool] = kind_domains.get(kind, "engine")
    settings = overrides(project)
    recipes: dict[str, Recipe] = {}
    issues: list[str] = []
    for path in recipe_paths(project):
        try:
            recipes[path.relative_to(project).as_posix()] = load_recipe(path)
        except ValueError as exc:
            issues.append(f"{path.name}: {exc}")
    lock = project / "joinery.lock"
    entries = []
    if lock.is_file():
        raw = tomllib.loads(lock.read_text())
        entries = TypeAdapter(list[AssetEntry]).validate_python(raw.get("asset", []))
    else:
        issues.append("joinery.lock is missing; assets have no recorded provenance")
    locked = {entry.path: entry for entry in entries}
    claims = {out: rel for rel, recipe in recipes.items() for out in recipe.outputs}
    pending, pending_recipes, pending_stages = _pending(project)
    paths = (
        asset_files(project)
        | set(locked)
        | set(claims)
        | set(pending)
        | _scene_models(project)
    )
    programs: dict[str, tuple[bool, str]] = {}
    for tool in names:
        try:
            launcher(tool, settings)
            programs[tool] = (True, "")
        except ValueError as exc:
            programs[tool] = (False, str(exc))
    assets: list[Asset] = []
    for rel in sorted(paths):
        try:
            path = project_file(project, rel)
        except ValueError as exc:
            issues.append(str(exc))
            continue
        entry = locked.get(rel)
        recipe_rel = getattr(entry, "recipe", None) or claims.get(rel, "")
        recipe = recipes.get(recipe_rel)
        tool = getattr(entry, "tool", "") or (recipe.tool if recipe else "")
        asset = Asset(
            rel, tool, entry.origin if entry else "unrecorded", recipe=recipe_rel
        )
        if rel in pending:
            change, app = pending[rel]
            asset.pending = change
            asset.staged = f"{TRANSACTIONS}/{change}/stage/{rel}"
            asset.tool = asset.tool or app
        if not path.is_file() and asset.pending:
            # New in a change waiting for review: nothing to open or check yet.
            asset.integrity = "pending"
            assets.append(asset)
            continue
        if not path.is_file():
            asset.integrity = "missing"
        elif entry:
            with path.open("rb") as handle:
                digest = hashlib.file_digest(handle, "sha256").hexdigest()
            asset.integrity = "in sync" if digest == entry.sha256 else "changed"
        if recipe_rel and recipe is None:
            asset.verification = "Recipe missing or unreadable"
        elif recipe:
            # Render-only sources behind .gdignore still have viewable outputs.
            for preview in dict.fromkeys((*recipe.renders, *recipe.outputs)):
                if preview == rel or Path(preview).suffix.lower() not in IMAGE_SUFFIXES:
                    continue
                try:
                    if project_file(project, preview).is_file():
                        asset.previews.append(preview)
                except ValueError as exc:
                    issues.append(str(exc))
            spec = TOOLS.get(tool)
            candidates = [
                p for p in recipe.sources if not p.startswith("recipes/inputs/")
            ]
            if spec:
                native_outputs = [
                    p for p in recipe.outputs if Path(p).suffix.lower() in spec.native
                ]
                candidates = list(dict.fromkeys([*native_outputs, *candidates]))
                if not candidates and Path(recipe.script).suffix.lower() in spec.native:
                    candidates = [recipe.script]
            for source in candidates:
                try:
                    project_file(project, source)
                    asset.sources.append(source)
                except ValueError as exc:
                    issues.append(str(exc))
        for candidate, name in names.items():
            spec = TOOLS.get(candidate)
            override = settings.get(candidate)
            extensions = (
                override.extensions
                if override and override.extensions
                else (spec.extensions if spec else ())
            )
            native_file = spec is not None and path.suffix.lower() in spec.native
            options = (
                [*asset.sources, rel]
                if candidate == tool and not native_file
                else [rel, *asset.sources]
            )
            target = next(
                (p for p in options if Path(p).suffix.lower() in extensions), None
            )
            if target is None and candidate == tool and override:
                target = next(iter(asset.sources), rel)
            if target is None:
                continue
            available, reason = programs[candidate]
            if candidate in desktop_only and candidate != tool and not available:
                continue
            exists = project_file(project, target).is_file()
            asset.open_options.append(
                OpenOption(
                    candidate,
                    name,
                    target,
                    available and exists,
                    reason if exists else f"Source missing: {target}",
                )
            )
        asset.open_options.sort(key=lambda option: (option.tool != tool, option.name))
        assets.append(asset)
    _fold(assets, {**pending_recipes, **recipes}, domains)
    made_by_kit = kits.kit_records(
        project,
        {
            path: (pending_stages[path], recipe)
            for path, recipe in pending_recipes.items()
            if path not in recipes
        },
    )
    for asset in assets:
        asset.kit = made_by_kit.get(asset.path, {})
    # Readiness comes from the last report only: this index is rebuilt every few
    # seconds, and asking Docker each time would stall the workspace.
    known = readiness.cached_tools()
    return {
        "tools": [
            {
                "id": tool,
                "name": name,
                "domain": domains[tool],
                "enabled": tool in enabled,
                "available": programs[tool][0],
                "reason": programs[tool][1],
                **_readiness(known.get(tool)),
            }
            for tool, name in names.items()
            if tool in TOOLS or tool in settings
        ],
        "assets": [asdict(asset) for asset in assets],
        "issues": issues,
    }


def open_asset(
    project: Path, asset_path: str, tool: str, *, dry_run: bool = False
) -> list[str]:
    """Resolve again at click time, never trust a UI-supplied executable or source."""
    data = index(project)
    for raw in data["assets"]:
        if raw["path"] != asset_path:
            continue
        for option in raw["open_options"]:
            if option["tool"] != tool:
                continue
            if not option["available"]:
                raise ValueError(option["reason"])
            target = project_file(project, option["path"])
            argv = launcher(tool, overrides(project))
            if tool == "godot" and argv[0] != "/usr/bin/open":
                argv += ["--editor", "--path", str(project.resolve())]
            elif tool == "godot":
                target = project_file(project, "project.godot")
            argv.append(str(target))
            if not dry_run:
                launch(argv, project)
            return argv
    raise ValueError(f"No compatible {tool!r} application for {asset_path!r}")
