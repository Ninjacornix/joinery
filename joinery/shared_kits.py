"""Shared kits: reusable things from one project, imported into another, rebuildable.

Export gathers what the user chose and everything it needs to be made again:
an asset brings its recipe, the recipe's script, inputs and every other output;
a prefab scene brings the files it uses; a generator brings its code and its
presets. Rule Zero decides what may go: tool-created with its recipe, from
another kit, or the user's import with a licence to share it. Assets made with
a custom tool go only when the user ticks the box, and stay marked.

Import is one reviewed change. The kit's files go under ``kits/<folder>/``
(their own art/ and audio/, checked by the asset gate like the game's), its
recipes into ``recipes/`` with the paths in them moved to match, its scenes
with their ``res://`` paths moved too. Every asset gets a ``kit`` entry in
joinery.lock naming the kit, its author and licence, and how it was first made;
``joinery rebuild`` then makes every kit asset again from its recipe. Importing
a newer version of a kit replaces it in place; removing a kit is refused while
the game still uses it.
"""

from __future__ import annotations

import datetime
import os
import re
import tomllib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from joinery import __version__, kits, tools
from joinery.kit_archive import (
    GAME_CODE,
    REFERENCE_ONLY,
    SCRIPT_SUFFIXES,
    Archive,
    Content,
    Generator,
    Manifest,
    Preset,
    check_path,
    sha256,
    slug,
)
from joinery.kit_library import (
    INSTALLED,
    KITS_ROOT,
    Installed,
    InstalledFile,
    installed,
)
from joinery.lockfile import (
    CustomOutput,
    HumanImport,
    KitAsset,
    ToolCreated,
    asset_files,
    read_assets,
    write_assets,
)
from joinery.locking import write_model_imports
from joinery.recipe import RECIPE_DIR, Recipe, load_recipe, recipe_paths

#: Text a kit's paths are moved in on import.
TEXT_RESOURCES = frozenset({".tscn", ".tres"})
#: Text sources whose paths move too, so a rebuild writes them the same.
TEXT_SOURCES = frozenset({".scd", ".json", ".txt", ".ptex"})
#: Folders a kit never takes anything from.
NEVER = (".godot", ".joinery", ".git", "addons", "recipes/history")
_RES = re.compile(r"res://([^\"'\s)\]]+)")
_UID = re.compile(r"\s+uid=\"uid://[^\"]*\"")
_FOLDER = re.compile(r"^kits/[a-z][a-z0-9_-]{0,31}$")
#: What a licence file says first, and the licence it is.
_LICENCES = (
    ("MIT License", "MIT"),
    ("Apache License", "Apache-2.0"),
    ("Creative Commons Attribution 4.0", "CC-BY-4.0"),
    ("Attribution 4.0 International", "CC-BY-4.0"),
    ("Attribution-ShareAlike 4.0", "CC-BY-SA-4.0"),
    ("CC0", "CC0-1.0"),
    ("GNU GENERAL PUBLIC LICENSE", "GPL-3.0"),
    ("Mozilla Public License", "MPL-2.0"),
)
ALL_RIGHTS = "All rights reserved"
#: Licences the export dialog offers; any other can be typed.
LICENCES = (
    "CC-BY-4.0",
    "CC0-1.0",
    "CC-BY-SA-4.0",
    "MIT",
    ALL_RIGHTS,
)


def game_licence(project: Path) -> str:
    """The game's licence: joinery.toml's [game] licence, else its LICENSE file."""
    config = project / "joinery.toml"
    if config.is_file():
        try:
            game = tomllib.loads(config.read_text("utf-8")).get("game", {})
        except tomllib.TOMLDecodeError:
            game = {}
        if isinstance(game, dict) and str(game.get("licence", "")).strip():
            return str(game["licence"]).strip()
    for name in ("LICENSE", "LICENSE.txt", "LICENSE.md", "COPYING"):
        path = project / name
        if path.is_file():
            head = path.read_text("utf-8", errors="replace")[:600]
            for words, licence in _LICENCES:
                if words.lower() in head.lower():
                    return licence
    return ALL_RIGHTS


