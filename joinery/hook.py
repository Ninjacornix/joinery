"""PreToolUse hook. Exit 2 blocks the call; any internal error also blocks."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from joinery.catalog import ROOT, allowed_servers, blocked_tools, load_catalog
from joinery.policy import command_violation, load_denylist, url_violation


def _project_config() -> Path:
    return Path(os.environ.get("CLAUDE_PROJECT_DIR", ROOT)) / "joinery.toml"


def _mcp_violation(tool_name: str) -> str | None:
    server, _, tool = tool_name.removeprefix("mcp__").partition("__")
    catalog = load_catalog()
    if server not in allowed_servers(catalog, _project_config()):
        return f"MCP server {server!r} is not enabled for this project."
    if tool in blocked_tools(catalog, server):
        return f"{tool} is blocked on the preferred {server} MCP."
    return None


def violation(event: dict[str, Any]) -> str | None:
    tool = str(event.get("tool_name", ""))
    tool_input = event.get("tool_input") or {}
    if tool.startswith("mcp__"):
        return _mcp_violation(tool)
    if tool == "Bash":
        return command_violation(str(tool_input.get("command", "")), load_denylist())
    if tool == "WebFetch":
        return url_violation(str(tool_input.get("url", "")), load_denylist())
    return None


def main() -> int:
    try:
        reason = violation(json.load(sys.stdin))
    except Exception as exc:  # noqa: BLE001 - a broken policy check must block, not pass.
        print(f"Joinery policy check failed: {exc}", file=sys.stderr)
        return 2
    if reason is None:
        return 0
    print(f"Blocked by Joinery policy: {reason}", file=sys.stderr)
    return 2
