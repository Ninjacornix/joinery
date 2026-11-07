"""Asset gate: every file under art/ and audio/ needs a joinery.lock entry."""

from __future__ import annotations

import hashlib
import tomllib
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from joinery.catalog import Catalog

LOCK_NAME = "joinery.lock"
ASSET_DIRS = ("art", "audio")
NOT_ASSETS = {".gitkeep", ".DS_Store"}
NOT_ASSET_PATHS = {"art/bible.md"}


class _Entry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    sha256: str


class ToolCreated(_Entry):
    origin: Literal["tool-created"]
    tool: str
    recipe: str


class HumanImport(_Entry):
    origin: Literal["human-import"]
    imported_by: str = Field(min_length=1)
    licence: str = Field(min_length=1)


AssetEntry = Annotated[ToolCreated | HumanImport, Field(discriminator="origin")]
_ENTRIES: TypeAdapter[list[ToolCreated | HumanImport]] = TypeAdapter(list[AssetEntry])


def _asset_files(project: Path) -> set[str]:
    found: set[str] = set()
    for directory in ASSET_DIRS:
        for path in (project / directory).rglob("*"):
            rel = path.relative_to(project).as_posix()
            skipped = (
                path.suffix == ".import"
                or path.name in NOT_ASSETS
                or rel in NOT_ASSET_PATHS
            )
            if path.is_file() and not skipped:
                found.add(rel)
    return found


def _entry_errors(
    project: Path, entry: ToolCreated | HumanImport, catalog: Catalog
) -> list[str]:
    errors: list[str] = []
    file = project / entry.path
    if not file.is_file():
        return [f"{entry.path}: in {LOCK_NAME} but missing on disk"]
    if hashlib.sha256(file.read_bytes()).hexdigest() != entry.sha256:
        errors.append(f"{entry.path}: sha256 differs from {LOCK_NAME}")
    if isinstance(entry, ToolCreated):
        if entry.tool not in {t.id for t in catalog.tool}:
            errors.append(f"{entry.path}: unknown tool {entry.tool!r}")
        if not (project / entry.recipe).is_file():
            errors.append(f"{entry.path}: recipe {entry.recipe} not found")
    return errors


def check_assets(project: Path, catalog: Catalog) -> list[str]:
    lock = project / LOCK_NAME
    if not lock.is_file():
        return [f"{lock}: missing"]
    try:
        raw = tomllib.loads(lock.read_text())
        entries = _ENTRIES.validate_python(raw.get("asset", []))
    except (tomllib.TOMLDecodeError, ValidationError) as exc:
        return [f"{lock}: invalid: {exc}"]
    errors: list[str] = []
    for entry in entries:
        errors.extend(_entry_errors(project, entry, catalog))
    locked = {e.path for e in entries}
    errors.extend(
        f"{p}: no {LOCK_NAME} entry" for p in sorted(_asset_files(project) - locked)
    )
    return errors
