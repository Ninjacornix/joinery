"""Blueprints (playable starting points) and features (add-ons) for game projects.

A blueprint is a small, playable, code-only greybox game: scenes and typed
GDScript under `game/`, plus the project settings and input actions it needs.
Every blueprint sits on the shared game core (`blueprints/_core`): menus,
settings, pause, sessions and a test harness under `core/`. Features (online
play, controller, saving…) add their own folder under `features/<id>/` and
merge into `project.godot` through the same tables.

Applying is a plain file and text edit that the user reviews like any other
change: nothing here talks to a model, fetches anything, or makes an asset.
Everything applied is recorded in `.joinery/game-manifest.json`, so applying
again changes no byte, a file the user edited is never overwritten, and a
feature can be removed cleanly. `joinery.toml [game]` records the choice.
"""

from __future__ import annotations

import hashlib
import importlib
import itertools
import json
import re
import secrets
import tomllib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
)

from joinery.catalog import ROOT
from joinery.godot_config import (
    KEYS,
    GodotConfig,
    action_deadzone,
    action_events,
    action_value,
    event_identity,
    key_event,
    mouse_event,
    packed_strings,
    pad_axis_event,
    pad_button_event,
    to_variant,
)

BLUEPRINTS_DIR = ROOT / "blueprints"
FEATURES_DIR = ROOT / "features"
PRESETS_DIR = ROOT / "templates" / "bible_presets"
CORE_DIR_NAME = "_core"
MANIFEST = ".joinery/game-manifest.json"
MANIFEST_FORMAT = 1

#: Asset files never ship in blueprints or features: visuals are code-only
#: greybox and blueprints are silent. Real assets come from the user's tools.
BANNED_SUFFIXES = frozenset(
    {
        ".png", ".jpg", ".jpeg", ".webp", ".svg", ".bmp", ".tga", ".exr", ".hdr",
        ".glb", ".gltf", ".blend", ".fbx", ".obj", ".dae",
        ".wav", ".ogg", ".mp3", ".flac",
        ".ttf", ".otf", ".woff", ".woff2",
        ".gif", ".tif", ".tiff", ".psd", ".kra", ".xcf", ".ase", ".aseprite",
        ".dds", ".ktx", ".ktx2", ".stl", ".ply", ".3ds", ".usd", ".usdz", ".vox",
        ".aif", ".aiff", ".opus", ".mid", ".midi",
        ".mp4", ".webm", ".ogv", ".mov", ".avi", ".ttc", ".fnt",
        ".scn", ".res",
    }
)
#: Third-party code never ships in blueprints or features either: an
#: extension such as GodotSteam is a separate step the user approves.
BINARY_SUFFIXES = frozenset(
    {
        ".gdextension", ".dll", ".so", ".dylib", ".framework", ".exe", ".wasm",
        ".pck", ".zip", ".jar", ".a", ".lib",
    }
)
#: What an overlay may hold: code and text Godot reads. Anything else is
#: refused by validation and never copied into a game.
SHIPPABLE_SUFFIXES = frozenset(
    {
        ".gd", ".uid", ".tscn", ".tres", ".gdshader", ".gdshaderinc",
        ".cfg", ".po", ".pot", ".csv", ".json", ".txt", ".md",
    }
)
#: Resource types that carry embedded pictures, sound, fonts or meshes, so a
#: .tscn/.tres can smuggle an asset in as text.
_EMBEDDED_MEDIA = re.compile(
    r'type="(Image|ImageTexture|ImageTexture3D|PortableCompressedTexture2D|'
    r"CompressedTexture2D|AudioStreamWAV|AudioStreamOggVorbis|AudioStreamMP3|"
    r'FontFile|ArrayMesh|BitMap)"'
)
#: Written by the engine itself, never by a unit.
ENGINE_KEYS = frozenset(
    {
        "application/config/name",
        "application/run/main_scene",
        "application/config/features",
        "game/blueprint",
        "game/hooks/level_scene",
        "game/hooks/player_scene",
    }
)
CORE_ROOTS = ("core/", "tests/support/", "default_bus_layout.tres")
BLUEPRINT_ROOTS = ("game/", "tests/game/")
EXTRA_ROOTS = frozenset({"locale"})
#: The standard action vocabulary (contract C3), plus the core's `pause`.
ACTIONS = frozenset(
    {
        "move_left", "move_right", "move_up", "move_down", "move_forward",
        "move_back", "look_left", "look_right", "look_up", "look_down", "jump",
        "interact", "attack", "dash", "sprint", "crouch", "restart", "pause",
    }
)
GODOT_VERSION = "4.7"
DEFAULT_DEADZONE = 0.2
PAD_BUTTONS = {
    0: "A", 1: "B", 2: "X", 3: "Y", 4: "Back", 5: "Guide", 6: "Start",
    7: "Left stick click", 8: "Right stick click", 9: "LB", 10: "RB",
    11: "D-pad up", 12: "D-pad down", 13: "D-pad left", 14: "D-pad right",
}
PAD_AXES = {
    "0-": "Left stick left", "0+": "Left stick right",
    "1-": "Left stick up", "1+": "Left stick down",
    "2-": "Right stick left", "2+": "Right stick right",
    "3-": "Right stick up", "3+": "Right stick down",
    "4+": "Left trigger", "5+": "Right trigger",
}
_SKIP = frozenset({".DS_Store", "__pycache__", ".godot", "Thumbs.db"})
_ID = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")
_AUTOLOAD = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
#: Uid texts a shipped sidecar may hold. `9` is allowed because some do and
#: Godot still parses it to a stable id; new_uid() writes only Godot's digits.
_UID = re.compile(r"^uid://[a-y0-9]+$")
_HEADER_UID = re.compile(r'\buid="(uid://[^"]+)"')
#: Godot's own uid digits (core/io/resource_uid.cpp): base 34, so `z` and `9`
#: never appear. A text in another base still parses, but not to the number
#: Godot would write back for it.
_UID_ALPHABET = "abcdefghijklmnopqrstuvwxy012345678"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RawVariant(_Strict):
    """Any Godot value, written as Godot's own variant text."""

    raw: str


SettingValue = StrictBool | StrictInt | StrictFloat | StrictStr | list[StrictStr] | RawVariant


class InputSpec(_Strict):
    keys: list[str] = []
    mouse: list[Literal["left", "right", "middle", "wheel_up", "wheel_down"]] = []
    pad_buttons: list[int] = []
    pad_axes: list[str] = []
    deadzone: float | None = Field(default=None, ge=0, le=1)
    #: Create the action when missing. Default: yes for core and blueprints,
    #: no for features (they only add events to actions that exist).
    create: bool | None = None

    @field_validator("keys")
    @classmethod
    def _known_keys(cls, value: list[str]) -> list[str]:
        unknown = [k for k in value if k not in KEYS]
        if unknown:
            raise ValueError("unknown key names: " + ", ".join(unknown))
        return value

    @field_validator("pad_axes")
    @classmethod
    def _axes(cls, value: list[str]) -> list[str]:
        bad = [a for a in value if not re.fullmatch(r"[0-5][+-]", a)]
        if bad:
            raise ValueError("pad axes are like '0-' or '5+': " + ", ".join(bad))
        return value

    @field_validator("pad_buttons")
    @classmethod
    def _buttons(cls, value: list[int]) -> list[int]:
        if any(not 0 <= b <= 127 for b in value):
            raise ValueError("pad buttons are 0-127")
        return value

    def events(self) -> list[str]:
        return [
            *(key_event(k) for k in self.keys),
            *(mouse_event(m) for m in self.mouse),
            *(pad_button_event(b) for b in self.pad_buttons),
            *(pad_axis_event(a) for a in self.pad_axes),
        ]


class ProjectMerge(_Strict):
    """What a unit merges into project.godot (contract C4)."""

    settings: dict[str, SettingValue] = {}
    autoload: dict[str, str] = {}
    input: dict[str, InputSpec] = {}
    append: dict[str, list[str]] = {}

    @field_validator("settings", "append")
    @classmethod
    def _setting_keys(cls, value: dict[str, Any]) -> dict[str, Any]:
        bad = [k for k in value if "/" not in k.strip("/") or k != k.strip()]
        if bad:
            raise ValueError('settings are "section/key": ' + ", ".join(bad))
        return value

    @field_validator("autoload")
    @classmethod
    def _autoloads(cls, value: dict[str, str]) -> dict[str, str]:
        for name, path in value.items():
            if not _AUTOLOAD.match(name):
                raise ValueError(f"autoload name {name!r} is not an identifier")
            if not path.startswith("res://") or path.startswith("res://*"):
                raise ValueError(f"autoload {name} must point at res://…")
        return value


