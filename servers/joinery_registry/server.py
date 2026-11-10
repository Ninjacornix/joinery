"""Actual read-only MCP source and provenance lookup for a bound project.

Besides sources and assets, the registry gives the agent the project's
direction: the art bible (`art_direction`), the game brief (`game_brief`) and
how the game is put together (`game_setup`). Every tool here only reads; the
model has no approval, feature or brief-writing operation.

The starting-point catalogue (`joinery.blueprints`) and the bible lint
(`joinery.bible_lint`) are optional imports: a Joinery without them still
serves every tool, with the parts they would add reported as unavailable.
"""

from __future__ import annotations

import argparse
import importlib
import json
import tomllib
from collections.abc import Mapping
from pathlib import Path, PurePath
from types import ModuleType
from typing import Any, cast

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from joinery import brief, parts
from joinery.art_approval import approved, fingerprint
from joinery.workspace import index
from servers.errors import tool_errors

#: How a game made from a starting point is meant to be extended (the game
#: core's public surface). The agent builds on these instead of editing the
#: files under `core/`, which belong to the starting point and its features.
EXTENSION_POINTS: dict[str, Any] = {
    "rule": (
        "Extend the game through these autoloads, groups and scene hooks. Put new "
        "game code under res://game/; never edit res://core/ or res://features/, "
        "which the starting point and features own and may update."
    ),
    "autoloads": {
        "Settings": "get_value(section, key, default), set_value(section, key, value); signal changed(section, key). Saved to user://settings.cfg.",
        "GameEvents": "Signals only: level_started(level), level_completed(result), level_failed(reason), checkpoint_reached(id), player_spawned(player, peer_id), score_changed(value), paused_changed(paused), menu_opened(menu), world_changed(node).",
        "Menus": "add_entry(menu &'main'|&'pause', id, label, action: Callable, order, enabled: Callable), remove_entry, add_settings_page(id, label, factory: Callable -> Control, order), open_main/open_pause/open_settings/open_page/close_all, toast(text, seconds).",
        "Game": "start_game(options), restart_level(), quit_to_menu(), level_ready(level), spawn(scene, parent, data) -> Node, request_action(target, action, args), actor_id(), players(), local_player(), current_level(), capture_state()/apply_state(nodes), pending_state.",
        "GameSession": "Offline by default (is_online false, can_pause true); online play swaps in a network session behind the same calls.",
    },
    "scenes": {
        "level": "The level_scene root calls Game.level_ready(self) in _ready, has a Players child, and one or more Marker2D/Marker3D in group player_spawn.",
        "player": "Reads input only when is_multiplayer_authority(); const SYNC_PROPERTIES: Array[String]; in group saveable with save_id, save_state() and load_state(state).",
        "world_objects": "State that matters (coins, doors, switches, enemies, cards) lives in group saveable with a unique save_id, changes only via Game.request_action(self, &'verb', args), then emits GameEvents.world_changed(self). Moving server-simulated objects are also in group net_synced.",
        "level_completed": "result has time (s) and score (int), plus starting-point keys.",
    },
    "input": "Use the standard action names (move_left, move_right, move_up, move_down, move_forward, move_back, look_*, jump, interact, attack, dash, sprint, crouch, restart, pause) so features such as controller support bind them.",
    "strings": "Every user-visible string goes through tr() or a Control's auto-translate; never build sentences by concatenation.",
    "visuals": "Until the art bible is approved and assets are made with the tools, the game stays greybox: primitive meshes, CSG, Polygon2D, ColorRect, StyleBoxFlat.",
}
FEATURE_ACTION = (
    "Adding or removing a feature is a user action (/feature add <id>, /feature "
    "remove <id>, or the CLI), reviewed as a before/after. Suggest it in one line "
    "when it is relevant; never edit joinery.toml or apply it yourself."
)


def _optional(name: str) -> ModuleType | None:
    """Import an optional Joinery module, or None when this Joinery lacks it."""
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name == name:
            return None
        raise


