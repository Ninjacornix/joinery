""".joinerykit files: a zip with a kit.toml manifest and the kit's files under files/.

A shared kit is a file the user moves between their projects (or hands to a
friend) themselves; Joinery never downloads one. Every file is listed in the
manifest with its SHA-256 and size, and reading a kit checks all of it before
anything is written: the zip's names (no absolute paths, no ``..``, no
symlinks, no executable bits), its sizes (per file, in total, and how far a
file inflates), and that every file is one of the kinds a kit carries. Python
is allowed only as a recipe's script or a kit generator, and both only ever run
inside the offline worker.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import stat
import tomllib
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

FORMAT = 1
SUFFIX = ".joinerykit"
MANIFEST = "kit.toml"
FILES = "files/"
#: Limits a kit must stay inside; a bigger one is refused before anything is read.
MAX_FILE = 512 * 1024 * 1024
MAX_TOTAL = 2 * 1024 * 1024 * 1024
MAX_ENTRIES = 5000
MAX_MANIFEST = 2 * 1024 * 1024
#: How far one file may inflate (a zip bomb inflates thousands of times).
MAX_RATIO = 200
#: Origins a kit's assets may have. custom-tool only when the user ticked the box.
ORIGINS = ("tool-created", "human-import", "custom-tool", "kit")
#: What each file of a kit is.
KINDS = (
    "asset",
    "recipe",
    "script",
    "input",
    "scene",
    "resource",
    "generator",
    "thumbnail",
)
#: A reference image's licence: it is never shipped, so never shared either.
REFERENCE_ONLY = "reference only, not shipped"
#: File types a kit may carry (Python separately: scripts and generators only).
DATA_SUFFIXES = frozenset(
    {
        ".blend", ".glb", ".gltf", ".bin", ".obj", ".mtl",
        ".png", ".jpg", ".jpeg", ".webp", ".exr", ".hdr", ".svg", ".xcf", ".kra",
        ".ptex", ".wav", ".ogg", ".flac", ".mid", ".midi", ".scd",
        ".json", ".txt", ".toml", ".tres", ".tscn", ".gdshader",
    }
)  # fmt: skip
SCRIPT_SUFFIXES = frozenset({".py", ".scd", ".json", ".txt"})
#: Code a game would run: a kit carries content, never game code.
GAME_CODE = frozenset({".gd", ".cs", ".gdextension", ".so", ".dll", ".dylib"})
_ID = re.compile(r"^[a-z][a-z0-9_]{0,23}$")
_VERSION = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+-]{0,31}$")
_PART = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_. ()+-]{0,127}$")

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Content(_Strict):
    """One file of a kit, by its path inside the kit."""

    path: str
    kind: Literal[
        "asset",
        "recipe",
        "script",
        "input",
        "scene",
        "resource",
        "generator",
        "thumbnail",
    ]
    sha256: Sha256
    size: int = Field(ge=0, le=MAX_FILE)
    #: Assets only: how it was made in the project it came from.
    origin: Literal["tool-created", "human-import", "custom-tool", "kit", ""] = ""
    tool: str = ""
    #: The kit path of the recipe that makes it, for an asset with one.
    recipe: str = ""
    #: A human import's own licence, or the licence of the kit an asset first came in.
    licence: str = ""
    #: For an asset that came from another kit: "Name version by author".
    upstream: str = ""
    #: Made with a custom tool the user added, which may be generative.
    custom: bool = False


class Generator(_Strict):
    id: Annotated[str, Field(pattern=_ID.pattern)]
    label: str = Field(min_length=1, max_length=60)
    #: generators/<id>.py
    file: str


class Preset(_Strict):
    name: str = Field(min_length=1, max_length=60)
    generator: str
    params: dict[str, Any] = Field(default_factory=dict)
    colours: dict[str, str] = Field(default_factory=dict)


class Manifest(_Strict):
    format: Literal[1] = 1
    name: str = Field(min_length=1, max_length=60)
    version: Annotated[str, Field(pattern=_VERSION.pattern)]
    author: str = Field(min_length=1, max_length=120)
    licence: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    joinery: str = Field(min_length=1, max_length=32)
    created: str = ""
    generators: tuple[Generator, ...] = ()
    presets: tuple[Preset, ...] = ()
    contents: tuple[Content, ...] = ()

    @property
    def size(self) -> int:
        return sum(c.size for c in self.contents)

    def credit(self) -> str:
        return f"{self.name} {self.version} by {self.author}, {self.licence}"


def check_path(path: str) -> str:
    """A path inside a kit: relative, plain parts, no way out."""
    pure = PurePosixPath(path)
    if (
        not path
        or "\\" in path
        or path.startswith("/")
        or pure.is_absolute()
        or any(p in {"", ".", ".."} or not _PART.match(p) for p in path.split("/"))
    ):
        raise ValueError(f"{path!r} is not a safe path inside a kit.")
    return path


def check_suffix(content: Content) -> None:
    suffix = PurePosixPath(content.path).suffix.lower()
    if suffix in GAME_CODE:
        raise ValueError(f"{content.path}: a kit carries no game code.")
    if content.kind == "generator":
        ok = suffix == ".py" and content.path.startswith("generators/")
    elif content.kind == "script":
        ok = suffix in SCRIPT_SUFFIXES and content.path.startswith("recipes/")
    elif content.kind == "recipe":
        ok = suffix == ".toml" and content.path.startswith("recipes/")
    elif content.kind == "thumbnail":
        ok = suffix == ".png" and content.path.startswith("thumbnails/")
    else:
        ok = suffix in DATA_SUFFIXES
    if not ok:
        raise ValueError(f"{content.path}: a kit can't carry this as a {content.kind}.")


def check_origin(content: Content) -> None:
    """Rule Zero: an asset is tool-made with its recipe, a licensed import, or marked."""
    if content.kind != "asset":
        if content.origin or content.custom:
            raise ValueError(f"{content.path}: only an asset has an origin.")
        return
    if content.origin == "tool-created" and not content.recipe:
        raise ValueError(f"{content.path}: tool-created but no recipe in the kit.")
    if content.origin == "human-import" and (
        not content.licence.strip() or content.licence == REFERENCE_ONLY
    ):
        raise ValueError(f"{content.path}: an import without a licence to share it.")
    if content.origin == "custom-tool" and not content.custom:
        raise ValueError(f"{content.path}: made with a custom tool but not marked.")
    if content.origin not in ORIGINS:
        raise ValueError(f"{content.path}: no origin.")


def read_spec(source: str, where: str = "the generator") -> dict[str, Any]:
    """A generator's SPEC, read as a literal: its code never runs outside the worker."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise ValueError(f"{where} is not valid Python: {exc.msg}") from None
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "SPEC"
        ):
            try:
                spec = ast.literal_eval(node.value)
            except ValueError:
                raise ValueError(f"{where}: SPEC is not plain data.") from None
            break
    else:
        raise ValueError(f"{where} has no SPEC.")
    if not any(isinstance(n, ast.FunctionDef) and n.name == "build" for n in tree.body):
        raise ValueError(f"{where} has no build().")
    _check_spec(spec, where)
    return dict(spec)