def author(project: Path) -> str:
    from joinery.timeline import identity

    return identity(project)[0]


# --- export -------------------------------------------------------------------------


def _blocked(rel: str) -> bool:
    return any(rel == n or rel.startswith(n + "/") for n in NEVER)


def _walk(project: Path, suffixes: set[str]) -> list[str]:
    """Project files with these suffixes, outside Godot's, Joinery's and git's folders."""
    found = []
    for directory, children, names in os.walk(project):
        here = Path(directory)
        children[:] = sorted(
            c
            for c in children
            if not c.startswith(".")
            and not _blocked((here / c).relative_to(project).as_posix())
        )
        found += [
            (here / n).relative_to(project).as_posix()
            for n in sorted(names)
            if Path(n).suffix in suffixes
        ]
    return found


def expand(project: Path, include: Iterable[str]) -> list[str]:
    """Project files named by paths, folders or globs (res:// allowed)."""
    found: list[str] = []
    root = project.resolve()
    for raw in include:
        pattern = raw.strip().removeprefix("res://").strip("/")
        if (
            not pattern
            or ".." in PurePosixPath(pattern).parts
            or pattern.startswith("/")
        ):
            raise ValueError(f"{raw!r} is not a path inside the project.")
        matches = (
            sorted(project.glob(pattern))
            if any(c in pattern for c in "*?[")
            else [project / pattern]
        )
        if not matches or not any(m.exists() for m in matches):
            raise ValueError(f"Nothing in the project matches {raw}.")
        for match in matches:
            files = sorted(match.rglob("*")) if match.is_dir() else [match]
            for path in files:
                rel = path.relative_to(project).as_posix()
                if not path.resolve().is_relative_to(root) or path.is_symlink():
                    raise ValueError(f"{rel} links outside the project.")
                name = path.name
                if (
                    path.is_file()
                    and not name.startswith(".")
                    and path.suffix not in {".import", ".uid"}
                    and not _blocked(rel)
                ):
                    found.append(rel)
    return list(dict.fromkeys(found))