class Hooks(_Strict):
    level_scene: str = ""
    player_scene: str = ""
    spawnable_scenes: list[str] = []


class BriefDefaults(_Strict):
    genre: list[str] = []
    camera: str = ""
    core_loop: str = ""
    progression: str = ""
    verbs: list[tuple[str, str, str]] = []


class Blueprint(_Strict):
    id: str
    name: str
    kind: Literal["blueprint", "core"] = Field(default="blueprint", exclude=True)
    pitch: str = ""
    version: int = Field(ge=1)
    dimension: Literal["2d", "3d", "ui"] = "2d"
    icon: str = "Node"
    default_features: list[str] = []
    bible_preset: str = "none"
    suggested_tools: list[str] = ["godot"]
    main_scene: str = "res://core/main.tscn"
    hooks: Hooks = Hooks()
    brief: BriefDefaults = BriefDefaults()
    merge: ProjectMerge = Field(default=ProjectMerge(), exclude=True)
    dir: Path = Field(exclude=True)


class External(_Strict):
    """Something a feature can use but Joinery never installs."""

    name: str
    source: str
    version: str
    install: str
    required: bool = False


class Feature(_Strict):
    id: str
    name: str
    summary: str
    version: int = Field(ge=1)
    requires: list[str] = []
    conflicts: list[str] = []
    enhances: list[str] = []
    blueprints: list[str] = ["*"]
    extra_roots: list[str] = []
    external: list[External] = []
    merge: ProjectMerge = Field(default=ProjectMerge(), exclude=True)
    adapters: dict[str, ProjectMerge] = Field(default={}, exclude=True)
    dir: Path = Field(exclude=True)

    def supports(self, blueprint_id: str) -> bool:
        return "*" in self.blueprints or blueprint_id in self.blueprints


class GameRecord(_Strict):
    blueprint: str
    blueprint_version: int
    features: list[str] = []
    feature_versions: dict[str, int] = {}


# --- Results ---------------------------------------------------------------------


@dataclass(frozen=True)
class Resolution:
    blueprint: Blueprint | None
    features: list[Feature]
    added: dict[str, str]
    problems: list[str]
    core: Blueprint | None = None


@dataclass(frozen=True)
class FileWrite:
    path: str
    data: bytes
    unit: str
    action: Literal["create", "update", "same"]


@dataclass(frozen=True)
class ConfigEdit:
    file: str
    section: str
    key: str
    before: str | None
    after: str | None


@dataclass(frozen=True)
class Plan:
    resolution: Resolution
    writes: list[FileWrite]
    deletes: list[str]
    config_edits: list[ConfigEdit]
    preset_edits: list[str]
    manifest: dict[str, Any]
    conflicts: list[str]
    notes: list[str] = field(default_factory=list)
    project_text: str | None = None
    presets_text: str | None = None
    record_text: str | None = None

    @property
    def changes(self) -> list[str]:
        """Every project path this plan would write or delete."""
        paths = {w.path for w in self.writes if w.action != "same"} | set(self.deletes)
        if self.project_text is not None:
            paths.add("project.godot")
        if self.presets_text is not None:
            paths.add("export_presets.cfg")
        if self.record_text is not None:
            paths.add("joinery.toml")
        return sorted(paths)


@dataclass(frozen=True)
class Applied:
    blueprint: str
    features: list[str]
    added: dict[str, str]
    written: list[str]
    deleted: list[str]
    config_edits: list[ConfigEdit]
    preset_edits: list[str]
    notes: list[str]
    manifest: dict[str, Any]


@dataclass(frozen=True)
class Removed:
    feature: str
    deleted: list[str]
    kept: list[str]
    config_edits: list[ConfigEdit]
    preset_edits: list[str]
    notes: list[str]
    manifest: dict[str, Any]


# --- Loading ---------------------------------------------------------------------


def new_uid() -> str:
    """A fresh Godot resource uid text, for `.uid` sidecars and scene headers."""
    number = secrets.randbits(63) or 1
    chars = ""
    while number:
        number, rest = divmod(number, len(_UID_ALPHABET))
        chars = _UID_ALPHABET[rest] + chars
    return "uid://" + chars


def _merge_from(data: dict[str, Any], where: str) -> ProjectMerge:
    project = data.pop("project", {})
    if not isinstance(project, dict) or set(project) - {"settings"}:
        raise ValueError(f"{where}: [project] only holds [project.settings]")
    return ProjectMerge(
        settings=project.get("settings", {}),
        autoload=data.pop("autoload", {}),
        input=data.pop("input", {}),
        append=data.pop("append", {}),
    )


