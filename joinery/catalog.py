"""Preferred tools, per-project custom tools, and which MCP servers may run."""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any, Literal, Self

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
    #: "tool:operation" pairs refused when a call's `operation` argument names
    #: them, for multi-operation tools that are only partly usable offline.
    blocked_operations: tuple[str, ...] = ()
    notes: str = ""

    @model_validator(mode="after")
    def _pinned(self) -> Self:
        if not re.fullmatch(r"[0-9a-f]{40}", self.ref):
            raise ValueError(f"{self.server}: ref must be a full commit SHA")
        for entry in self.blocked_operations:
            if not re.fullmatch(r"[A-Za-z0-9_.-]+:[A-Za-z0-9_.-]+", entry):
                raise ValueError(
                    f"{self.server}: blocked operation {entry!r} must be tool:operation"
                )
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
    builtin_server: str = ""
    candidates: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _status_matches_mcp(self) -> Self:
        if self.status == "available" and self.mcp is None and not self.builtin_server:
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
            if tool.builtin_server and tool.builtin_server not in self.builtin_servers:
                raise ValueError(f"{tool.id}: first-party server is not in the builtin allowlist")
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
    aliases = {server.replace("-", "_") for server in taken_servers}
    for tool in custom:
        if tool.id in taken_ids or tool.server.replace("-", "_") in aliases:
            raise ValueError(f"custom tool {tool.id!r} clashes with a preferred tool")
        taken_ids.add(tool.id)
        taken_servers.add(tool.server)
        aliases.add(tool.server.replace("-", "_"))


#: Tools Joinery used to offer and no longer does, with what to tell a project
#: that still enables one. Such a project keeps working: the id is skipped, and
#: the notice is shown wherever tool status is shown. Adding the program back
#: as the user's own custom tool stays possible.
REMOVED_TOOLS: dict[str, str] = {
    "aseprite": (
        "Aseprite was removed: it can't be packaged in Joinery's offline workers "
        "(paid licence). Use Krita for pixel art, or add Aseprite yourself as a "
        "custom tool."
    ),
    "reaper": (
        "REAPER was removed: it can't be packaged in Joinery's offline workers "
        "(paid licence) and needs a live desktop session. Use SuperCollider or "
        "Surge XT, or add REAPER yourself as a custom tool."
    ),
    "musescore": (
        "MuseScore was removed: it has no offline mode Joinery can drive. Music is "
        "written as MIDI sources and rendered through FluidSynth and Surge XT; you "
        "can still add MuseScore yourself as a custom tool."
    ),
}


def removed_notices(tools: ProjectTools) -> list[str]:
    """One notice per removed tool this project still enables."""
    custom = {c.id for c in tools.custom}
    return [
        REMOVED_TOOLS[tool_id]
        for tool_id in dict.fromkeys(tools.enabled)
        if tool_id in REMOVED_TOOLS and tool_id not in custom
    ]


def selection_ids(catalog: Catalog, tools: ProjectTools) -> list[str]:
    """Preferred ids in use. A required tool can be replaced by a custom one in its domain.

    Ids of removed tools are skipped rather than refused, so an older project
    still opens; `removed_notices` says what happened to them.
    """
    _check_custom(catalog, tools.custom)
    enabled = {t for t in tools.enabled if t not in REMOVED_TOOLS}
    for tool_id in enabled:
        if catalog.get(tool_id).status != "available":
            raise ValueError(f"{tool_id} has no vetted MCP yet")
    replaced = {c.domain for c in tools.custom}
    required = {t.id for t in catalog.tool if t.required and t.domain not in replaced}
    return sorted(enabled | required)


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


def blocked_operation(
    catalog: Catalog, server: str, tool: str, arguments: Mapping[str, Any]
) -> str:
    """The blocked operation this call asks for, or ""."""
    operation = arguments.get("operation")
    if not isinstance(operation, str):
        return ""
    for entry in catalog.tool:
        if (
            entry.mcp is not None
            and entry.mcp.server == server
            and f"{tool}:{operation}" in entry.mcp.blocked_operations
        ):
            return operation
    return ""