@dataclass
class _Gather:
    project: Path
    allow_custom: bool
    lock: dict[str, Any]
    claims: dict[str, tuple[str, Recipe]]
    assets: set[str]
    rows: dict[str, dict[str, Any]] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)
    queue: list[str] = field(default_factory=list)

    def add(self, rel: str, kind: str = "", **extra: Any) -> None:
        if rel not in self.rows:
            self.rows[rel] = {"path": rel, "kind": kind, **extra}
            self.queue.append(rel)

    def problem(self, text: str) -> None:
        if text not in self.problems:
            self.problems.append(text)

    def recipe(self, rel: str) -> str:
        """Add the recipe that makes ``rel`` with its script, inputs and outputs."""
        toml, made = self.claims[rel]
        if toml not in self.rows:
            self.add(toml, "recipe")
            script = made.script
            if (
                not script.startswith(RECIPE_DIR + "/")
                or "/" in script[len(RECIPE_DIR) + 1 :]
            ):
                self.problem(f"{toml}: its script {script} is not in recipes/.")
            elif PurePosixPath(script).suffix not in SCRIPT_SUFFIXES:
                self.problem(f"{toml}: its script {script} can't go in a kit.")
            else:
                self.add(script, "script")
            for other in made.outputs:
                self.add(other)
            for source in made.sources:
                self.add(source)
        return toml

    def visit(self, rel: str) -> None:
        path = self.project / rel
        row = self.rows[rel]
        suffix = path.suffix.lower()
        if not path.is_file() or path.is_symlink():
            self.problem(f"{rel} is not a file in the project.")
            return
        if suffix in GAME_CODE:
            self.problem(
                f"{rel} is game code: a kit carries assets, scenes and recipes."
            )
            return
        if row["kind"] in {"recipe", "script"}:
            return
        if rel in self.assets:
            self._asset(rel, row)
        elif rel.startswith(f"{RECIPE_DIR}/inputs/"):
            row["kind"] = "input"
        elif suffix == ".tscn":
            row["kind"] = "scene"
        elif suffix in {".tres", ".gdshader"}:
            row["kind"] = "resource"
        else:
            self.problem(
                f"{rel} is not an asset with a joinery.lock entry, a scene or a resource."
            )
            return
        if suffix in TEXT_RESOURCES:
            for dep in _RES.findall(path.read_text("utf-8", errors="replace")):
                dep = dep.split("::")[0]
                if PurePosixPath(dep).suffix in GAME_CODE:
                    self.problem(
                        f"{rel} uses the script {dep}: a kit carries no game code."
                    )
                elif _blocked(dep):
                    self.problem(f"{rel} uses {dep}, which can't go in a kit.")
                elif not (self.project / dep).is_file():
                    self.problem(f"{rel} uses {dep}, which isn't in the project.")
                else:
                    self.add(dep)

    def _asset(self, rel: str, row: dict[str, Any]) -> None:
        row["kind"] = "asset"
        entry = self.lock.get(rel)
        if entry is None:
            self.problem(
                f"{rel} has no joinery.lock entry, so where it came from is unknown."
            )
            return
        custom = isinstance(entry, CustomOutput) or (
            isinstance(entry, KitAsset) and entry.made_as == "custom-tool"
        )
        if custom and not self.allow_custom:
            self.problem(
                f"{rel} was made with a custom tool. Tick “Include custom-tool assets” "
                "to share it, marked as such."
            )
            return
        if isinstance(entry, HumanImport):
            if entry.licence == REFERENCE_ONLY or rel.startswith("art/reference/"):
                self.problem(
                    f"{rel} is a reference image: references are never shared."
                )
                return
            row.update(origin="human-import", licence=entry.licence)
        elif isinstance(entry, KitAsset):
            row.update(
                origin="kit",
                tool=entry.tool,
                licence=entry.asset_licence or entry.licence,
                upstream=entry.upstream
                or f"{entry.kit} {entry.kit_version} by {entry.kit_author}",
            )
        elif isinstance(entry, CustomOutput):
            row.update(origin="custom-tool", tool=entry.tool)
        else:
            row.update(origin="tool-created", tool=entry.tool)
        row["custom"] = custom
        if rel in self.claims:
            row["recipe"] = self.recipe(rel)
        elif isinstance(entry, ToolCreated):
            self.problem(f"{rel}: no recipe in the project makes it.")


def _claims(project: Path) -> dict[str, tuple[str, Recipe]]:
    found: dict[str, tuple[str, Recipe]] = {}
    for path in recipe_paths(project):
        try:
            made = load_recipe(path)
        except (OSError, ValueError):
            continue
        for out in made.outputs:
            found[out] = (path.relative_to(project).as_posix(), made)
    return found


def parse_preset(text: str) -> tuple[str, str, dict[str, Any]]:
    """'Harbour rock=rock:{"moss": 0.6}' -> (name, generator, params)."""
    import json

    name, _, rest = text.partition("=")
    generator, _, params = rest.partition(":")
    try:
        values = json.loads(params or "{}")
    except ValueError:
        values = None
    if not name.strip() or not generator.strip() or not isinstance(values, dict):
        raise ValueError('A preset is NAME=GENERATOR:{"param": value}.')
    return name.strip(), generator.strip(), values