def _describe_error(path: Path, exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        details = "; ".join(
            f"{'.'.join(str(p) for p in error['loc']) or 'file'}: {error['msg']}"
            for error in exc.errors()
        )
        return f"{path}: {details}"
    return f"{path}: {exc}"


def load_blueprint(directory: Path) -> Blueprint:
    core = directory.name == CORE_DIR_NAME
    path = directory / ("unit.toml" if core else "blueprint.toml")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        merge = _merge_from(data, str(path))
        unit = Blueprint(**data, merge=merge, dir=directory)
    except (OSError, tomllib.TOMLDecodeError, ValidationError, ValueError, TypeError) as exc:
        raise ValueError(_describe_error(path, exc)) from None
    if core and unit.kind != "core":
        raise ValueError(f'{path}: the shared core needs kind = "core"')
    if not core and unit.kind != "blueprint":
        raise ValueError(f"{path}: only {CORE_DIR_NAME} may be the core")
    return unit


def load_feature(directory: Path) -> Feature:
    path = directory / "feature.toml"
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        merge = _merge_from(data, str(path))
        raw_adapters = data.pop("adapters", {})
        if not isinstance(raw_adapters, dict):
            raise TypeError("[adapters] is a table of blueprint ids")
        adapters = {
            bp: _merge_from(dict(table), f"{path} [adapters.{bp}]")
            for bp, table in raw_adapters.items()
        }
        unit = Feature(**data, merge=merge, adapters=adapters, dir=directory)
    except (OSError, tomllib.TOMLDecodeError, ValidationError, ValueError, TypeError) as exc:
        raise ValueError(_describe_error(path, exc)) from None
    return unit


@dataclass
class _Catalogue:
    core: Blueprint | None
    blueprints: dict[str, Blueprint]
    features: dict[str, Feature]


def _unit_dirs(base: Path) -> list[Path]:
    if not base.is_dir():
        return []
    return sorted(p for p in base.iterdir() if p.is_dir() and p.name not in _SKIP)


def _load(errors: list[str] | None = None) -> _Catalogue:
    """Load the catalogue. With `errors`, bad units are reported and skipped."""
    catalogue = _Catalogue(None, {}, {})

    def attempt(load: Any, directory: Path) -> Any:
        try:
            return load(directory)
        except ValueError as exc:
            if errors is None:
                raise
            errors.append(str(exc))
            return None

    for directory in _unit_dirs(BLUEPRINTS_DIR):
        marker = "unit.toml" if directory.name == CORE_DIR_NAME else "blueprint.toml"
        if not (directory / marker).is_file():
            if errors is not None:
                errors.append(f"{directory}: missing {marker}")
            continue
        unit = attempt(load_blueprint, directory)
        if unit is None:
            continue
        if directory.name == CORE_DIR_NAME:
            catalogue.core = unit
        else:
            catalogue.blueprints[unit.id] = unit
    for directory in _unit_dirs(FEATURES_DIR):
        if not (directory / "feature.toml").is_file():
            if errors is not None:
                errors.append(f"{directory}: missing feature.toml")
            continue
        feature = attempt(load_feature, directory)
        if feature is not None:
            catalogue.features[feature.id] = feature
    return catalogue


def list_blueprints() -> list[Blueprint]:
    return sorted(_load().blueprints.values(), key=lambda b: (b.id != "blank", b.id))


def list_features() -> list[Feature]:
    return sorted(_load().features.values(), key=lambda f: f.id)


# --- Description -----------------------------------------------------------------


def _pad_labels(spec: InputSpec) -> list[str]:
    return [PAD_BUTTONS.get(b, f"Button {b}") for b in spec.pad_buttons] + [
        PAD_AXES.get(a, f"Axis {a}") for a in spec.pad_axes
    ]


def _describe(catalogue: _Catalogue, blueprint: Blueprint) -> dict[str, Any]:
    data = blueprint.model_dump(mode="json")
    actions: dict[str, dict[str, list[str]]] = {}
    for unit in (catalogue.core, blueprint):
        if unit is None:
            continue
        for action, spec in unit.merge.input.items():
            entry = actions.setdefault(action, {"keys": [], "mouse": [], "pad": []})
            entry["keys"] += [k for k in spec.keys if k not in entry["keys"]]
            entry["mouse"] += [m for m in spec.mouse if m not in entry["mouse"]]
    controller = catalogue.features.get("controller")
    if controller is not None and controller.supports(blueprint.id):
        merges = [controller.merge]
        if blueprint.id in controller.adapters:
            merges.append(controller.adapters[blueprint.id])
        for merge in merges:
            for action, spec in merge.input.items():
                if action in actions:
                    pad = actions[action]["pad"]
                    pad += [p for p in _pad_labels(spec) if p not in pad]
    data["input"] = actions
    data["features_supported"] = sorted(
        f.id for f in catalogue.features.values() if f.supports(blueprint.id)
    )
    return data


def describe(blueprint_id: str) -> dict[str, Any]:
    """A blueprint as JSON-safe data: its fields, input actions and features."""
    catalogue = _load()
    if blueprint_id not in catalogue.blueprints:
        raise ValueError(f"Unknown blueprint: {blueprint_id}")
    return _describe(catalogue, catalogue.blueprints[blueprint_id])


def _feature_json(feature: Feature) -> dict[str, Any]:
    return {
        "id": feature.id,
        "name": feature.name,
        "summary": feature.summary,
        "version": feature.version,
        "requires": list(feature.requires),
        "conflicts": list(feature.conflicts),
        "enhances": list(feature.enhances),
        "blueprints": list(feature.blueprints),
        "external": [e.model_dump(mode="json") for e in feature.external],
    }


def _presets() -> list[dict[str, str]]:
    try:
        module = importlib.import_module("joinery.bible_presets")
    except ImportError:
        module = None
    if module is not None:
        listed: list[dict[str, str]] = module.list_presets()
        return listed
    found = [{"id": "none", "name": "None"}]
    for path in sorted(PRESETS_DIR.glob("*.toml")) if PRESETS_DIR.is_dir() else []:
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError:
            continue
        if path.stem != "none":
            found.append({"id": path.stem, "name": str(data.get("name", path.stem))})
    return found


def catalogue_json() -> dict[str, Any]:
    catalogue = _load()
    blueprints = sorted(catalogue.blueprints.values(), key=lambda b: (b.id != "blank", b.id))
    return {
        "blueprints": [_describe(catalogue, b) for b in blueprints],
        "features": [_feature_json(f) for f in sorted(catalogue.features.values(), key=lambda f: f.id)],
        "presets": _presets(),
    }


# --- Resolution ------------------------------------------------------------------


def _resolve(catalogue: _Catalogue, blueprint_id: str, features: Sequence[str]) -> Resolution:
    problems: list[str] = []
    blueprint = catalogue.blueprints.get(blueprint_id)
    if blueprint is None:
        problems.append(f"Unknown blueprint: {blueprint_id}")
    if catalogue.core is None:
        problems.append(f"The shared game core is missing ({BLUEPRINTS_DIR / CORE_DIR_NAME}).")
    requested = list(dict.fromkeys(features))
    chosen: set[str] = set()
    for feature_id in requested:
        if feature_id in catalogue.features:
            chosen.add(feature_id)
        else:
            problems.append(f"Unknown feature: {feature_id}")
    added: dict[str, str] = {}
    queue = sorted(chosen)
    while queue:
        current = queue.pop(0)
        for needed in catalogue.features[current].requires:
            if needed not in catalogue.features:
                problems.append(f"{current} requires an unknown feature: {needed}")
            elif needed not in chosen:
                chosen.add(needed)
                added[needed] = f"required by {catalogue.features[current].name}"
                queue.append(needed)
    reported: set[frozenset[str]] = set()
    for feature_id in sorted(chosen):
        feature = catalogue.features[feature_id]
        for other in feature.conflicts:
            pair = frozenset({feature_id, other})
            if other in chosen and pair not in reported:
                reported.add(pair)
                problems.append(
                    f"{feature.name} conflicts with {catalogue.features[other].name}; choose one."
                )
        for other_id in sorted(chosen):
            other_feature = catalogue.features[other_id]
            pair = frozenset({feature_id, other_id})
            if feature_id in other_feature.conflicts and pair not in reported:
                reported.add(pair)
                problems.append(f"{other_feature.name} conflicts with {feature.name}; choose one.")
        if blueprint is not None and not feature.supports(blueprint.id):
            problems.append(f"{feature.name} does not work with the {blueprint.name} blueprint.")
    ordered: list[Feature] = []
    remaining = set(chosen)
    while remaining:
        ready = sorted(
            f for f in remaining if not set(catalogue.features[f].requires) & remaining
        )
        if not ready:
            problems.append("Features require each other in a cycle: " + ", ".join(sorted(remaining)))
            ordered += [catalogue.features[f] for f in sorted(remaining)]
            break
        for feature_id in ready:
            ordered.append(catalogue.features[feature_id])
            remaining.discard(feature_id)
    return Resolution(blueprint, ordered, added, problems, catalogue.core)


def resolve(blueprint_id: str, features: Sequence[str]) -> Resolution:
    """Close `requires`, order features, and report conflicts or refusals."""
    return _resolve(_load(), blueprint_id, features)


# --- Units -----------------------------------------------------------------------


@dataclass(frozen=True)
class _Unit:
    id: str
    kind: Literal["core", "blueprint", "feature"]
    version: int
    files: dict[str, Path]
    merges: tuple[ProjectMerge, ...]
    presets: tuple[tuple[str, Path], ...]
    roots: tuple[str, ...]

    @property
    def create_default(self) -> bool:
        return self.kind != "feature"


def _overlay(directory: Path) -> dict[str, Path]:
    if not directory.is_dir():
        return {}
    found: dict[str, Path] = {}
    for path in sorted(directory.rglob("*")):
        if (
            path.is_file()
            and not _SKIP.intersection(path.relative_to(directory).parts)
            and path.suffix.lower() in SHIPPABLE_SUFFIXES
        ):
            found[path.relative_to(directory).as_posix()] = path
    return found


def _preset_name(path: Path) -> str:
    config = GodotConfig.parse(path.read_text(encoding="utf-8"))
    for section in config.sections():
        if re.fullmatch(r"preset\.\d+", section):
            raw = config.get(section, "name")
            if raw is not None:
                return raw.strip().strip('"')
    raise ValueError(f"{path}: export preset fragment has no [preset.N] name")


def _feature_roots(feature: Feature) -> tuple[str, ...]:
    return (
        f"features/{feature.id}/",
        f"tests/features/{feature.id}/",
        *(f"{root.strip('/')}/" for root in feature.extra_roots),
    )


def _units(resolution: Resolution) -> list[_Unit]:
    units: list[_Unit] = []
    core, blueprint = resolution.core, resolution.blueprint
    if core is not None:
        units.append(
            _Unit(core.id, "core", core.version, _overlay(core.dir / "overlay"),
                  (core.merge,), (), CORE_ROOTS)
        )
    if blueprint is not None:
        units.append(
            _Unit(blueprint.id, "blueprint", blueprint.version,
                  _overlay(blueprint.dir / "overlay"), (blueprint.merge,), (), BLUEPRINT_ROOTS)
        )
    for feature in resolution.features:
        files = _overlay(feature.dir / "overlay")
        merges = [feature.merge]
        if blueprint is not None:
            files.update(_overlay(feature.dir / "adapters" / blueprint.id / "overlay"))
            if blueprint.id in feature.adapters:
                merges.append(feature.adapters[blueprint.id])
        presets = tuple(
            (_preset_name(path), path)
            for path in sorted((feature.dir / "export_presets").glob("*.cfg"))
        )
        units.append(
            _Unit(feature.id, "feature", feature.version, files, tuple(merges), presets,
                  _feature_roots(feature))
        )
    return units


def _setting_raw(value: SettingValue) -> str:
    if isinstance(value, RawVariant):
        return value.raw
    return to_variant(value)


def _unit_settings(unit: _Unit) -> dict[str, str]:
    merged: dict[str, str] = {}
    for merge in unit.merges:  # an adapter refines its own feature
        merged.update({k: _setting_raw(v) for k, v in merge.settings.items()})
    return merged


def _compose(units: Sequence[_Unit]) -> list[str]:
    """Static clashes between units, without looking at any project."""
    problems: list[str] = []
    owners: dict[str, str] = {}
    settings: dict[str, tuple[str, str]] = {}
    autoloads: dict[str, str] = {}
    appended: dict[str, str] = {}
    presets: dict[str, str] = {}
    for unit in units:
        for path in unit.files:
            if path in owners:
                problems.append(f"{owners[path]} and {unit.id} both write {path}")
            else:
                owners[path] = unit.id
        for key, raw in _unit_settings(unit).items():
            if key in ENGINE_KEYS:
                problems.append(f"{unit.id} sets {key}, which the engine writes")
            elif key in settings and settings[key][1] != raw:
                problems.append(
                    f"{settings[key][0]} and {unit.id} set {key} differently "
                    f"({settings[key][1]} vs {raw})"
                )
            else:
                settings.setdefault(key, (unit.id, raw))
        for merge in unit.merges:
            for name in merge.autoload:
                if name in autoloads and autoloads[name] != unit.id:
                    problems.append(f"{autoloads[name]} and {unit.id} both add autoload {name}")
                autoloads.setdefault(name, unit.id)
            for key in merge.append:
                appended.setdefault(key, unit.id)
        for name, _ in unit.presets:
            if name in presets:
                problems.append(f"{presets[name]} and {unit.id} both add export preset {name!r}")
            presets.setdefault(name, unit.id)
    for key, owner in appended.items():
        if key in settings:
            problems.append(f"{owner} appends to {key}, which {settings[key][0]} sets outright")
    return sorted(set(problems))


# --- Project state ---------------------------------------------------------------


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_manifest(target: Path, project: Path) -> dict[str, Any]:
    # A transaction stage skips `.joinery/`, so fall back to the live project.
    for base in (target, project):
        path = base / MANIFEST
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"{path} is damaged: {exc}") from None
            if not isinstance(data, dict) or data.get("format", 0) > MANIFEST_FORMAT:
                raise ValueError(f"{path} was written by a newer Joinery")
            return data
    return {}


