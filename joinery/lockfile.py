"""Asset gate: every file under art/ and audio/ needs a joinery.lock entry.

`joinery.lock` also records code extensions Joinery downloaded into the project
(`[[extensions]]`, such as GodotSteam). They are code, not assets: their
binaries are never asset files, but the files on disk must still match the
hash recorded when they were installed.
"""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
)

from joinery.catalog import ROOT, Catalog, load_project_tools

LOCK_NAME = "joinery.lock"
LOCK_HEADER = "version = 1\n"
ASSET_DIRS = ("art", "audio")
#: Imported shared kits: kits/<folder>/ with their own art/ and audio/.
KITS_DIR = "kits"
NOT_ASSETS = {".gitkeep", ".DS_Store", ".gdignore"}
NOT_ASSET_PATHS = {"art/bible.md"}
#: Files Godot or the OS add beside an extension's own; not part of its hash.
NOT_EXTENSION_FILES = {".DS_Store", ".gdignore"}
NOT_EXTENSION_SUFFIXES = {".import", ".uid"}
#: The repo's pinned code downloads. An `[[extensions]]` entry must match one.
PINNED_RELEASES = (ROOT / "policy" / "godotsteam.toml",)

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


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


class CustomOutput(_Entry):
    """Made by a user-added tool, which may be generative. Never counted as tool-created."""

    origin: Literal["custom-tool"]
    tool: str
    recipe: str | None = None


class KitAsset(_Entry):
    """Came in a shared kit the user imported: the kit's name, author and licence.

    ``made_as`` keeps how it was made where the kit came from; ``recipe`` is the
    recipe that rebuilds it here (none for an import the kit carried).
    """

    origin: Literal["kit"]
    kit: str = Field(min_length=1)
    kit_version: str = Field(min_length=1)
    kit_author: str = Field(min_length=1)
    licence: str = Field(min_length=1)
    made_as: Literal["tool-created", "human-import", "custom-tool", "kit"]
    tool: str = ""
    recipe: str | None = None
    #: A human import's own licence, or the kit it first came in.
    asset_licence: str = ""
    upstream: str = ""


Entry = ToolCreated | HumanImport | CustomOutput | KitAsset
AssetEntry = Annotated[Entry, Field(discriminator="origin")]
_ENTRIES: TypeAdapter[list[Entry]] = TypeAdapter(list[AssetEntry])


