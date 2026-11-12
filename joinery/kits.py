"""Parametric kits: models made in Blender by hand-written procedural generators.

A kit is a generator in ``kits/blender/<id>.py`` (MIT, copied into the game's
recipes): a ``SPEC`` of typed parameters and material roles, and a ``build``
that makes one mesh from bmesh shapes. No model is involved anywhere: the
shapes come from the code and the seed (Rule Zero).

Joinery checks the parameters here and turns the art bible into the build's
context: material colours from the bible's palette, roughness and metallic
inside its ranges, the triangle budget and the pivot of the folder the model
goes in. One Blender recipe then makes every model of a set, each as its own
``.blend`` source and GLB (plus LOD GLBs when asked). The recipe carries the
kit's code, so ``joinery rebuild`` replays it without this repository, and a
marker line records the kit, its parameters and seeds, so ``kit_edit`` can make
the set again with new values.
"""

from __future__ import annotations

import asyncio
import json
import math
import random
import re
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path, PurePosixPath
from typing import Any

from joinery.authoring import _record, confined, enabled, run_offline
from joinery.bible_rules import (
    BibleRules,
    _folder_value,
    _lab,
    delta_e_2000,
    hex_rgb,
    load_rules,
    measure_glb,
    relative_luminance,
)
from joinery.recipe import Recipe, load_recipe, recipe_paths
from joinery.tools import blender
from joinery.tools.blender import FAIL_ON_ERROR

KITS_DIR = Path(__file__).resolve().parents[1] / "kits" / "blender"
#: Where every kit-made .blend lives in a game project.
SOURCE_DIR = "art/sources/kits"
#: The recipe line that records how a set was made.
MARKER = "# joinery-kit: "
MAX_COUNT = 100
MAX_LODS = 2
KINDS = ("float", "int", "choice", "bool")
#: How far a batch varies each parameter by default (a share of its range).
DEFAULT_VARY = 0.25
_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,47}$")
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


@dataclass(frozen=True)
class Param:
    name: str
    kind: str
    default: Any
    label: str
    help: str
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    choices: tuple[str, ...] = ()
    unit: str = ""
    #: How a batch varies it: "scale" (relative), "range" (share of the range) or "".
    vary: str = ""

    def check(self, value: Any) -> Any:
        """The value as this parameter's type, or a ValueError saying what it takes."""
        if self.kind == "bool":
            if not isinstance(value, bool):
                raise ValueError(f"{self.name} is true or false.")
            return value
        if self.kind == "choice":
            if value not in self.choices:
                raise ValueError(f"{self.name} is one of: {', '.join(self.choices)}.")
            return value
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{self.name} is a number.")  # noqa: TRY004 - shown to the agent
        if not math.isfinite(value):
            raise ValueError(f"{self.name} must be a finite number.")
        if self.kind == "int":
            if value != int(value):
                raise ValueError(f"{self.name} is a whole number.")
            value = int(value)
        else:
            value = float(value)
        low, high = self.minimum, self.maximum
        if (low is not None and value < low) or (high is not None and value > high):
            raise ValueError(
                f"{self.name} is from {_number(low)} to {_number(high)}"
                f"{' ' + self.unit if self.unit else ''}; got {_number(value)}."
            )
        return value

    def as_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "name": self.name,
            "kind": self.kind,
            "default": self.default,
            "label": self.label,
            "help": self.help,
        }
        if self.kind in {"float", "int"}:
            data.update(min=self.minimum, max=self.maximum)
            data["step"] = self.step or (1 if self.kind == "int" else 0.01)
        if self.choices:
            data["choices"] = list(self.choices)
        if self.unit:
            data["unit"] = self.unit
        if self.vary:
            data["vary"] = self.vary
        return data


def _number(value: float | None) -> str:
    return "?" if value is None else f"{value:g}"