def _manifest_text(manifest: dict[str, Any]) -> str:
    return json.dumps(manifest, indent=2, sort_keys=True) + "\n"


_GAME_HEADER = re.compile(r"^[ \t]*\[game\][ \t]*(?:#.*)?$")
_TABLE_HEADER = re.compile(r"^[ \t]*\[")


def _game_span(text: str) -> tuple[int, int] | None:
    """Character span of the `[game]` table: its header to the next table.

    Comments inside the table go with it; blank and comment lines just before
    the next table header stay, since they introduce that table.
    """
    lines = text.splitlines(keepends=True)
    start: int | None = None
    end = len(lines)
    for index, line in enumerate(lines):
        if start is None:
            if _GAME_HEADER.match(line.rstrip("\r\n")):
                start = index
        elif _TABLE_HEADER.match(line):
            end = index
            break
    if start is None:
        return None
    if end < len(lines):
        while end > start + 1 and (
            not lines[end - 1].strip() or lines[end - 1].lstrip().startswith("#")
        ):
            end -= 1
    offsets = list(itertools.accumulate((len(line) for line in lines), initial=0))
    return offsets[start], offsets[end]


def _record_block(record: GameRecord) -> str:
    versions = ", ".join(f"{k} = {v}" for k, v in record.feature_versions.items())
    features = ", ".join(json.dumps(f) for f in record.features)
    return (
        "[game]\n"
        f"blueprint = {json.dumps(record.blueprint)}\n"
        f"blueprint_version = {record.blueprint_version}\n"
        f"features = [{features}]\n"
        f"feature_versions = {{ {versions} }}\n".replace("{  }", "{}")
    )


def record_text(text: str, record: GameRecord) -> str:
    """`joinery.toml` with its `[game]` table replaced; every other table kept."""
    block = _record_block(record)
    span = _game_span(text)
    if span is not None:
        before, after = text[: span[0]], text[span[1] :]
        if after.strip():
            return before + block + "\n" + after.lstrip("\n")
        return before + block
    if not text.strip():
        return block
    return text.rstrip("\n") + "\n\n" + block


def read_record(target: Path) -> GameRecord | None:
    path = target / "joinery.toml"
    if not path.is_file():
        return None
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"{path}: {exc}") from None
    game = data.get("game")
    if game is None:
        return None
    try:
        return GameRecord.model_validate(game)
    except ValidationError as exc:
        raise ValueError(_describe_error(path, exc)) from None


def _split_key(key: str) -> tuple[str, str]:
    section, _, rest = key.partition("/")
    return section, rest


def _config_edits(file: str, before: GodotConfig, after: GodotConfig) -> list[ConfigEdit]:
    edits: list[ConfigEdit] = []
    sections = list(dict.fromkeys([*before.sections(), *after.sections()]))
    for section in sections:
        keys = list(dict.fromkeys([*before.keys(section), *after.keys(section)]))
        for key in keys:
            old, new = before.get(section, key), after.get(section, key)
            if old != new:
                edits.append(ConfigEdit(file, section, key, old, new))
    return edits


@dataclass
class _Work:
    """Mutable state while composing units onto one project."""

    target: Path
    config: GodotConfig
    presets: GodotConfig
    old_units: dict[str, Any]
    conflicts: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _apply_setting(
    work: _Work, entry: dict[str, Any], old: dict[str, Any], key: str, raw: str
) -> None:
    section, name = _split_key(key)
    current = work.config.get(section, name)
    recorded = old.get("settings", {}).get(key)
    if recorded is not None:
        if current == recorded["value"]:
            if current != raw:
                work.config.set(section, name, raw)
            entry["settings"][key] = {"value": raw, "previous": recorded["previous"]}
        else:
            work.notes.append(f"Kept your value for {key} (it was changed after it was added).")
            entry["settings"][key] = recorded
        return
    entry["settings"][key] = {"value": raw, "previous": current}
    if current != raw:
        work.config.set(section, name, raw)


def _apply_append(
    work: _Work, entry: dict[str, Any], old: dict[str, Any], key: str, values: Sequence[str]
) -> None:
    section, name = _split_key(key)
    current = work.config.get(section, name)
    try:
        existing = packed_strings(current) if current is not None else []
    except ValueError:
        work.conflicts.append(f"{key} is not a list of strings, so {values} cannot be added")
        return
    recorded = old.get("append", {}).get(key)
    previous = set(recorded["values"]) if recorded else set()
    new = [v for v in dict.fromkeys(values) if v not in existing]
    ours = [v for v in dict.fromkeys(values) if v in existing and v in previous] + new
    if new:
        work.config.set(section, name, to_variant(existing + new))
    if ours:
        created = bool(recorded["created"]) if recorded else current is None
        entry["append"][key] = {"values": ours, "created": created}


def _apply_input(
    work: _Work, unit: _Unit, entry: dict[str, Any], old: dict[str, Any], action: str, spec: InputSpec
) -> None:
    events = spec.events()
    current = work.config.get("input", action)
    recorded = old.get("input_events", {}).get(action, [])
    if current is None:
        create = spec.create if spec.create is not None else unit.create_default
        if action in old.get("input_created", []) or recorded:
            work.notes.append(f"Left out the {action} action: it was removed from the project.")
            return
        if not create:
            return
        deadzone = spec.deadzone if spec.deadzone is not None else DEFAULT_DEADZONE
        work.config.set("input", action, action_value(deadzone, events))
        entry["input_events"].setdefault(action, []).extend(events)
        if action not in entry["input_created"]:
            entry["input_created"].append(action)
        return
    existing = action_events(current)
    seen = {event_identity(e) for e in existing}
    recorded_ids = {event_identity(e) for e in recorded}
    added: list[str] = []
    ours: list[str] = []
    for event in events:
        identity = event_identity(event)
        if identity not in seen and identity in recorded_ids:
            continue  # the user unbound it; keep it that way
        if identity not in seen:
            added.append(event)
            ours.append(event)
            seen.add(identity)
        elif identity in recorded_ids:
            ours.append(event)
    deadzone = action_deadzone(current)
    if spec.deadzone is not None:
        old_dz = old.get("input_deadzones", {}).get(action)
        if old_dz is not None:
            if deadzone == old_dz["value"]:
                if deadzone != spec.deadzone:
                    deadzone = spec.deadzone
                entry["input_deadzones"][action] = {"value": spec.deadzone, "previous": old_dz["previous"]}
            else:
                entry["input_deadzones"][action] = old_dz
        elif deadzone != spec.deadzone:
            entry["input_deadzones"][action] = {"value": spec.deadzone, "previous": deadzone}
            deadzone = spec.deadzone
    if added or deadzone != action_deadzone(current):
        work.config.set("input", action, action_value(deadzone, existing + added))
    if ours:
        listed = entry["input_events"].setdefault(action, [])
        listed += [e for e in ours if e not in listed]
    if action in old.get("input_created", []) and action not in entry["input_created"]:
        entry["input_created"].append(action)


