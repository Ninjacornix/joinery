"""Terrain: typed operations on a game's heightmap terrain, made in Godot and reviewed.

A terrain (the terrain feature's ``Terrain`` node, ADR 0068) reads two images:
``art/terrain/<name>/height.png`` (16-bit grey, 0-1 across its height range) and
``art/terrain/<name>/mask.png`` (8-bit RGBA: r, g, b paint layers 1-3, a is free
for scatter). Both are made by Godot itself: the feature's ``terrain_bake.gd`` runs
a document of typed operations (noise, raise, lower, flatten, smooth, erode, path,
paint) and writes them byte for byte the same every time.

The agent never edits the pixels. It calls ``terrain_new`` or ``terrain_edit``,
which validate the operations here, append them to the terrain's document, and
write a recipe: ``recipes/<id>.json`` (the whole document: the operation log),
``recipes/<id>.gd`` (three lines that call the bake) and ``recipes/<id>.toml``
(tool ``godot``, both images as outputs). Godot runs the recipe in the offline
worker; the images get ``joinery.lock`` entries like every tool-made asset, and
the change is staged for the user's review. ``joinery rebuild`` replays it.

A heightmap painted in Krita can be the starting point (``base``); the
operations then work on top of it, and the painting stays the user's source.
"""

from __future__ import annotations

import json
import re
import tomllib
import uuid
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from joinery.authoring import _record, confined, require_transaction, run_offline
from joinery.catalog import ROOT, load_catalog, load_project_tools, selection_ids
from joinery.recipe import IMAGE_SUFFIXES, Recipe, load_recipe, recipe_paths
from joinery.tools import godot

TERRAIN_DIR = "art/terrain"
BAKE_SCRIPT = "features/terrain/terrain_bake.gd"
#: Points along each side of the grid: a power of two plus one, so chunks fit.
RESOLUTIONS = (33, 65, 129, 257, 513, 1025)
MAX_OPS = 400
#: Godot keeps these files as they are (no texture import), so the game reads the
#: full 16 bits and an exported game still has them.
KEEP_IMPORT = '[remap]\n\nimporter="keep"\n'
ALTERNATIVES = ROOT / "policy" / "terrain_alternatives.toml"
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_STATS = re.compile(r"^JOINERY_TERRAIN (\{.*\})\s*$", re.MULTILINE)

Metres = Annotated[float, Field(ge=-100_000, le=100_000, allow_inf_nan=False)]
Point = tuple[Metres, Metres]