def _check_spec(spec: Any, where: str) -> None:
    keys = {"id", "label", "summary", "folder", "params", "materials"}
    if not isinstance(spec, dict) or not keys <= set(spec):
        raise ValueError(f"{where}: SPEC needs {', '.join(sorted(keys))}.")
    if not all(isinstance(spec[k], str) for k in ("id", "label", "summary", "folder")):
        raise ValueError(f"{where}: SPEC id, label, summary and folder are text.")
    params, materials = spec["params"], spec["materials"]
    if not isinstance(params, dict) or not isinstance(materials, dict) or not materials:
        raise ValueError(f"{where}: SPEC params and materials are tables.")
    for name, raw in params.items():
        if not isinstance(raw, dict) or not {"kind", "default", "label", "help"} <= set(
            raw
        ):
            raise ValueError(
                f"{where}: parameter {name} needs kind, default, label, help."
            )
    for role, look in materials.items():
        if not isinstance(look, dict) or not {"colour", "roughness", "metallic"} <= set(
            look
        ):
            raise ValueError(
                f"{where}: material {role} needs colour, roughness, metallic."
            )


# --- writing ------------------------------------------------------------------------


def _toml(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return json.dumps(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml(v) for v in value) + "]"
    if isinstance(value, dict):
        inner = ", ".join(
            f"{json.dumps(str(k))} = {_toml(v)}" for k, v in value.items()
        )
        return "{" + (" " + inner + " " if inner else "") + "}"
    raise ValueError(f"Can't write {value!r} in a kit manifest.")


def manifest_text(manifest: Manifest) -> str:
    """The manifest as TOML, keys in a fixed order, so the same kit writes the same file."""
    data = manifest.model_dump()
    lines = ["# A Joinery shared kit. Every file is listed with its SHA-256."]
    for key in ("format", "name", "version", "author", "licence", "description"):
        lines.append(f"{key} = {_toml(data[key])}")
    lines += [
        f"joinery = {_toml(data['joinery'])}",
        f"created = {_toml(data['created'])}",
    ]
    for table in ("generators", "presets", "contents"):
        for row in data[table]:
            lines += ["", f"[[{table}]]"]
            for key, value in row.items():
                if value in ("", False, {}) and key not in {"path", "sha256", "size"}:
                    continue
                lines.append(f"{key} = {_toml(value)}")
    text = "\n".join(lines) + "\n"
    parse_manifest(text.encode())  # never write a manifest that does not read back
    return text


def parse_manifest(data: bytes) -> Manifest:
    try:
        raw = tomllib.loads(data.decode("utf-8"))
        manifest = Manifest.model_validate(raw)
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, ValidationError) as exc:
        raise ValueError(f"The kit's manifest can't be read: {_first(exc)}") from None
    seen: set[str] = set()
    for content in manifest.contents:
        check_path(content.path)
        if content.path in seen:
            raise ValueError(f"{content.path} is listed twice.")
        seen.add(content.path)
        check_suffix(content)
        check_origin(content)
    for generator in manifest.generators:
        if (
            generator.file != f"generators/{generator.id}.py"
            or generator.file not in seen
        ):
            raise ValueError(f"Generator {generator.id}: its file is not in the kit.")
    ids = {g.id for g in manifest.generators}
    for preset in manifest.presets:
        if preset.generator not in ids:
            raise ValueError(f"Preset {preset.name}: no generator {preset.generator}.")
    return manifest