def _apply_autoload(
    work: _Work, unit: _Unit, entry: dict[str, Any], old: dict[str, Any], name: str, path: str
) -> None:
    value = to_variant("*" + path)
    current = work.config.get("autoload", name)
    if current is None and name in old.get("autoloads", []):
        work.notes.append(f"Left out the {name} autoload: it was removed from the project.")
    elif current is None:
        work.config.set("autoload", name, value)
        entry["autoloads"].append(name)
    elif current == value:
        if name in old.get("autoloads", []):
            entry["autoloads"].append(name)
    else:
        work.conflicts.append(
            f"Autoload {name} already points at {current}; {unit.id} needs {value}."
        )


def _next_preset_index(config: GodotConfig) -> int:
    numbers = [
        int(match.group(1))
        for section in config.sections()
        if (match := re.fullmatch(r"preset\.(\d+)", section))
    ]
    return max(numbers, default=-1) + 1


def _preset_names(config: GodotConfig) -> dict[str, int]:
    names: dict[str, int] = {}
    for section in config.sections():
        match = re.fullmatch(r"preset\.(\d+)", section)
        if match:
            raw = config.get(section, "name")
            if raw is not None:
                names[raw.strip().strip('"')] = int(match.group(1))
    return names


def _apply_preset(work: _Work, unit: _Unit, entry: dict[str, Any], old: dict[str, Any], name: str, path: Path) -> None:
    existing = _preset_names(work.presets)
    if name in existing:
        if name in old.get("export_presets", []):
            entry["export_presets"].append(name)
        else:
            work.conflicts.append(f"Export preset {name!r} already exists; {unit.id} would add it.")
        return
    index = _next_preset_index(work.presets)
    fragment = GodotConfig.parse(path.read_text(encoding="utf-8"))
    for section in fragment.sections():
        match = re.fullmatch(r"preset\.\d+(\..+)?", section)
        if match is None:
            continue
        pairs = [(k, str(fragment.get(section, k))) for k in fragment.keys(section)]
        work.presets.add_section(f"preset.{index}{match.group(1) or ''}", pairs, sort=False)
    entry["export_presets"].append(name)


def _empty_entry(unit: _Unit) -> dict[str, Any]:
    return {
        "kind": unit.kind,
        "version": unit.version,
        "files": {},
        "settings": {},
        "autoloads": [],
        "input_events": {},
        "input_created": [],
        "input_deadzones": {},
        "append": {},
        "export_presets": [],
    }


def _engine_settings(resolution: Resolution) -> dict[str, str]:
    blueprint = resolution.blueprint
    assert blueprint is not None
    settings = {
        "application/run/main_scene": to_variant(blueprint.main_scene),
        "game/blueprint": to_variant(blueprint.id),
    }
    if blueprint.hooks.level_scene:
        settings["game/hooks/level_scene"] = to_variant(blueprint.hooks.level_scene)
    if blueprint.hooks.player_scene:
        settings["game/hooks/player_scene"] = to_variant(blueprint.hooks.player_scene)
    return settings


def _plan(project: Path, target: Path, resolution: Resolution) -> Plan:
    conflicts = list(resolution.problems)
    if conflicts or resolution.blueprint is None:
        return Plan(resolution, [], [], [], [], {}, conflicts)
    units = _units(resolution)
    conflicts += _compose(units)
    godot_path = target / "project.godot"
    if not godot_path.is_file():
        return Plan(resolution, [], [], [], [], {}, [*conflicts, f"No project.godot in {target}"])
    godot_text = godot_path.read_text(encoding="utf-8")
    presets_path = target / "export_presets.cfg"
    presets_text = presets_path.read_text(encoding="utf-8") if presets_path.is_file() else ""
    manifest_old = _read_manifest(target, project)
    old_units: dict[str, Any] = manifest_old.get("units", {})
    work = _Work(target, GodotConfig.parse(godot_text), GodotConfig.parse(presets_text), old_units, conflicts)
    recorded_hashes: dict[str, str] = {}
    for unit_entry in old_units.values():
        recorded_hashes.update(unit_entry.get("files", {}))

    writes: list[FileWrite] = []
    deletes: list[str] = []
    manifest_units: dict[str, Any] = {}
    for unit in units:
        old = old_units.get(unit.id, {})
        entry = _empty_entry(unit)
        for rel, source in sorted(unit.files.items()):
            data = source.read_bytes()
            entry["files"][rel] = _sha(data)
            destination = target / rel
            action: Literal["create", "update", "same"] = "create"
            if destination.is_file():
                current = destination.read_bytes()
                recorded = recorded_hashes.get(rel)
                if current == data:
                    action = "same"
                elif recorded == _sha(current):
                    action = "update"
                elif recorded == _sha(data):
                    # The user edited a file this unit wrote, and the unit has
                    # not changed it since: their edit is the project now.
                    continue
                elif recorded is not None:
                    work.conflicts.append(
                        f"{rel} was edited, and the new version of {unit.id} changes it "
                        "too. Keep a copy of your changes, restore the file, then try again."
                    )
                    continue
                else:
                    work.conflicts.append(
                        f"{rel} already exists with different content, so {unit.id} "
                        "will not overwrite it. Move or rename your file, then try again."
                    )
                    continue
            elif recorded_hashes.get(rel) == _sha(data) and rel in old.get("files", {}):
                work.notes.append(f"Left out {rel}: it was deleted from the project.")
                continue
            elif destination.exists() or any(
                parent.is_file() for parent in destination.parents if target in parent.parents
            ):
                work.conflicts.append(f"{rel}: something else is in the way.")
                continue
            writes.append(FileWrite(rel, data, unit.id, action))
        dropped = {
            rel: digest for rel, digest in old.get("files", {}).items() if rel not in unit.files
        }
        gone, edited = _split_deletable(target, dropped)
        deletes += gone
        work.notes += [
            f"Kept {rel}: it was edited, and {unit.id} no longer ships it." for rel in edited
        ]
        settings = _unit_settings(unit)
        if unit.kind == "blueprint":
            settings = {**_engine_settings(resolution), **settings}
        for key, raw in settings.items():
            _apply_setting(work, entry, old, key, raw)
        if unit.kind == "blueprint":
            _apply_append(work, entry, old, "application/config/features", [GODOT_VERSION])
        for merge in unit.merges:
            for name, path in merge.autoload.items():
                _apply_autoload(work, unit, entry, old, name, path)
            for action_name, spec in merge.input.items():
                _apply_input(work, unit, entry, old, action_name, spec)
            for key, values in merge.append.items():
                _apply_append(work, entry, old, key, values)
        for name, preset_path in unit.presets:
            _apply_preset(work, unit, entry, old, name, preset_path)
        manifest_units[unit.id] = entry

    blueprint = resolution.blueprint
    manifest = {
        "format": MANIFEST_FORMAT,
        "blueprint": blueprint.id,
        "features": [f.id for f in resolution.features],
        "units": manifest_units,
    }
    new_godot = work.config.dump()
    new_presets = work.presets.dump()
    record = GameRecord(
        blueprint=blueprint.id,
        blueprint_version=blueprint.version,
        features=[f.id for f in resolution.features],
        feature_versions={f.id: f.version for f in resolution.features},
    )
    toml_path = target / "joinery.toml"
    toml_text = toml_path.read_text(encoding="utf-8") if toml_path.is_file() else ""
    new_toml = record_text(toml_text, record)
    return Plan(
        resolution=resolution,
        writes=writes,
        deletes=deletes,
        config_edits=_config_edits("project.godot", GodotConfig.parse(godot_text), work.config),
        preset_edits=[
            name for entry in manifest_units.values() for name in entry["export_presets"]
            if name not in _preset_names(GodotConfig.parse(presets_text))
        ],
        manifest=manifest,
        conflicts=sorted(dict.fromkeys(work.conflicts), key=work.conflicts.index),
        notes=work.notes,
        project_text=new_godot if new_godot != godot_text else None,
        presets_text=new_presets if new_presets != presets_text else None,
        record_text=new_toml if new_toml != toml_text else None,
    )