@dataclass(frozen=True)
class Kit:
    id: str
    label: str
    summary: str
    folder: str
    params: tuple[Param, ...]
    #: Material role -> {colour, roughness, metallic, double_sided?}.
    looks: Mapping[str, Mapping[str, Any]]
    #: The kit's own code, copied into each recipe.
    source: str
    limits: Callable[[dict[str, Any]], list[str]] | None = None
    #: Where a shared kit came from ("Harbour 1.0 by Ana, CC-BY-4.0"); "" for Joinery's.
    credit: str = ""
    #: Named parameter sets a shared kit brings: [{name, params, colours}].
    presets: tuple[Mapping[str, Any], ...] = ()
    #: Project files the build reads (a user's own .blend), listed as recipe sources.
    sources: tuple[str, ...] = ()

    def param(self, name: str) -> Param:
        for param in self.params:
            if param.name == name:
                return param
        raise ValueError(
            f"The {self.label.lower()} kit has no {name!r}. It takes: "
            + ", ".join(p.name for p in self.params)
            + "."
        )

    def defaults(self) -> dict[str, Any]:
        return {p.name: p.default for p in self.params}


def from_spec(
    spec: Mapping[str, Any], source: str, limits: Any = None, **extra: Any
) -> Kit:
    """A kit from its SPEC dict and code; ``extra`` sets id, label, credit or presets."""
    params = []
    for name, raw in spec["params"].items():
        if raw["kind"] not in KINDS:
            raise ValueError(f"{name} has an unknown kind {raw['kind']!r}")
        params.append(
            Param(
                name=name,
                kind=raw["kind"],
                default=raw["default"],
                label=raw["label"],
                help=raw["help"],
                minimum=raw.get("min"),
                maximum=raw.get("max"),
                step=raw.get("step"),
                choices=tuple(raw.get("choices", ())),
                unit=raw.get("unit", ""),
                vary=raw.get("vary", ""),
            )
        )
    fields: dict[str, Any] = {k: spec[k] for k in ("id", "label", "summary", "folder")}
    sources = spec.get("sources", ())
    if not isinstance(sources, (list, tuple)) or not all(
        isinstance(s, str)
        and s.startswith("art/")
        and ".." not in PurePosixPath(s).parts
        for s in sources
    ):
        raise ValueError("A kit's sources are project files under art/.")
    fields["sources"] = tuple(sources)
    return Kit(
        **{**fields, **extra},
        params=tuple(params),
        looks=spec["materials"],
        source=source,
        limits=limits if callable(limits) else None,
    )


def _load(path: Path) -> Kit:
    source = path.read_text(encoding="utf-8")
    # The kit file is plain Python until build() runs: SPEC, the parameter
    # limits and their helpers need no Blender.
    namespace: dict[str, Any] = {"__name__": f"joinery_kit_{path.stem}"}
    exec(compile(source, str(path), "exec"), namespace)  # noqa: S102 - our own kit code
    return from_spec(namespace["SPEC"], source, namespace.get("limits"))


#: The order kits are listed in; any other kit follows by name.
ORDER = ("rock", "crate", "barrel", "fence", "hut", "tree", "bush")


@cache
def catalogue() -> dict[str, Kit]:
    """Every kit in the library, by id."""
    found = [_load(p) for p in sorted(KITS_DIR.glob("*.py")) if p.stem != "common"]
    found.sort(key=lambda k: (ORDER.index(k.id) if k.id in ORDER else len(ORDER), k.id))
    return {kit.id: kit for kit in found}


@cache
def common_source() -> str:
    return (KITS_DIR / "common.py").read_text(encoding="utf-8")


def every(project: Path | None = None) -> dict[str, Kit]:
    """Joinery's kits, then the project's own and those from imported shared kits."""
    if project is None:
        return catalogue()
    from joinery.kit_library import project_kits

    return {**catalogue(), **project_kits(project)}


def get(kit_id: str, project: Path | None = None) -> Kit:
    kits = every(project)
    if kit_id not in kits:
        raise ValueError(f"No kit {kit_id!r}. Kits: {', '.join(kits)}.")
    return kits[kit_id]


def check_params(kit: Kit, params: Mapping[str, Any] | None) -> dict[str, Any]:
    """Every parameter of the kit: the given ones checked, the rest at defaults."""
    values = kit.defaults()
    for name, value in (params or {}).items():
        values[name] = kit.param(name).check(value)
    problems = kit.limits(dict(values)) if kit.limits else []
    if problems:
        raise ValueError("; ".join(problems))
    return values


def _vary_value(param: Param, base: Any, share: float, rng: random.Random) -> Any:
    shift = rng.uniform(-1.0, 1.0) * share
    low = param.minimum if param.minimum is not None else -math.inf
    high = param.maximum if param.maximum is not None else math.inf
    if param.vary == "scale":
        value = base * (1.0 + shift)
    else:
        span = (high - low) if math.isfinite(high - low) else abs(base)
        value = base + shift * span / 2
    value = min(high, max(low, value))
    return round(value) if param.kind == "int" else round(value, 4)


