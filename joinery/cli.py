"""`joinery` command line."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

from joinery import __version__, connection, history, hook
from joinery.agents import all_agents
from joinery.agents import get as get_agent
from joinery.api_profiles import configure as configure_api
from joinery.auth import for_agent
from joinery.bible import grouped, write_from_answers_file
from joinery.catalog import (
    CONFIG_PATH,
    ROOT,
    ProjectTools,
    allowed_servers,
    load_catalog,
    load_project_tools,
    selection_ids,
)
from joinery.deps import check_deps
from joinery.doctor import report as doctor_report
from joinery.installer import (
    Selection,
    custom_choices,
    render,
    run_menu,
    tool_menu,
    validate_choice,
    write_mcp_config,
    write_selection,
)
from joinery.lockfile import check_assets
from joinery.locking import write_lock
from joinery.policy import load_denylist, normalise
from joinery.project import create_project
from joinery.recipe import rebuild_all, recipe_paths, record
from joinery.runner import pick
from joinery.vcs import ensure_lfs
from joinery.wiring import unwireable, write_agent_wiring
from joinery.workspace import index, open_asset, watch_stamp


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


def _choose(
    tools: str | None, config: Path, custom_mcps: str | None = None
) -> Selection:
    catalog = load_catalog()
    current = load_project_tools(config) if config.is_file() else None
    custom = list(current.custom) if current else []
    if custom_mcps is not None:
        custom = custom_choices(custom_mcps, custom)
    if tools is not None:
        return Selection(
            validate_choice(catalog, [t for t in tools.split(",") if t], custom), custom
        )
    return run_menu(catalog, current.enabled if current else (), custom)


def _setup(tools: str | None, project: Path, custom_mcps: str | None = None) -> int:
    config = project / "joinery.toml"
    try:
        selection = _choose(tools, config, custom_mcps)
    except (TypeError, ValueError) as exc:
        print(f"setup: {exc}", file=sys.stderr)
        return 1
    write_selection(selection, config)
    write_mcp_config(selection, project)
    # Setting a project up includes gating it. A project created through the
    # Project Manager reaches this path rather than `joinery new`, and without
    # this it would have no creation-only hooks for any agent, which is the one
    # thing a Joinery project must not be missing.
    wired = write_agent_wiring(project)
    # Git LFS before the first binary lands, because converting later means
    # rewriting history. Not an option at creation time, just how a project
    # starts: its sources are .blend, .kra, WAV and PNG files.
    if ensure_lfs(project):
        print("Tracking native sources with Git LFS (.gitattributes)")
    print(f"Saved: {', '.join(selection.tools)}")
    print(f"Gate wired for: {', '.join(sorted({p.split('/')[0] for p in wired}))}")
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


def _rebuild(project: Path, prefer: str, record_mode: bool, timeout: int) -> int:
    """Replay every recipe and report whether each asset came back identical."""
    runner = pick(prefer)
    if not runner.available():
        print(
            f"runner {runner.name!r} is not available. Build the sandbox "
            "(sandbox/build.sh) or use --runner host with the tools installed.",
            file=sys.stderr,
        )
        return 1
    paths = recipe_paths(project)
    if not paths:
        print(f"no recipes in {project / 'recipes'}")
        return 0

    if record_mode:
        # Measures the committed outputs; it does not rebuild them.
        for path in paths:
            updated = record(path, project, runner)
            fps = ", ".join(sorted(updated.expect.values()))
            print(f"recorded {path.name}: {fps}")
        return 0

    # Replay in a copy, never in place. A rebuild rewrites its outputs, and a
    # .blend is not byte-reproducible, so rebuilding the working tree would
    # leave every asset's joinery.lock hash stale even when the recipe is
    # perfectly correct. Verification must not dirty the thing it verifies.
    failed = 0
    with tempfile.TemporaryDirectory(prefix="joinery-rebuild-") as tmp:
        scratch = Path(tmp) / project.name
        shutil.copytree(project, scratch, symlinks=True)
        print(
            f"rebuilding {len(paths)} recipe(s) via the {runner.name} runner, "
            "in a scratch copy"
        )
        for result in rebuild_all(scratch, runner, timeout):
            mark = "ok " if result.ok else "FAIL"
            print(f"  [{mark}] {result.recipe:<28} {result.seconds:6.1f}s")
            for problem in result.problems:
                print(f"         {problem}", file=sys.stderr)
            failed += not result.ok
    print("rebuild: " + ("FAILED" if failed else "ok"))
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="joinery")
    parser.add_argument("--version", action="version", version="Joinery " + __version__)
    commands = parser.add_subparsers(dest="command", required=True)
    package_cmd = commands.add_parser(
        "package",
        help="bundle the current source and matching engine with a measured manifest",
    )
    package_cmd.add_argument("--output", type=Path, required=True)
    package_cmd.add_argument(
        "--desktop",
        action="store_true",
        help="create the platform desktop launcher and application layout",
    )
    package_cmd.add_argument(
        "--target",
        default="",
        help="require a matching executable target, such as macos-arm64",
    )
    package_cmd.add_argument(
        "--binary", type=Path, help="package this measured editor binary"
    )
    launch_cmd = commands.add_parser(
        "launch",
        help="start Joinery from its project manager, or safely update and open a closed project",
    )
    launch_cmd.add_argument("--project", type=Path)
    diagnostics = commands.add_parser(
        "diagnostics",
        help="show matching build, addon, provider and transaction status without secrets",
    )
    diagnostics.add_argument("--project", type=Path, required=True)
    bootstrap = commands.add_parser(
        "bootstrap", help="install pinned project servers and offline runtime images"
    )
    bootstrap.add_argument("--project", type=Path, required=True)
    delivery = commands.add_parser(
        "update-addon", help="update a closed project's addon with a reversible backup"
    )
    delivery.add_argument("--project", type=Path, required=True)
    tx_cmd = commands.add_parser(
        "transactions", help="inspect, recover or revert durable project transactions"
    )
    tx_cmd.add_argument("action", choices=["list", "recover", "revert"])
    tx_cmd.add_argument("--project", type=Path, required=True)
    tx_cmd.add_argument("--id", default="")
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
    custom_input = setup.add_mutually_exclusive_group()
    custom_input.add_argument(
        "--custom-mcps", help="explicit custom MCP choices as a JSON array"
    )
    custom_input.add_argument(
        "--custom-mcps-file", type=Path, help="JSON file of explicit custom MCP choices"
    )
    tools = commands.add_parser("tools", help="list tools and their MCP status")
    tools.add_argument("--project", type=Path, default=ROOT)
    tools.add_argument(
        "--json", action="store_true", help="machine-readable, for a UI front end"
    )
    agents_cmd = commands.add_parser(
        "agents", help="list supported agents and gate status"
    )
    agents_cmd.add_argument("--json", action="store_true", help="agent menu data")
    workspace = commands.add_parser(
        "workspace", help="list assets and desktop viewers as JSON"
    )
    workspace.add_argument("--project", type=Path, required=True)
    workspace.add_argument("--since", default="", help="skip unchanged workspace data")
    open_cmd = commands.add_parser(
        "open", help="open an asset's source in a desktop tool"
    )
    open_cmd.add_argument("asset")
    open_cmd.add_argument("--tool", required=True)
    open_cmd.add_argument("--project", type=Path, required=True)
    open_cmd.add_argument("--dry-run", action="store_true")
    doctor = commands.add_parser(
        "doctor", help="check the programs this project's tools need"
    )
    doctor.add_argument("--project", type=Path, default=ROOT)
    rebuild = commands.add_parser(
        "rebuild", help="replay every recipe and check the assets come back"
    )
    rebuild.add_argument("--project", type=Path, default=ROOT / "testbed")
    rebuild.add_argument(
        "--runner", choices=["auto", "sandbox", "host"], default="auto"
    )
    rebuild.add_argument(
        "--record",
        action="store_true",
        help="measure the current outputs and write their fingerprints",
    )
    rebuild.add_argument("--timeout", type=int, default=900)
    lock = commands.add_parser(
        "lock", help="write joinery.lock from what the recipes produced"
    )
    lock.add_argument("--project", type=Path, default=ROOT / "testbed")
    bible = commands.add_parser(
        "bible", help="write a project's art bible from interview answers"
    )
    bible.add_argument("--project", type=Path, required=True)
    bible.add_argument(
        "--approve",
        metavar="YOUR_NAME",
        help="explicitly approve the current completed art bible by its content hash",
    )
    bible.add_argument(
        "--questions",
        action="store_true",
        help="print the interview questions as JSON, for a UI front end",
    )
    auth = commands.add_parser("auth", help="show how to sign an agent in")
    auth.add_argument(
        "agent", nargs="?", choices=[a.id for a in all_agents()], default=None
    )
    connect = commands.add_parser("connect", help="connect a local agent account")
    connect.add_argument("action", choices=["status", "login", "use-key", "configure"])
    connect.add_argument("--agent", choices=[a.id for a in all_agents()])
    chat = commands.add_parser("chat", help="connect the editor to an agent over stdio")
    chat.add_argument("--project", type=Path, required=True)
    chat.add_argument("--agent", choices=[a.id for a in all_agents()], required=True)
    chat.add_argument("--profile", default="")
    chat.add_argument("--addon-protocol", type=int, default=0)
    mcp_cmd = commands.add_parser(
        "mcp", help="install and inspect real project MCP connections"
    )
    mcp_cmd.add_argument("action", choices=["install", "status"])
    mcp_cmd.add_argument("--project", type=Path, required=True)
    mcp_cmd.add_argument("--tool", help="preferred tool id to install")
    mcp_cmd.add_argument(
        "--probe",
        action="store_true",
        help="run the server's read-only inspection probe",
    )
    check = commands.add_parser("check", help="run policy checks")
    check.add_argument("target", choices=["all", "catalog", "deps", "mcp", "assets"])
    check.add_argument("--project", type=Path, default=ROOT / "testbed")
    history.register(commands)
    args = parser.parse_args()

    if args.command == "history":
        return history.run(args)

    if args.command == "diagnostics":
        from joinery.diagnostics import report as diagnostic_report

        print(json.dumps(diagnostic_report(args.project.resolve())))
        return 0

    if args.command in {"package", "launch"}:
        from joinery.distribution import launch, package

        try:
            artifact = (
                package(
                    args.output,
                    desktop=args.desktop,
                    target=args.target,
                    binary=args.binary,
                )
                if args.command == "package"
                else launch(args.project)
            )
            print(json.dumps(artifact))
            return 0
        except (OSError, ValueError) as exc:
            print(json.dumps({"error": str(exc)}))
            return 1

    if args.command in {"bootstrap", "update-addon", "transactions"}:
        from joinery.bootstrap import setup as setup_runtime
        from joinery.bootstrap import transactions, update_addon

        try:
            if args.command == "bootstrap":
                outcome = setup_runtime(args.project.resolve())
            elif args.command == "update-addon":
                outcome = dict(update_addon(args.project.resolve()))
            else:
                if args.action == "revert" and not args.id:
                    raise ValueError(
                        "Choose one accepted transaction with --id before reverting."
                    )
                outcome = {
                    "transactions": transactions(
                        args.project.resolve(), args.action, args.id
                    )
                }
            print(json.dumps(outcome))
            return 0
        except (OSError, ValueError, RuntimeError) as exc:
            print(json.dumps({"error": str(exc)}))
            return 1

    if args.command == "bible" and args.approve:
        from joinery.art_approval import approve

        try:
            approve(args.project.resolve(), args.approve)
            print(
                json.dumps({"approved": True, "project": str(args.project.resolve())})
            )
            return 0
        except (OSError, ValueError) as exc:
            print(json.dumps({"error": str(exc)}))
            return 1

    if args.command == "mcp":
        import asyncio

        from joinery.server_install import inspect_servers, install

        try:
            if args.action == "install":
                if not args.tool:
                    raise ValueError(
                        "Specify --tool with the preferred tool to install."
                    )
                spec = install(args.tool)
                print(json.dumps({"installed": spec.name, "revision": spec.revision}))
            else:
                server_states = asyncio.run(
                    inspect_servers(args.project.resolve(), args.probe)
                )
                print(json.dumps({"servers": server_states}))
                return int(
                    any(
                        item["state"] in {"error", "not-installed"}
                        for item in server_states
                    )
                )
        except (OSError, ValueError, RuntimeError) as exc:
            print(json.dumps({"error": str(exc)}))
            return 1
        return 0
    if args.command == "chat":
        from bridge.stdio import run

        if args.addon_protocol != 2:
            print(
                json.dumps(
                    {
                        "kind": "connection",
                        "state": "error",
                        "agent": args.agent,
                        "message": "This editor addon uses an older tool protocol. Close the project and run joinery update-addon --project <project>, then reopen it.",
                    }
                )
            )
            return 1
        return run(args.project, args.agent, args.profile)

    if args.command == "connect":
        try:
            if args.action == "status":
                result = connection.all_statuses()
            elif args.action == "configure":
                try:
                    payload = json.loads(sys.stdin.readline())
                except ValueError:
                    raise ValueError("Could not read connection settings.") from None
                result = {"profile": configure_api(payload)}
            elif args.agent is None:
                raise ValueError("Choose an agent first.")
            elif args.action == "login":
                result = connection.login(args.agent)
            else:
                result = connection.use_key(args.agent)
        except (TypeError, ValueError, OSError) as exc:
            print(json.dumps({"error": str(exc)}))
            return 1
        print(json.dumps(result))
        return 0

    if args.command in ("workspace", "open"):
        try:
            if args.command == "workspace":
                stamp = watch_stamp(args.project)
                data = (
                    {"unchanged": True} if args.since == stamp else index(args.project)
                )
                print(json.dumps({**data, "stamp": stamp}))
            else:
                argv = open_asset(
                    args.project, args.asset, args.tool, dry_run=args.dry_run
                )
                print(json.dumps({"command": argv, "opened": not args.dry_run}))
        except (ValueError, OSError) as exc:
            print(json.dumps({"error": str(exc)}))
            return 1
        return 0
    if args.command == "hook":
        return hook.main(args.agent)
    if args.command == "new":
        return _new(args.path, args.name, args.tools)
    if args.command == "setup":
        try:
            payload = (
                args.custom_mcps_file.read_text()
                if args.custom_mcps_file is not None
                else args.custom_mcps
            )
        except OSError as exc:
            print(f"setup: {exc}", file=sys.stderr)
            return 1
        return _setup(args.tools, args.project, payload)
    if args.command == "agents":
        if args.json:
            print(
                json.dumps(
                    {"agents": [{"id": a.id, "name": a.name} for a in all_agents()]}
                )
            )
            return 0
        return _agents()
    if args.command == "doctor":
        text, code = doctor_report(args.project / "joinery.toml")
        print(text)
        return code
    if args.command == "rebuild":
        return _rebuild(args.project, args.runner, args.record, args.timeout)
    if args.command == "bible":
        if args.questions:
            print(json.dumps({"pages": grouped()}, indent=2))
            return 0
        try:
            written = write_from_answers_file(args.project)
        except (ValueError, FileExistsError) as exc:
            print(f"bible: {exc}", file=sys.stderr)
            return 1
        if written is None:
            print("bible: no answers file to read; nothing written")
            return 0
        print(f"bible: wrote {written}")
        return 0
    if args.command == "lock":
        problems = write_lock(args.project)
        for problem in problems:
            print(problem, file=sys.stderr)
        if problems:
            print("lock: FAILED (nothing written)")
            return 1
        print(f"lock: wrote {args.project / 'joinery.lock'}")
        return 0
    if args.command == "auth":
        return _auth(args.agent)
    if args.command == "tools":
        catalog = load_catalog()
        # A project that does not exist yet has no selection, which is the
        # normal case when a UI asks for the menu before creating anything.
        config = args.project / "joinery.toml"
        current = load_project_tools(config) if config.is_file() else ProjectTools()
        if args.json:
            # Machine-readable, for front ends that build their own menu. The
            # Project Manager screen in the fork reads this, so the catalog
            # stays the single source of truth instead of being duplicated in
            # C++ where it would drift.
            print(json.dumps(tool_menu(catalog, current), indent=2))
            return 0
        print(render(catalog, set(selection_ids(catalog, current)), current.custom))
        return 0
    return _check(args.target, args.project)
