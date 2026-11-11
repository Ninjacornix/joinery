"""The sound pass: what a level and its player are missing to sound like a game.

`joinery sound-pass` reads the project's scenes and scripts as text, with no
model and no Godot, and lists what has no sound yet:

* player footsteps, one per floor surface (floors without a ``metadata/surface``
  get one guessed from their node and material names, or their colour);
* the player's jump and land;
* pickups (nodes in the groups coin, pickup or collectible, or scripts that
  collect or score);
* hits and damage (signals such as hurt, damaged, died);
* doors, buttons, levers and pressure plates (signals such as activated,
  toggled, pressed, opened, pulled);
* UI clicks and hovers (the project settings ``audio/ui/click`` and ``hover``);
* ambience (no AmbienceZone in the level).

Each finding names the scene and node, what is missing, the sound to make (a
`joinery.sound_kit` spec) and where it goes (the game core's SoundCue,
Footsteps or AmbienceZone, a floor's surface, a project setting). The latest
playtest report, when there is one, ranks what the player actually ran into
first. `joinery.sound_plan` turns the findings into reviewed changes.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from joinery import scene_text, sound_kit
from joinery.art_approval import approved
from joinery.scene_text import Node, Scene

Category = Literal[
    "footsteps", "jump_land", "pickups", "hits", "switches", "ambience", "ui"
]
#: Categories in the order the card shows them, with their names.
CATEGORIES: dict[str, str] = {
    "footsteps": "Footsteps",
    "jump_land": "Jump and land",
    "pickups": "Pickups",
    "hits": "Hits",
    "switches": "Doors and switches",
    "ambience": "Ambience",
    "ui": "UI",
}
#: The game core's sound building blocks (blueprints/_core/overlay/core/audio).
FOOTSTEPS = "res://core/audio/footsteps.gd"
SOUND_CUE = "res://core/audio/sound_cue.gd"
AMBIENCE_ZONE = "res://core/audio/ambience_zone.gd"
UI_SOUNDS = "res://core/audio/ui_sounds.gd"
RUNTIME = (FOOTSTEPS, SOUND_CUE, AMBIENCE_ZONE, UI_SOUNDS)

PICKUP_GROUPS = {"coin", "coins", "pickup", "pickups", "collectible", "collectable"}
PICKUP_SIGNALS = ("collected", "picked_up", "collected_changed", "pickup")
HIT_SIGNALS = (
    "hurt",
    "damaged",
    "took_damage",
    "hit",
    "died",
    "killed",
    "defeated",
    "health_changed",
)
HIT_FUNCTIONS = ("take_damage", "hurt", "damage", "apply_damage", "die")
SWITCH_SIGNALS = (
    "activated",
    "toggled",
    "pressed",
    "pressed_changed",
    "opened",
    "closed",
    "pulled",
    "switched",
    "triggered",
    "used",
    "interacted",
)
#: Floor surface keywords, checked in node, parent and material names.
SURFACE_WORDS: dict[str, tuple[str, ...]] = {
    "water": ("water", "puddle", "shallow", "river", "pond", "stream"),
    "metal": ("metal", "steel", "iron", "grate", "grating", "catwalk", "tin"),
    "wood": ("wood", "plank", "deck", "bridge", "timber", "board", "log"),
    "sand": ("sand", "desert", "beach", "dune"),
    "stone": (
        "stone",
        "rock",
        "brick",
        "cobble",
        "concrete",
        "tile",
        "marble",
        "granite",
        "pavement",
        "gravel",
    ),
    "grass": ("grass", "lawn", "meadow", "turf", "moss", "field"),
}
_NOT_FLOORS = ("wall", "ceiling", "roof", "fence", "gate", "door", "window", "rail")
_FLOOR_TYPES_3D = {
    "CSGBox3D",
    "CSGCylinder3D",
    "CSGMesh3D",
    "CSGPolygon3D",
    "CSGCombiner3D",
    "StaticBody3D",
}
_FLOOR_TYPES_2D = {"StaticBody2D", "TileMapLayer", "TileMap"}
_SIGNAL = re.compile(r"^signal\s+(\w+)", re.MULTILINE)
_FUNC = re.compile(r"^func\s+(\w+)", re.MULTILINE)
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?(?:e[-+]?\d+)?")


def _numbers(value: str) -> list[float]:
    """The numbers inside a constructor such as Vector3(1, 2, 3)."""
    inside = value[value.find("(") + 1 :] if "(" in value else value
    return [float(n) for n in _NUMBER.findall(inside)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlannedSound(_Model):
    """A sound the pass would make: the make_sound arguments and its export."""

    kind: str
    name: str
    surface: str | None = None
    variant: int = 0
    export: str
    tool: str
    #: What it is, in words ("A footstep on grass").
    words: str


class Placement(_Model):
    """Where the sound goes in the game."""

    #: The project file this changes (project-relative, such as game/level.tscn).
    file: str
    #: footsteps | sound_cue | ambience_zone | ambience_player | surfaces | ui_settings
    what: str
    #: The node it goes under or onto ("." for the scene's root).
    node: str
    detail: dict[str, Any] = {}


class Finding(_Model):
    id: str
    category: Category
    #: The scene the thing lives in (res://…), or project.godot.
    scene: str
    node: str
    #: What is missing, in a few words.
    missing: str
    sounds: list[PlannedSound]
    placement: Placement | None
    #: Why Joinery can't place it, when it can't.
    needs_you: str = ""
    #: What the latest playtest saw of it ("picked up 5 times").
    evidence: str = ""
    weight: int = 0


class Audit(_Model):
    level: str
    player: str
    dimension: Literal["3d", "2d"]
    #: The game core's sound nodes are in the project.
    runtime: bool
    runtime_missing: list[str]
    #: Art direction is approved, so sounds may be made.
    approved: bool
    findings: list[Finding]
    #: What already has sound, in words.
    present: list[str]
    #: Floor surfaces of the level, as tagged or guessed.
    surfaces: list[str]
    playtest: dict[str, Any] | None
    headline: str
    categories: list[dict[str, Any]]


def res(path: str) -> str:
    return path if path.startswith("res://") else "res://" + path.lstrip("/")


def rel(path: str) -> str:
    return path.removeprefix("res://")


def _read(project: Path, path: str) -> str:
    target = project / rel(path)
    try:
        return target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _stem(path: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", Path(rel(path)).stem.lower()).strip("_") or "prop"


def _plain(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", re.sub(r"([a-z])([A-Z])", r"\1 \2", text).lower())


# --- Playtest evidence -------------------------------------------------------------


def latest_playtest(project: Path) -> dict[str, Any] | None:
    """The newest playtest report's facts the sound pass ranks by."""
    folder = project / ".joinery" / "playtests"
    reports = sorted(
        folder.glob("*/report.json"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    for path in reports:
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(report, dict):
            continue
        goals = report.get("goals") if isinstance(report.get("goals"), dict) else {}
        assert isinstance(goals, dict)
        return {
            "id": path.parent.name,
            "played_seconds": float(report.get("played_seconds", 0) or 0),
            "levels_started": int(goals.get("levels_started", 0) or 0),
            "pickups": int(goals.get("score_changes", 0) or 0),
            "switches": int(goals.get("world_changes", 0) or 0),
            "checkpoints": int(goals.get("checkpoints", 0) or 0),
            "completed": len(goals.get("completed", []) or []),
            "failed": len(goals.get("failed", []) or [])
            + int(goals.get("respawns", 0) or 0),
        }
    return None


def _evidence(category: str, playtest: dict[str, Any] | None) -> tuple[str, int]:
    if not playtest:
        return "", 0
    played = playtest["played_seconds"]
    minutes = f"{int(played // 60)}:{int(played % 60):02d}"
    if category in {"footsteps", "jump_land"} and played > 0:
        return f"the playtest bot ran and jumped for {minutes}", 3
    if category == "pickups" and playtest["pickups"]:
        count = playtest["pickups"]
        return f"the playtest bot picked up {count} thing{'s' if count != 1 else ''}", 4
    if category == "switches" and playtest["switches"]:
        count = playtest["switches"]
        return (
            f"the playtest bot changed the world {count} time{'s' if count != 1 else ''}",
            2,
        )
    if category == "hits" and playtest["failed"]:
        return f"the playtest bot died or respawned {playtest['failed']} times", 2
    if category == "ambience" and playtest["levels_started"]:
        return f"the level played for {minutes} in silence", 1
    return "", 0


# --- Reading the game ----------------------------------------------------------------


class _Game:
    """The project's scenes and scripts, read once."""

    def __init__(self, project: Path) -> None:
        self.project = project
        self.settings = _read(project, "project.godot")
        self._scenes: dict[str, Scene | None] = {}
        self._scripts: dict[str, str] = {}

    def setting(self, section: str, key: str) -> str:
        return scene_text.unquote(scene_text.get_setting(self.settings, section, key))

    def scene(self, path: str) -> Scene | None:
        path = res(path)
        if path not in self._scenes:
            text = _read(self.project, path)
            self._scenes[path] = Scene(text) if text.strip() else None
        return self._scenes[path]

    def script(self, path: str) -> str:
        if not path:
            return ""
        path = res(path)
        if path not in self._scripts:
            self._scripts[path] = _read(self.project, path)
        return self._scripts[path]

    def signals(self, path: str) -> list[str]:
        return _SIGNAL.findall(self.script(path))

    def functions(self, path: str) -> list[str]:
        return _FUNC.findall(self.script(path))


def _spec(**fields: Any) -> sound_kit.SoundSpec:
    return sound_kit.SoundSpec.model_validate(fields)


def _sound(spec: sound_kit.SoundSpec) -> PlannedSound:
    return PlannedSound(
        kind=spec.kind,
        name=spec.name,
        surface=spec.surface,
        variant=spec.variant,
        export=spec.export_path(),
        tool=spec.tool,
        words=spec.describe(),
    )


def _cues(scene: Scene, node: str) -> list[dict[str, str]]:
    """Sound nodes directly under ``node``: SoundCues and audio players."""
    found: list[dict[str, str]] = []
    for child in scene.children(node):
        script = scene.script_of(child)
        props = child.props()
        if script == SOUND_CUE:
            found.append(
                {
                    "trigger": scene_text.unquote(props.get("trigger", "")),
                    "path": child.path,
                }
            )
        elif child.type.startswith("AudioStreamPlayer"):
            found.append({"trigger": "*", "path": child.path})
    return found


def _material_words(scene: Scene, node: Node) -> tuple[str, list[float] | None]:
    """A floor's material name and albedo (0-1), from its sub-resource."""
    props = node.props()
    for key in ("material", "material_override", "surface_material_override/0"):
        if key not in props:
            continue
        section = scene.sub_of(props[key])
        if section is None:
            path = scene.ext_path(props[key])
            return _plain(Path(path).stem), None
        values = section.props()
        words = " ".join(
            [section.attr("id"), scene_text.unquote(values.get("resource_name", ""))]
        )
        colour = _numbers(values.get("albedo_color", ""))
        albedo = colour[:3] if len(colour) >= 3 else None
        return _plain(words), albedo
    return "", None


def guess_surface(
    name: str, parent: str, material: str, albedo: list[float] | None
) -> str:
    """A floor's surface from its names; a green albedo reads as grass."""
    words = " ".join(_plain(x) for x in (name, material, parent)).split()
    for surface, keys in SURFACE_WORDS.items():
        if any(word.startswith(key) for key in keys for word in words):
            return surface
    if albedo is not None:
        r, g, b = albedo
        if g > r * 1.1 and g > b * 1.1:
            return "grass"
        if b > r * 1.25 and b > g * 1.05 and b > 0.45 and "water" in words:
            return "water"
    return "default"


def floors(scene: Scene, dimension: str) -> list[dict[str, str]]:
    """Walkable bodies of the level: path, surface, and whether it is tagged."""
    found: list[dict[str, str]] = []
    types = _FLOOR_TYPES_3D if dimension == "3d" else _FLOOR_TYPES_2D
    for node in scene.nodes():
        if node.type not in types or node.path == ".":
            continue
        props = node.props()
        if node.type.startswith("CSG") and props.get("use_collision") != "true":
            continue
        if any(
            word in _plain(node.name).split() or _plain(node.name).startswith(word)
            for word in _NOT_FLOORS
        ):
            continue
        if "metadata/surface" in props:
            found.append(
                {
                    "path": node.path,
                    "surface": scene_text.unquote(props["metadata/surface"]),
                    "tagged": "yes",
                }
            )
            continue
        material, albedo = _material_words(scene, node)
        parent = node.parent if node.parent != "." else ""
        found.append(
            {
                "path": node.path,
                "surface": guess_surface(node.name, parent, material, albedo),
                "tagged": "",
            }
        )
    return found


def _origin(scene: Scene, node: Node) -> tuple[float, float, float]:
    """The node's position in the scene, adding its parents' (rotation ignored)."""
    x = y = z = 0.0
    current: Node | None = node
    while current is not None and current.path != ".":
        values = current.props()
        numbers = _numbers(values.get("transform", ""))
        if len(numbers) >= 12:
            x, y, z = x + numbers[9], y + numbers[10], z + numbers[11]
        elif "position" in values:
            point = _numbers(values["position"])
            if len(point) >= 3:
                x, y, z = x + point[0], y + point[1], z + point[2]
        current = scene.node(current.parent) if current.parent else None
    return x, y, z


def bounds(scene: Scene) -> dict[str, list[float]]:
    """The level's extent, estimated from box sizes and node positions, padded."""
    low = [float("inf")] * 3
    high = [float("-inf")] * 3
    for node in scene.nodes():
        if node.path == ".":
            continue
        props = node.props()
        size_text = props.get("size", "")
        if not size_text and "shape" in props:
            shape = scene.sub_of(props["shape"])
            if shape is not None:
                size_text = shape.props().get("size", "")
        size = _numbers(size_text)[:3]
        if len(size) != 3:
            size = [0.0, 0.0, 0.0]
        centre = _origin(scene, node)
        if not any(centre) and not any(size):
            continue
        for axis in range(3):
            low[axis] = min(low[axis], centre[axis] - size[axis] / 2)
            high[axis] = max(high[axis], centre[axis] + size[axis] / 2)
    if low[0] == float("inf"):
        return {"centre": [0.0, 5.0, 0.0], "size": [80.0, 30.0, 80.0]}
    pad = [4.0, 0.0, 4.0]
    low = [low[0] - pad[0], low[1] - 4.0, low[2] - pad[2]]
    high = [high[0] + pad[0], high[1] + 10.0, high[2] + pad[2]]
    return {
        "centre": [round((low[i] + high[i]) / 2, 2) for i in range(3)],
        "size": [round(high[i] - low[i], 2) for i in range(3)],
    }


def ambience_kind(scene: Scene, surfaces: list[str]) -> str:
    words = " ".join(_plain(n.name) for n in scene.nodes()).split()
    if any(
        w.startswith(k)
        for w in words
        for k in ("cave", "dungeon", "crypt", "mine", "tunnel")
    ):
        return "cave"
    if any(
        w.startswith(k)
        for w in words
        for k in ("tree", "forest", "wood", "bush", "leaf", "leaves")
    ):
        return "forest"
    outdoor = any(
        n.type in {"WorldEnvironment", "DirectionalLight3D"} for n in scene.nodes()
    ) and any(
        s.kind == "sub_resource" and s.attr("type") in {"Sky", "ProceduralSkyMaterial"}
        for s in scene.sections
    )
    if (
        any(
            w.startswith(k)
            for w in words
            for k in ("room", "interior", "house", "office", "hall")
        )
        and not outdoor
    ):
        return "room_tone"
    return "wind" if outdoor or "grass" in surfaces else "room_tone"


# --- The audit -----------------------------------------------------------------------


def _player_body(game: _Game, scene: Scene | None) -> Node | None:
    if scene is None:
        return None
    for node in scene.nodes():
        if node.type in {"CharacterBody3D", "CharacterBody2D"}:
            return node
    return None


def audit(project: Path, scene: str = "") -> Audit:
    """What the level and its player are missing, ranked."""
    game = _Game(project)
    level_path = (
        res(scene) if scene else res(game.setting("game", "hooks/level_scene") or "")
    )
    player_path = (
        res(game.setting("game", "hooks/player_scene") or "")
        if game.setting("game", "hooks/player_scene")
        else ""
    )
    level = game.scene(level_path) if level_path != "res://" else None
    if level is None:
        raise ValueError(
            "No level scene to listen to: set game/hooks/level_scene, or name one with --scene."
        )
    player = game.scene(player_path) if player_path else None
    body = _player_body(game, player)
    if body is None:
        # A player placed in the level itself.
        for node in level.nodes():
            if (
                node.type in {"CharacterBody3D", "CharacterBody2D"}
                and "player" in node.groups
            ):
                body, player, player_path = node, level, level_path
                break
    root = level.node(".")
    dimension: Literal["3d", "2d"] = (
        "2d"
        if (
            (body is not None and body.type.endswith("2D"))
            or (root is not None and root.type.endswith("2D"))
        )
        else "3d"
    )
    playtest = latest_playtest(project)
    findings: list[Finding] = []
    present: list[str] = []

    def add(finding: Finding) -> None:
        words, weight = _evidence(finding.category, playtest)
        finding.evidence = words
        finding.weight = weight
        findings.append(finding)

    # Floors and footsteps.
    level_floors = floors(level, dimension)
    surfaces = sorted(
        {"default", *(f["surface"] for f in level_floors)}, key=sound_kit.SURFACES.index
    )
    untagged = {
        f["path"]: f["surface"]
        for f in level_floors
        if not f["tagged"] and f["surface"] != "default"
    }
    if untagged:
        add(
            Finding(
                id=f"surfaces:{rel(level_path)}",
                category="footsteps",
                scene=level_path,
                node=".",
                missing=f"{len(untagged)} floor{'s' if len(untagged) != 1 else ''} without a surface",
                sounds=[],
                placement=Placement(
                    file=rel(level_path),
                    what="surfaces",
                    node=".",
                    detail={"floors": untagged},
                ),
            )
        )
    if body is not None and player is not None:
        steps_node = next(
            (c for c in player.children(body.path) if player.script_of(c) == FOOTSTEPS),
            None,
        )
        have: dict[str, str] = {}
        props: dict[str, str] = {}
        if steps_node is not None:
            props = steps_node.props()
            have = scene_text.dictionary_keys(props.get("surfaces", ""))
        missing = [s for s in surfaces if s not in have]
        anchor = steps_node.path if steps_node is not None else body.path
        if missing:
            add(
                Finding(
                    id=f"footsteps:{rel(player_path)}",
                    category="footsteps",
                    scene=player_path,
                    node=body.path,
                    missing=(
                        "No footsteps"
                        if not have
                        else "No footsteps on " + ", ".join(missing)
                    ),
                    sounds=[
                        _sound(_spec(kind="footstep", name=f"step_{s}", surface=s))
                        for s in missing
                    ],
                    placement=Placement(
                        file=rel(player_path),
                        what="footsteps",
                        node=anchor,
                        detail={
                            "surfaces": missing,
                            "existing": steps_node is not None,
                        },
                    ),
                )
            )
        else:
            present.append(
                f"Footsteps on {len(have)} surface{'s' if len(have) != 1 else ''}"
            )
        jumpers = [k for k in ("jump", "land") if k not in props]
        if jumpers:
            add(
                Finding(
                    id=f"jump_land:{rel(player_path)}",
                    category="jump_land",
                    scene=player_path,
                    node=body.path,
                    missing="No " + " or ".join(jumpers) + " sound",
                    sounds=[_sound(_spec(kind=k, name=f"player_{k}")) for k in jumpers],
                    placement=Placement(
                        file=rel(player_path),
                        what="footsteps",
                        node=anchor,
                        detail={
                            "jump_land": jumpers,
                            "existing": steps_node is not None,
                        },
                    ),
                )
            )
        else:
            present.append("Jump and land")

    # Props: scenes instanced in the level, and scripted nodes of its own.
    seen_scenes: set[str] = set()
    for node in level.nodes():
        if node.path == ".":
            continue
        if node.instance and node.instance.endswith(".tscn"):
            if node.instance in seen_scenes or node.instance == player_path:
                continue
            seen_scenes.add(node.instance)
            prop = game.scene(node.instance)
            if prop is None:
                continue
            prop_root = prop.node(".")
            if prop_root is None:
                continue
            for item in _prop_findings(
                game, prop, node.instance, prop_root, node.groups
            ):
                add(item)
        elif level.script_of(node):
            for item in _prop_findings(game, level, level_path, node, node.groups):
                add(item)

    # Ambience.
    has_ambience = any(
        level.script_of(n) == AMBIENCE_ZONE
        or (
            n.type.startswith("AudioStreamPlayer")
            and scene_text.unquote(n.props().get("bus", "")) == "Ambience"
        )
        for n in level.nodes()
    )
    if has_ambience:
        present.append("Ambience")
    else:
        kind = ambience_kind(level, surfaces)
        layer = "2"
        if body is not None:
            layer = body.props().get("collision_layer", "1")
        add(
            Finding(
                id=f"ambience:{rel(level_path)}",
                category="ambience",
                scene=level_path,
                node=".",
                missing="The level is silent between events",
                sounds=[_sound(_spec(kind=kind, name=f"{_stem(level_path)}_{kind}"))],
                placement=Placement(
                    file=rel(level_path),
                    what="ambience_zone" if dimension == "3d" else "ambience_player",
                    node=".",
                    detail={"bounds": bounds(level), "mask": layer},
                ),
            )
        )

    # UI.
    missing_ui = [
        key for key in ("click", "hover") if not game.setting("audio", f"ui/{key}")
    ]
    if missing_ui:
        add(
            Finding(
                id="ui:project.godot",
                category="ui",
                scene="project.godot",
                node="",
                missing="Buttons make no sound on " + " or ".join(missing_ui),
                sounds=[
                    _sound(_spec(kind=f"ui_{k}", name=f"ui_{k}")) for k in missing_ui
                ],
                placement=Placement(
                    file="project.godot",
                    what="ui_settings",
                    node="",
                    detail={"keys": missing_ui},
                ),
            )
        )
    else:
        present.append("UI clicks")

    order = list(CATEGORIES)
    findings.sort(key=lambda f: (-f.weight, order.index(f.category), f.scene, f.node))
    runtime_missing = [p for p in RUNTIME if not (project / rel(p)).is_file()]
    categories = []
    for category, label in CATEGORIES.items():
        items = [f for f in findings if f.category == category]
        categories.append(
            {
                "id": category,
                "label": label,
                "missing": sum(len(f.sounds) for f in items),
                "findings": len(items),
                "placeable": all(f.placement is not None for f in items),
            }
        )
    return Audit(
        level=level_path,
        player=player_path,
        dimension=dimension,
        runtime=not runtime_missing,
        runtime_missing=runtime_missing,
        approved=approved(project),
        findings=findings,
        present=present,
        surfaces=surfaces,
        playtest=playtest,
        headline=headline(findings, level_path),
        categories=categories,
    )


def headline(findings: list[Finding], level: str) -> str:
    count = sum(len(f.sounds) for f in findings)
    name = Path(rel(level)).name
    if count == 0:
        return (
            f"Nothing missing in {name}"
            if not findings
            else f"{len(findings)} things to place in {name}"
        )
    return f"{count} sound{'s' if count != 1 else ''} missing in {name}"


def _prop_findings(
    game: _Game, scene: Scene, path: str, node: Node, groups: list[str]
) -> list[Finding]:
    """A prop's pickup, switch or hit that has no sound yet."""
    script = scene.script_of(node)
    signals = game.signals(script)
    functions = game.functions(script)
    all_groups = {g.lower() for g in (*node.groups, *groups)}
    cues = _cues(scene, node.path)
    triggers = {c["trigger"] for c in cues}
    stem = _stem(path) if node.path == "." else _stem(node.name)
    owner = res(path)
    results: list[Finding] = []
    file = rel(path)

    def cue(
        category: Category, kind: str, trigger: str, only_group: str, missing: str
    ) -> Finding:
        name = stem if kind in stem.split("_") else f"{kind}_{stem}"
        return Finding(
            id=f"{category}:{file}:{node.path}:{trigger}",
            category=category,
            scene=owner,
            node=node.path,
            missing=missing,
            sounds=[_sound(_spec(kind=kind, name=name))],
            placement=Placement(
                file=file,
                what="sound_cue",
                node=node.path,
                detail={"trigger": trigger, "only_group": only_group},
            ),
        )

    if "*" in triggers:
        return results
    is_pickup = (
        bool(all_groups & PICKUP_GROUPS)
        or any(s in signals for s in PICKUP_SIGNALS)
        or ("collect" in functions)
    )
    if is_pickup and "checkpoint" not in all_groups:
        if node.type in {"Area3D", "Area2D"}:
            trigger, only = "body_entered", "player"
        else:
            trigger = next((s for s in PICKUP_SIGNALS if s in signals), "")
            only = ""
        if trigger and trigger not in triggers:
            results.append(
                cue(
                    "pickups",
                    "pickup",
                    trigger,
                    only,
                    f"{node.name} is picked up in silence",
                )
            )
        elif not trigger:
            results.append(
                Finding(
                    id=f"pickups:{file}:{node.path}",
                    category="pickups",
                    scene=owner,
                    node=node.path,
                    missing=f"{node.name} is picked up in silence",
                    sounds=[],
                    placement=None,
                    needs_you="its script has no signal to play a sound on",
                )
            )
        return results
    switch = next((s for s in SWITCH_SIGNALS if s in signals), "")
    if switch and switch not in triggers:
        words = _plain(f"{node.name} {Path(rel(path)).stem}")
        kind = "door" if switch in {"opened", "closed"} else "button"
        for key, value in (
            ("door", "door"),
            ("gate", "door"),
            ("lever", "lever"),
            ("switch", "lever"),
            ("plate", "plate"),
            ("button", "button"),
        ):
            if key in words:
                kind = value
                break
        if switch in {"pulled", "switched"} and kind == "button":
            kind = "lever"
        results.append(
            cue("switches", kind, switch, "", f"{node.name} works without a sound")
        )
    hit = next((s for s in HIT_SIGNALS if s in signals), "")
    if hit and hit not in triggers:
        kind = "damage" if "player" in all_groups else "hit"
        results.append(cue("hits", kind, hit, "", f"{node.name} takes hits in silence"))
    elif not hit and any(f in functions for f in HIT_FUNCTIONS):
        results.append(
            Finding(
                id=f"hits:{file}:{node.path}",
                category="hits",
                scene=owner,
                node=node.path,
                missing=f"{node.name} takes hits in silence",
                sounds=[],
                placement=None,
                needs_you="it has a damage function but no signal to play a sound on",
            )
        )
    return results


# --- CLI -------------------------------------------------------------------------------


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--plan", action="store_true", help="also list the changes it would start"
    )
    parser.add_argument(
        "--scene",
        default="",
        help="the level scene (res://…); default: game/hooks/level_scene",
    )


def main(args: argparse.Namespace) -> int:
    from joinery import sound_plan

    project = args.project.resolve()
    try:
        found = audit(project, args.scene)
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}) if args.json else str(exc))
        return 1
    planned = sound_plan.plan(project, found) if args.plan else []
    if args.json:
        data = found.model_dump()
        if args.plan:
            data["plan"] = [task.model_dump() for task in planned]
        print(json.dumps(data))
        return 0
    print(found.headline)
    for finding in found.findings:
        sounds = ", ".join(s.name for s in finding.sounds) or "-"
        where = (
            finding.placement.what
            if finding.placement
            else "needs you: " + finding.needs_you
        )
        extra = f" ({finding.evidence})" if finding.evidence else ""
        print(
            f"  {CATEGORIES[finding.category]:<18} {rel(finding.scene)} {finding.node}: {finding.missing}; make {sounds}; {where}{extra}"
        )
    if found.present:
        print("  Already sounding: " + ", ".join(found.present))
    if not found.runtime:
        print(
            "  The game core has no sound nodes yet: "
            + ", ".join(found.runtime_missing)
        )
    for task in planned:
        print(f"  change: {task.title} ({', '.join(task.apps)})")
    return 0
