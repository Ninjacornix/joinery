"""Provider-neutral MCP tools that connect to the current editor's project gateway."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from joinery.editor_ops import SceneEdit
from joinery.gateway_rpc import rpc
from servers.errors import tool_errors


def make_server(socket: Path) -> MCPServer[Any]:
    server: MCPServer[Any] = MCPServer(
        "joinery-registry",
        instructions=(
            "Use discover_tools to check real connected capabilities, then tool_schema before calling a tool. "
            "Mutations are staged for user review. Never claim staged changes are accepted or saved."
        ),
    )

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    @tool_errors
    async def discover_tools(query: str = "") -> dict[str, Any]:
        """Find installed and connected project tools by purpose, with readiness and missing-tool reasons."""
        return await rpc(socket, "inventory", {"query": query})

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    @tool_errors
    async def tool_schema(server: str, tool: str) -> dict[str, Any]:
        """Read the actual MCP input schema for a project tool before invoking it."""
        return await rpc(socket, "schema", {"server": server, "tool": tool})

    @server.tool()
    @tool_errors
    async def edit_scene(edit: SceneEdit) -> dict[str, Any]:
        """Preview native changes in the current live Godot editor, including unsaved scenes. Reusable create/delete/duplicate/rename/reparent/property operations. Uses stable context identities. The user accepts or rejects; never claim it is saved."""
        return await rpc(socket, "editor_edit", edit.model_dump(mode="json"))

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    @tool_errors
    async def inspect_editor() -> dict[str, Any]:
        """Read the current project's live editor scene, node identities, selection and unsaved scene context. Use these identities for edit_scene. Saved-file MCP tools and game_* runtime tools are different targets."""
        return await rpc(socket, "editor_context", {})

    @server.tool()
    @tool_errors
    async def call_tool(
        server: str, tool: str, arguments: dict[str, Any]
    ) -> CallToolResult:
        """Operate a connected project tool; changes are staged, policy-checked, and require user review."""
        data = await rpc(
            socket,
            "call",
            {"server": server, "tool": tool, "arguments": arguments},
        )
        result = CallToolResult.model_validate(data)
        if "joinery" in data:
            result.content.append(
                TextContent(type="text", text="Joinery: " + str(data["joinery"]))
            )
        return result

    return server


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", type=Path, required=True)
    args = parser.parse_args()
    make_server(args.socket).run()


if __name__ == "__main__":
    main()