def _first(exc: Exception) -> str:
    text = str(exc).strip().splitlines()
    return text[0] if text else type(exc).__name__


def write(path: Path, manifest: Manifest, files: dict[str, bytes]) -> None:
    """Write a kit: the manifest first, then every file, with fixed dates and modes."""
    listed = {c.path for c in manifest.contents}
    if listed != set(files):
        raise ValueError("The kit's files don't match its manifest.")
    temporary = path.with_name(path.name + ".partial")
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in [(MANIFEST, manifest_text(manifest).encode())] + [
            (FILES + c.path, files[c.path]) for c in manifest.contents
        ]:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    temporary.replace(path)


# --- reading ------------------------------------------------------------------------


@dataclass
class Archive:
    """A kit read and checked: its manifest, raw manifest bytes and every file."""

    manifest: Manifest
    raw: bytes
    files: dict[str, bytes] = field(default_factory=dict)

    def content(self, path: str) -> Content:
        for content in self.manifest.contents:
            if content.path == path:
                return content
        raise KeyError(path)


def _check_entry(info: zipfile.ZipInfo) -> None:
    name = info.filename
    mode = info.external_attr >> 16
    if info.flag_bits & 0x1:
        raise ValueError(f"{name}: encrypted files are refused.")
    if stat.S_ISLNK(mode):
        raise ValueError(f"{name}: a kit can't contain links.")
    if mode & 0o111 and not info.is_dir():
        raise ValueError(f"{name}: a kit can't contain executable files.")
    if mode and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
        raise ValueError(f"{name}: not a plain file.")
    if name != MANIFEST and not name.startswith(FILES):
        raise ValueError(f"{name}: outside the kit's files.")
    if name.startswith(FILES) and not info.is_dir():
        check_path(name.removeprefix(FILES))
    if info.file_size > MAX_FILE:
        raise ValueError(f"{name}: bigger than {MAX_FILE >> 20} MB.")
    if info.file_size > (1 << 20) and info.file_size > MAX_RATIO * max(
        info.compress_size, 1
    ):
        raise ValueError(f"{name}: inflates too far to be a real file.")


def _read(archive: zipfile.ZipFile, info: zipfile.ZipInfo, limit: int) -> bytes:
    with archive.open(info) as handle:
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"{info.filename}: bigger than the kit says.")
    return data


def read(path: Path) -> Archive:
    """Open a kit and check every byte of it, or raise ValueError saying why not."""
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{path.name} is not a kit file.")
    if path.stat().st_size > MAX_TOTAL:
        raise ValueError("The kit is bigger than 2 GB.")
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError):
        raise ValueError(f"{path.name} is not a Joinery kit (not a zip).") from None
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_ENTRIES:
            raise ValueError(f"The kit has more than {MAX_ENTRIES} files.")
        names = [i.filename for i in infos]
        if len(set(names)) != len(names):
            raise ValueError("The kit lists a file twice.")
        total = 0
        for info in infos:
            _check_entry(info)
            total += info.file_size
        if total > MAX_TOTAL:
            raise ValueError("The kit unpacks to more than 2 GB.")
        try:
            head = archive.getinfo(MANIFEST)
        except KeyError:
            raise ValueError("The kit has no kit.toml.") from None
        raw = _read(archive, head, MAX_MANIFEST)
        manifest = parse_manifest(raw)
        stored = {
            i.filename.removeprefix(FILES): i
            for i in infos
            if i.filename.startswith(FILES) and not i.is_dir()
        }
        listed = {c.path: c for c in manifest.contents}
        if extra := sorted(set(stored) - set(listed)):
            raise ValueError(f"{extra[0]} is in the kit but not in its manifest.")
        if missing := sorted(set(listed) - set(stored)):
            raise ValueError(f"{missing[0]} is in the manifest but not in the kit.")
        found = Archive(manifest, raw)
        for content in manifest.contents:
            data = _read(archive, stored[content.path], content.size)
            if len(data) != content.size or (
                hashlib.sha256(data).hexdigest() != content.sha256
            ):
                raise ValueError(
                    f"{content.path} doesn't match its SHA-256: the kit was changed "
                    "after it was made."
                )
            found.files[content.path] = data
    for generator in manifest.generators:
        read_spec(
            found.files[generator.file].decode("utf-8", "replace"), generator.file
        )
    return found


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def slug(name: str) -> str:
    """A kit's name as a folder and id prefix: harbour_props."""
    words = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    if not words or not words[0].isalpha():
        words = "kit_" + words
    return words[:16].rstrip("_")
