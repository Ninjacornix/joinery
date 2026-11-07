"""Recipes: how an asset was made, and how to prove it can be made again.

Rule Zero says an asset that cannot be reproduced from its recipe does not go
in the game. A recipe records the tool, the operation log that produced the
asset, and a fingerprint of the result. `joinery rebuild` replays it and
compares.

Verifying by file hash does not work for every format. Blender writes pointers
and timestamps into a `.blend`, so two runs of the same script produce different
bytes while describing exactly the same model. Comparing those hashes would fail
every time and teach us nothing. So each output is fingerprinted the strictest
way that is actually stable for its format:

* `sha256:` byte-identical, for formats that are deterministic (WAV, MIDI, text
  graphs, and baked textures). Anything that can be compared exactly, is.
* `blend:`  a semantic signature taken by asking Blender what is in the file:
  object names and types, mesh topology counts, materials, modifiers and rounded
  transforms. Byte noise is ignored, a changed model is not.
* `img:`    a coarse signature for *camera renders*, which are not bit-exact.
  Cycles renders in Blender 5.2 differ slightly between runs whatever you fix:
  seed, thread count, adaptive sampling, sun disc and pixel filter all pinned,
  and every sky model still varies. A bake has no camera and is exact, a render
  is not. So a render is downsampled hard and quantised before hashing, which
  survives path-tracing noise but still catches a moved sun, changed colours or
  a different resolution.

The fingerprint prefix records which check was used, so a recipe cannot quietly
downgrade from an exact comparison to a loose one.
"""

from __future__ import annotations

import hashlib
import json
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from joinery.runner import DEFAULT_TIMEOUT, Result, Runner

RECIPE_DIR = "recipes"
#: Formats whose bytes are stable enough to compare exactly.
EXACT_SUFFIXES = frozenset(
    {".wav", ".ogg", ".mid", ".midi", ".scd", ".ptex", ".tres", ".json", ".txt"}
)
#: Raster formats. Exact when baked, coarse when rendered through a camera;
#: which one applies is recorded per output in the recipe's expectations.
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".exr"})
#: Downsample size and bit depth for a render signature. 32x16 averages ~1000
#: source pixels into each cell, and 4 bits per channel then ignores the last
#: of the path-tracing noise while keeping real changes visible.
IMG_GRID = (32, 16)
IMG_BITS = 4

# Asks Blender what is in a file and prints a stable signature of it. Passed as
# a single argv element, so the newlines are fine and no temp file is needed.
_BLEND_SIGNATURE = r"""
import bpy, json, hashlib
items = []
for obj in sorted(bpy.data.objects, key=lambda o: o.name):
    mesh = getattr(obj.data, "vertices", None)
    items.append(
        {
            "name": obj.name,
            "type": obj.type,
            "verts": len(obj.data.vertices) if mesh is not None else 0,
            "polys": len(obj.data.polygons) if mesh is not None else 0,
            "materials": sorted(s.name for s in obj.material_slots if s.name),
            "modifiers": [m.type for m in obj.modifiers],
            "loc": [round(v, 4) for v in obj.location],
            "scale": [round(v, 4) for v in obj.scale],
        }
    )
doc = {"objects": items, "materials": sorted(m.name for m in bpy.data.materials)}
blob = json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()
print("JOINERY_SIG:" + hashlib.sha256(blob).hexdigest())
"""


