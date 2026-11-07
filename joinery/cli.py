"""`joinery` command line."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from joinery import hook
from joinery.catalog import (
    CONFIG_PATH,
    ROOT,
    allowed_servers,
    enabled_tools,
    load_catalog,
)
from joinery.deps import check_deps
from joinery.installer import render, run_menu, validate_choice, write_selection
from joinery.lockfile import check_assets
from joinery.policy import load_denylist, normalise


def check_mcp_config(root: Path) -> list[str]:
    """Check .mcp.json against the selected tools and the denylists."""
    path = root / ".mcp.json"
    if not path.is_file():
        return []
    catalog = load_catalog()
    allowed = allowed_servers(catalog)
    denied = load_denylist().packages
    errors: list[str] = []
    for name, server in json.loads(path.read_text()).get("mcpServers", {}).items():
        if name not in allowed:
            errors.append(f".mcp.json: server {name!r} is not allowed")
        parts = [str(server.get("command", "")), *map(str, server.get("args", []))]
        for part in parts:
            if normalise(part.partition("==")[0]) in denied:
                errors.append(f".mcp.json: {name} uses denied package {part}")
            if any(repo in part.lower() for repo in catalog.rejected_repos()):
                errors.append(f".mcp.json: {name} uses rejected source {part}")
    return errors


def _check(target: str, project: Path) -> int:
    errors: list[str] = []
    catalog = load_catalog()  # Loading validates the admission bar.
    enabled_tools(catalog)
    if target in ("deps", "all"):
        errors += check_deps(ROOT, load_denylist())
    if target in ("mcp", "all"):
        errors += check_mcp_config(ROOT)
    if target in ("assets", "all"):
        errors += check_assets(project, catalog)
    for error in errors:
        print(error, file=sys.stderr)
    print(f"check {target}: {'FAILED' if errors else 'ok'}")
    return 1 if errors else 0


def _setup(tools: str | None) -> int:
    catalog = load_catalog()
    if tools is None:
        selection = run_menu(catalog, enabled_tools(catalog))
    else:
        selection = validate_choice(catalog, [t for t in tools.split(",") if t])
    write_selection(selection)
    print(f"Saved to {CONFIG_PATH.name}: {', '.join(selection)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="joinery")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("hook", help="PreToolUse policy hook (reads JSON on stdin)")
    setup = commands.add_parser("setup", help="choose creation tools")
    setup.add_argument("--tools", help="comma-separated ids; skips the menu")
    commands.add_parser("tools", help="list tools and their MCP status")
    check = commands.add_parser("check", help="run policy checks")
    check.add_argument("target", choices=["all", "catalog", "deps", "mcp", "assets"])
    check.add_argument("--project", type=Path, default=ROOT / "testbed")
    args = parser.parse_args()

    if args.command == "hook":
        return hook.main()
    if args.command == "setup":
        return _setup(args.tools)
    if args.command == "tools":
        catalog = load_catalog()
        print(render(catalog, set(enabled_tools(catalog))))
        return 0
    return _check(args.target, args.project)