def _shares(
    kit: Kit, vary: float | Mapping[str, float] | None, count: int
) -> dict[str, float]:
    if vary is None:
        vary = DEFAULT_VARY if count > 1 else 0.0
    if isinstance(vary, Mapping):
        shares = {}
        for name, share in vary.items():
            param = kit.param(name)
            if not param.vary:
                raise ValueError(f"{name} does not vary between models of a set.")
            shares[name] = float(share)
    else:
        shares = {p.name: float(vary) for p in kit.params if p.vary}
    for name, share in shares.items():
        if not math.isfinite(share) or not 0.0 <= share <= 1.0:
            raise ValueError(f"vary for {name} is from 0 to 1.")
    return shares


def variants(
    kit: Kit,
    base: Mapping[str, Any],
    count: int,
    seed: int,
    vary: float | Mapping[str, float] | None = None,
) -> list[tuple[int, dict[str, Any]]]:
    """(seed, parameters) of each model of a set: the same every time for a seed."""
    if count == 1:
        return [(seed, dict(base))]
    shares = _shares(kit, vary, count)
    rng = random.Random(f"joinery-kit:{kit.id}:{seed}")
    made = []
    for _index in range(count):
        own = rng.randrange(1, 2**31)
        values = dict(base)
        for param in kit.params:
            share = shares.get(param.name, 0.0)
            if share > 0:
                values[param.name] = _vary_value(param, base[param.name], share, rng)
        if kit.limits and kit.limits(dict(values)):
            values = dict(base)  # a variation the kit can't build: use the base
        made.append((own, values))
    return made


# --- the art bible --------------------------------------------------------------


def _distance(first: str, second: str) -> float:
    return delta_e_2000(_lab(hex_rgb(first)), _lab(hex_rgb(second)))


def looks(
    kit: Kit, rules: BibleRules, prefix: str, colours: Mapping[str, str] | None = None
) -> dict[str, dict[str, Any]]:
    """Each material role's look, inside the art bible.

    The colour is the palette colour nearest the kit's own (or the one the user
    gave, which must be in the palette); roughness and metallic sit inside the
    bible's ranges, and metallic is kept only where the material's name says metal
    when the bible asks for that.
    """
    palette = rules.palette.all_colours()
    lumen = rules.materials.albedo_luminance
    given = dict(colours or {})
    unknown = set(given) - set(kit.looks)
    if unknown:
        raise ValueError(
            f"The {kit.label.lower()} kit has no material {min(unknown)!r}. "
            f"Its materials: {', '.join(kit.looks)}."
        )
    made: dict[str, dict[str, Any]] = {}
    for role, look in kit.looks.items():
        colour = str(look["colour"])
        if role in given:
            colour = given[role]
            if not isinstance(colour, str) or not _HEX.match(colour):
                raise ValueError(f"The {role} colour is '#rrggbb'.")
            if palette and min(_distance(colour, c) for c in palette) > (
                rules.palette.max_delta_e
            ):
                raise ValueError(
                    f"{colour} is not in the art bible's palette. "
                    f"Use one of: {', '.join(palette)}."
                )
        elif palette:
            usable = [
                c
                for c in palette
                if lumen is None or lumen[0] <= relative_luminance(c) <= lumen[1]
            ] or palette
            colour = min(usable, key=lambda c: (_distance(colour, c), c))
        roughness = float(look["roughness"])
        if rules.materials.roughness is not None:
            low, high = rules.materials.roughness
            roughness = min(high, max(low, roughness))
        metallic = float(look["metallic"])
        if rules.materials.metallic is not None:
            low, high = rules.materials.metallic
            metallic = min(high, max(low, metallic))
        pattern = rules.materials.metallic_requires_name
        name = material_name(prefix, role)
        if metallic > 0.05 and pattern and not re.search(pattern, name):
            metallic = 0.0
        made[role] = {
            "colour": colour.lower(),
            "roughness": round(roughness, 4),
            "metallic": round(metallic, 4),
            "double_sided": bool(look.get("double_sided", False)),
        }
    return made


def material_name(prefix: str, role: str) -> str:
    """The Blender material of a role, as common.make_variant names it."""
    return f"{prefix}_{role.title().replace('_', '')}"