class Recipe(BaseModel):
    """One asset's build instructions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = 1
    #: Catalog tool id, e.g. "blender". Must match the joinery.lock entry.
    tool: str
    #: Script the tool runs, relative to the project. The operation log; typed
    #: ops replace this as the op library lands.
    script: str
    #: Files the script produces, relative to the project.
    outputs: tuple[str, ...] = Field(min_length=1)
    #: Outputs that are camera renders, which cannot be compared byte for byte.
    #: Must be a subset of `outputs`. Everything not listed is compared exactly.
    renders: tuple[str, ...] = ()
    #: output path -> fingerprint, e.g. {"art/door.blend": "blend:ab12..."}.
    expect: dict[str, str] = Field(default_factory=dict)
    note: str = ""


@dataclass(frozen=True)
class Rebuilt:
    """What replaying one recipe produced."""

    recipe: str
    ok: bool
    seconds: float
    problems: tuple[str, ...] = ()
    #: output path -> fingerprint measured after the replay.
    measured: Mapping[str, str] = field(default_factory=dict)


def load_recipe(path: Path) -> Recipe:
    with path.open("rb") as handle:
        return Recipe.model_validate(tomllib.load(handle))


def _exact(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def blend_signature(path: Path, project: Path, runner: Runner) -> str:
    """Ask Blender for a semantic signature of a .blend."""
    rel = path.relative_to(project).as_posix()
    got = runner.run(
        ["blender", "--background", rel, "--python-expr", _BLEND_SIGNATURE],
        project,
        timeout=DEFAULT_TIMEOUT,
    )
    for line in got.output.splitlines():
        if line.startswith("JOINERY_SIG:"):
            return "blend:" + line.removeprefix("JOINERY_SIG:").strip()
    raise RuntimeError(f"no signature from Blender for {rel}: {got.output[-400:]}")


def image_signature(rel: str, project: Path, runner: Runner) -> str:
    """A coarse signature for a camera render, robust to path-tracing noise.

    ffmpeg decodes and area-averages the image down to a small grid; each
    channel is then quantised, so the last of the per-run sampling noise drops
    out. A moved sun, a recoloured sky or a changed resolution all still change
    the result. This is deliberately weaker than a hash and says so in its
    prefix: use it only where exactness is genuinely unattainable.
    """
    width, height = IMG_GRID
    scratch = project / ".joinery-fingerprint.raw"
    got = runner.run(
        [
            "ffmpeg", "-v", "error", "-y", "-i", rel,
            "-vf", f"scale={width}:{height}:flags=area",
            "-pix_fmt", "rgb24", "-f", "rawvideo", scratch.name,
        ],
        project,
        timeout=DEFAULT_TIMEOUT,
    )
    try:
        if not scratch.is_file():
            raise RuntimeError(f"ffmpeg produced nothing for {rel}: {got.output[-300:]}")
        raw = scratch.read_bytes()
    finally:
        scratch.unlink(missing_ok=True)
    if len(raw) != width * height * 3:
        raise RuntimeError(f"unexpected raw size {len(raw)} for {rel}")
    shift = 8 - IMG_BITS
    quantised = bytes(byte >> shift for byte in raw)
    return "img:" + hashlib.sha256(quantised).hexdigest()


def fingerprint(rel: str, project: Path, runner: Runner, *, render: bool = False) -> str:
    """Fingerprint one output the strictest way that is stable for its format.

    `render=True` marks a camera render, the one case where exactness is not
    attainable. Recipes declare those explicitly in their `renders` list, so a
    loose check is always a visible choice rather than a silent default.
    """
    path = project / rel
    if not path.is_file():
        raise FileNotFoundError(rel)
    suffix = path.suffix.lower()
    if render:
        if suffix not in IMAGE_SUFFIXES:
            raise ValueError(f"{rel}: only an image can be fingerprinted as a render")
        return image_signature(rel, project, runner)
    if suffix == ".blend":
        return blend_signature(path, project, runner)
    # Everything else is compared exactly, including baked textures.
    return _exact(path)


def _tool_argv(recipe: Recipe) -> list[str]:
    """The command that replays a recipe, per tool."""
    if recipe.tool == "blender":
        # --factory-startup so a user's preferences cannot change the result.
        return [
            "blender",
            "--background",
            "--factory-startup",
            "--python",
            recipe.script,
        ]
    if recipe.tool == "supercollider":
        return ["sclang", recipe.script]
    if recipe.tool == "godot":
        return ["godot", "--headless", "--script", recipe.script]
    raise ValueError(f"no replay command for tool {recipe.tool!r}")


def run_recipe(
    recipe: Recipe, project: Path, runner: Runner, timeout: int = DEFAULT_TIMEOUT
) -> Result:
    """Run a recipe's script in its tool, producing its outputs."""
    return runner.run(_tool_argv(recipe), project, timeout=timeout)