def _requested(target: Path, blueprint_id: str, features: Sequence[str]) -> list[str]:
    """Features to apply: the ones asked for plus the ones already applied."""
    record = read_record(target)
    if record is None:
        return list(features)
    if record.blueprint != blueprint_id:
        raise ValueError(
            f"This project was started from the {record.blueprint} blueprint. "
            "A blueprint is never swapped; start a new project instead."
        )
    return list(dict.fromkeys([*record.features, *features]))


def plan(
    project: Path, blueprint_id: str, features: Sequence[str], *, root: Path | None = None
) -> Plan:
    """What applying would change. Pure: reads the project, writes nothing."""
    target = root or project
    resolution = resolve(blueprint_id, _requested(target, blueprint_id, features))
    return _plan(project, target, resolution)


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _write_text(path: Path, text: str) -> None:
    _write(path, text.encode("utf-8"))


def _prune(target: Path, rel: str) -> None:
    parent = (target / rel).parent
    while parent != target and target in parent.parents:
        try:
            parent.rmdir()
        except OSError:
            return
        parent = parent.parent


def _delete(target: Path, rel: str, tracked: Iterable[str] = ()) -> list[str]:
    """Delete `rel` and the sidecars Godot made for it.

    A sidecar the unit shipped itself (in `tracked`) is not touched here: it is
    deleted on its own entry, so its own hash is checked.
    """
    tracked = set(tracked)
    removed: list[str] = []
    for candidate in (rel, rel + ".uid", rel + ".import"):
        if candidate != rel and candidate in tracked:
            continue
        path = target / candidate
        if path.is_file():
            path.unlink()
            removed.append(candidate)
    _prune(target, rel)
    return removed


def _sidecar_of(rel: str) -> str | None:
    for suffix in (".uid", ".import"):
        if rel.endswith(suffix):
            return rel.removesuffix(suffix)
    return None


def _split_deletable(target: Path, files: dict[str, str]) -> tuple[list[str], list[str]]:
    """(unchanged files to delete, edited files to keep) among recorded ones.

    The `.uid` of a kept file is kept too: deleting it would give the file a
    new uid and break every scene that refers to it by uid.
    """
    delete: list[str] = []
    kept: list[str] = []
    for rel, digest in sorted(files.items()):
        path = target / rel
        if not path.is_file():
            continue
        if _sha(path.read_bytes()) == digest:
            delete.append(rel)
        else:
            kept.append(rel)
    kept_set = set(kept)
    delete = [rel for rel in delete if _sidecar_of(rel) not in kept_set]
    return delete, kept


def apply(
    project: Path,
    blueprint_id: str,
    features: Sequence[str] = (),
    *,
    root: Path | None = None,
) -> Applied:
    """Apply the core, a blueprint and features. Raises ValueError, writing nothing, on any conflict."""
    target = root or project
    resolution = resolve(blueprint_id, _requested(target, blueprint_id, features))
    result = _plan(project, target, resolution)
    if result.conflicts:
        raise ValueError("\n".join(result.conflicts))
    return _commit(project, target, result)


def _commit(project: Path, target: Path, result: Plan) -> Applied:
    written: list[str] = []
    for write in result.writes:
        if write.action != "same":
            _write(target / write.path, write.data)
            written.append(write.path)
    deleted: list[str] = []
    tracked = set(result.deletes) | {
        rel for entry in result.manifest.get("units", {}).values() for rel in entry["files"]
    }
    for rel in result.deletes:
        deleted += _delete(target, rel, tracked)
    if result.project_text is not None:
        _write_text(target / "project.godot", result.project_text)
        written.append("project.godot")
    if result.presets_text is not None:
        _write_text(target / "export_presets.cfg", result.presets_text)
        written.append("export_presets.cfg")
    manifest_path = target / MANIFEST
    text = _manifest_text(result.manifest)
    if not manifest_path.is_file() or manifest_path.read_text(encoding="utf-8") != text:
        _write_text(manifest_path, text)
        written.append(MANIFEST)
    if result.record_text is not None:
        _write_text(target / "joinery.toml", result.record_text)
        written.append("joinery.toml")
    blueprint = result.resolution.blueprint
    assert blueprint is not None
    return Applied(
        blueprint=blueprint.id,
        features=[f.id for f in result.resolution.features],
        added=result.resolution.added,
        written=written,
        deleted=deleted,
        config_edits=result.config_edits,
        preset_edits=result.preset_edits,
        notes=result.notes,
        manifest=result.manifest,
    )


def add_feature(project: Path, feature_id: str, *, root: Path | None = None) -> Applied:
    """Add one feature (and what it requires) to a project made from a blueprint."""
    target = root or project
    record = read_record(target)
    if record is None:
        raise ValueError("This project was not started from a blueprint, so features cannot be added.")
    return apply(project, record.blueprint, [*record.features, feature_id], root=root)


def _dependants(catalogue: _Catalogue, applied: Iterable[str], feature_id: str) -> list[str]:
    applied = list(applied)
    needs: dict[str, set[str]] = {}
    for fid in applied:
        feature = catalogue.features.get(fid)
        needs[fid] = set(feature.requires) if feature else set()
    found: set[str] = set()
    frontier = {feature_id}
    while frontier:
        frontier = {f for f in applied if needs[f] & frontier and f not in found and f != feature_id}
        found |= frontier
    return sorted(found)


def remove_feature(project: Path, feature_id: str, *, root: Path | None = None) -> Removed:
    """Remove a feature: its unmodified files, its settings, inputs and presets."""
    target = root or project
    record = read_record(target)
    if record is None or feature_id not in record.features:
        raise ValueError(f"The feature {feature_id} is not part of this project.")
    catalogue = _load()
    dependants = _dependants(catalogue, record.features, feature_id)
    if dependants:
        needing = ", ".join(
            catalogue.features[d].name if d in catalogue.features else d for d in dependants
        )
        raise ValueError(f"{needing} needs {feature_id}; remove that first.")
    manifest = _read_manifest(target, project)
    entry: dict[str, Any] = manifest.get("units", {}).get(feature_id, {})
    files: dict[str, str] = entry.get("files", {})
    to_delete, kept = _split_deletable(target, files)
    deleted: list[str] = []
    for rel in to_delete:
        deleted += _delete(target, rel, files)
    notes = [f"Kept {rel}: it was edited after the feature added it." for rel in kept]

    godot_path = target / "project.godot"
    godot_text = godot_path.read_text(encoding="utf-8")
    config = GodotConfig.parse(godot_text)
    had_entries = {s for s in config.sections() if config.keys(s)}
    for key, setting in entry.get("settings", {}).items():
        section, name = _split_key(key)
        if config.get(section, name) == setting["value"]:
            if setting["previous"] is None:
                config.erase(section, name)
            else:
                config.set(section, name, setting["previous"])
        else:
            notes.append(f"Kept your value for {key}.")
    for name in entry.get("autoloads", []):
        config.erase("autoload", name)
    for action, events in entry.get("input_events", {}).items():
        current = config.get("input", action)
        if current is None:
            continue
        drop = {event_identity(e) for e in events}
        left = [e for e in action_events(current) if event_identity(e) not in drop]
        deadzone = action_deadzone(current)
        dz = entry.get("input_deadzones", {}).get(action)
        if dz is not None and deadzone == dz["value"]:
            deadzone = dz["previous"]
        if not left and action in entry.get("input_created", []):
            config.erase("input", action)
        else:
            config.set("input", action, action_value(deadzone, left))
    for action, dz in entry.get("input_deadzones", {}).items():
        if action in entry.get("input_events", {}):
            continue
        current = config.get("input", action)
        if current is not None and action_deadzone(current) == dz["value"]:
            config.set("input", action, action_value(dz["previous"], action_events(current)))
    for key, appended in entry.get("append", {}).items():
        section, name = _split_key(key)
        current = config.get(section, name)
        if current is None:
            continue
        values = [v for v in packed_strings(current) if v not in appended["values"]]
        if not values and appended["created"]:
            config.erase(section, name)
        else:
            config.set(section, name, to_variant(values))
    for section in config.sections():
        if section in had_entries and not config.keys(section):
            config.remove_section(section)
    config_edits = _config_edits("project.godot", GodotConfig.parse(godot_text), config)
    if config.dump() != godot_text:
        _write_text(godot_path, config.dump())

    preset_edits: list[str] = []
    presets_path = target / "export_presets.cfg"
    if entry.get("export_presets") and presets_path.is_file():
        presets = GodotConfig.parse(presets_path.read_text(encoding="utf-8"))
        names = _preset_names(presets)
        for name in entry["export_presets"]:
            if name in names:
                index = names[name]
                for section in list(presets.sections()):
                    if section == f"preset.{index}" or section.startswith(f"preset.{index}."):
                        presets.remove_section(section)
                preset_edits.append(name)
        _renumber(presets)
        text = presets.dump()
        if presets.sections():
            _write_text(presets_path, text)
        else:
            presets_path.unlink()

    remaining = [f for f in record.features if f != feature_id]
    manifest_units = {k: v for k, v in manifest.get("units", {}).items() if k != feature_id}
    manifest = {**manifest, "features": [f for f in manifest.get("features", []) if f != feature_id], "units": manifest_units}
    _write_text(target / MANIFEST, _manifest_text(manifest))
    new_record = GameRecord(
        blueprint=record.blueprint,
        blueprint_version=record.blueprint_version,
        features=remaining,
        feature_versions={k: v for k, v in record.feature_versions.items() if k != feature_id},
    )
    toml_path = target / "joinery.toml"
    _write_text(toml_path, record_text(toml_path.read_text(encoding="utf-8"), new_record))
    return Removed(feature_id, deleted, kept, config_edits, preset_edits, notes, manifest)