def object_name(stem: str) -> str:
    """rock_01 -> Rock_01; mossy_rock -> MossyRock."""
    head, _, number = stem.rpartition("_")
    if head and number.isdigit():
        return object_name(head) + "_" + number
    return "".join(part.capitalize() for part in re.split(r"[_\-\s]+", stem) if part)


# --- planning a set ---------------------------------------------------------------


@dataclass(frozen=True)
class Plan:
    kit: Kit
    params: dict[str, Any]
    seed: int
    name: str
    count: int
    vary: float | dict[str, float] | None
    folder: str
    lods: int
    colours: dict[str, str]
    #: Per model: name, object, prefix, seed, params, blend, glb, lods.
    models: list[dict[str, Any]]
    budget: int | None
    pivot: str
    looks: dict[str, dict[str, Any]]

    @property
    def outputs(self) -> list[str]:
        found: list[str] = []
        for model in self.models:
            found += [model["blend"], model["glb"], *model["lods"]]
        return found

    def marker(self) -> dict[str, Any]:
        return {
            "kit": self.kit.id,
            "name": self.name,
            "params": self.params,
            "seed": self.seed,
            "count": self.count,
            "vary": self.vary,
            "folder": self.folder,
            "lods": self.lods,
            "colours": self.colours,
            "models": [
                {
                    "glb": m["glb"],
                    "blend": m["blend"],
                    "seed": m["seed"],
                    "params": m["params"],
                }
                for m in self.models
            ],
        }


def check_name(name: str) -> str:
    if not _NAME.match(name):
        raise ValueError(
            "The name is 1 to 48 letters, digits, '_' or '-', starting with a letter."
        )
    return name.lower().replace("-", "_")


def check_folder(folder: str) -> str:
    path = PurePosixPath(folder.removeprefix("res://").strip("/"))
    if (
        not path.parts
        or path.parts[0] != "art"
        or ".." in path.parts
        or path.is_relative_to("art/sources")
        or path.is_relative_to("art/reference")
    ):
        raise ValueError(
            "Models go in a folder under art/ (not art/sources/ or art/reference/)."
        )
    return path.as_posix()


def plan(
    project: Path,
    kit_id: str,
    params: Mapping[str, Any] | None = None,
    *,
    seed: int = 1,
    name: str = "",
    count: int = 1,
    vary: float | Mapping[str, float] | None = None,
    folder: str = "",
    lods: int = 0,
    colours: Mapping[str, str] | None = None,
    rules: BibleRules | None = None,
) -> Plan:
    """Everything a set needs before Blender runs, checked."""
    kit = get(kit_id, project)
    values = check_params(kit, params)
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**31:
        raise ValueError("The seed is a whole number from 0 to 2147483647.")
    if (
        isinstance(count, bool)
        or not isinstance(count, int)
        or not 1 <= count <= MAX_COUNT
    ):
        raise ValueError(f"A set has 1 to {MAX_COUNT} models.")
    if isinstance(lods, bool) or not isinstance(lods, int) or not 0 <= lods <= MAX_LODS:
        raise ValueError(f"lods is 0 to {MAX_LODS}.")
    stem = check_name(name or kit.id)
    where = check_folder(folder or kit.folder)
    rules = rules if rules is not None else load_rules(project)
    width = 3 if count > 99 else 2
    stems = (
        [stem] if count == 1 else [f"{stem}_{i:0{width}d}" for i in range(1, count + 1)]
    )
    prefix = object_name(stem)
    resolved = looks(kit, rules, prefix, colours)
    vary_record: float | dict[str, float] | None = (
        dict(vary) if isinstance(vary, Mapping) else vary
    )
    models: list[dict[str, Any]] = []
    for own, (model_seed, model_params) in zip(
        stems, variants(kit, values, count, seed, vary), strict=True
    ):
        glb = f"{where}/{own}.glb"
        models.append(
            {
                "name": own,
                "object": object_name(own),
                "prefix": prefix,
                "seed": model_seed,
                "params": model_params,
                "blend": f"{SOURCE_DIR}/{own}.blend",
                "glb": glb,
                "lods": [
                    f"{where}/{own}_lod{level}.glb" for level in range(1, lods + 1)
                ],
            }
        )
    probe = models[0]["glb"]
    budget = _folder_value(rules.models.max_triangles, probe)
    pivot = _folder_value(rules.models.pivot, probe) or "bottom-centre"
    names = [m["object"] for m in models] + [
        material_name(prefix, r) for r in kit.looks
    ]
    for path in (probe, models[0]["blend"]):
        pattern = _folder_value(rules.naming.nodes, path)
        if pattern and (bad := [n for n in names if not re.fullmatch(pattern, n)]):
            raise ValueError(
                f"The names {', '.join(bad[:3])} don't follow the art bible's naming "
                f"pattern {pattern}. Choose a name that does."
            )
        pattern = _folder_value(rules.naming.files, path)
        if pattern and not all(
            re.fullmatch(pattern, Path(m["glb"]).stem) for m in models
        ):
            raise ValueError(
                f"The file name {stem!r} doesn't follow the art bible's file naming "
                f"pattern {pattern}. Choose a name that does."
            )
    return Plan(
        kit=kit,
        params=values,
        seed=seed,
        name=stem,
        count=count,
        vary=vary_record,
        folder=where,
        lods=lods,
        colours=dict(colours or {}),
        models=models,
        budget=budget,
        pivot=pivot,
        looks=resolved,
    )


