"""Which model reads what: a light model for light work, the strong one for the rest.

The same for every provider (Codex, Claude Code, Gemini CLI, opencode). Two
settings in ``[agents]`` of joinery.toml:

* ``light_model``: a cheaper, faster model for light work. Either one name for
  every provider (``light_model = "haiku"``) or one per provider
  (``light_model = { claude = "haiku", codex = "gpt-5-mini" }``). Unset (the
  default) means everything runs on the main model, as before. It is used for
  the reviewer's first pass on a low-risk change, the session summary written
  before a long chat continues fresh, and the accessibility and performance
  agents' wording of a fix. Whenever the light pass sends a change back, is
  unsure, fails or runs out of time, the main model does the work again and its
  answer is the one that counts: a light model never decides alone.
* ``review``: ``"always"`` (default, today's behaviour), ``"risky"`` (skip the
  reviewer agent when the deterministic checks fully cover the change) or
  ``"never"``.

Risk is read from the staged change itself (:func:`change_risk`): which files,
created or modified, and how big a scene is. Low risk means every file is one
Joinery's own checks judge completely: an existing texture recoloured within
the palette, a loudness fix of an existing sound, a material's values, the lock
and recipe bookkeeping that goes with them. Code, scenes with many nodes, new
scenes, any model, the art bible, the game brief, project settings, deletions
and anything unrecognised are high risk, and keep the reviewer.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Literal, get_args

ReviewMode = Literal["always", "risky", "never"]
REVIEW_MODES: tuple[ReviewMode, ...] = get_args(ReviewMode)
#: Which model ran a turn, recorded in the change's metrics.
Tier = Literal["light", "main"]
#: How one review is routed.
Route = Literal["skip", "light", "main"]
MODEL_CHARS = 120
#: ``light_model`` keys: the four providers' agent ids.
PROVIDER_IDS = ("claude", "codex", "gemini", "opencode")


@dataclass(frozen=True)
class RoutingSettings:
    """``[agents] review`` and ``light_model`` in joinery.toml."""

    review: ReviewMode = "always"
    #: Provider id -> light model; "*" applies to every provider.
    light_models: Mapping[str, str] = field(default_factory=dict)

    def light_model(self, agent: str) -> str:
        """The light model for ``agent`` ("" when none is set: use the main one)."""
        return self.light_models.get(agent, "") or self.light_models.get("*", "")


def _clean_model(value: object) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if len(text) > MODEL_CHARS or any(c in text for c in '"\\\n'):
        return ""
    return text


def routing_settings(project: Path) -> RoutingSettings:
    """Read each time: the Settings page or CLI may change them while a chat runs."""
    try:
        raw = tomllib.loads((project / "joinery.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return RoutingSettings()
    table = raw.get("agents", {})
    if not isinstance(table, dict):
        return RoutingSettings()
    review = table.get("review", "always")
    light = table.get("light_model", "")
    models: dict[str, str] = {}
    if isinstance(light, str):
        if _clean_model(light):
            models["*"] = _clean_model(light)
    elif isinstance(light, dict):
        for key, value in light.items():
            if key in PROVIDER_IDS or key == "*":
                name = _clean_model(value)
                if name:
                    models[key] = name
    return RoutingSettings(
        review=review if review in REVIEW_MODES else "always",
        light_models=models,
    )


def set_routing(
    project: Path,
    *,
    review: str | None = None,
    light_model: str | None = None,
    provider: str = "",
) -> RoutingSettings:
    """Save ``review`` and/or ``light_model`` (for one ``provider``, or for every
    provider when ``provider`` is ""), keeping every other line of joinery.toml.

    An empty ``light_model`` clears it. Raises ValueError on a bad value."""
    from joinery.roles import set_agents_values

    values: dict[str, str] = {}
    if review is not None:
        if review not in REVIEW_MODES:
            raise ValueError("review is one of " + ", ".join(REVIEW_MODES) + ".")
        values["review"] = f'"{review}"'
    if light_model is not None:
        clean = light_model.strip()
        if clean and not _clean_model(clean):
            raise ValueError("Name the light model in one plain line.")
        if provider and provider not in PROVIDER_IDS:
            raise ValueError("provider is one of " + ", ".join(PROVIDER_IDS) + ".")
        if provider:
            current = dict(routing_settings(project).light_models)
            # One name for every provider becomes the others' entries.
            every = current.pop("*", "")
            table = {p: every for p in PROVIDER_IDS if every}
            table.update({k: v for k, v in current.items() if k in PROVIDER_IDS})
            table[provider] = clean
            table = {k: v for k, v in table.items() if v}
            values["light_model"] = (
                "{ " + ", ".join(f'{k} = "{v}"' for k, v in table.items()) + " }"
                if table
                else '""'
            )
        else:
            values["light_model"] = f'"{clean}"'
    if values:
        set_agents_values(project, values)
    saved = routing_settings(project)
    if review is not None and saved.review != review:
        raise ValueError("joinery.toml keeps [agents] in a form Joinery can't edit.")
    if (
        light_model is not None
        and saved.light_model(provider or "*") != light_model.strip()
    ):
        raise ValueError("joinery.toml keeps [agents] in a form Joinery can't edit.")
    return saved


# --- Risk of a staged change ------------------------------------------------------------

#: Above this many nodes a scene is read by the reviewer whatever else holds.
SCENE_NODES = 12
#: Above this many files a change is not light work.
MANY_FILES = 8

_CODE = frozenset(
    {".gd", ".cs", ".gdshader", ".shader", ".gdshaderinc", ".py", ".lua", ".scd"}
)
_SCENES = frozenset({".tscn", ".scn"})
_MODELS = frozenset({".blend", ".glb", ".gltf", ".fbx", ".obj", ".dae", ".vox"})
#: Rasters and their native paint documents: the bible's palette and size checks
#: judge them completely when an existing one changes.
_IMAGES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".exr", ".kra", ".ora"})
#: Rendered sound: the loudness and format checks judge them completely.
_SOUNDS = frozenset({".wav", ".ogg", ".flac"})
#: Material values and procedural material graphs.
_MATERIALS = frozenset({".tres", ".material", ".mmg", ".ptex"})
#: Bookkeeping that follows an asset change.
_BOOKKEEPING_FILES = frozenset({"joinery.lock"})
_BOOKKEEPING_SUFFIXES = frozenset({".import", ".uid"})
_BOOKKEEPING_DIRS = ("recipes/",)
#: The project's own rules and settings: always read by the reviewer.
_RULE_FILES = frozenset(
    {
        "art/bible.md",
        "design/brief.md",
        "project.godot",
        "joinery.toml",
        "export_presets.cfg",
    }
)
_NODE = re.compile(r"(?m)^\[node\b")


@dataclass(frozen=True)
class Risk:
    """How much a staged change needs a reviewer: "low" or "high", and why."""

    level: Literal["low", "high"]
    #: Plain reasons; for "high" what made it so, for "low" what the change is.
    reasons: tuple[str, ...] = ()

    @property
    def low(self) -> bool:
        return self.level == "low"

    def line(self) -> str:
        return "; ".join(self.reasons)


def _scene_nodes(path: Path) -> int | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return len(_NODE.findall(text))


def change_risk(stage: Path, changes: Sequence[Mapping[str, str]]) -> Risk:
    """The risk of the change staged at ``stage`` (its ``diff()`` rows)."""
    high: list[str] = []
    kinds: list[str] = []
    counted = 0
    for change in changes:
        path = str(change.get("path", "")).removeprefix("res://")
        how = str(change.get("change", ""))
        pure = PurePosixPath(path)
        suffix = pure.suffix.lower()
        if not path:
            continue
        if path in _BOOKKEEPING_FILES or suffix in _BOOKKEEPING_SUFFIXES:
            continue
        if path.startswith(_BOOKKEEPING_DIRS):
            continue
        counted += 1
        if how == "deleted":
            high.append(f"deletes {path}")
        elif path in _RULE_FILES or path.startswith("design/"):
            high.append(f"changes {path}")
        elif suffix in _CODE:
            high.append(f"code: {path}")
        elif suffix in _MODELS:
            high.append(("new model: " if how == "created" else "model: ") + path)
        elif suffix in _SCENES:
            if how == "created":
                high.append(f"new scene: {path}")
            else:
                nodes = _scene_nodes(stage / path) if suffix == ".tscn" else None
                if nodes is None:
                    high.append(f"scene: {path}")
                elif nodes > SCENE_NODES:
                    high.append(f"scene with {nodes} nodes: {path}")
                else:
                    kinds.append("small scene edit")
        elif how != "modified":
            high.append(f"new file: {path}")
        elif suffix in _IMAGES:
            kinds.append("texture edit")
        elif suffix in _SOUNDS:
            kinds.append("sound edit")
        elif suffix in _MATERIALS:
            kinds.append("material values")
        else:
            high.append(f"unrecognised file: {path}")
    if counted > MANY_FILES:
        high.append(f"{counted} files")
    if high:
        return Risk("high", tuple(high))
    return Risk("low", tuple(dict.fromkeys(kinds)) or ("bookkeeping only",))


def review_route(settings: RoutingSettings, risk: Risk, agent: str) -> Route:
    """Skip the reviewer, give it the light model first, or the main model."""
    if settings.review == "never":
        return "skip"
    if risk.low and settings.review == "risky":
        return "skip"
    if risk.low and settings.light_model(agent):
        return "light"
    return "main"