def _renumber(presets: GodotConfig) -> None:
    numbers = sorted(
        {int(m.group(1)) for s in presets.sections() if (m := re.match(r"preset\.(\d+)(?:\.|$)", s))}
    )
    for new, old in enumerate(numbers):
        if new == old:
            continue
        for section in list(presets.sections()):
            match = re.fullmatch(rf"preset\.{old}(\..+)?", section)
            if match:
                presets.rename_section(section, f"preset.{new}{match.group(1) or ''}")


# --- Project summary -------------------------------------------------------------


def project_game(project: Path) -> dict[str, Any] | None:
    """The project's blueprint and features as JSON-safe data, or None."""
    record = read_record(project)
    if record is None:
        return None
    manifest = _read_manifest(project, project)
    units = manifest.get("units", {})
    catalogue = _load(errors=[])
    blueprint = catalogue.blueprints.get(record.blueprint)
    applied = set(record.features)
    addable: list[str] = []
    for feature in sorted(catalogue.features.values(), key=lambda f: f.id):
        if feature.id in applied or not feature.supports(record.blueprint):
            continue
        if _resolve(catalogue, record.blueprint, [*record.features, feature.id]).problems:
            continue
        addable.append(feature.id)
    return {
        "blueprint": record.blueprint,
        "blueprint_version": record.blueprint_version,
        "features": list(record.features),
        "feature_versions": dict(record.feature_versions),
        "name": blueprint.name if blueprint else record.blueprint,
        "dimension": blueprint.dimension if blueprint else None,
        "main_scene": blueprint.main_scene if blueprint else None,
        "hooks": blueprint.hooks.model_dump(mode="json") if blueprint else None,
        "autoloads": [n for u in units.values() for n in u.get("autoloads", [])],
        "input_actions": sorted({a for u in units.values() for a in u.get("input_events", {})}),
        "units": {
            unit_id: {
                "kind": u.get("kind", ""),
                "version": u.get("version", 0),
                "files": sorted(u.get("files", {})),
            }
            for unit_id, u in units.items()
        },
        "can_add": addable,
    }


# --- Catalogue validation --------------------------------------------------------


def _res_to_overlay(path: str) -> str | None:
    return path[len("res://"):] if path.startswith("res://") else None


def _check_files(unit_id: str, directory: Path, files: dict[str, Path], roots: Sequence[str], errors: list[str], uids: dict[str, str]) -> None:
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or _SKIP.intersection(path.parts):
            continue
        rel_path = path.relative_to(directory)
        suffix = path.suffix.lower()
        if suffix in BANNED_SUFFIXES:
            errors.append(
                f"{unit_id}: {rel_path} is an asset file; "
                "blueprints and features are code-only greybox"
            )
        elif suffix in BINARY_SUFFIXES:
            errors.append(
                f"{unit_id}: {rel_path} is a binary or extension; blueprints and "
                "features never ship third-party code"
            )
        elif "overlay" in rel_path.parts or directory.name == "overlay":
            if suffix not in SHIPPABLE_SUFFIXES:
                errors.append(
                    f"{unit_id}: {rel_path} is not a code or text file Godot reads; "
                    "it would not be copied into a game"
                )
            elif suffix in (".tscn", ".tres"):
                media = _EMBEDDED_MEDIA.search(path.read_text(encoding="utf-8", errors="replace"))
                if media is not None:
                    errors.append(
                        f"{unit_id}: {rel_path} embeds a {media.group(1)}; "
                        "blueprints and features are code-only greybox"
                    )
    for rel, source in files.items():
        if not any(rel == root or (root.endswith("/") and rel.startswith(root)) for root in roots):
            errors.append(f"{unit_id}: {rel} is outside its folders ({', '.join(roots)})")
        if rel.endswith(".gd") and not (source.parent / (source.name + ".uid")).is_file():
            errors.append(f"{unit_id}: {rel} has no .gd.uid")
        if rel.endswith(".uid"):
            text = source.read_text(encoding="utf-8").strip()
            if not _UID.match(text):
                errors.append(f"{unit_id}: {rel} does not hold a uid://…")
            _claim(uids, text, f"{unit_id}:{rel}", errors)
        if rel.endswith((".tscn", ".tres")):
            first = source.read_text(encoding="utf-8").split("\n", 1)[0]
            match = _HEADER_UID.search(first)
            if match is None:
                if rel.endswith(".tscn"):
                    errors.append(f"{unit_id}: {rel} header has no uid")
            else:
                _claim(uids, match.group(1), f"{unit_id}:{rel}", errors)


def _claim(uids: dict[str, str], uid: str, owner: str, errors: list[str]) -> None:
    if uid in uids:
        errors.append(f"{owner} reuses {uid} from {uids[uid]}")
    else:
        uids[uid] = owner


def _catalog_tool_ids() -> set[str]:
    from joinery.catalog import load_catalog

    return {tool.id for tool in load_catalog().tool}


def _preset_exists(preset: str) -> bool:
    return preset == "none" or (PRESETS_DIR / f"{preset}.toml").is_file()


def _check_inputs(unit_id: str, merges: Iterable[ProjectMerge], errors: list[str]) -> None:
    for merge in merges:
        for action in merge.input:
            if action.startswith("ui_"):
                errors.append(
                    f"{unit_id}: input {action} would replace Godot's built-in "
                    "ui_* bindings; leave ui_* actions alone"
                )
            elif action not in ACTIONS:
                errors.append(f"{unit_id}: input {action} is not in the standard action vocabulary")


