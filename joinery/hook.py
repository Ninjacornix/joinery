"""PreToolUse hook. Exit 2 blocks the call; any internal error also blocks."""

from __future__ import annotations

import json
import sys
from typing import Any

from joinery.catalog import allowed_servers, load_catalog
from joinery.policy import command_violation, load_denylist, url_violation


def _mcp_server(tool_name: str) -> str:
    return tool_name.removeprefix("mcp__").partition("__")[0]


def violation(event: dict[str, Any]) -> str | None:
    tool = str(event.get("tool_name", ""))
    tool_input = event.get("tool_input") or {}
    if tool.startswith("mcp__"):
        server = _mcp_server(tool)
        if server in allowed_servers(load_catalog()):
            return None
        return f"MCP server {server!r} is not allowed. Enable a catalog tool with `joinery setup`."
    if tool == "Bash":
        return command_violation(str(tool_input.get("command", "")), load_denylist())
    if tool == "WebFetch":
        return url_violation(str(tool_input.get("url", "")), load_denylist())
    return None


def main() -> int:
    try:
        reason = violation(json.load(sys.stdin))
    except Exception as exc:  # noqa: BLE001 - a broken policy check must block, not pass.
        print(f"Creation-only policy check failed: {exc}", file=sys.stderr)
        return 2
    if reason is None:
        return 0
    print(f"Blocked by creation-only policy: {reason}", file=sys.stderr)
    return 2