class Extension(BaseModel):
    """A pinned code download installed into the project (never an asset)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    url: str = Field(pattern=r"^https://")
    #: The downloaded archive's SHA-256, as recorded in the repo's policy.
    sha256: Sha256
    licence: str = Field(min_length=1)
    origin: Literal["pinned-download"]
    #: The folder the extension was installed to, relative to the project.
    path: str
    #: `tree_sha256` of that folder right after installing.
    files_sha256: Sha256

    @field_validator("path")
    @classmethod
    def _relative(cls, value: str) -> str:
        parts = PurePosixPath(value).parts
        if not parts or value.startswith(("/", "\\")) or ".." in parts or "\\" in value:
            raise ValueError("must be a relative folder inside the project")
        if parts[0].lower() in ASSET_DIRS:
            # Extensions are code; they never sit among the assets the gate checks.
            raise ValueError("must not be inside art/ or audio/")
        return value


_EXTENSIONS: TypeAdapter[list[Extension]] = TypeAdapter(list[Extension])


def asset_roots(project: Path) -> list[Path]:
    """art/ and audio/, and the same folders of every kit imported under kits/."""
    roots = [project / d for d in ASSET_DIRS]
    for record in sorted((project / KITS_DIR).glob("*/installed.toml")):
        roots += [record.parent / d for d in ASSET_DIRS]
    return roots


def asset_files(project: Path) -> set[str]:
    """Every file under art/ and audio/ (a kit's too) that the gate considers an asset."""
    found: set[str] = set()
    for directory in asset_roots(project):
        for path in directory.rglob("*"):
            rel = path.relative_to(project).as_posix()
            skipped = (
                path.suffix == ".import"
                or path.name in NOT_ASSETS
                or rel in NOT_ASSET_PATHS
            )
            if path.is_file() and not skipped:
                found.add(rel)
    return found


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_sha256(folder: Path) -> str:
    """One hash over every file in `folder`: relative path and content.

    Files Godot adds on import (`.import`, `.uid`) are left out, so opening
    the project in Godot does not change it.
    """
    lines: list[str] = []
    for path in sorted(folder.rglob("*")):
        if path.is_symlink():
            lines.append(f"{path.relative_to(folder).as_posix()}\0symlink\n")
            continue
        if not path.is_file():
            continue
        if path.name in NOT_EXTENSION_FILES or path.suffix in NOT_EXTENSION_SUFFIXES:
            continue
        lines.append(f"{path.relative_to(folder).as_posix()}\0{_file_sha256(path)}\n")
    return hashlib.sha256("".join(lines).encode()).hexdigest()


def _entry_errors(
    project: Path, entry: Entry, catalog: Catalog, custom: set[str]
) -> list[str]:
    errors: list[str] = []
    file = project / entry.path
    if not file.is_file():
        return [f"{entry.path}: in {LOCK_NAME} but missing on disk"]
    if hashlib.sha256(file.read_bytes()).hexdigest() != entry.sha256:
        errors.append(f"{entry.path}: sha256 differs from {LOCK_NAME}")
    recipe = entry.recipe if isinstance(entry, KitAsset) else None
    if recipe and not (project / recipe).is_file():
        errors.append(f"{entry.path}: recipe {recipe} not found")
    if isinstance(entry, ToolCreated):
        if entry.tool not in {t.id for t in catalog.tool}:
            errors.append(f"{entry.path}: {entry.tool!r} is not a preferred tool")
        if not (project / entry.recipe).is_file():
            errors.append(f"{entry.path}: recipe {entry.recipe} not found")
    if isinstance(entry, CustomOutput) and entry.tool not in custom:
        errors.append(
            f"{entry.path}: {entry.tool!r} is not a custom tool in this project"
        )
    return errors


def _pinned() -> set[tuple[str, str, str]]:
    """(name, url, sha256) of every release the repo pins."""
    found: set[tuple[str, str, str]] = set()
    for path in PINNED_RELEASES:
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            continue
        found.add((str(raw.get("name")), str(raw.get("url")), str(raw.get("sha256"))))
    return found


def _extension_errors(project: Path, extension: Extension) -> list[str]:
    if (extension.name, extension.url, extension.sha256) not in _pinned():
        return [
            (
                f"{extension.path}: {extension.name} {extension.version} is not a "
                "release Joinery pins; install it again"
            )
        ]
    folder = project / extension.path
    if not folder.is_dir():
        return [
            f"{extension.path}: {extension.name} is in {LOCK_NAME} but missing on disk"
        ]
    if tree_sha256(folder) != extension.files_sha256:
        return [
            (
                f"{extension.path}: files differ from {extension.name} "
                f"{extension.version} in {LOCK_NAME}; install it again"
            )
        ]
    return []


def _custom_ids(project: Path) -> set[str]:
    config = project / "joinery.toml"
    return (
        {c.id for c in load_project_tools(config).custom} if config.is_file() else set()
    )


def _parse(text: str) -> tuple[list[Entry], list[Extension]]:
    raw = tomllib.loads(text)
    return (
        _ENTRIES.validate_python(raw.get("asset", [])),
        _EXTENSIONS.validate_python(raw.get("extensions", [])),
    )


def read_assets(project: Path) -> list[Entry]:
    """The asset entries in the project's lock; [] without a lock."""
    lock = project / LOCK_NAME
    if not lock.is_file():
        return []
    return _parse(lock.read_text(encoding="utf-8"))[0]


def read_extensions(project: Path) -> list[Extension]:
    """The `[[extensions]]` in the project's lock; [] without a lock."""
    lock = project / LOCK_NAME
    if not lock.is_file():
        return []
    return _parse(lock.read_text(encoding="utf-8"))[1]


_EXTENSIONS_HEADER = re.compile(r"^[ \t]*\[\[[ \t]*extensions[ \t]*\]\][ \t]*(?:#.*)?$")
_ANY_HEADER = re.compile(r"^[ \t]*\[")


def _without_extensions(text: str) -> str:
    """The lock text with every `[[extensions]]` block taken out."""
    kept: list[str] = []
    inside = False
    for line in text.splitlines(keepends=True):
        bare = line.rstrip("\r\n")
        if _EXTENSIONS_HEADER.match(bare):
            inside = True
            continue
        if inside and _ANY_HEADER.match(line):
            inside = False
        if not inside:
            kept.append(line)
    return "".join(kept)


def _extension_block(extension: Extension) -> str:
    lines = ["[[extensions]]"]
    for key, value in extension.model_dump().items():
        lines.append(f"{key} = {json.dumps(value)}")
    return "\n".join(lines) + "\n"


def _write_extensions(project: Path, extensions: list[Extension]) -> None:
    lock = project / LOCK_NAME
    text = lock.read_text(encoding="utf-8") if lock.is_file() else LOCK_HEADER
    text = _without_extensions(text).rstrip("\n") + "\n"
    if extensions:
        text += "\n" + "\n".join(_extension_block(e) for e in extensions)
    _parse(text)  # never write a lock that does not read back
    lock.write_text(text, encoding="utf-8")


def record_extension(project: Path, extension: Extension) -> None:
    """Add or replace the `[[extensions]]` entry with this name."""
    others = [e for e in read_extensions(project) if e.name != extension.name]
    _write_extensions(project, [*others, extension])


def forget_extension(project: Path, name: str) -> bool:
    """Drop the `[[extensions]]` entry with this name; False when there was none."""
    current = read_extensions(project)
    kept = [e for e in current if e.name != name]
    if len(kept) == len(current):
        return False
    _write_extensions(project, kept)
    return True


def record_imports(project: Path, imports: list[HumanImport]) -> int:
    """Add `human-import` entries for paths the lock has no entry for yet.

    Only for files the user explicitly added. Everything already in the lock
    (assets and `[[extensions]]`) stays as it is. Returns how many were added.
    """
    lock = project / LOCK_NAME
    text = lock.read_text(encoding="utf-8") if lock.is_file() else LOCK_HEADER
    known = {entry.path for entry in _parse(text)[0]}
    blocks: list[str] = []
    for entry in imports:
        if entry.path in known:
            continue
        known.add(entry.path)
        blocks.append(
            "[[asset]]\n"
            + "".join(
                f"{key} = {json.dumps(value)}\n"
                for key, value in entry.model_dump().items()
            )
        )
    if not blocks:
        return 0
    updated = text.rstrip("\n") + "\n\n" + "\n".join(blocks)
    _parse(updated)  # never write a lock that does not read back
    lock.write_text(updated, encoding="utf-8")
    return len(blocks)


def write_assets(project: Path, drop: set[str], add: list[Entry]) -> None:
    """Rewrite the lock's assets: without ``drop`` and with ``add`` (replacing by path)."""
    lock = project / LOCK_NAME
    text = lock.read_text(encoding="utf-8") if lock.is_file() else LOCK_HEADER
    entries, extensions = _parse(text)
    replaced = drop | {e.path for e in add}
    kept = [e for e in entries if e.path not in replaced] + list(add)
    blocks = [
        "[[asset]]\n"
        + "".join(
            f"{key} = {json.dumps(value)}\n"
            for key, value in entry.model_dump(exclude_defaults=True).items()
            if value is not None
        )
        for entry in sorted(kept, key=lambda e: e.path)
    ]
    blocks += [_extension_block(e) for e in extensions]
    updated = LOCK_HEADER + "".join("\n" + block for block in blocks)
    _parse(updated)  # never write a lock that does not read back
    lock.write_text(updated, encoding="utf-8")


def check_assets(project: Path, catalog: Catalog) -> list[str]:
    lock = project / LOCK_NAME
    if not lock.is_file():
        return [f"{lock}: missing"]
    try:
        entries, extensions = _parse(lock.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, ValidationError) as exc:
        return [f"{lock}: invalid: {exc}"]
    errors: list[str] = []
    custom = _custom_ids(project)
    for entry in entries:
        errors.extend(_entry_errors(project, entry, catalog, custom))
    for extension in extensions:
        errors.extend(_extension_errors(project, extension))
    locked = {e.path for e in entries}
    unlocked = asset_files(project) - locked
    errors.extend(f"{p}: no {LOCK_NAME} entry" for p in sorted(unlocked))
    return errors
