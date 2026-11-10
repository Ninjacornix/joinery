"""The registry gives the agent the brief, the game setup and the bible lint, read-only."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest
from mcp import Client

from joinery import brief
from joinery.read_access import read_only
from servers.joinery_registry import server as registry
from servers.joinery_registry.server import make_server

PLATFORMER: dict[str, Any] = {
    "id": "platformer-2d",
    "name": "2D platformer",
    "dimension": "2d",
    "brief": {"genre": ["platformer"], "core_loop": "30 s: jump. 5 min: flag."},
    "input": {"jump": {"keys": ["Space"], "mouse": [], "pad": ["0"]}},
}

CATALOGUE: dict[str, Any] = {
    "blueprints": [{"id": "blank"}, {"id": "platformer-2d"}, {"id": "top-down"}],
    "features": [
        {
            "id": "controller",
            "name": "Controller",
            "requires": [],
            "conflicts": [],
            "blueprints": ["*"],
        },
        {
            "id": "save-load",
            "name": "Save and load",
            "requires": [],
            "conflicts": [],
            "blueprints": ["*"],
        },
        {
            "id": "online-play",
            "name": "Online play",
            "requires": [],
            "conflicts": [],
            "blueprints": ["*"],
        },
        {
            "id": "steam",
            "name": "Steam",
            "requires": [],
            "conflicts": [],
            "enhances": ["online-play"],
            "blueprints": ["*"],
        },
        {
            "id": "twin-stick",
            "name": "Twin stick",
            "requires": [],
            "conflicts": [],
            "blueprints": ["top-down"],
        },
        {
            "id": "mouse-only",
            "name": "Mouse only",
            "requires": [],
            "conflicts": ["controller"],
            "blueprints": ["*"],
        },
        {
            "id": "cloud-saves",
            "name": "Cloud saves",
            "requires": ["save-load", "steam"],
            "conflicts": [],
            "blueprints": ["*"],
        },
    ],
    "presets": [{"id": "none", "name": "None"}, {"id": "pixel-2d", "name": "Pixel 2D"}],
}


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "project.godot").write_text(
        'config_version=5\n\n[application]\n\nconfig/name="Rooftops"\n\n'
        '[game]\n\nblueprint="platformer-2d"\nhooks/level_scene="res://game/level.tscn"\n'
        'hooks/player_scene="res://game/player/player.tscn"\n'
    )
    (tmp_path / "joinery.toml").write_text(
        '[tools]\nenabled = ["godot"]\n\n[game]\nblueprint = "platformer-2d"\n'
        'blueprint_version = 1\nfeatures = ["controller", "save-load"]\n'
        "feature_versions = { controller = 1, save-load = 1 }\n"
    )
    (tmp_path / "joinery.lock").write_text("version=1\n")
    (tmp_path / "art").mkdir()
    return tmp_path


def call(project: Path, tool: str, arguments: dict[str, Any] | None = None) -> Any:
    async def run() -> Any:
        async with Client(make_server(project)) as client:
            return await client.call_tool(tool, arguments or {})

    result = asyncio.run(run())
    assert not result.is_error, result.content[0].text
    return json.loads(result.content[0].text)


@pytest.fixture
def no_optional(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Joinery without the starting-point catalogue or the bible lint."""
    real = registry._optional
    monkeypatch.setattr(
        registry,
        "_optional",
        lambda name: (
            None if name in {"joinery.blueprints", "joinery.bible_lint"} else real(name)
        ),
    )


@pytest.fixture
def fake_blueprints(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    module = types.ModuleType("joinery.blueprints")

    def project_game(project: Path) -> dict[str, Any] | None:
        record = registry._toml_game(project)
        if record is not None:
            record = {**record, "dimension": "2d", "manifest": {"units": 3}}
        return record

    module.project_game = project_game  # type: ignore[attr-defined]
    module.catalogue_json = lambda: CATALOGUE  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "joinery.blueprints", module)
    return module


def test_every_tool_stays_read_only_and_none_adds_features(project: Path) -> None:
    async def tools() -> list[Any]:
        async with Client(make_server(project)) as client:
            return list((await client.list_tools()).tools)

    listed = asyncio.run(tools())
    names = {tool.name for tool in listed}
    assert {"game_brief", "game_setup", "art_direction"} <= names
    assert all(tool.annotations.read_only_hint for tool in listed)
    # The hook and gateway judge reads by the shared list, not by the hint:
    # a registry tool missing from it would be refused during inspection.
    assert all(read_only("joinery-registry", name) for name in names), names
    assert not any(
        word in tool.name
        for tool in listed
        for word in ("approve", "accept", "add", "remove", "write")
    )