def export_plan(
    project: Path,
    name: str,
    *,
    include: Sequence[str] = (),
    generators: Sequence[str] = (),
    presets: Sequence[str] = (),
    licence: str = "",
    version: str = "1.0.0",
    description: str = "",
    allow_custom: bool = False,
) -> tuple[Manifest, dict[str, bytes]]:
    """What a kit would hold, checked; ValueError lists everything that can't go."""
    lock = {e.path: e for e in read_assets(project)}
    gather = _Gather(
        project, allow_custom, lock, _claims(project), asset_files(project)
    )
    for rel in expand(project, include):
        gather.add(rel)
    while gather.queue:
        gather.visit(gather.queue.pop(0))
    files = {
        rel: (project / rel).read_bytes() for rel in gather.rows if not gather.problems
    }
    chosen: list[Generator] = []
    for kit_id in dict.fromkeys(generators):
        try:
            kit = kits.get(kit_id, project)
        except ValueError as exc:
            gather.problem(str(exc))
            continue
        file = f"generators/{kit.id}.py"
        files[file] = kit.source.encode()
        gather.rows[file] = {"path": file, "kind": "generator"}
        chosen.append(Generator(id=kit.id, label=kit.label, file=file))
        previews = sorted(
            (project / ".joinery/kits/previews").glob(f"{kit.id}-*.png"),
            key=lambda p: p.stat().st_mtime,
        )
        if previews:
            thumb = f"thumbnails/{kit.id}.png"
            files[thumb] = previews[-1].read_bytes()
            gather.rows[thumb] = {"path": thumb, "kind": "thumbnail"}
    picked = _presets(project, presets, chosen, gather)
    if gather.problems:
        raise ValueError("; ".join(gather.problems))
    contents = tuple(
        Content(**row, sha256=sha256(files[row["path"]]), size=len(files[row["path"]]))
        for row in sorted(gather.rows.values(), key=lambda r: r["path"])
    )
    if not contents:
        raise ValueError("Choose something to put in the kit.")
    manifest = Manifest(
        name=name.strip(),
        version=version.strip() or "1.0.0",
        author=author(project),
        licence=licence.strip() or game_licence(project),
        description=description.strip(),
        joinery=__version__,
        created=datetime.date.today().isoformat(),
        generators=tuple(chosen),
        presets=tuple(picked),
        contents=contents,
    )
    return manifest, files


def _presets(
    project: Path, given: Sequence[str], chosen: list[Generator], gather: _Gather
) -> list[Preset]:
    ids = {g.id for g in chosen}
    found: dict[str, Preset] = {}
    for text in given:
        try:
            label, generator, params = parse_preset(text)
            if generator not in ids:
                raise ValueError(f"Preset {label}: add the {generator} generator too.")
            kits.check_params(kits.get(generator, project), params)
        except ValueError as exc:
            gather.problem(str(exc))
            continue
        found[label] = Preset(name=label, generator=generator, params=params)
    # A set made with an included generator comes with its values as a preset.
    for rel, row in gather.rows.items():
        if row.get("kind") != "recipe":
            continue
        script = project / load_recipe(project / rel).script
        marker = kits.read_marker(script.read_text("utf-8", errors="replace"))
        if not marker or marker.get("kit") not in ids:
            continue
        label = str(marker.get("name", "")).replace("_", " ").capitalize()
        found.setdefault(
            label,
            Preset(
                name=label,
                generator=str(marker["kit"]),
                params=dict(marker.get("params", {})),
                colours=dict(marker.get("colours", {})),
            ),
        )
    return list(found.values())


def export(
    project: Path, out: Path, manifest: Manifest, files: dict[str, bytes]
) -> dict[str, Any]:
    from joinery.kit_archive import SUFFIX, write

    target = out if out.suffix == SUFFIX else out.with_suffix(SUFFIX)
    inside = target.resolve()
    for folder in ("art", "audio", "kits", "recipes"):
        if inside.is_relative_to((project / folder).resolve()):
            raise ValueError("Save the kit outside the project's art, audio and kits.")
    target.parent.mkdir(parents=True, exist_ok=True)
    write(target, manifest, files)
    return {**describe(manifest), "file": str(target), "bytes": target.stat().st_size}