def script(made: Plan) -> str:
    """The Blender recipe that makes every model of the set, self-contained."""
    context = {"looks": made.looks, "budget": made.budget, "pivot": made.pivot}
    jobs = [
        {
            key: m[key]
            for key in ("object", "prefix", "seed", "params", "blend", "glb", "lods")
        }
        for m in made.models
    ]
    return "\n".join(
        [
            (
                f"# Made by Joinery's {made.kit.label.lower()} kit (kits/blender/"
                f"{made.kit.id}.py, MIT): hand-written procedural code, no models."
                if not made.kit.credit
                else f"# Made by the {made.kit.id} kit from {made.kit.credit}."
            ),
            MARKER + json.dumps(made.marker(), sort_keys=True),
            "import bpy, os, math, json",
            FAIL_ON_ERROR,
            "os.chdir('/work')",
            "# --- kits/blender/common.py",
            common_source(),
            f"# --- kits/blender/{made.kit.id}.py",
            made.kit.source,
            "# --- this set",
            f"CONTEXT = json.loads({json.dumps(context, sort_keys=True)!r})",
            f"MODELS = json.loads({json.dumps(jobs, sort_keys=True)!r})",
            "for _model in MODELS:",
            "    make_variant(build, _model, CONTEXT)",
            "",
        ]
    )


def read_marker(text: str) -> dict[str, Any] | None:
    for line in text.splitlines()[:5]:
        if line.startswith(MARKER):
            try:
                data = json.loads(line.removeprefix(MARKER))
            except ValueError:
                return None
            return data if isinstance(data, dict) else None
    return None


def _stats(output: str) -> list[dict[str, Any]]:
    rows = []
    for line in output.splitlines():
        if line.startswith("JOINERY_KIT:"):
            rows.append(json.loads(line.removeprefix("JOINERY_KIT:")))
    return rows


def _taken(project: Path, outputs: list[str]) -> list[str]:
    claimed: set[str] = set()
    for path in recipe_paths(project):
        try:
            claimed.update(load_recipe(path).outputs)
        except (OSError, ValueError):
            continue
    return [p for p in outputs if p in claimed or (project / p).exists()]


def free_name(project: Path, kit_id: str, count: int, folder: str = "") -> str:
    """The kit's own name, or the first numbered one no file uses yet."""
    kit = get(kit_id, project)
    where = check_folder(folder or kit.folder)
    for number in range(1, 1000):
        stem = kit.id if number == 1 else f"{kit.id}_{number}"
        if count > 1:
            stem = f"{kit.id}s" if number == 1 else f"{kit.id}s_{number}"
        first = f"{stem}_01" if count > 1 else stem
        if not _taken(project, [f"{where}/{first}.glb", f"{SOURCE_DIR}/{first}.blend"]):
            return stem
    raise ValueError("Choose a name for the new models.")


