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
import shutil
import tempfile
import tomllib
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from xml.etree import ElementTree

from pydantic import BaseModel, ConfigDict, Field, model_validator

from joinery import tools
from joinery.runner import DEFAULT_TIMEOUT, Result, Runner

RECIPE_DIR = "recipes"
#: Immutable documents an incremental edit starts from, named by content hash.
PREIMAGE_DIR = "recipes/inputs"
#: Recipes replaced by a later edit of the same outputs. History only.
HISTORY_DIR = "recipes/history"
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
#: Entries of a Krita document that are XML, compared without attribute order.
_KRA_XML = (".xml", ".filterconfig")
IMG_GRID = (32, 16)
IMG_BITS = 4

# Asks Blender what is in a file and prints a stable signature of it. Passed as
# a single argv element, so the newlines are fine and no temp file is needed.
_BLEND_SIGNATURE_FN = r"""
import bpy, json, hashlib
def joinery_signature():
    items = []
    for obj in sorted(bpy.data.objects, key=lambda o: o.name):
        mesh = getattr(obj.data, "vertices", None)
        items.append(
            {
                "name": obj.name,
                "type": obj.type,
                "verts": len(obj.data.vertices) if mesh is not None else 0,
                "polys": len(obj.data.polygons) if mesh is not None else 0,
                "geometry": hashlib.sha256(json.dumps({
                    "vertices": [[round(x, 6) for x in v.co] for v in obj.data.vertices],
                    "polygons": [list(p.vertices) for p in obj.data.polygons],
                }, sort_keys=True).encode()).hexdigest() if mesh is not None else "",
                "materials": sorted(s.name for s in obj.material_slots if s.name),
                "modifiers": [m.type for m in obj.modifiers],
                "loc": [round(v, 4) for v in obj.location],
                "scale": [round(v, 4) for v in obj.scale],
                "matrix": [[round(v, 6) for v in row] for row in obj.matrix_world],
            }
        )
    doc = {"objects": items, "materials": [{
        "name": m.name, "diffuse": list(m.diffuse_color), "nodes": sorted([
            {"name": n.name, "type": n.bl_idname, "inputs": [
                {"name": i.name, "value": list(i.default_value) if hasattr(i.default_value, '__len__') and not isinstance(i.default_value, str) else str(i.default_value)}
                for i in n.inputs if hasattr(i, 'default_value')
            ]} for n in m.node_tree.nodes
        ], key=lambda n: n['name']) if m.use_nodes and m.node_tree else []
    } for m in sorted(bpy.data.materials, key=lambda m: m.name)]}
    blob = json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()
"""
_BLEND_SIGNATURE = _BLEND_SIGNATURE_FN + 'print("JOINERY_SIG:" + joinery_signature())\n'
# Signs several files in one Blender run: a kit set has a .blend per model, and
# starting Blender once per file would cost seconds each.
_BLEND_SIGNATURES = (
    _BLEND_SIGNATURE_FN
    + """
for _rel in json.loads(RELS):
    bpy.ops.wm.open_mainfile(filepath=_rel, load_ui=False)
    print("JOINERY_SIG:" + _rel + "|" + joinery_signature())
"""
)


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
    #: Native documents to open for editing, when they are not recipe outputs.
    sources: tuple[str, ...] = ()
    #: Outputs that are camera renders, which cannot be compared byte for byte.
    #: Must be a subset of `outputs`. Everything not listed is compared exactly.
    renders: tuple[str, ...] = ()
    #: output path -> fingerprint, e.g. {"art/door.blend": "blend:ab12..."}.
    expect: dict[str, str] = Field(default_factory=dict)
    note: str = ""

    @model_validator(mode="after")
    def safe_paths(self) -> Recipe:
        for value in (self.script, *self.outputs, *self.sources, *self.renders):
            path = Path(value)
            if not value or path.is_absolute() or ".." in path.parts:
                raise ValueError("Recipe paths must stay within the project.")
        return self


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


def blend_signatures(rels: list[str], project: Path, runner: Runner) -> dict[str, str]:
    """Semantic signatures of several .blend files, from one Blender run."""
    if len(rels) == 1:
        return {rels[0]: blend_signature(project / rels[0], project, runner)}
    script = f"RELS = {json.dumps(json.dumps(rels))}\n" + _BLEND_SIGNATURES
    got = runner.run(
        ["blender", "--background", "--factory-startup", "--python-expr", script],
        project,
        timeout=DEFAULT_TIMEOUT,
    )
    found = {}
    for line in got.output.splitlines():
        if line.startswith("JOINERY_SIG:"):
            rel, _, digest = line.removeprefix("JOINERY_SIG:").rpartition("|")
            found[rel] = "blend:" + digest.strip()
    missing = [rel for rel in rels if rel not in found]
    if missing:
        raise RuntimeError(
            f"no signature from Blender for {missing[0]}: {got.output[-400:]}"
        )
    return found


