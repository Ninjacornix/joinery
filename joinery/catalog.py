"""Tool catalog: what the install menu offers and which MCP servers may run."""

from __future__ import annotations

import re
import tomllib
from datetime import date
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "policy" / "tools.toml"
CONFIG_PATH = ROOT / "joinery.toml"

# Admission bar for community MCPs. Official and first-party servers skip it.
MIN_STARS = 50
MAX_DAYS_SINCE_PUSH = 180
KNOWN_LICENCES = frozenset(
    {
        "Apache-2.0",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "CC0-1.0",
        "GPL-3.0",
        "MIT",
        "MPL-2.0",
    }
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class McpSource(_Strict):
    server: str
    repo: str
    ref: str
    tier: Literal["official", "community", "first-party"]
    stars: int
    last_push: date
    licence: str
    notes: str = ""

    @model_validator(mode="after")
    def _pinned(self) -> Self:
        if not re.fullmatch(r"[0-9a-f]{40}", self.ref):
            raise ValueError(f"{self.server}: ref must be a full commit SHA")
        return self


class Tool(_Strict):
    id: str
    name: str
    domain: Literal["engine", "3d", "materials", "2d", "audio"]
    status: Literal["available", "needs-mcp", "first-party-planned"]
    required: bool = False
    paid: bool = False
    mcp: McpSource | None = None
    candidates: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _status_matches_mcp(self) -> Self:
        if self.status == "available" and self.mcp is None:
            raise ValueError(f"{self.id}: an available tool needs an MCP")
        if self.status != "available" and self.mcp is not None:
            raise ValueError(f"{self.id}: only an available tool may name an MCP")
        if self.required and self.status != "available":
            raise ValueError(f"{self.id}: a required tool must be available")
        return self


class Rejected(_Strict):
    repo: str
    reason: str


class Catalog(_Strict):
    vetted_on: date
    builtin_servers: tuple[str, ...]
    tool: tuple[Tool, ...]
    rejected: tuple[Rejected, ...] = ()

    @model_validator(mode="after")
    def _admission_bar(self) -> Self:
        ids = [t.id for t in self.tool]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate tool id")
        banned = {_repo_key(r.repo): r.reason for r in self.rejected}
        for tool in self.tool:
            if tool.mcp is None:
                continue
            reason = banned.get(_repo_key(tool.mcp.repo))
            if reason:
                raise ValueError(f"{tool.id}: rejected MCP: {reason}")
            if tool.mcp.tier == "community":
                self._check_community(tool.id, tool.mcp)
        return self

    def _check_community(self, tool_id: str, mcp: McpSource) -> None:
        if mcp.stars < MIN_STARS:
            raise ValueError(f"{tool_id}: {mcp.stars} stars is under {MIN_STARS}")
        age = (self.vetted_on - mcp.last_push).days
        if age > MAX_DAYS_SINCE_PUSH:
            raise ValueError(f"{tool_id}: last push {age} days before vetting")
        if mcp.licence not in KNOWN_LICENCES:
            raise ValueError(f"{tool_id}: licence {mcp.licence!r} not accepted")

    def get(self, tool_id: str) -> Tool:
        for tool in self.tool:
            if tool.id == tool_id:
                return tool
        raise KeyError(tool_id)

    def required_ids(self) -> list[str]:
        return [t.id for t in self.tool if t.required]

    def rejected_repos(self) -> list[str]:
        return [_repo_key(r.repo) for r in self.rejected]


def _repo_key(url: str) -> str:
    return url.lower().removesuffix(".git").rstrip("/")


def load_catalog(path: Path = CATALOG_PATH) -> Catalog:
    with path.open("rb") as f:
        return Catalog.model_validate(tomllib.load(f))


def enabled_tools(catalog: Catalog, config: Path = CONFIG_PATH) -> list[str]:
    """Selected tool ids, plus required ones. Unknown or unavailable ids are errors."""
    with config.open("rb") as f:
        chosen: list[str] = tomllib.load(f).get("tools", {}).get("enabled", [])
    for tool_id in chosen:
        if catalog.get(tool_id).status != "available":
            raise ValueError(f"{tool_id} is not available yet")
    return sorted({*chosen, *catalog.required_ids()})


def allowed_servers(catalog: Catalog, config: Path = CONFIG_PATH) -> set[str]:
    servers = set(catalog.builtin_servers)
    for tool_id in enabled_tools(catalog, config):
        mcp = catalog.get(tool_id).mcp
        if mcp is not None:
            servers.add(mcp.server)
    return servers
