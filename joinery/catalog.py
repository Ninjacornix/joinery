"""Preferred tools, per-project custom tools, and which MCP servers may run."""

from __future__ import annotations

import re
import tomllib
from datetime import date
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "policy" / "preferred_tools.toml"
CONFIG_PATH = ROOT / "joinery.toml"

# Bar for community MCPs. Every preferred MCP must also be non-generative and
# send no telemetry, whatever its tier.
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
    telemetry: Literal["none", "opt-out", "always"] = "none"
    generative: bool = False
    stars: int
    last_push: date
    licence: str
    blocked_tools: tuple[str, ...] = ()
    notes: str = ""

    @model_validator(mode="after")
    def _pinned(self) -> Self:
        if not re.fullmatch(r"[0-9a-f]{40}", self.ref):
            raise ValueError(f"{self.server}: ref must be a full commit SHA")
        return self


Domain = Literal["engine", "3d", "materials", "2d", "audio"]


class Tool(_Strict):
    id: str
    name: str
    domain: Domain
    also_domains: tuple[Domain, ...] = ()
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
    #: Which tool this was a candidate for, so a UI can show it alongside the
    #: one that was chosen. Empty when it was never tied to a single tool.
    tool: str = ""
    #: A few words for a menu, where the full reason will not fit. "rejected"
    #: on its own means nothing to someone creating a project.
    summary: str = ""


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
            if tool.mcp.telemetry != "none":
                raise ValueError(f"{tool.id}: a preferred MCP must send no telemetry")
            if tool.mcp.generative:
                raise ValueError(f"{tool.id}: a preferred MCP must not be generative")
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

    def rejected_repos(self) -> list[str]:
        return [_repo_key(r.repo) for r in self.rejected]

    def rejected_for(self, tool_id: str) -> list[Rejected]:
        """Vetted and turned down for this tool. Shown so the call is visible."""
        return [r for r in self.rejected if r.tool == tool_id]


def _repo_key(url: str) -> str:
    return url.lower().removesuffix(".git").rstrip("/")


def load_catalog(path: Path = CATALOG_PATH) -> Catalog:
    with path.open("rb") as f:
        return Catalog.model_validate(tomllib.load(f))


class CustomTool(_Strict):
    """A user's own program and MCP. Not vetted: the project owner's choice."""

    id: str
    name: str
    domain: Domain
    server: str
    command: str
    args: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _safe_names(self) -> Self:
        for value in (self.id, self.server):
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", value):
                raise ValueError(f"invalid name: {value!r}")
        return self


class ProjectTools(_Strict):
    enabled: tuple[str, ...] = ()
    custom: tuple[CustomTool, ...] = ()


def load_project_tools(config: Path = CONFIG_PATH) -> ProjectTools:
    with config.open("rb") as f:
        return ProjectTools.model_validate(tomllib.load(f).get("tools", {}))


def _check_custom(catalog: Catalog, custom: tuple[CustomTool, ...]) -> None:
    taken_ids = {t.id for t in catalog.tool}
    taken_servers = {t.mcp.server for t in catalog.tool if t.mcp} | set(
        catalog.builtin_servers
    )
    for tool in custom:
        if tool.id in taken_ids or tool.server in taken_servers:
            raise ValueError(f"custom tool {tool.id!r} clashes with a preferred tool")
        taken_ids.add(tool.id)
        taken_servers.add(tool.server)


def selection_ids(catalog: Catalog, tools: ProjectTools) -> list[str]:
    """Preferred ids in use. A required tool can be replaced by a custom one in its domain."""
    _check_custom(catalog, tools.custom)
    for tool_id in tools.enabled:
        if catalog.get(tool_id).status != "available":
            raise ValueError(f"{tool_id} has no vetted MCP yet")
    replaced = {c.domain for c in tools.custom}
    required = {t.id for t in catalog.tool if t.required and t.domain not in replaced}
    return sorted(set(tools.enabled) | required)


def enabled_tools(catalog: Catalog, config: Path = CONFIG_PATH) -> list[str]:
    return selection_ids(catalog, load_project_tools(config))


def allowed_servers(catalog: Catalog, config: Path = CONFIG_PATH) -> set[str]:
    tools = load_project_tools(config)
    servers = set(catalog.builtin_servers) | {c.server for c in tools.custom}
    for tool_id in selection_ids(catalog, tools):
        mcp = catalog.get(tool_id).mcp
        if mcp is not None:
            servers.add(mcp.server)
    return servers


def blocked_tools(catalog: Catalog, server: str) -> tuple[str, ...]:
    for tool in catalog.tool:
        if tool.mcp is not None and tool.mcp.server == server:
            return tool.mcp.blocked_tools
    return ()