def rebuild_one(
    path: Path, project: Path, runner: Runner, timeout: int = DEFAULT_TIMEOUT
) -> Rebuilt:
    """Replay one recipe and check every output still fingerprints the same.

    The outputs are rebuilt in place, which is the point: a recipe that cannot
    reproduce the committed asset is a broken recipe, and we want to find that
    out here rather than trust a note in a file.
    """
    name = path.name
    try:
        recipe = load_recipe(path)
    except Exception as exc:  # noqa: BLE001 - a bad recipe is a reportable failure.
        return Rebuilt(name, False, 0.0, (f"unreadable recipe: {exc}",))

    if not (project / recipe.script).is_file():
        return Rebuilt(name, False, 0.0, (f"script {recipe.script} not found",))

    try:
        got = run_recipe(recipe, project, runner, timeout=timeout)
    except ValueError as exc:
        return Rebuilt(name, False, 0.0, (str(exc),))
    if not got.ok:
        tail = got.output[-600:] or "no output"
        return Rebuilt(name, False, got.seconds, (f"{recipe.tool} failed: {tail}",))
    problems: list[str] = []
    measured: dict[str, str] = {}
    for rel in recipe.outputs:
        try:
            measured[rel] = fingerprint(
                rel, project, runner, render=rel in recipe.renders
            )
        except FileNotFoundError:
            problems.append(f"{rel}: not produced")
            continue
        except RuntimeError as exc:
            problems.append(f"{rel}: {exc}")
            continue
        wanted = recipe.expect.get(rel)
        if wanted is None:
            problems.append(f"{rel}: recipe records no expected fingerprint")
        elif wanted != measured[rel]:
            problems.append(f"{rel}: expected {wanted}, rebuilt {measured[rel]}")
    return Rebuilt(name, not problems, got.seconds, tuple(problems), measured)


def recipe_paths(project: Path) -> list[Path]:
    return sorted((project / RECIPE_DIR).glob("*.toml"))


def rebuild_all(
    project: Path, runner: Runner, timeout: int = DEFAULT_TIMEOUT
) -> list[Rebuilt]:
    return [rebuild_one(p, project, runner, timeout) for p in recipe_paths(project)]


def record(path: Path, project: Path, runner: Runner) -> Recipe:
    """Fingerprint a recipe's current outputs and write them into it.

    Used when an asset is first created: the asset exists, and the recipe needs
    to learn what a correct rebuild looks like. Never invents a fingerprint, it
    measures the real file, which is the same rule the sandbox checksums follow.
    """
    recipe = load_recipe(path)
    expect = {
        rel: fingerprint(rel, project, runner, render=rel in recipe.renders)
        for rel in recipe.outputs
    }
    updated = recipe.model_copy(update={"expect": expect})
    lines = [
        f"version = {updated.version}",
        f"tool = {json.dumps(updated.tool)}",
        f"script = {json.dumps(updated.script)}",
        "outputs = ["
        + ", ".join(json.dumps(o) for o in updated.outputs)
        + "]",
    ]
    if updated.renders:
        lines.append(
            "renders = [" + ", ".join(json.dumps(r) for r in updated.renders) + "]"
        )
    if updated.note:
        lines.append(f"note = {json.dumps(updated.note)}")
    lines.append("")
    lines.append("# Measured from the real outputs by `joinery rebuild --record`.")
    lines.append("[expect]")
    lines += [f"{json.dumps(k)} = {json.dumps(v)}" for k, v in sorted(expect.items())]
    path.write_text("\n".join(lines) + "\n")
    return updated
