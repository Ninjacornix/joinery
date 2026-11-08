import copy
import tomllib
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from joinery.catalog import (
    CATALOG_PATH,
    Catalog,
    allowed_servers,
    blocked_tools,
    enabled_tools,
    load_catalog,
)


def raw_catalog() -> dict[str, Any]:
    with CATALOG_PATH.open("rb") as f:
        return copy.deepcopy(tomllib.load(f))


def community_tool(**overrides: Any) -> dict[str, Any]:
    mcp = {
        "server": "new",
        "repo": "https://github.com/x/new-mcp",
        "ref": "a" * 40,
        "tier": "community",
        "stars": 100,
        "last_push": date(2026, 11, 1),
        "licence": "MIT",
    }
    return {
        "id": "new",
        "name": "New",
        "domain": "2d",
        "status": "available",
        "mcp": mcp | overrides,
    }


def test_shipped_catalog_is_valid() -> None:
    catalog = load_catalog()
    assert all(
        t.mcp.telemetry == "none" and not t.mcp.generative
        for t in catalog.tool
        if t.mcp
    )


def test_future_tool_can_be_added() -> None:
    raw = raw_catalog()
    raw["tool"].append(community_tool())
    assert Catalog.model_validate(raw).get("new").status == "available"


@pytest.mark.parametrize(
    "overrides",
    [
        {"stars": 49},
        {"last_push": date(2026, 1, 1)},
        {"licence": "NOASSERTION"},
        {"ref": "main"},
        {"repo": "https://github.com/ahujasid/blender-mcp.git"},
        {"telemetry": "opt-out"},
        {"telemetry": "always"},
        {"generative": True},
        {"tier": "official", "telemetry": "opt-out"},
    ],
)
def test_admission_bar_rejects(overrides: dict[str, Any]) -> None:
    raw = raw_catalog()
    raw["tool"].append(community_tool(**overrides))
    with pytest.raises(ValidationError):
        Catalog.model_validate(raw)


def test_available_tool_needs_an_mcp() -> None:
    raw = raw_catalog()
    raw["tool"].append({"id": "x", "name": "X", "domain": "2d", "status": "available"})
    with pytest.raises(ValidationError):
        Catalog.model_validate(raw)


def test_custom_tool_cannot_collide_with_normalized_builtin_namespace(
    tmp_path: Path,
) -> None:
    config = tmp_path / "joinery.toml"
    config.write_text(
        '[tools]\nenabled=[]\n[[tools.custom]]\nid="x"\nname="X"\ndomain="2d"\nserver="joinery_registry"\ncommand="x"\n'
    )
    with pytest.raises(ValueError, match="clashes"):
        enabled_tools(load_catalog(), config)


def test_required_tools_always_enabled_and_allowed(tmp_path: Path) -> None:
    config = tmp_path / "joinery.toml"
    config.write_text('[tools]\nenabled = ["blender"]\n')
    catalog = load_catalog()
    assert enabled_tools(catalog, config) == ["blender", "godot"]
    servers = allowed_servers(catalog, config)
    assert {"blender", "godot", "joinery-verify"} <= servers
    assert "aseprite" not in servers


def test_enabling_unavailable_tool_fails(tmp_path: Path) -> None:
    config = tmp_path / "joinery.toml"
    config.write_text('[tools]\nenabled = ["future-synth"]\n')
    raw = raw_catalog()
    raw["tool"].append(
        {
            "id": "future-synth",
            "name": "Future Synth",
            "domain": "audio",
            "status": "first-party-planned",
        }
    )
    with pytest.raises(ValueError, match="future-synth"):
        enabled_tools(Catalog.model_validate(raw), config)


CUSTOM = (
    '[[tools.custom]]\nid = "sdxl"\nname = "SDXL"\ndomain = "2d"\n'
    'server = "sdxl"\ncommand = "sdxl-mcp"\n'
)


def test_custom_tool_is_allowed_for_its_project_only(tmp_path: Path) -> None:
    config = tmp_path / "joinery.toml"
    config.write_text(f"[tools]\nenabled = []\n{CUSTOM}")
    assert "sdxl" in allowed_servers(load_catalog(), config)
    config.write_text("[tools]\nenabled = []\n")
    assert "sdxl" not in allowed_servers(load_catalog(), config)


def test_custom_tool_cannot_impersonate_preferred(tmp_path: Path) -> None:
    config = tmp_path / "joinery.toml"
    config.write_text(
        f"[tools]\nenabled = []\n{CUSTOM.replace('server = "sdxl"', 'server = "blender"')}"
    )
    with pytest.raises(ValueError, match="clashes"):
        allowed_servers(load_catalog(), config)


def test_custom_engine_tool_replaces_required_godot_mcp(tmp_path: Path) -> None:
    config = tmp_path / "joinery.toml"
    custom = CUSTOM.replace('domain = "2d"', 'domain = "engine"')
    config.write_text(f"[tools]\nenabled = []\n{custom}")
    assert enabled_tools(load_catalog(), config) == []


def test_preferred_godot_blocks_generic_network_tool() -> None:
    assert "game_http_request" in blocked_tools(load_catalog(), "godot")


def test_preferred_musescore_blocks_remote_viewer() -> None:
    assert "show_score" in blocked_tools(load_catalog(), "musescore")
