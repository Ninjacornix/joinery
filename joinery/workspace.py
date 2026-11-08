"""Shared read-only asset index for every desktop tool workspace."""

from __future__ import annotations

import hashlib
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from joinery.catalog import load_catalog, load_project_tools, selection_ids
from joinery.desktop import TOOLS, launch, launcher, overrides, project_file
from joinery.lockfile import AssetEntry, asset_files
from joinery.recipe import IMAGE_SUFFIXES, Recipe, load_recipe, recipe_paths


def watch_stamp(project: Path) -> str:
    """Cheap change detection for automatic refresh; never a provenance check."""
    paths = asset_files(project) | {"joinery.toml", "joinery.lock"}
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


def index(project: Path) -> dict[str, Any]:
    catalog = load_catalog()
    config = project / "joinery.toml"
    selected = load_project_tools(config) if config.is_file() else None
    enabled = selection_ids(catalog, selected) if selected else []
    names = {t.id: t.name for t in catalog.tool}
    domains = {t.id: t.domain for t in catalog.tool}
    if selected:
        names.update({t.id: t.name for t in selected.custom})
        domains.update({t.id: t.domain for t in selected.custom})
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
    paths = asset_files(project) | set(locked) | set(claims)
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
            candidates = [p for p in recipe.sources if not p.startswith("recipes/inputs/")]
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
            exists = project_file(project, target).is_file()
            available, reason = programs[candidate]
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
    return {
        "tools": [
            {
                "id": tool,
                "name": name,
                "domain": domains[tool],
                "enabled": tool in enabled,
                "available": programs[tool][0],
                "reason": programs[tool][1],
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
