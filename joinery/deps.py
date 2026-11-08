"""Fail when a denied package appears in any manifest or lockfile."""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Iterator
from pathlib import Path

from joinery.policy import Denylist, normalise

_SKIPPED_DIRS = {".git", ".private", ".venv", "godot-src", "node_modules"}
_REQUIREMENT = re.compile(r"^\s*(@?[A-Za-z0-9][\w.\-/]*)")


def _requirement_name(spec: str) -> str | None:
    match = _REQUIREMENT.match(spec)
    return match.group(1) if match else None


def _pyproject(path: Path) -> Iterator[str]:
    data = tomllib.loads(path.read_text())
    specs = list(data.get("project", {}).get("dependencies", []))
    for extra in data.get("project", {}).get("optional-dependencies", {}).values():
        specs.extend(extra)
    for group in data.get("dependency-groups", {}).values():
        specs.extend(s for s in group if isinstance(s, str))
    for spec in specs:
        name = _requirement_name(spec)
        if name:
            yield name


def _uv_lock(path: Path) -> Iterator[str]:
    for package in tomllib.loads(path.read_text()).get("package", []):
        yield package["name"]


def _requirements(path: Path) -> Iterator[str]:
    for line in path.read_text().splitlines():
        if not line.lstrip().startswith(("#", "-")):
            name = _requirement_name(line)
            if name:
                yield name


def _package_json(path: Path) -> Iterator[str]:
    data = json.loads(path.read_text())
    for key in ("dependencies", "devDependencies", "optionalDependencies"):
        yield from data.get(key, {})


def _package_lock(path: Path) -> Iterator[str]:
    for key in json.loads(path.read_text()).get("packages", {}):
        if "node_modules/" in key:
            yield key.rsplit("node_modules/", 1)[1]


def _reader(path: Path) -> Iterator[str] | None:
    if path.name == "pyproject.toml":
        return _pyproject(path)
    if path.name == "uv.lock":
        return _uv_lock(path)
    if "requirements" in path.name and path.suffix == ".txt":
        return _requirements(path)
    if path.name == "package.json":
        return _package_json(path)
    if path.name == "package-lock.json":
        return _package_lock(path)
    return None


def check_deps(root: Path, denylist: Denylist) -> list[str]:
    errors: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or _SKIPPED_DIRS.intersection(
            path.relative_to(root).parts
        ):
            continue
        names = _reader(path)
        if names is None:
            continue
        for name in names:
            if normalise(name) in denylist.packages:
                errors.append(f"{path.relative_to(root)}: denied package {name}")
    return errors
