"""`joinery` command line."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from joinery import hook
from joinery.agents import all_agents
from joinery.agents import get as get_agent
from joinery.auth import for_agent
from joinery.catalog import (
    CONFIG_PATH,
    ROOT,
    allowed_servers,
    load_catalog,
    load_project_tools,
    selection_ids,
)
from joinery.deps import check_deps
from joinery.installer import (
    Selection,
    render,
    run_menu,
    validate_choice,
    write_mcp_config,
    write_selection,
)
from joinery.lockfile import check_assets
from joinery.policy import load_denylist, normalise
from joinery.project import create_project
from joinery.wiring import unwireable


def check_mcp_config(root: Path, config: Path = CONFIG_PATH) -> list[str]:
    """Check .mcp.json. Preferred entries must also avoid denied and rejected sources."""
    path = root / ".mcp.json"
    if not path.is_file():
        return []
    catalog = load_catalog()
    allowed = allowed_servers(catalog, config)
    custom = {c.server for c in load_project_tools(config).custom}
    denied = load_denylist().packages
    errors: list[str] = []
    for name, server in json.loads(path.read_text()).get("mcpServers", {}).items():
        if name not in allowed:
            errors.append(f".mcp.json: server {name!r} is not enabled")
        if name in custom:
            continue
        parts = [str(server.get("command", "")), *map(str, server.get("args", []))]
        for part in parts:
            if normalise(part.partition("==")[0]) in denied:
                errors.append(f".mcp.json: {name} uses denied package {part}")
            if any(repo in part.lower() for repo in catalog.rejected_repos()):
                errors.append(f".mcp.json: {name} uses rejected source {part}")
    return errors


def _check(target: str, project: Path) -> int:
    errors: list[str] = []
    catalog = load_catalog()  # Loading validates the preferred list.
    selection_ids(catalog, load_project_tools())
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


def _choose(tools: str | None, config: Path) -> Selection:
    catalog = load_catalog()
    current = load_project_tools(config) if config.is_file() else None
    custom = list(current.custom) if current else []
    if tools is not None:
        return Selection(
            validate_choice(catalog, [t for t in tools.split(",") if t], custom), custom
        )
    return run_menu(catalog, current.enabled if current else (), custom)


def _setup(tools: str | None, project: Path) -> int:
    config = project / "joinery.toml"
    selection = _choose(tools, config)
    write_selection(selection, config)
    write_mcp_config(selection, project)
    print(f"Saved: {', '.join(selection.tools)}")
    return 0


def _new(path: Path, name: str | None, tools: str | None) -> int:
    selection = _choose(tools, path / "joinery.toml")
    create_project(path, name or path.name, selection, load_catalog())
    print(f"Created {path} with: {', '.join(selection.tools)}")
    return 0


def _agents() -> int:
    """List supported agents and how the creation-only gate lands in each."""
    blocked = set(unwireable())
    print("Supported agents. No tool is disabled; every call is judged:")
    print("  stop = Rule Zero violation, halt the turn and tell the user")
    print("  deny = not enabled here, refuse the call, the turn continues\n")
    for agent in all_agents():
        state = "gated" if agent.id not in blocked else "NOT GATED (fail-closed)"
        how = "stops the turn" if agent.stop_mode == "json" else "refuses the call"
        print(f"  {agent.name:<14} {state}  via {agent.config_path}")
        print(f"      on a Rule Zero hit: {how}")
        for limit in agent.gate_limits:
            print(f"      - {limit}")
    print(
        "\nThe hook is defense-in-depth per agent. The real guarantee is the "
        "offline sandbox worker plus CI (`joinery check deps|assets`), which no "
        "agent can bypass."
    )
    return 0


def _auth(agent_id: str | None) -> int:
    """Show how to sign an agent in: subscription or API key."""
    ids = [agent_id] if agent_id else [a.id for a in all_agents()]
    for aid in ids:
        agent = get_agent(aid)
        info = for_agent(aid)
        print(f"{agent.name}:")
        for path in (info.subscription, info.api_key):
            print(f"  {path.kind}: {path.how}")
            if path.key_var:
                print(f"    key: {path.key_var}")
            if path.get_at:
                print(f"    at:  {path.get_at}")
            if path.notes:
                print(f"    note: {path.notes}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="joinery")
    commands = parser.add_subparsers(dest="command", required=True)
    hook_cmd = commands.add_parser(
        "hook", help="PreToolUse policy hook (reads JSON on stdin)"
    )
    hook_cmd.add_argument(
        "--agent",
        choices=[a.id for a in all_agents()],
        help="calling agent; selects how a Rule Zero stop is signalled",
    )
    new = commands.add_parser("new", help="create a project and choose its tools")
    new.add_argument("path", type=Path)
    new.add_argument("--name")
    new.add_argument("--tools", help="comma-separated ids; skips the menu")
    setup = commands.add_parser("setup", help="change a project's tools")
    setup.add_argument("--tools", help="comma-separated ids; skips the menu")
    setup.add_argument("--project", type=Path, default=ROOT)
    tools = commands.add_parser("tools", help="list tools and their MCP status")
    tools.add_argument("--project", type=Path, default=ROOT)
    commands.add_parser("agents", help="list supported agents and gate status")
    auth = commands.add_parser("auth", help="show how to sign an agent in")
    auth.add_argument(
        "agent", nargs="?", choices=[a.id for a in all_agents()], default=None
    )
    check = commands.add_parser("check", help="run policy checks")
    check.add_argument("target", choices=["all", "catalog", "deps", "mcp", "assets"])
    check.add_argument("--project", type=Path, default=ROOT / "testbed")
    args = parser.parse_args()

    if args.command == "hook":
        return hook.main(args.agent)
    if args.command == "new":
        return _new(args.path, args.name, args.tools)
    if args.command == "setup":
        return _setup(args.tools, args.project)
    if args.command == "agents":
        return _agents()
    if args.command == "auth":
        return _auth(args.agent)
    if args.command == "tools":
        catalog = load_catalog()
        current = load_project_tools(args.project / "joinery.toml")
        print(render(catalog, set(selection_ids(catalog, current)), current.custom))
        return 0
    return _check(args.target, args.project)