#: How a kit's contents read in a sentence, by kind and file type.
_WORDS = (
    ("model", "models", {".glb", ".gltf"}),
    ("sound", "sounds", {".wav", ".ogg", ".flac"}),
    ("picture", "pictures", {".png", ".jpg", ".jpeg", ".webp", ".exr"}),
    ("material", "materials", {".tres", ".ptex"}),
)


def holds(manifest: Manifest) -> str:
    """'3 models, 1 sound, 1 scene and 1 generator': what a kit is for, at a glance."""
    found: dict[tuple[str, str], int] = {}
    for content in manifest.contents:
        suffix = PurePosixPath(content.path).suffix.lower()
        if content.kind == "scene":
            key = ("scene", "scenes")
        elif content.kind == "generator":
            key = ("generator", "generators")
        elif content.kind in {"asset", "resource"}:
            key = next(((a, b) for a, b, s in _WORDS if suffix in s), ("", ""))
        else:
            continue
        if key[0]:
            found[key] = found.get(key, 0) + 1
    words = [f"{n} {one if n == 1 else many}" for (one, many), n in found.items()]
    if len(words) > 1:
        return ", ".join(words[:-1]) + " and " + words[-1]
    return words[0] if words else "only source files"


def describe(manifest: Manifest) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for content in manifest.contents:
        counts[content.kind] = counts.get(content.kind, 0) + 1
    return {
        "holds": holds(manifest),
        "name": manifest.name,
        "version": manifest.version,
        "author": manifest.author,
        "licence": manifest.licence,
        "description": manifest.description,
        "joinery": manifest.joinery,
        "size": manifest.size,
        "counts": counts,
        "custom": sum(1 for c in manifest.contents if c.custom),
        "generators": [g.model_dump() for g in manifest.generators],
        "presets": [p.model_dump() for p in manifest.presets],
        "files": [
            {k: v for k, v in c.model_dump().items() if v not in ("", False)}
            for c in manifest.contents
        ],
    }


# --- import -------------------------------------------------------------------------


@dataclass
class ImportPlan:
    archive: Archive
    folder: str
    slug: str
    #: The installed version this replaces, when updating.
    previous: Installed | None
    writes: dict[str, bytes]
    record: Installed
    lock_add: list[KitAsset]
    lock_drop: set[str]
    removals: list[str]
    notes: list[str]

    @property
    def manifest(self) -> Manifest:
        return self.archive.manifest

    def summary(self) -> str:
        m = self.manifest
        if self.previous is None:
            return f"Add the {m.name} kit {m.version} by {m.author}"
        return f"Update the {m.name} kit from {self.previous.version} to {m.version}"

    def as_json(self) -> dict[str, Any]:
        before = (
            {f.path: f.sha256 for f in self.previous.files} if self.previous else {}
        )
        rows = []
        for row in self.record.files:
            change = (
                "added"
                if row.path not in before
                else "unchanged"
                if before[row.path] == row.sha256
                else "changed"
            )
            rows.append({"path": row.path, "kind": row.kind, "change": change})
        rows += [{"path": p, "kind": "", "change": "removed"} for p in self.removals]
        return {
            **describe(self.manifest),
            "folder": self.folder,
            "summary": self.summary(),
            "update": (
                {"from": self.previous.version, "to": self.manifest.version}
                if self.previous
                else None
            ),
            "changes": rows,
            "generator_ids": [f"{self.slug}_{g.id}" for g in self.manifest.generators],
            "notes": self.notes,
        }


def _relocate(text: str, roots: set[str], folder: str, *, res_only: bool) -> str:
    """Move every path under the kit's roots into ``folder``: art/x -> kits/k/art/x."""
    if not roots:
        return text
    alternation = "|".join(re.escape(r) for r in sorted(roots, key=len, reverse=True))
    if res_only:
        return re.sub(rf"res://({alternation})/", rf"res://{folder}/\1/", text)
    pattern = rf"(?<![A-Za-z0-9_./\-])(res://)?({alternation})/"
    return re.sub(pattern, lambda m: f"{m[1] or ''}{folder}/{m[2]}/", text)


