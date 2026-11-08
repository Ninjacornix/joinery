"""Actual read-only MCP source and provenance lookup for a bound project."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from joinery.art_approval import approved, fingerprint
from joinery.workspace import index
from servers.errors import tool_errors


def make_server(project: Path) -> MCPServer[Any]:
    project = project.resolve()
    if not (project / "project.godot").is_file():
        raise ValueError("Expected a Godot project.")
    server: MCPServer[Any] = MCPServer("joinery-registry")

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    @tool_errors
    async def art_direction() -> dict[str, Any]:
        """Read the project's actual art bible and its user approval status. The model has no approval operation."""
        path = project / "art/bible.md"
        return {
            "approved": approved(project),
            "sha256": fingerprint(project) if path.is_file() else "",
            "text": path.read_text()
            if path.is_file()
            else "No art direction has been written for this project.",
            "approval_action": "In chat, type /art and click Approve this version. Saving or accepting a draft does not approve it.",
        }

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    @tool_errors
    async def list_assets() -> dict[str, Any]:
        """List actual project assets, their source documents, owners and recipes."""
        return index(project)

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    @tool_errors
    async def resolve_source(resource: str) -> dict[str, Any]:
        """Resolve a Godot resource path to its native source and owning application."""
        relative = resource.removeprefix("res://").split("::", 1)[0]
        if not (project / relative).resolve().is_relative_to(project):
            raise ValueError("Resource is outside this project.")
        data = index(project)
        for asset in data.get("assets", []):
            if asset["path"] == relative:
                return dict(asset)
        raise ValueError(
            "The resource is not registered in this project. Use list_assets to find existing sources; do not inspect a document before creating it."
        )

    return server


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    arguments = parser.parse_args()
    make_server(arguments.project).run()