def validate_catalogue() -> list[str]:
    """Every check the catalogue must pass, as readable problems (empty = fine)."""
    errors: list[str] = []
    catalogue = _load(errors)
    if catalogue.core is None and catalogue.blueprints and not any(
        d.name == CORE_DIR_NAME for d in _unit_dirs(BLUEPRINTS_DIR)
    ):
        errors.append(f"{BLUEPRINTS_DIR / CORE_DIR_NAME} is missing")
    tools = _catalog_tool_ids()
    uids: dict[str, str] = {}
    autoload_owner: dict[str, str] = {}
    root_owner: dict[str, str] = {}
    all_units: list[tuple[str, Sequence[ProjectMerge]]] = []

    core = catalogue.core
    core_files = _overlay(core.dir / "overlay") if core else {}
    if core is not None:
        _check_files(core.id, core.dir, core_files, CORE_ROOTS, errors, uids)
        all_units.append((core.id, [core.merge]))
        _check_inputs(core.id, [core.merge], errors)
        if core.id in catalogue.blueprints or core.id in catalogue.features:
            errors.append(f"unit id {core.id} is used twice")

    for blueprint in catalogue.blueprints.values():
        where = f"blueprint {blueprint.id}"
        if blueprint.dir.name != blueprint.id:
            errors.append(f"{where}: folder name {blueprint.dir.name} differs from its id")
        if not _ID.match(blueprint.id):
            errors.append(f"{where}: ids are lowercase words joined by '-'")
        if blueprint.id in catalogue.features:
            errors.append(f"{where}: a feature has the same id")
        if not blueprint.pitch.strip():
            errors.append(f"{where}: needs a one-line pitch")
        files = _overlay(blueprint.dir / "overlay")
        _check_files(blueprint.id, blueprint.dir, files, BLUEPRINT_ROOTS, errors, uids)
        _check_inputs(blueprint.id, [blueprint.merge], errors)
        if {"dash", "crouch"} <= set(blueprint.merge.input):
            errors.append(f"{where}: dash and crouch share a pad button; use one")
        available = {**core_files, **files}
        for label, scene in (
            ("main_scene", blueprint.main_scene),
            ("hooks.level_scene", blueprint.hooks.level_scene),
            ("hooks.player_scene", blueprint.hooks.player_scene),
            *(("hooks.spawnable_scenes", s) for s in blueprint.hooks.spawnable_scenes),
        ):
            if not scene:
                if label in ("main_scene", "hooks.level_scene"):
                    errors.append(f"{where}: {label} is empty")
                continue
            rel = _res_to_overlay(scene)
            if rel is None or rel not in available:
                errors.append(f"{where}: {label} {scene} is not shipped")
        for tool in blueprint.suggested_tools:
            if tool not in tools:
                errors.append(f"{where}: suggested tool {tool} is not in the tool catalog")
        if not _preset_exists(blueprint.bible_preset):
            errors.append(f"{where}: bible preset {blueprint.bible_preset} does not exist")
        for feature_id in blueprint.default_features:
            feature = catalogue.features.get(feature_id)
            if feature is None:
                errors.append(f"{where}: default feature {feature_id} does not exist")
            elif not feature.supports(blueprint.id):
                errors.append(f"{where}: default feature {feature_id} does not support it")
        defaults = _resolve(catalogue, blueprint.id, blueprint.default_features)
        errors += [
            f"{where}: default features: {p}"
            for p in defaults.problems
            if not p.startswith(("Unknown", "The shared game core"))
        ]
        for name, path in blueprint.merge.autoload.items():
            rel = _res_to_overlay(path)
            if rel is None or rel not in available:
                errors.append(f"{where}: autoload {name} {path} is not shipped")
        all_units.append((blueprint.id, [blueprint.merge]))

    for feature in catalogue.features.values():
        where = f"feature {feature.id}"
        if feature.dir.name != feature.id:
            errors.append(f"{where}: folder name {feature.dir.name} differs from its id")
        if not _ID.match(feature.id):
            errors.append(f"{where}: ids are lowercase words joined by '-'")
        for kind, ids in (("requires", feature.requires), ("conflicts", feature.conflicts), ("enhances", feature.enhances)):
            for other in ids:
                if other not in catalogue.features:
                    errors.append(f"{where}: {kind} unknown feature {other}")
                if other == feature.id:
                    errors.append(f"{where}: {kind} itself")
        for bp in feature.blueprints:
            if bp != "*" and bp not in catalogue.blueprints:
                errors.append(f"{where}: unknown blueprint {bp}")
        for extra in feature.extra_roots:
            if extra.strip("/") not in EXTRA_ROOTS:
                errors.append(f"{where}: extra root {extra} is not allowed ({', '.join(sorted(EXTRA_ROOTS))})")
        for external in feature.external:
            if external.required:
                errors.append(
                    f"{where}: external {external.name} is required; a feature must "
                    "run without anything Joinery does not ship"
                )
        roots = _feature_roots(feature)
        for root in roots:
            if root in root_owner:
                errors.append(f"{where}: folder {root} is already used by {root_owner[root]}")
            root_owner.setdefault(root, feature.id)
        files = _overlay(feature.dir / "overlay")
        _check_files(feature.id, feature.dir, files, roots, errors, uids)
        adapter_dirs = _unit_dirs(feature.dir / "adapters")
        for adapter in adapter_dirs:
            if adapter.name not in catalogue.blueprints:
                errors.append(f"{where}: adapter for unknown blueprint {adapter.name}")
            elif not feature.supports(adapter.name):
                errors.append(f"{where}: adapter for unsupported blueprint {adapter.name}")
            adapter_files = _overlay(adapter / "overlay")
            _check_files(f"{feature.id}/{adapter.name}", adapter / "overlay", adapter_files, roots, errors, uids)
            clash = set(adapter_files) & set(files)
            if clash:
                errors.append(f"{where}: adapter {adapter.name} repeats {', '.join(sorted(clash))}")
        for bp in feature.adapters:
            if bp not in catalogue.blueprints:
                errors.append(f"{where}: [adapters.{bp}] names an unknown blueprint")
            elif not feature.supports(bp):
                errors.append(f"{where}: [adapters.{bp}] for an unsupported blueprint")
        for fragment in sorted((feature.dir / "export_presets").glob("*.cfg")):
            try:
                _preset_name(fragment)
            except ValueError as exc:
                errors.append(str(exc))
        feature_merges = [feature.merge, *feature.adapters.values()]
        _check_inputs(feature.id, feature_merges, errors)
        all_units.append((feature.id, feature_merges))

    for unit_id, merges in all_units:
        for merge in merges:
            for name in merge.autoload:
                owner = autoload_owner.setdefault(name, unit_id)
                if owner != unit_id:
                    errors.append(f"autoload {name} is added by both {owner} and {unit_id}")
            for key in merge.settings:
                if key in ENGINE_KEYS:
                    errors.append(f"{unit_id}: {key} is written by the engine")

    errors += _cycles(catalogue)

    errors += _check_combos(catalogue)
    return list(dict.fromkeys(errors))


def _cycles(catalogue: _Catalogue) -> list[str]:
    found: list[str] = []
    state: dict[str, int] = {}

    def visit(feature_id: str, trail: list[str]) -> None:
        state[feature_id] = 1
        feature = catalogue.features[feature_id]
        for needed in feature.requires:
            if needed not in catalogue.features:
                continue
            if state.get(needed) == 1:
                loop = [*trail[trail.index(needed):], needed] if needed in trail else [feature_id, needed]
                found.append("Features require each other in a cycle: " + " -> ".join(loop))
            elif needed not in state:
                visit(needed, [*trail, needed])
        state[feature_id] = 2

    for feature_id in sorted(catalogue.features):
        if feature_id not in state:
            visit(feature_id, [feature_id])
    return found


def combinations(catalogue: _Catalogue | None = None) -> list[tuple[str, tuple[str, ...]]]:
    """Every blueprint with every subset of the features that support it."""
    catalogue = catalogue or _load()
    combos: list[tuple[str, tuple[str, ...]]] = []
    for blueprint_id in sorted(catalogue.blueprints):
        supported = sorted(f.id for f in catalogue.features.values() if f.supports(blueprint_id))
        for size in range(len(supported) + 1):
            combos += [(blueprint_id, subset) for subset in itertools.combinations(supported, size)]
    return combos


def _check_combos(catalogue: _Catalogue) -> list[str]:
    problems: list[str] = []
    for blueprint_id, subset in combinations(catalogue):
        resolution = _resolve(catalogue, blueprint_id, subset)
        if resolution.problems:
            continue  # declared conflicts are refused up front, not clashes
        try:
            units = _units(resolution)
        except ValueError as exc:
            problems.append(str(exc))
            continue
        label = "+".join((blueprint_id, *subset))
        problems += [f"{label}: {p}" for p in _compose(units)]
    return problems


def check_static(blueprint_id: str, features: Sequence[str]) -> list[str]:
    """Resolution problems plus static clashes for one combination."""
    resolution = resolve(blueprint_id, features)
    if resolution.problems:
        return list(resolution.problems)
    return _compose(_units(resolution))


__all__ = [
    "ACTIONS",
    "BANNED_SUFFIXES",
    "BINARY_SUFFIXES",
    "BLUEPRINTS_DIR",
    "FEATURES_DIR",
    "MANIFEST",
    "SHIPPABLE_SUFFIXES",
    "Applied",
    "Blueprint",
    "BriefDefaults",
    "ConfigEdit",
    "External",
    "Feature",
    "FileWrite",
    "GameRecord",
    "Hooks",
    "InputSpec",
    "Plan",
    "ProjectMerge",
    "Removed",
    "Resolution",
    "add_feature",
    "apply",
    "catalogue_json",
    "check_static",
    "combinations",
    "describe",
    "list_blueprints",
    "list_features",
    "new_uid",
    "plan",
    "project_game",
    "read_record",
    "record_text",
    "remove_feature",
    "resolve",
    "validate_catalogue",
]