async def make(project: Path, made: Plan, *, replacing: str = "") -> dict[str, Any]:
    """Run the set's recipe in Blender in the offline worker and record it."""
    enabled(project, "blender")
    outputs = made.outputs
    for model in made.models:
        blender.MODULE.check_destinations(
            model["blend"], [model["glb"], *model["lods"]]
        )
        for path in (model["blend"], model["glb"], *model["lods"]):
            confined(project, path)
    if not replacing:
        taken = _taken(project, outputs)
        if taken:
            raise ValueError(
                f"{taken[0]} already exists. Choose another name, or change those "
                "models with kit_edit."
            )
    for needed in made.kit.sources:
        if not confined(project, needed).is_file():
            raise ValueError(
                f"The {made.kit.label.lower()} kit builds from {needed}, which is missing."
            )
    kept = {p: (project / p).read_bytes() for p in outputs if (project / p).is_file()}
    identifier = uuid.uuid4().hex
    recipes = project / "recipes"
    recipes.mkdir(exist_ok=True)
    path = recipes / (identifier + ".py")
    path.write_text(script(made), encoding="utf-8")
    first = made.params
    recipe = Recipe(
        tool="blender",
        script=path.relative_to(project).as_posix(),
        outputs=tuple(outputs),
        sources=made.kit.sources,
        note=(
            f"{made.kit.label} kit"
            + (f", {made.count} models" if made.count > 1 else "")
            + ": "
            + ", ".join(f"{k} {_shown(v)}" for k, v in first.items())
            + f", seed {made.seed}"
        ),
    )
    try:
        result = await run_offline(
            blender.MODULE.replay_argv(recipe.script), project, 600 + 30 * made.count
        )
        if not result.ok:
            raise RuntimeError(
                "Blender did not finish the kit: " + _reason(result.output)
            )
        stats = _stats(result.output)
        missing = [p for p in outputs if not (project / p).is_file()]
        if missing or len(stats) != made.count:
            raise RuntimeError(
                "Blender did not write every model: " + ", ".join(missing[:3])
            )
        problems = _measure(project, made)
        if problems:
            raise ValueError("; ".join(problems))
        (project / SOURCE_DIR / ".gdignore").write_text("")
        recorded = await _record(project, recipe, path.with_suffix(".toml"))
    except BaseException:
        path.unlink(missing_ok=True)
        path.with_suffix(".toml").unlink(missing_ok=True)
        for output in outputs:
            if output in kept:
                (project / output).write_bytes(kept[output])
            else:
                (project / output).unlink(missing_ok=True)
        raise
    models = []
    for model, row in zip(made.models, stats, strict=True):
        models.append(
            {
                "glb": model["glb"],
                "blend": model["blend"],
                "lods": model["lods"],
                "seed": model["seed"],
                "params": model["params"],
                "triangles": row["triangles"],
                "lod_triangles": row["lod_triangles"],
                "size_m": row["size"],
                **(
                    {"reduced_from": row["built_triangles"]}
                    if row["built_triangles"] != row["triangles"]
                    else {}
                ),
            }
        )
    return {
        **recorded,
        "kit": made.kit.id,
        "name": made.name,
        "budget": made.budget,
        "pivot": made.pivot,
        "materials": made.looks,
        "models": models,
        "seconds": round(result.seconds, 1),
    }


def _shown(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:g}"
    return str(value).lower() if isinstance(value, bool) else str(value)


def _reason(output: str) -> str:
    lines = [line for line in output.splitlines() if "Error" in line]
    return (lines[-1] if lines else output[-600:]).strip()


def _measure(project: Path, made: Plan) -> list[str]:
    """Each GLB as Godot will read it: inside the budget, its pivot where asked."""
    problems = []
    for model in made.models:
        for path in (model["glb"], *model["lods"]):
            measured = measure_glb((project / path).read_bytes())
            if made.budget is not None and measured.triangles > made.budget:
                problems.append(
                    f"{path}: {measured.triangles} triangles, over the art bible's "
                    f"{made.budget}"
                )
            if made.pivot == "bottom-centre" and measured.bbox_min is not None:
                low, high = measured.bbox_min, measured.bbox_max or measured.bbox_min
                off = max(
                    abs(low[1]),
                    abs((low[0] + high[0]) / 2),
                    abs((low[2] + high[2]) / 2),
                )
                if off > 0.02:
                    problems.append(f"{path}: pivot is {off:.3f} m off bottom-centre")
    return problems


# --- finding kit-made models ---------------------------------------------------------


