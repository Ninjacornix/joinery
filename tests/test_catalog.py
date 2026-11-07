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
    assert catalog.required_ids() == ["godot"]


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
    config.write_text('[tools]\nenabled = ["krita"]\n')
    with pytest.raises(ValueError, match="krita"):
        enabled_tools(load_catalog(), config)