def fingerprints(
    rels: tuple[str, ...], project: Path, runner: Runner, renders: tuple[str, ...] = ()
) -> dict[str, str | Exception]:
    """Each output's fingerprint, or the error measuring it (FileNotFoundError: missing).

    Every .blend is signed in one Blender run; the rest one by one.
    """
    measured: dict[str, str | Exception] = {}
    blends = [
        rel
        for rel in rels
        if Path(rel).suffix.lower() == ".blend"
        and rel not in renders
        and (project / rel).is_file()
    ]
    if len(blends) > 1:
        try:
            measured.update(blend_signatures(blends, project, runner))
        except RuntimeError as exc:
            measured.update(dict.fromkeys(blends, exc))
    for rel in rels:
        if rel in measured:
            continue
        try:
            measured[rel] = fingerprint(rel, project, runner, render=rel in renders)
        except (FileNotFoundError, RuntimeError) as exc:
            measured[rel] = exc
    return measured


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
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            rel,
            "-vf",
            f"scale={width}:{height}:flags=area",
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            scratch.name,
        ],
        project,
        timeout=DEFAULT_TIMEOUT,
    )
    try:
        if not scratch.is_file():
            raise RuntimeError(
                f"ffmpeg produced nothing for {rel}: {got.output[-300:]}"
            )
        raw = scratch.read_bytes()
    finally:
        scratch.unlink(missing_ok=True)
    if len(raw) != width * height * 3:
        raise RuntimeError(f"unexpected raw size {len(raw)} for {rel}")
    shift = 8 - IMG_BITS
    quantised = bytes(byte >> shift for byte in raw)
    return "img:" + hashlib.sha256(quantised).hexdigest()


def fingerprint(
    rel: str, project: Path, runner: Runner, *, render: bool = False
) -> str:
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
    if suffix == ".kra":
        digest = hashlib.sha256()
        with zipfile.ZipFile(path) as archive:
            for name in sorted(archive.namelist()):
                if name in {"documentinfo.xml", "preview.png"} or name.endswith("/"):
                    continue
                data = archive.read(name)
                # Krita writes XML attributes in Qt hash order, which changes
                # from run to run; that holds for a filter layer's settings
                # (layerN.filterconfig) as much as for maindoc.xml.
                if name.endswith(_KRA_XML):
                    xml = ElementTree.fromstring(data)
                    for element in xml.iter():
                        for key in list(element.attrib):
                            if "uuid" in key.lower():
                                element.attrib.pop(key)
                    data = ElementTree.canonicalize(
                        ElementTree.tostring(xml, encoding="unicode"), strip_text=True
                    ).encode()
                digest.update(name.encode() + b"\0" + data)
        return "kra:" + digest.hexdigest()
    # Everything else is compared exactly, including baked textures.
    return _exact(path)


def _tool_argv(recipe: Recipe) -> list[str]:
    """The command that replays a recipe: its tool module's replay command."""
    try:
        module = tools.get(recipe.tool)
    except ValueError:
        raise ValueError(f"No replay command for tool {recipe.tool!r}.") from None
    return module.replay_argv(recipe.script)


def run_recipe(
    recipe: Recipe, project: Path, runner: Runner, timeout: int = DEFAULT_TIMEOUT
) -> Result:
    """Run a recipe's script in its tool, producing its outputs."""
    return _runner_for(recipe, runner).run(_tool_argv(recipe), project, timeout=timeout)


def _runner_for(recipe: Recipe, runner: Runner) -> Runner:
    """The runner in the tool's own worker image, for tools that have one."""
    from joinery.runner import SandboxRunner

    try:
        image = tools.get(recipe.tool).replay_image
    except ValueError:
        return runner
    if image and isinstance(runner, SandboxRunner):
        return replace(runner, image=image)
    return runner