# --------------------------------------------------------------------------
# game_brief


def test_game_brief_without_a_brief_offers_to_draft_one(
    project: Path, no_optional: None
) -> None:
    data = call(project, "game_brief")
    assert data["exists"] is False
    assert data["edit_hint"] == brief.EDIT_HINT
    assert "/brief" in data["suggestions"][0]


def test_game_brief_compares_facts_with_the_toml_record(
    project: Path, no_optional: None
) -> None:
    brief.write_brief(
        project,
        {"pitch": "Hop."},
        PLATFORMER,
        ["online-play", "controller", "save-load"],
    )
    before = {p: p.read_bytes() for p in project.rglob("*") if p.is_file()}
    data = call(project, "game_brief")
    assert data["exists"] is True
    assert data["facts"]["online"] == "lan"
    assert "Hop." in data["text"]
    assert any("/feature add online-play" in line for line in data["suggestions"])
    assert data["suggestions"][0].startswith("The brief says LAN play")
    assert {p: p.read_bytes() for p in project.rglob("*") if p.is_file()} == before


def test_game_brief_uses_the_catalogue_record_when_present(
    project: Path, fake_blueprints: types.ModuleType
) -> None:
    text = brief.draft(
        {}, {**PLATFORMER, "dimension": "3d"}, ["controller", "save-load"]
    )
    (project / "design").mkdir()
    (project / "design/brief.md").write_text(text)
    data = call(project, "game_brief")
    assert any("3D" in line and "2D" in line for line in data["suggestions"])


def test_game_brief_reports_a_broken_facts_block(
    project: Path, no_optional: None
) -> None:
    (project / "design").mkdir()
    (project / "design/brief.md").write_text(
        '# Brief\n\n## 19. Facts (checked)\n\n```toml\nonline = "mmo"\n```\n'
    )
    data = call(project, "game_brief")
    assert data["facts"] is None
    assert data["problems"] and "online" in data["problems"][0]


# --------------------------------------------------------------------------
# game_setup


def test_game_setup_without_the_catalogue(project: Path, no_optional: None) -> None:
    data = call(project, "game_setup")
    assert data["game"]["blueprint"] == "platformer-2d"
    assert data["game"]["features"] == ["controller", "save-load"]
    assert data["catalogue"].startswith("The starting-point catalogue is not part")
    assert data["can_add"] == {"features": [], "blocked": []}
    assert data["hooks"]["hooks/level_scene"] == "res://game/level.tscn"
    assert "Game" in data["extension_points"]["autoloads"]
    assert "never edit res://core/" in data["extension_points"]["rule"]
    assert "user action" in data["feature_action"]


def test_game_setup_lists_only_what_can_still_be_added(
    project: Path, fake_blueprints: types.ModuleType
) -> None:
    data = call(project, "game_setup")
    assert data["catalogue"] == "available"
    assert data["game"]["manifest"] == {"units": 3}
    can_add = {f["id"]: f for f in data["can_add"]["features"]}
    blocked = {b["id"]: b["reason"] for b in data["can_add"]["blocked"]}
    assert set(can_add) == {"online-play", "steam", "cloud-saves"}
    assert can_add["cloud-saves"]["would_also_add"] == ["steam"]
    assert "would_also_add" not in can_add["steam"]
    assert blocked == {
        "twin-stick": "not available for platformer-2d",
        "mouse-only": "conflicts with controller",
    }
    assert "blueprints" not in data["can_add"]  # a starting point is already chosen
    assert [p["id"] for p in data["presets"]] == ["none", "pixel-2d"]


def test_game_setup_offers_starting_points_to_a_project_without_one(
    project: Path, fake_blueprints: types.ModuleType
) -> None:
    (project / "joinery.toml").write_text('[tools]\nenabled = ["godot"]\n')
    data = call(project, "game_setup")
    assert data["game"] is None
    assert [b["id"] for b in data["can_add"]["blueprints"]] == [
        "blank",
        "platformer-2d",
        "top-down",
    ]
    assert {b["id"] for b in data["can_add"]["blocked"]} == set()
    assert "twin-stick" in {f["id"] for f in data["can_add"]["features"]}