def _toml_game(project: Path) -> dict[str, Any] | None:
    """The `[game]` record of joinery.toml, read directly."""
    path = project / "joinery.toml"
    if not path.is_file():
        return None
    try:
        game = tomllib.loads(path.read_text(encoding="utf-8")).get("game")
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"joinery.toml is not valid TOML: {exc}") from exc
    return dict(game) if isinstance(game, dict) else None


def project_game(project: Path) -> dict[str, Any] | None:
    """`blueprints.project_game`, or the bare `[game]` record without it."""
    blueprints = _optional("joinery.blueprints")
    if blueprints is not None:
        return cast(dict[str, Any] | None, blueprints.project_game(project))
    return _toml_game(project)


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, default=_plain))


def _plain(value: Any) -> str:
    """Project paths read the same on every platform: forward slashes."""
    if isinstance(value, PurePath):
        return value.as_posix()
    return str(value)


def _hooks(project: Path) -> dict[str, str]:
    """The `[game]` settings the starting point wrote into project.godot."""
    path = project / "project.godot"
    if not path.is_file():
        return {}
    raw = brief.godot_section(path.read_text(encoding="utf-8"), "game")
    hooks: dict[str, str] = {}
    for key, value in raw.items():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = value
        hooks[key] = parsed if isinstance(parsed, str) else value
    return hooks


def _list(value: object) -> list[str]:
    if isinstance(value, list | tuple):
        return [str(item) for item in value]
    return []


def addable(
    catalogue: Mapping[str, Any], game: Mapping[str, Any] | None
) -> dict[str, Any]:
    """What can still be added to the project, and what cannot and why."""
    blueprint = str(game.get("blueprint") or "") if game else ""
    applied = set(_list(game.get("features"))) if game else set()
    features = [f for f in catalogue.get("features", []) if isinstance(f, Mapping)]
    conflicts_with = {
        str(f.get("id")): set(_list(f.get("conflicts"))) for f in features
    }
    can_add: list[dict[str, Any]] = []
    blocked: list[dict[str, str]] = []
    for feature in features:
        feature_id = str(feature.get("id"))
        if feature_id in applied:
            continue
        supported = _list(feature.get("blueprints")) or ["*"]
        if blueprint and "*" not in supported and blueprint not in supported:
            blocked.append(
                {"id": feature_id, "reason": f"not available for {blueprint}"}
            )
            continue
        clash = sorted(
            (conflicts_with.get(feature_id, set()) & applied)
            | {a for a in applied if feature_id in conflicts_with.get(a, set())}
        )
        if clash:
            blocked.append(
                {"id": feature_id, "reason": "conflicts with " + ", ".join(clash)}
            )
            continue
        resolved = _list(game.get("can_add")) if game and "can_add" in game else None
        if resolved is not None and feature_id not in resolved:
            # The engine resolves the whole set (transitive requires, setting
            # clashes); trust its verdict over this quick check.
            blocked.append(
                {
                    "id": feature_id,
                    "reason": "does not resolve with the applied features",
                }
            )
            continue
        entry = dict(feature)
        also = [r for r in _requires(features, feature_id) if r not in applied]
        if also:
            entry["would_also_add"] = also
        can_add.append(entry)
    result: dict[str, Any] = {"features": can_add, "blocked": blocked}
    if game is None or not blueprint:
        result["blueprints"] = [
            {key: b[key] for key in _BLUEPRINT_KEYS if key in b}
            for b in catalogue.get("blueprints", [])
            if isinstance(b, Mapping)
        ]
    return result


_BLUEPRINT_KEYS = (
    "id",
    "name",
    "pitch",
    "dimension",
    "default_features",
    "features_supported",
)


def _requires(features: list[Mapping[str, Any]], feature_id: str) -> list[str]:
    """Every feature `feature_id` pulls in, transitively, in discovery order."""
    by_id = {str(f.get("id")): f for f in features}
    found: list[str] = []
    stack = list(reversed(_list(by_id.get(feature_id, {}).get("requires"))))
    while stack:
        current = stack.pop()
        if current in found or current == feature_id:
            continue
        found.append(current)
        stack += reversed(_list(by_id.get(current, {}).get("requires")))
    return found