class _Op(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Brush(_Op):
    at: Point
    radius: float = Field(8.0, gt=0, le=100_000)
    #: Share of the radius over which the effect fades out (0 a hard edge).
    falloff: float = Field(0.5, ge=0, le=1)


class Noise(_Op):
    op: Literal["noise"]
    amplitude: float = Field(4.0, ge=-1000, le=1000)
    #: Size of the features in metres.
    scale: float = Field(32.0, gt=0, le=100_000)
    octaves: int = Field(4, ge=1, le=8)
    #: Defaults to the terrain's seed.
    seed: int | None = Field(None, ge=0, le=2**31 - 1)
    ridged: bool = False
    offset: float = Field(0.0, ge=-1000, le=1000)


class Raise(_Brush):
    op: Literal["raise"]
    amount: float = Field(1.0, gt=0, le=10_000)


class Lower(_Brush):
    op: Literal["lower"]
    amount: float = Field(1.0, gt=0, le=10_000)


class Flatten(_Brush):
    op: Literal["flatten"]
    #: Metres; defaults to the ground's height at `at`.
    height: Metres | None = None


class Smooth(_Op):
    op: Literal["smooth"]
    #: Without `at` the whole terrain is smoothed.
    at: Point | None = None
    radius: float = Field(8.0, gt=0, le=100_000)
    falloff: float = Field(0.5, ge=0, le=1)
    strength: float = Field(0.5, ge=0, le=1)
    iterations: int = Field(2, ge=1, le=20)


class Erode(_Op):
    op: Literal["erode"]
    iterations: int = Field(10, ge=1, le=50)
    #: The steepest slope that stays (rise per metre).
    talus: float = Field(0.6, ge=0, le=10)
    amount: float = Field(0.5, ge=0, le=1)


class Path_(_Op):
    op: Literal["path"]
    points: list[Point] = Field(min_length=2, max_length=200)
    width: float = Field(3.0, gt=0, le=1000)
    falloff: float | None = Field(None, ge=0, le=1000)
    #: Metres the path is sunk below the eased ground.
    depth: float = Field(0.0, ge=-100, le=100)
    #: Mask channel to paint the path into ("" paints nothing).
    paint: Literal["", "r", "g", "b", "a"] = ""
    value: float = Field(1.0, ge=0, le=1)


class Paint(_Brush):
    op: Literal["paint"]
    channel: Literal["r", "g", "b", "a"]
    #: 1 paints the channel in, 0 erases it.
    value: float = Field(1.0, ge=0, le=1)


Op = Annotated[
    Noise | Raise | Lower | Flatten | Smooth | Erode | Path_ | Paint,
    Field(discriminator="op"),
]
_OPS: TypeAdapter[list[Op]] = TypeAdapter(list[Op])


class Spec(BaseModel):
    """A terrain's grid: its size, resolution and height range, and its seed."""

    model_config = ConfigDict(extra="forbid")

    size: tuple[
        Annotated[float, Field(ge=1, le=16_384)],
        Annotated[float, Field(ge=1, le=16_384)],
    ] = (64.0, 64.0)
    resolution: int = 129
    min_height: float = Field(0.0, ge=-10_000, le=10_000)
    max_height: float = Field(20.0, ge=-10_000, le=10_000)
    seed: int = Field(1, ge=0, le=2**31 - 1)
    #: A heightmap to start from (PNG or EXR under art/, e.g. painted in Krita).
    base: str = ""
    #: A mask to start from (RGBA PNG under art/).
    base_mask: str = ""

    @model_validator(mode="after")
    def _sane(self) -> Spec:
        if self.resolution not in RESOLUTIONS:
            raise ValueError(
                "resolution is the points along a side: one of "
                + ", ".join(str(r) for r in RESOLUTIONS)
            )
        if self.max_height <= self.min_height:
            raise ValueError("max_height must be above min_height")
        return self


def _problem(exc: ValidationError) -> str:
    first = exc.errors()[0]
    where = ".".join(str(p) for p in first.get("loc", ()))
    return f"{where}: {first.get('msg', 'invalid')}" if where else str(first.get("msg"))


def check_name(name: str) -> str:
    if not _NAME.match(name):
        raise ValueError(
            "A terrain name is lower case letters, digits and _, starting with a letter."
        )
    return name


def check_spec(raw: dict[str, Any]) -> Spec:
    try:
        return Spec.model_validate(raw)
    except ValidationError as exc:
        raise ValueError("Terrain settings: " + _problem(exc)) from None


def check_ops(raw: list[dict[str, Any]], spec: Spec) -> list[dict[str, Any]]:
    """The operations as the bake reads them, or a ValueError naming the first problem."""
    try:
        ops = _OPS.validate_python(raw)
    except ValidationError as exc:
        raise ValueError("Terrain operation " + _problem(exc)) from None
    half_x, half_z = spec.size[0] / 2, spec.size[1] / 2
    rows: list[dict[str, Any]] = []
    for number, op in enumerate(ops, 1):
        points = (
            [op.at] if isinstance(op, _Brush | Smooth) and op.at is not None else []
        )
        if isinstance(op, Path_):
            points = list(op.points)
        reach = getattr(op, "radius", 0.0) if not isinstance(op, Path_) else op.width
        for x, z in points:
            if abs(x) > half_x + reach or abs(z) > half_z + reach:
                raise ValueError(
                    f"Terrain operation {number} ({op.op}) is at [{x:g}, {z:g}], outside "
                    f"the terrain (x and z run from -{half_x:g} to {half_x:g} and "
                    f"-{half_z:g} to {half_z:g} m)."
                )
        if (
            isinstance(op, Flatten)
            and op.height is not None
            and not spec.min_height <= op.height <= spec.max_height
        ):
            raise ValueError(
                f"Terrain operation {number} flattens to {op.height:g} m, outside the "
                f"height range {spec.min_height:g} to {spec.max_height:g} m."
            )
        rows.append(op.model_dump(exclude_none=True))
    return rows


# --- Documents and recipes --------------------------------------------------------------


def outputs(name: str) -> dict[str, str]:
    return {
        "height": f"{TERRAIN_DIR}/{name}/height.png",
        "mask": f"{TERRAIN_DIR}/{name}/mask.png",
    }


def current(project: Path, name: str) -> tuple[Path, dict[str, Any]] | None:
    """The recipe that makes a terrain now, and its document."""
    height = outputs(name)["height"]
    for path in recipe_paths(project):
        try:
            recipe = load_recipe(path)
        except (OSError, ValueError, tomllib.TOMLDecodeError):
            continue
        if recipe.tool != "godot" or height not in recipe.outputs or not recipe.sources:
            continue
        try:
            doc = json.loads((project / recipe.sources[0]).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict):
            return path, doc
    return None


def node_settings(doc: dict[str, Any]) -> dict[str, Any]:
    """What the Terrain node in a scene needs to show this terrain."""
    files = doc["outputs"]
    return {
        "heightmap": "res://" + files["height"],
        "mask": "res://" + files["mask"],
        "size": list(doc["size"]),
        "min_height": doc["min_height"],
        "max_height": doc["max_height"],
    }


def recipe_script(document: str, name: str) -> str:
    return (
        "extends SceneTree\n"
        f"## Terrain {name!r}: writes its heightmap and mask from {document}. Made by\n"
        "## Joinery's terrain tools; `joinery rebuild` runs it again in Godot.\n\n\n"
        "func _initialize() -> void:\n"
        f'\tvar bake: RefCounted = load("res://{BAKE_SCRIPT}").new()\n'
        f'\tquit(bake.call(&"bake", "res://{document}"))\n'
    )


def _summary(op: dict[str, Any]) -> str:
    kind = str(op["op"])
    if "at" in op:
        x, z = op["at"]
        return f"{kind} at [{x:g}, {z:g}]"
    if kind == "path":
        return f"path of {len(op['points'])} points"
    return kind


def _check_base(project: Path, path: str, what: str) -> None:
    if not path:
        return
    target = confined(project, path)
    if not path.startswith("art/") or target.suffix.lower() not in IMAGE_SUFFIXES:
        raise ValueError(f"The {what} is an image under art/ (PNG or EXR).")
    if not target.is_file():
        raise ValueError(f"{path} is missing.")


def _ready(project: Path) -> None:
    require_transaction(project)
    if "godot" not in selection_ids(
        load_catalog(), load_project_tools(project / "joinery.toml")
    ):
        raise PermissionError("godot is not selected for this project.")
    if not (project / BAKE_SCRIPT).is_file():
        raise ValueError(
            "This game has no terrain feature. Ask the user to add it "
            "(joinery feature add terrain) first."
        )


async def create(
    project: Path, name: str, settings: dict[str, Any], ops: list[dict[str, Any]]
) -> dict[str, Any]:
    """Make a new terrain: its grid, then the operations, in Godot."""
    check_name(name)
    spec = check_spec(settings)
    _ready(project)
    files = outputs(name)
    if current(project, name) is not None or any(
        (project / f).exists() for f in files.values()
    ):
        raise ValueError(
            f"A terrain called {name} exists; change it with terrain_edit."
        )
    _check_base(project, spec.base, "base heightmap")
    _check_base(project, spec.base_mask, "base mask")
    doc = {
        "version": 1,
        "name": name,
        **spec.model_dump(),
        "size": list(spec.size),
        "ops": check_ops(ops, spec),
        "outputs": files,
    }
    note = (
        f"Terrain {name}: {spec.size[0]:g} x {spec.size[1]:g} m, "
        f"{spec.resolution} points a side, {spec.min_height:g} to {spec.max_height:g} m, "
        f"seed {spec.seed}, {len(doc['ops'])} operations"
    )
    return await bake(project, doc, note)


async def edit(project: Path, name: str, ops: list[dict[str, Any]]) -> dict[str, Any]:
    """Change a terrain: its operations are appended and the whole log runs again."""
    check_name(name)
    _ready(project)
    found = current(project, name)
    if found is None:
        raise ValueError(f"No terrain called {name}; make it with terrain_new.")
    _, old = found
    spec = check_spec(
        {k: old[k] for k in Spec.model_fields if k in old}
        | {"size": tuple(old["size"])}
    )
    if not ops:
        raise ValueError("Give the operations to apply.")
    added = check_ops(ops, spec)
    if len(old["ops"]) + len(added) > MAX_OPS:
        raise ValueError(
            f"A terrain keeps at most {MAX_OPS} operations; this one has "
            f"{len(old['ops'])}. Smooth or flatten larger areas instead of many small ones."
        )
    doc = {**old, "ops": [*old["ops"], *added]}
    note = f"Terrain {name}: " + ", ".join(_summary(op) for op in added)
    return await bake(project, doc, note)


async def bake(project: Path, doc: dict[str, Any], note: str) -> dict[str, Any]:
    """Write the document and recipe, run it in Godot in the offline worker, record it."""
    files: dict[str, str] = doc["outputs"]
    targets = [confined(project, f) for f in files.values()]
    kept = {t: t.read_bytes() for t in targets if t.is_file()}
    identifier = uuid.uuid4().hex
    recipes = project / "recipes"
    recipes.mkdir(exist_ok=True)
    document = recipes / f"{identifier}.json"
    script = recipes / f"{identifier}.gd"
    toml = recipes / f"{identifier}.toml"
    document.write_text(
        json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    script.write_text(
        recipe_script(document.relative_to(project).as_posix(), str(doc["name"])),
        encoding="utf-8",
    )
    for target in targets:
        target.parent.mkdir(parents=True, exist_ok=True)
        settings = target.with_name(target.name + ".import")
        if not settings.is_file() or 'importer="keep"' not in settings.read_text():
            settings.write_text(KEEP_IMPORT, encoding="utf-8")
    sources = [document.relative_to(project).as_posix()]
    sources += [str(doc[k]) for k in ("base", "base_mask") if doc.get(k)]
    recipe = Recipe(
        tool="godot",
        script=script.relative_to(project).as_posix(),
        outputs=tuple(files.values()),
        sources=tuple(sources),
        note=note,
    )
    resolution = int(doc["resolution"])
    timeout = 120 + resolution * resolution * (1 + len(doc["ops"])) // 20_000
    try:
        result = await run_offline(
            godot.MODULE.replay_argv(recipe.script), project, timeout
        )
        found = _STATS.search(result.output)
        stats = json.loads(found.group(1)) if found else {}
        if (
            not result.ok
            or not stats.get("ok")
            or not all(t.is_file() for t in targets)
        ):
            tail = result.output.strip()[-600:] or "no output"
            raise RuntimeError("Godot did not make the terrain: " + tail)
        recorded = await _record(project, recipe, toml)
    except BaseException:
        for path in (document, script, toml):
            path.unlink(missing_ok=True)
        for target in targets:
            if target in kept:
                target.write_bytes(kept[target])
            else:
                target.unlink(missing_ok=True)
        raise
    return {
        **recorded,
        "terrain": doc["name"],
        "heights_m": [stats.get("low"), stats.get("high")],
        "operations": len(doc["ops"]),
        "problems": list(stats.get("problems", [])),
        "node": node_settings(doc),
        "next": (
            "Set these on the Terrain node that shows it (see "
            "res://features/terrain/GUIDE.md); the user reviews the change."
        ),
    }


def describe(project: Path, name: str = "") -> dict[str, Any]:
    """The terrains this project has: settings, operations and files."""
    names = sorted(
        p.parent.name
        for p in (project / TERRAIN_DIR).glob("*/height.png")
        if not name or p.parent.name == name
    )
    rows: list[dict[str, Any]] = []
    for each in names:
        found = current(project, each)
        if found is None:
            rows.append({"name": each, "recipe": None, "note": "No recipe makes it."})
            continue
        path, doc = found
        rows.append(
            {
                "name": each,
                "recipe": path.relative_to(project).as_posix(),
                "size": doc.get("size"),
                "resolution": doc.get("resolution"),
                "heights": [doc.get("min_height"), doc.get("max_height")],
                "seed": doc.get("seed"),
                "base": doc.get("base") or None,
                "operations": [_summary(op) for op in doc.get("ops", [])],
                "node": node_settings(doc),
            }
        )
    if name and not rows:
        raise ValueError(f"No terrain called {name}.")
    return {
        "terrains": rows,
        "feature": (project / BAKE_SCRIPT).is_file(),
        "guide": "res://features/terrain/GUIDE.md",
    }


# --- Larger alternatives -------------------------------------------------------------------


def alternatives() -> list[dict[str, Any]]:
    """Terrain3D, ProtonScatter and Spatial Gardener as the policy pins them."""
    with ALTERNATIVES.open("rb") as f:
        raw = tomllib.load(f)
    return [dict(item) for item in raw.get("addon", [])]


def install_alternative(name: str) -> dict[str, Any]:
    """Refuses: an alternative installs only once a reviewed change pins its hash.

    Joinery never downloads one by itself; with a pin, installing is still a
    separate step the user approves (ADR 0068), and it is not built yet.
    """
    for item in alternatives():
        if item.get("name") == name:
            if not item.get("sha256"):
                raise PermissionError(
                    f"{name} isn't pinned (policy/terrain_alternatives.toml has no hash), "
                    "so Joinery won't install it. The terrain feature works without it."
                )
            raise NotImplementedError(
                f"{name} is pinned, but installing it is a separate step the user "
                "approves and has not been built."
            )
    raise ValueError(f"{name} is not a terrain alternative Joinery knows.")
