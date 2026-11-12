"""Kits a game project brings: its own generators and those of imported shared kits.

Joinery's own kits run their SPEC as Python on the host (joinery.kits). A
project's kits never do: their SPEC is read as plain data (ast.literal_eval),
and their code only runs inside the offline worker, as part of a recipe, like
every other recipe. An imported generator whose file no longer matches the
SHA-256 recorded when it was imported is left out.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from joinery.kit_archive import Manifest, parse_manifest, read_spec, sha256
from joinery.kits import Kit, catalogue, from_spec

#: A project's own generators, written by the user (MIT or the game's licence).
OWN_DIR = "kits/blender"
#: Where imported kits go: kits/<folder>/ with kit.toml and installed.toml.
KITS_ROOT = "kits"
INSTALLED = "installed.toml"


class InstalledFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    #: The file's path inside the kit.
    source: str
    #: Where it went in this project.
    path: str
    sha256: str
    kind: str


class Installed(BaseModel):
    """What importing a kit wrote, so updating or removing it touches only those files."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    version: str
    folder: str
    slug: str
    imported: str = ""
    files: tuple[InstalledFile, ...] = Field(default_factory=tuple)

    def text(self) -> str:
        lines = [
            "# Written by `joinery kit import`: where each of the kit's files went.",
            *(
                f"{key} = {json.dumps(getattr(self, key))}"
                for key in ("name", "version", "folder", "slug", "imported")
            ),
        ]
        for row in self.files:
            lines += ["", "[[files]]"]
            lines += [f"{k} = {json.dumps(v)}" for k, v in row.model_dump().items()]
        return "\n".join(lines) + "\n"


def installed(project: Path) -> list[tuple[Manifest, Installed]]:
    """Every kit imported into the project, by folder name."""
    found = []
    for record in sorted((project / KITS_ROOT).glob(f"*/{INSTALLED}")):
        try:
            manifest = parse_manifest((record.parent / "kit.toml").read_bytes())
            done = Installed.model_validate(tomllib.loads(record.read_text("utf-8")))
        except (OSError, ValueError, ValidationError, tomllib.TOMLDecodeError):
            continue
        found.append((manifest, done))
    return found


def project_kits(project: Path) -> dict[str, Kit]:
    """The project's own generators, then those of every imported kit, by id."""
    found: dict[str, Kit] = {}
    builtin = catalogue()
    for path in sorted((project / OWN_DIR).glob("*.py")):
        if path.stem == "common":
            continue
        try:
            source = path.read_text(encoding="utf-8")
            spec = read_spec(source, path.name)
            kit_id = spec["id"] if spec["id"] not in builtin else f"own_{spec['id']}"
            found[kit_id] = _kit(
                spec,
                source,
                id=kit_id,
                credit=f"this project ({path.name})",
                presets=_presets(spec),
            )
        except (OSError, ValueError):
            continue
    for manifest, done in installed(project):
        written = {f.source: f for f in done.files}
        for generator in manifest.generators:
            row = written.get(generator.file)
            if row is None:
                continue
            kit_id = f"{done.slug}_{generator.id}"
            presets = tuple(
                {"name": p.name, "params": p.params, "colours": p.colours}
                for p in manifest.presets
                if p.generator == generator.id
            )
            try:
                data = (project / row.path).read_bytes()
                if sha256(data) != row.sha256:
                    continue  # changed since it was imported: not the kit any more
                source = data.decode("utf-8")
                found[kit_id] = _kit(
                    read_spec(source, generator.file),
                    source,
                    id=kit_id,
                    label=f"{manifest.name} {generator.label.lower()}",
                    credit=manifest.credit(),
                    presets=presets,
                )
            except (OSError, ValueError):
                continue
    return found


def _presets(spec: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    """A project generator's own named parameter sets (SPEC["presets"])."""
    found = []
    for row in spec.get("presets") or ():
        if (
            isinstance(row, dict)
            and isinstance(row.get("name"), str)
            and isinstance(row.get("params", {}), dict)
            and isinstance(row.get("colours", {}), dict)
        ):
            found.append(
                {
                    "name": row["name"],
                    "params": dict(row.get("params", {})),
                    "colours": dict(row.get("colours", {})),
                }
            )
    return tuple(found)


def _kit(spec: dict[str, Any], source: str, **extra: Any) -> Kit:
    try:
        return from_spec(spec, source, None, **extra)
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError(f"The generator's SPEC is incomplete: {exc}") from None