def _recipe_text(
    made: Recipe, target: dict[str, str], rewritten: dict[str, bytes]
) -> str:
    import json

    def moved(path: str) -> str:
        if path not in target:
            raise ValueError(f"A recipe names {path}, which the kit doesn't carry.")
        return target[path]

    expect = {}
    for path, value in made.expect.items():
        new = moved(path)
        if new in rewritten and value.startswith("sha256:"):
            value = "sha256:" + sha256(rewritten[new])  # its res:// paths moved
        expect[new] = value
    lines = [
        f"version = {made.version}",
        f"tool = {json.dumps(made.tool)}",
        f"script = {json.dumps(moved(made.script))}",
        f"outputs = {json.dumps([moved(p) for p in made.outputs])}",
    ]
    for key in ("sources", "renders"):
        values = [moved(p) for p in getattr(made, key)]
        if values:
            lines.append(f"{key} = {json.dumps(values)}")
    if made.note:
        lines.append(f"note = {json.dumps(made.note)}")
    lines += ["", "[expect]"]
    lines += [f"{json.dumps(k)} = {json.dumps(v)}" for k, v in sorted(expect.items())]
    return "\n".join(lines) + "\n"


def _place(
    project: Path, manifest: Manifest, into: str
) -> tuple[str, str, Installed | None]:
    """The folder and id prefix for a kit: its own, when updating; else a free one."""
    done = installed(project)
    for kit_manifest, record in done:
        if kit_manifest.name == manifest.name:
            return record.folder, record.slug, record
    taken = {record.slug for _m, record in done}
    if into:
        folder = into.strip().strip("/").removeprefix("res://")
        if not _FOLDER.match(folder):
            raise ValueError(
                "Import a kit into a folder of kits/, such as kits/harbour."
            )
        prefix = slug(folder.split("/")[-1])
        if (project / folder).exists() or prefix in taken:
            raise ValueError(f"{folder} is already used. Choose another folder.")
        return folder, prefix, None
    base = slug(manifest.name)
    prefix, number = base, 1
    # Same names from different kits: each kit gets its own namespace.
    while (project / KITS_ROOT / prefix).exists() or prefix in taken:
        number += 1
        prefix = f"{base}_{number}"
    return f"{KITS_ROOT}/{prefix}", prefix, None


