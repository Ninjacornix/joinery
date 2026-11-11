"""Provider-neutral MCP tools that connect to the current editor's project gateway."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Annotated, Any

from mcp.server import MCPServer
from mcp.types import CallToolResult, ImageContent, TextContent, ToolAnnotations
from pydantic import Field

from joinery import chat_attachments
from joinery.editor_ops import SceneEdit
from joinery.gateway_rpc import rpc
from servers.errors import tool_errors


def make_server(socket: Path) -> MCPServer[Any]:
    server: MCPServer[Any] = MCPServer(
        "joinery-registry",
        instructions=(
            "Use discover_tools to check real connected capabilities, then tool_schema before calling a tool. "
            "Mutations are staged; call submit_for_review when the change is complete. "
            "Never claim staged changes are accepted or saved."
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

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    @tool_errors
    async def view_editor() -> CallToolResult:
        """See the editor's current 3D or 2D viewport exactly as the user sees it, with the camera position. Use it when the user asks you to look at something. The screenshot is context only, never an asset."""
        data = await rpc(socket, "editor_view", {})
        media, encoded = chat_attachments.encoded(Path(str(data["path"])))
        return CallToolResult(
            content=[
                ImageContent(type="image", data=encoded, mime_type=media),
                TextContent(type="text", text=json.dumps(data)),
            ]
        )

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    @tool_errors
    async def inspect_in_parallel(tasks: list[dict[str, str]]) -> dict[str, Any]:
        """Delegate one or two independent read-only inspection/validation questions to fast specialist agents. Each task has name and question. No edits or nested delegation; use the main agent for all changes. Results can fail independently and must be checked."""
        return await rpc(socket, "delegate", {"tasks": tasks})

    @server.tool()
    @tool_errors
    async def submit_for_review(
        summary: Annotated[str, Field(min_length=1, max_length=160)],
    ) -> dict[str, Any]:
        """Hand your finished change to Joinery. Joinery checks it (art bible rules, Godot import, missing res:// files, asset provenance, project tests). status 'in_review': the user now sees it; make no more changes this turn. 'failed' and not final: fix every problem listed and submit again (attempts_left). 'needs_user', or final: stop and tell the user plainly what failed. summary: one short line in the user's words naming what changed."""
        return await rpc(socket, "submit", {"summary": summary})

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