def kit_records(
    project: Path, recipes: Mapping[str, tuple[Path, Recipe]] | None = None
) -> dict[str, dict[str, Any]]:
    """Output path -> how a kit made it, for every kit recipe in the project.

    ``recipes`` (recipe path -> (project root, recipe)) adds recipes kept elsewhere,
    such as a change waiting for review.
    """
    found: dict[str, dict[str, Any]] = {}
    known = every(project)
    listed: dict[str, tuple[Path, Recipe]] = {}
    for path in recipe_paths(project):
        try:
            listed[path.relative_to(project).as_posix()] = (project, load_recipe(path))
        except (OSError, ValueError):
            continue
    listed.update(recipes or {})
    for relative, (root, recipe) in listed.items():
        if recipe.tool != "blender":
            continue
        try:
            with (root / recipe.script).open(encoding="utf-8") as handle:
                head = "".join(handle.readline() for _ in range(3))
        except OSError:
            continue
        marker = read_marker(head)
        if marker is None:
            continue
        kit = known.get(str(marker.get("kit")))
        for model in marker.get("models", []):
            info = {
                "kit": marker.get("kit"),
                "label": kit.label if kit else str(marker.get("kit")),
                "name": marker.get("name"),
                "params": model.get("params", {}),
                "base": marker.get("params", {}),
                "seed": marker.get("seed"),
                "count": marker.get("count", 1),
                "recipe": relative,
            }
            for path in (model.get("glb"), model.get("blend")):
                if path:
                    found[str(path)] = info
    return found


def find(project: Path, asset: str) -> tuple[str, dict[str, Any]]:
    """The recipe and marker of the set an asset belongs to."""
    relative = asset.removeprefix("res://")
    for path in recipe_paths(project):
        try:
            recipe = load_recipe(path)
        except (OSError, ValueError):
            continue
        if relative not in recipe.outputs:
            continue
        marker = read_marker((project / recipe.script).read_text(encoding="utf-8"))
        if marker is None:
            break
        return path.relative_to(project).as_posix(), marker
    raise ValueError(
        f"{relative} was not made with a kit. Change it in Blender with the other "
        "Blender operations."
    )


def edit_plan(
    project: Path,
    asset: str,
    params: Mapping[str, Any] | None = None,
    *,
    seed: int | None = None,
    colours: Mapping[str, str] | None = None,
) -> tuple[Plan, dict[str, Any]]:
    """The set an asset belongs to, planned again with new values; and its old marker."""
    _recipe, marker = find(project, asset)
    merged = {**marker.get("params", {}), **(params or {})}
    vary = marker.get("vary")
    return (
        plan(
            project,
            str(marker["kit"]),
            merged,
            seed=int(marker["seed"]) if seed is None else seed,
            name=str(marker["name"]),
            count=int(marker.get("count", 1)),
            vary=vary if isinstance(vary, (int, float, dict)) else None,
            folder=str(marker["folder"]),
            lods=int(marker.get("lods", 0)),
            colours={**marker.get("colours", {}), **(colours or {})},
        ),
        marker,
    )


def describe(project: Path | None = None) -> dict[str, Any]:
    """The kit catalogue, with each kit's materials and budget under this project's bible."""
    rules = load_rules(project) if project is not None else BibleRules()
    rows = []
    for kit in every(project).values():
        folder = kit.folder
        shared = (
            {"from": kit.credit, "presets": list(kit.presets)} if kit.credit else {}
        )
        rows.append(
            {
                "id": kit.id,
                "label": kit.label,
                "summary": kit.summary,
                "folder": folder,
                "params": [p.as_json() for p in kit.params],
                "materials": looks(kit, rules, object_name(kit.id)),
                "budget": _folder_value(rules.models.max_triangles, f"{folder}/x.glb"),
                "pivot": _folder_value(rules.models.pivot, f"{folder}/x.glb")
                or "bottom-centre",
                **shared,
            }
        )
    return {
        "kits": rows,
        "batch": {"max_count": MAX_COUNT, "default_vary": DEFAULT_VARY},
        "lods": {"max": MAX_LODS},
        "note": (
            "Kits are hand-written procedural generators run in Blender: the seed "
            "and parameters make the shape. kit_make makes one model, kit_batch a "
            "set that varies, kit_edit changes a made set, scatter places models "
            "in a scene."
        ),
    }


def run(coroutine: Any) -> Any:
    """Run a kit coroutine from synchronous code (the CLI)."""
    return asyncio.run(coroutine)