def plan_import(project: Path, archive: Archive, into: str = "") -> ImportPlan:
    """Where every file of the kit goes and what changes, checked; nothing is written."""
    manifest = archive.manifest
    folder, prefix, previous = _place(project, manifest, into)
    target: dict[str, str] = {}
    for content in manifest.contents:
        if content.kind in {"recipe", "script"}:
            name = content.path.removeprefix(RECIPE_DIR + "/")
            if "/" in name:
                raise ValueError(f"{content.path}: a recipe sits directly in recipes/.")
            target[content.path] = f"{RECIPE_DIR}/{prefix}__{name}"
        else:
            target[content.path] = f"{folder}/{content.path}"
    roots = {
        "recipes/inputs" if c.kind == "input" else c.path.split("/")[0]
        for c in manifest.contents
        if c.kind in {"asset", "input", "scene", "resource"}
    }
    writes: dict[str, bytes] = {}
    for content in manifest.contents:
        data = archive.files[content.path]
        suffix = PurePosixPath(content.path).suffix
        if suffix in TEXT_RESOURCES:
            text = _relocate(data.decode("utf-8"), roots, folder, res_only=True)
            text = _UID.sub("", text)
            for dep in _RES.findall(text):
                if dep.split("::")[0] not in target.values():
                    raise ValueError(
                        f"{content.path} uses {dep}, which the kit doesn't carry."
                    )
            data = text.encode()
        elif content.kind == "script":
            text = _relocate(data.decode("utf-8"), roots, folder, res_only=False)
            # Kit settings change a set where it was made; here it is the kit's.
            data = text.replace(kits.MARKER, "# joinery-kit-shared: ", 1).encode()
        elif suffix in TEXT_SOURCES and content.kind in {"asset", "input"}:
            # A text source (a SuperCollider patch) holds the paths its script
            # writes into it: moved the same way, a rebuild writes the same text.
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                text = ""
            if text:
                data = _relocate(text, roots, folder, res_only=False).encode()
        writes[target[content.path]] = data
    for content in manifest.contents:
        if content.kind == "recipe":
            made = Recipe.model_validate(
                tomllib.loads(archive.files[content.path].decode())
            )
            tools.get(made.tool)  # a recipe only for a tool Joinery knows
            writes[target[content.path]] = _recipe_text(made, target, writes).encode()
    rows = tuple(
        InstalledFile(
            source=c.path,
            path=target[c.path],
            sha256=sha256(writes[target[c.path]]),
            kind=c.kind,
        )
        for c in manifest.contents
    )
    record = Installed(
        name=manifest.name,
        version=manifest.version,
        folder=folder,
        slug=prefix,
        imported=datetime.date.today().isoformat(),
        files=rows,
    )
    owned = {f.path for f in previous.files} if previous else set()
    if {f"{folder}/kit.toml", f"{folder}/{INSTALLED}"} & set(writes):
        raise ValueError("The kit carries a file named like its own manifest.")
    for path in writes:
        if (project / path).exists() and path not in owned:
            raise ValueError(f"{path} already exists in this project.")
    lock_add = [
        KitAsset(
            path=target[c.path],
            sha256=sha256(writes[target[c.path]]),
            origin="kit",
            kit=manifest.name,
            kit_version=manifest.version,
            kit_author=manifest.author,
            licence=manifest.licence,
            made_as=c.origin or "tool-created",
            tool=c.tool,
            recipe=target[c.recipe] if c.recipe else None,
            asset_licence=c.licence,
            upstream=c.upstream,
        )
        for c in manifest.contents
        if c.kind == "asset"
    ]
    notes = []
    if _newer(manifest.joinery, __version__):
        notes.append(
            f"Made with Joinery {manifest.joinery}; this is {__version__}. Its generators "
            "may need the newer Joinery."
        )
    if any(c.custom for c in manifest.contents):
        notes.append(
            "Some assets were made with a custom tool, which may be generative."
        )
    return ImportPlan(
        archive=archive,
        folder=folder,
        slug=prefix,
        previous=previous,
        writes=writes,
        record=record,
        lock_add=lock_add,
        lock_drop=owned,
        removals=sorted(owned - set(writes)),
        notes=notes,
    )


def _newer(theirs: str, ours: str) -> bool:
    def parts(text: str) -> tuple[int, ...]:
        return tuple(int(p) for p in re.findall(r"\d+", text)[:3])

    return parts(theirs) > parts(ours)


def apply_import(stage: Path, plan: ImportPlan) -> None:
    """Write the kit into ``stage`` (a transaction's copy of the project)."""
    for path in plan.removals:
        for extra in ("", ".import", ".uid"):
            (stage / (path + extra)).unlink(missing_ok=True)
    for path, data in plan.writes.items():
        check_path(path)
        file = stage / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(data)
        if file.suffix == ".blend" or path.startswith(f"{plan.folder}/recipes/inputs/"):
            (file.parent / ".gdignore").write_text("")
    for folder in ("generators", "thumbnails"):
        if (stage / plan.folder / folder).is_dir():
            (stage / plan.folder / folder / ".gdignore").write_text("")
    (stage / plan.folder / "kit.toml").write_bytes(plan.archive.raw)
    (stage / plan.folder / INSTALLED).write_text(plan.record.text(), encoding="utf-8")
    write_assets(stage, plan.lock_drop, list(plan.lock_add))
    write_model_imports(stage)
    _tidy(stage, plan.folder)