def rebuild_one(
    path: Path, project: Path, runner: Runner, timeout: int = DEFAULT_TIMEOUT
) -> Rebuilt:
    """Replay one recipe and check every output still fingerprints the same.

    Outputs are removed only in a disposable copy. Keeping the old outputs
    around would let a no-op recipe pass without recreating anything.
    """
    name = path.name
    try:
        recipe = load_recipe(path)
    except Exception as exc:  # noqa: BLE001 - a bad recipe is a reportable failure.
        return Rebuilt(name, False, 0.0, (f"unreadable recipe: {exc}",))

    if not (project / recipe.script).is_file():
        return Rebuilt(name, False, 0.0, (f"script {recipe.script} not found",))

    try:
        root = project.resolve()
        inputs = {recipe.script, *recipe.sources}
        for relative in (*inputs, *recipe.outputs):
            target = project / relative
            if not target.resolve().is_relative_to(root):
                raise ValueError(
                    f"{relative}: replay paths cannot link outside the project"
                )
        if inputs & set(recipe.outputs):
            raise ValueError(
                "Recipe outputs cannot overwrite their script or source inputs"
            )
        with tempfile.TemporaryDirectory(prefix="joinery-recipe-") as directory:
            scratch = Path(directory) / "project"
            shutil.copytree(
                project,
                scratch,
                symlinks=True,
                ignore=shutil.ignore_patterns(
                    ".git", ".joinery", ".venv", "__pycache__"
                ),
            )
            # An absolute symlink that pointed inside the original project can
            # point back out of this copy. Recheck here before removing anything.
            scratch_root = scratch.resolve()
            for relative in (*inputs, *recipe.outputs):
                if not (scratch / relative).resolve().is_relative_to(scratch_root):
                    raise ValueError(
                        f"{relative}: replay paths cannot link outside the scratch copy"
                    )
            input_paths = {(scratch / relative).resolve() for relative in inputs}
            if any(
                (scratch / relative).resolve() in input_paths
                for relative in recipe.outputs
            ):
                raise ValueError(
                    "Recipe outputs cannot alias their script or source inputs"
                )
            for relative in recipe.outputs:
                output = scratch / relative
                if output.is_symlink() or (output.exists() and not output.is_file()):
                    raise ValueError(
                        f"{relative}: recipe outputs must be regular files"
                    )
                output.unlink(missing_ok=True)
            return _verify_rebuilt(name, recipe, scratch, runner, timeout)
    except (OSError, ValueError) as exc:
        return Rebuilt(name, False, 0.0, (str(exc),))


def _verify_rebuilt(
    name: str, recipe: Recipe, project: Path, runner: Runner, timeout: int
) -> Rebuilt:
    got = run_recipe(recipe, project, runner, timeout=timeout)
    if not got.ok:
        tail = got.output[-600:] or "no output"
        return Rebuilt(name, False, got.seconds, (f"{recipe.tool} failed: {tail}",))
    problems: list[str] = []
    measured: dict[str, str] = {}
    found = fingerprints(recipe.outputs, project, runner, recipe.renders)
    for rel in recipe.outputs:
        value = found[rel]
        if isinstance(value, FileNotFoundError):
            problems.append(f"{rel}: not produced")
            continue
        if isinstance(value, Exception):
            problems.append(f"{rel}: {value}")
            continue
        measured[rel] = value
        wanted = recipe.expect.get(rel)
        if wanted is None:
            problems.append(f"{rel}: recipe records no expected fingerprint")
        elif wanted != measured[rel]:
            problems.append(f"{rel}: expected {wanted}, rebuilt {measured[rel]}")
    return Rebuilt(name, not problems, got.seconds, tuple(problems), measured)


def store_preimage(project: Path, source: Path) -> str:
    """Keep an immutable copy of a document an edit starts from.

    Preimages are named by their content hash, so editing the same document
    twice from the same state, or several recipes sharing one starting point,
    stores it once. Returns the project-relative path a recipe lists in
    ``sources``.
    """
    from joinery.transactions import copy_file

    digest = hashlib.sha256()
    with source.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    folder = project / PREIMAGE_DIR
    folder.mkdir(parents=True, exist_ok=True)
    (folder / ".gdignore").touch()
    target = folder / (digest.hexdigest() + source.suffix)
    if not target.is_file() or _exact(target) != "sha256:" + digest.hexdigest():
        copy_file(source, target)
    return target.relative_to(project).as_posix()


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
    expect: dict[str, str] = {}
    for rel, value in fingerprints(
        recipe.outputs, project, runner, recipe.renders
    ).items():
        if isinstance(value, Exception):
            raise value
        expect[rel] = value
    updated = recipe.model_copy(update={"expect": expect})
    path.write_text(recipe_text(updated))
    return updated


def recipe_text(recipe: Recipe) -> str:
    """A recipe file's text, with its measured expectations."""
    lines = [
        f"version = {recipe.version}",
        f"tool = {json.dumps(recipe.tool)}",
        f"script = {json.dumps(recipe.script)}",
        "outputs = [" + ", ".join(json.dumps(o) for o in recipe.outputs) + "]",
    ]
    if recipe.renders:
        lines.append(
            "renders = [" + ", ".join(json.dumps(r) for r in recipe.renders) + "]"
        )
    if recipe.sources:
        lines.append(
            "sources = [" + ", ".join(json.dumps(s) for s in recipe.sources) + "]"
        )
    if recipe.note:
        lines.append(f"note = {json.dumps(recipe.note)}")
    lines.append("")
    lines.append("# Measured from the real outputs by `joinery rebuild --record`.")
    lines.append("[expect]")
    lines += [
        f"{json.dumps(k)} = {json.dumps(v)}" for k, v in sorted(recipe.expect.items())
    ]
    return "\n".join(lines) + "\n"