def make_server(project: Path) -> MCPServer[Any]:
    project = project.resolve()
    if not (project / "project.godot").is_file():
        raise ValueError("Expected a Godot project.")
    server: MCPServer[Any] = MCPServer("joinery-registry")

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    @tool_errors
    async def art_direction() -> dict[str, Any]:
        """Read the project's actual art bible, its user approval status, and a lint of the bible (problems, open sections, reference board, suggestions). The model has no approval operation."""
        path = project / "art/bible.md"
        lint: dict[str, Any] | None = None
        bible_lint = _optional("joinery.bible_lint")
        if bible_lint is not None and path.is_file():
            try:
                lint = _json_safe(bible_lint.summary(project))
            except (ValueError, OSError) as exc:
                lint = {"error": str(exc)}
        return {
            "approved": approved(project),
            "sha256": fingerprint(project) if path.is_file() else "",
            "text": path.read_text()
            if path.is_file()
            else "No art direction has been written for this project.",
            "approval_action": "In chat, type /art and click Approve this version. Saving or accepting a draft does not approve it.",
            "lint": lint,
        }

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    @tool_errors
    async def game_brief() -> dict[str, Any]:
        """Read the project's game brief (design/brief.md): pitch, loops, verbs, controls, platforms, online, saving, scope; its machine-readable facts; open sections; and one-line suggestions where the brief and the game's setup disagree. Offer a suggestion only when it is relevant to the user's request; never act on one unasked."""
        data = brief.summary(project, project_game(project))
        data["edit_hint"] = brief.EDIT_HINT
        return cast(dict[str, Any], _json_safe(data))

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    @tool_errors
    async def game_setup() -> dict[str, Any]:
        """Read how the game is put together: its starting point and features, what features can still be added (and what cannot, and why), and the extension points to build on instead of editing core files. Features are added by the user, never by the model."""
        game = project_game(project)
        blueprints = _optional("joinery.blueprints")
        result: dict[str, Any] = {
            "game": game,
            "hooks": _hooks(project),
            "extension_points": EXTENSION_POINTS,
            "feature_action": FEATURE_ACTION,
        }
        if blueprints is None:
            result["catalogue"] = (
                "The starting-point catalogue is not part of this Joinery."
            )
            result["can_add"] = {"features": [], "blocked": []}
        else:
            catalogue = cast(Mapping[str, Any], blueprints.catalogue_json())
            result["catalogue"] = "available"
            result["can_add"] = addable(catalogue, game)
            result["presets"] = list(catalogue.get("presets", []))
        return cast(dict[str, Any], _json_safe(result))

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    @tool_errors
    async def list_assets() -> dict[str, Any]:
        """List actual project assets, their source documents, owners and recipes."""
        return index(project)

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    @tool_errors
    async def resolve_source(
        resource: str, part: dict[str, str] | None = None
    ) -> dict[str, Any]:
        """Resolve a Godot resource path to its native source and owning application. With a part (a selection target, or the fields object/material/image/bone/shape_key; a part id after '#' in the resource works too), also check that part exists in the source and return which tool owns it."""
        base, _, fragment = resource.partition("#")
        relative = base.removeprefix("res://").split("::", 1)[0]
        if not (project / relative).resolve().is_relative_to(project):
            raise ValueError("Resource is outside this project.")
        wanted = {str(k): str(v) for k, v in (part or {}).items()}
        if fragment:
            wanted = {**parts.parse_part_id("#" + fragment)[1], **wanted}
        data = index(project)
        for asset in data.get("assets", []):
            if asset["path"] == relative:
                found = dict(asset)
                if any(wanted.get(key) for key in parts.ID_FIELDS):
                    found["part"] = await _resolve_part(relative, wanted)
                return found
        raise ValueError(
            "The resource is not registered in this project. Use list_assets to find existing sources; do not inspect a document before creating it."
        )

    async def _resolve_part(relative: str, wanted: dict[str, str]) -> dict[str, str]:
        report = parts.cached_report(project, relative)
        if report is None:
            report = await parts.report(project, relative)
        target = report.find(wanted).target()
        if wanted.get("object") and not target["object"]:
            # A map picked on a surface keeps the object it was picked on.
            target["object"] = wanted["object"]
        return target

    return server


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    arguments = parser.parse_args()
    make_server(arguments.project).run()