def _tidy(stage: Path, folder: str) -> None:
    """Drop folders an update or removal left empty (and Godot's files in them)."""
    root = stage / folder
    if not root.is_dir():
        return
    for path in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path.is_dir():
            left = [
                p for p in path.iterdir() if p.name not in {".gdignore", ".DS_Store"}
            ]
            if not left:
                for p in path.iterdir():
                    p.unlink()
                path.rmdir()


# --- remove -------------------------------------------------------------------------


def find_installed(project: Path, name: str) -> tuple[Manifest, Installed]:
    for manifest, record in installed(project):
        if name in {manifest.name, record.slug, record.folder}:
            return manifest, record
    names = ", ".join(m.name for m, _r in installed(project)) or "none"
    raise ValueError(f"No imported kit called {name}. Imported kits: {names}.")


def users(project: Path, record: Installed) -> list[str]:
    """Scenes, resources and scripts outside the kit that still use its files."""
    mine = {f.path for f in record.files}
    prefix = f"res://{record.folder}/"
    found = []
    for rel in _walk(project, {".tscn", ".tres", ".gd", ".gdshader", ".cs"}):
        if rel.startswith(record.folder + "/") or rel in mine:
            continue
        if prefix in (project / rel).read_text("utf-8", errors="replace"):
            found.append(rel)
    for recipe in recipe_paths(project):
        rel = recipe.relative_to(project).as_posix()
        if rel in mine:
            continue
        try:
            made = load_recipe(recipe)
        except (OSError, ValueError):
            continue
        if any(s.startswith(record.folder + "/") for s in made.sources):
            found.append(rel)
    return found


def apply_remove(stage: Path, record: Installed) -> None:
    for row in record.files:
        (stage / row.path).unlink(missing_ok=True)
        for extra in (".import", ".uid"):
            (stage / (row.path + extra)).unlink(missing_ok=True)
    for name in ("kit.toml", INSTALLED):
        (stage / record.folder / name).unlink(missing_ok=True)
    write_assets(stage, {f.path for f in record.files}, [])
    _tidy(stage, record.folder)
    root = stage / record.folder
    if root.is_dir() and not any(root.iterdir()):
        root.rmdir()


def listing(project: Path) -> dict[str, Any]:
    """The imported kits, and what this project could put in a kit of its own."""
    rows = []
    for manifest, record in installed(project):
        rows.append(
            {
                **{k: v for k, v in describe(manifest).items() if k != "files"},
                "folder": record.folder,
                "imported": record.imported,
                "generator_ids": [f"{record.slug}_{g.id}" for g in manifest.generators],
                "used_by": users(project, record),
            }
        )
    lock = {e.path: e for e in read_assets(project)}
    shareable = []
    for rel in sorted(asset_files(project)):
        if rel.startswith(f"{KITS_ROOT}/"):
            continue
        entry = lock.get(rel)
        why = ""
        if entry is None:
            why = "no joinery.lock entry"
        elif isinstance(entry, HumanImport) and entry.licence == REFERENCE_ONLY:
            why = "a reference image"
        shareable.append(
            {
                "path": rel,
                "origin": entry.origin if entry else "",
                "custom": isinstance(entry, CustomOutput),
                "refused": why,
            }
        )
    scenes = [p for p in _walk(project, {".tscn"}) if not p.startswith(f"{KITS_ROOT}/")]
    return {
        "installed": rows,
        "assets": shareable,
        "scenes": scenes,
        "generators": [
            {"id": k.id, "label": k.label, "from": k.credit}
            for k in kits.every(project).values()
        ],
        "author": author(project),
        "licence": game_licence(project),
        "licences": list(LICENCES),
    }