def test_conflicts_are_checked_in_both_directions() -> None:
    catalogue = {
        "features": [
            {"id": "a", "conflicts": ["b"], "blueprints": ["*"]},
            {"id": "b", "conflicts": [], "blueprints": ["*"]},
        ]
    }
    result = registry.addable(catalogue, {"blueprint": "blank", "features": ["a"]})
    assert result["blocked"] == [{"id": "b", "reason": "conflicts with a"}]


# --------------------------------------------------------------------------
# art_direction lint


def test_art_direction_without_the_lint(project: Path, no_optional: None) -> None:
    (project / "art/bible.md").write_text("# Bible\n")
    assert call(project, "art_direction")["lint"] is None


def test_art_direction_carries_the_bible_lint(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = types.ModuleType("joinery.bible_lint")
    seen: list[Path] = []

    def lint_summary(path: Path) -> dict[str, Any]:
        seen.append(path)
        return {
            "version": 2,
            "rules": {},
            "problems": ["accent and danger merge"],
            "gaps": [],
            "references": [{"id": "R1", "file": Path("art/reference/a.kra")}],
            "suggestions": ["Ask about shape."],
        }

    module.summary = lint_summary  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "joinery.bible_lint", module)
    (project / "art/bible.md").write_text("# Bible\n")
    data = call(project, "art_direction")
    assert seen == [project.resolve()]
    assert data["lint"]["problems"] == ["accent and danger merge"]
    assert (
        data["lint"]["references"][0]["file"] == "art/reference/a.kra"
    )  # made JSON-safe
    # No bible, no lint.
    (project / "art/bible.md").unlink()
    assert call(project, "art_direction")["lint"] is None


def test_a_failing_lint_does_not_hide_the_bible(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = types.ModuleType("joinery.bible_lint")

    def broken(_path: Path) -> dict[str, Any]:
        raise ValueError("rules TOML invalid")

    module.summary = broken  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "joinery.bible_lint", module)
    (project / "art/bible.md").write_text("# Bible\nSteel.\n")
    data = call(project, "art_direction")
    assert "Steel." in data["text"]
    assert data["lint"] == {"error": "rules TOML invalid"}


# --------------------------------------------------------------------------
# Against the real modules, once they are part of this Joinery


def _has(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


@pytest.mark.skipif(
    not _has("joinery.blueprints"), reason="starting-point catalogue not present"
)
def test_game_setup_with_the_real_catalogue(project: Path) -> None:
    data = call(project, "game_setup")
    assert data["catalogue"] == "available"
    json.dumps(data)


@pytest.mark.skipif(not _has("joinery.bible_lint"), reason="bible lint not present")
def test_art_direction_with_the_real_lint(project: Path) -> None:
    from joinery.bible import write_bible

    write_bible(project, {"vision": "Quiet stone."})
    data = call(project, "art_direction")
    assert isinstance(data["lint"], dict)
    assert "problems" in data["lint"] or "error" in data["lint"]


def test_requires_are_followed_transitively_and_the_engine_verdict_wins() -> None:
    catalogue = {
        "features": [
            {"id": "a", "requires": ["b"], "blueprints": ["*"]},
            {"id": "b", "requires": ["c"], "blueprints": ["*"]},
            {"id": "c", "requires": [], "blueprints": ["*"]},
            {"id": "d", "requires": [], "blueprints": ["*"]},
        ]
    }
    result = registry.addable(catalogue, {"blueprint": "blank", "features": ["c"]})
    by_id = {f["id"]: f for f in result["features"]}
    assert by_id["a"]["would_also_add"] == ["b"]
    assert "would_also_add" not in by_id["b"]
    engine = registry.addable(
        catalogue, {"blueprint": "blank", "features": [], "can_add": ["a", "b", "c"]}
    )
    assert [f["id"] for f in engine["features"]] == ["a", "b", "c"]
    assert engine["features"][0]["would_also_add"] == ["b", "c"]
    assert engine["blocked"] == [
        {"id": "d", "reason": "does not resolve with the applied features"}
    ]


def test_starting_points_are_offered_compactly() -> None:
    catalogue = {
        "blueprints": [
            {"id": "blank", "name": "Blank", "pitch": "Empty.", "input": {"x": {}}}
        ],
        "features": [],
    }
    assert registry.addable(catalogue, None)["blueprints"] == [
        {"id": "blank", "name": "Blank", "pitch": "Empty."}
    ]
