"""`joinery` command line."""

from __future__ import annotations

import argparse
import importlib
import json
import shlex
import shutil
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from joinery import (
    __version__,
    blueprints,
    brief,
    chats,
    claims,
    complex_models,
    connection,
    game_setup,
    godotsteam,
    history,
    hook,
    kit_share_cli,
    kits_cli,
    match_cli,
    playtest,
    references,
    setup_imports,
    share,
    timeline,
)
from joinery.agents import all_agents
from joinery.agents import get as get_agent
from joinery.api_profiles import configure as configure_api
from joinery.auth import for_agent
from joinery.bible import BIBLE_PATH, grouped, write_from_answers_file
from joinery.blockout import command as blockout_command
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
from joinery.transactions import (
    DIRECTORY,
    FileTransaction,
    HistoryPolicy,
    load_policy,
)
from joinery.vcs import ensure_lfs
from joinery.wiring import unwireable, write_agent_wiring
from joinery.workspace import index, open_asset, watch_stamp

#: `joinery mcp status` states that mean a server cannot work yet.
MCP_FAILURES = frozenset({"error", "not-installed", "runtime-missing"})


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


def _setup(
    tools: str | None,
    project: Path,
    custom_mcps: str | None = None,
    policy_args: argparse.Namespace | None = None,
) -> int:
    config = project / "joinery.toml"
    # First setup of a project the wizard is making: its timeline starts here.
    fresh = timeline.looks_new(project)
    try:
        selection = _choose(tools, config, custom_mcps)
        policy = (
            history.policy_from_args(policy_args, _current_policy(project))
            if policy_args is not None
            else None
        )
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
    if policy is not None:
        try:
            print(history.describe_policy(history.set_policy(project, policy)))
        except (OSError, ValueError) as exc:
            print(f"setup: {exc}", file=sys.stderr)
            return 1
    started = _timeline_begin(project, fresh)
    if started:
        print(started)
    return 0


def _current_policy(project: Path) -> HistoryPolicy | None:
    """The saved policy custom numbers start from; None when there is none to read."""
    try:
        return load_policy(project)
    except ValueError:
        return None


def _feature_list(text: str | None) -> list[str] | None:
    """`--features a,b`; None when the flag is absent (the blueprint's defaults)."""
    if text is None:
        return None
    return [f.strip() for f in text.split(",") if f.strip()]


def _defaults(blueprint: str, features: list[str] | None) -> list[str]:
    if features is not None:
        return features
    return list(blueprints.describe(blueprint).get("default_features", []))


def _print_step(step: dict[str, Any]) -> None:
    stream = sys.stderr if step.get("state") == "failed" else sys.stdout
    print(str(step.get("message", "")), file=stream)


def _timeline_begin(project: Path, fresh: bool) -> str:
    """Start a new project's timeline, or fold a creation step into its first entry.

    Returns the sentence to show, or "". Never fails the step it follows.
    """
    try:
        outcome = timeline.begin_project(project, fresh)
    except (OSError, ValueError) as exc:
        return f"Couldn't start the timeline. {exc}"
    if outcome.get("started"):
        return "Keeping a timeline of your changes with git."
    if fresh and outcome.get("state") == "no-git":
        return str(outcome["message"])
    return ""


def _new(
    path: Path,
    name: str | None,
    tools: str | None,
    blueprint: str | None = None,
    features: str | None = None,
    steam_app_id: str | None = None,
    connect: str | None = None,
    policy_args: argparse.Namespace | None = None,
) -> int:
    try:
        policy = history.policy_from_args(policy_args) if policy_args else None
        chosen = _feature_list(features)
        if blueprint is not None:
            chosen = _defaults(blueprint, chosen)
            game_setup.check_choices(blueprint, chosen, steam_app_id, connect)
        picked = [
            setup_imports.parse_reference(text)
            for text in getattr(policy_args, "reference", None) or []
        ]
        documents = [
            Path(text).expanduser()
            for text in getattr(policy_args, "design_doc", None) or []
        ]
        selection = _choose(tools, path / "joinery.toml")
        create_project(
            path,
            name or path.name,
            selection,
            load_catalog(),
            blueprint=blueprint,
            features=chosen or (),
            steam_app_id=steam_app_id,
            connect=connect,
            report=_print_step,
            wizard=True,
            references=picked,
            documents=documents,
        )
        if policy is not None:
            history.write_policy(path.resolve(), policy)
    except (TypeError, ValueError, OSError) as exc:
        print(f"new: {exc}", file=sys.stderr)
        return 1
    if policy is not None:
        print(f"Keeping older versions: {policy.label}.")
    started = _timeline_begin(path, fresh=True)
    if started:
        print(started)
    print(f"Created {path} with: {', '.join(selection.tools)}")
    print("Open it with: " + shlex.join(["joinery", "launch", "--project", str(path)]))
    return 0


def _game_apply(args: argparse.Namespace) -> int:
    """The wizard's game step: one JSON line per finished step."""

    def emit(step: dict[str, Any]) -> None:
        print(json.dumps(step), flush=True)

    # The wizard runs this before `setup`, on a project with no work in it yet.
    fresh = timeline.looks_new(args.project)
    try:
        policy = history.policy_from_args(args)
        chosen = _defaults(args.blueprint, _feature_list(args.features))
        # The flags, and the wizard's files beside the project; all checked first.
        imports = setup_imports.plan_from_args(
            args.project.resolve(),
            args.reference or [],
            args.design_doc or [],
            answers=(setup_imports.REFERENCES_FILE, setup_imports.DOCUMENTS_FILE),
        )
        game_setup.apply_choices(
            args.project.resolve(),
            args.blueprint,
            chosen,
            args.steam_app_id,
            args.connect,
            addon=not args.no_addon,
            report=emit,
            imports=imports,
        )
        if policy is not None:
            history.write_policy(args.project.resolve(), policy)
            emit(
                {
                    "step": "history",
                    "state": "done",
                    "message": f"Keeping older versions: {policy.label}.",
                }
            )
    except (OSError, ValueError, RuntimeError) as exc:
        emit({"step": "error", "state": "failed", "message": str(exc)})
        return 1
    started = _timeline_begin(args.project, fresh)
    if started:
        emit({"step": "timeline", "state": "done", "message": started})
    emit({"step": "finished", "state": "done", "message": "Your game is ready."})
    return 0


def _import_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--reference",
        action="append",
        metavar="PATH[=NOTE]",
        help="a PNG, JPEG or WebP reference image, and what to take from it (repeatable)",
    )
    parser.add_argument(
        "--design-doc",
        action="append",
        metavar="PATH",
        help="a Markdown, text or PDF design document (repeatable)",
    )


def _references(args: argparse.Namespace) -> int:
    """`joinery references add`: the user's files, as one reviewed change."""
    project = args.project.resolve()
    try:
        if not (project / "project.godot").is_file():
            raise ValueError(f"No project.godot in {project}")
        chosen = setup_imports.plan_from_args(
            project, args.reference or [], args.design_doc or []
        )
        if not chosen:
            raise ValueError("Pick at least one reference image or design document.")
        if args.plan:
            print(
                json.dumps(
                    {
                        "changes": chosen.changes(),
                        "references": [p.destination for p in chosen.references],
                        "documents": [p.destination for p in chosen.documents],
                        "notes": setup_imports.plan_notes(project, chosen),
                    },
                    indent=2,
                )
            )
            return 0
        steps: list[dict[str, Any]] = []
        tx = game_setup.stage_change(
            project,
            setup_imports.summary(chosen),
            lambda stage: steps.extend(setup_imports.apply(stage, chosen)),
        )
    except (OSError, ValueError) as exc:
        if args.json:
            print(json.dumps({"error": str(exc)}))
        else:
            print(f"references: {exc}", file=sys.stderr)
        return 1
    return _decide(tx, tx.changes, yes=args.yes, as_json=args.json, after=lambda: steps)


def _blueprints(args: argparse.Namespace) -> int:
    try:
        if args.action == "describe":
            if not args.id:
                raise ValueError("Name the blueprint to describe.")
            print(json.dumps(game_setup.describe(args.id), indent=2))
            return 0
        if args.json:
            print(json.dumps(game_setup.wizard_catalogue(), indent=2))
            return 0
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}))
        return 1
    for blueprint in blueprints.list_blueprints():
        print(f"  {blueprint.id:<18} {blueprint.name}: {blueprint.pitch}")
    return 0


def _features(as_json: bool) -> int:
    listed = blueprints.catalogue_json()["features"]
    if as_json:
        print(json.dumps({"features": listed}, indent=2))
        return 0
    for feature in listed:
        print(f"  {feature['id']:<14} {feature['name']}: {feature['summary']}")
    return 0


def _show_review(tx: FileTransaction, changes: list[dict[str, str]]) -> None:
    print(tx.summary)
    for change in changes:
        if DIRECTORY not in {change["before"], change["after"]}:
            print(f"  {change['change']:<9} {change['path']}")


def _decide(
    tx: FileTransaction,
    changes: list[dict[str, str]],
    *,
    yes: bool,
    as_json: bool,
    after: Callable[[], list[dict[str, Any]]] | None = None,
) -> int:
    """Accept on --yes or a typed yes; otherwise leave the change for review."""
    project = tx.project
    if as_json and not yes:
        print(
            json.dumps(
                {
                    "transaction_id": tx.id,
                    "summary": tx.summary,
                    "state": tx.state,
                    "changes": changes,
                }
            )
        )
        return 0
    if not as_json:
        _show_review(tx, changes)
    if not yes:
        if not sys.stdin.isatty():
            print(
                "Waiting for your review. Accept with: joinery transactions accept "
                f"--project {project} --id {tx.id}"
            )
            return 0
        answer = input("Apply these changes? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            tx.reject()
            print("Nothing changed.")
            return 0
    tx.accept()
    steps = after() if after is not None else []
    if as_json:
        print(
            json.dumps(
                {
                    "transaction_id": tx.id,
                    "state": tx.state,
                    "changes": changes,
                    "steps": steps,
                }
            )
        )
    else:
        print("Done.")
        for step in steps:
            _print_step(step)
            if step.get("offer"):
                print("  " + shlex.join(step["offer"]))
    return 0


def _steam(args: argparse.Namespace) -> int:
    """Show or set the Steam app ID; Steam works without one until it is set."""
    project = Path(args.project).resolve()
    if not (project / "features" / "steam").is_dir():
        print(
            "steam: this project has no Steam feature. Add it first with "
            "`joinery feature add steam`.",
            file=sys.stderr,
        )
        return 1
    current = godotsteam.read_steam(project)
    if args.app_id is not None or args.connect is not None:
        try:
            if args.app_id is not None:
                app = int(game_setup.check_app_id(args.app_id) or 0)
            else:
                app = int(str(current.get("app_id", 0)) or 0)
            connect = args.connect or str(current.get("connect", "steam"))
            if connect not in godotsteam.CONNECT_CHOICES:
                connect = "steam"
            godotsteam.configure(project, app or None, connect)  # type: ignore[arg-type]
        except (ValueError, godotsteam.SteamSetupError) as exc:
            print(f"steam: {exc}", file=sys.stderr)
            return 1
        current = godotsteam.read_steam(project)
    app_id = int(str(current.get("app_id", 0)) or 0)
    connect = str(current.get("connect", "steam"))
    if args.json:
        print(json.dumps({"app_id": app_id, "connect": connect}))
    elif app_id:
        print(f"Steam app ID {app_id}, players connect through {connect}.")
    else:
        print(
            "No Steam app ID yet. Steam stays off in the game until you set one: "
            "joinery steam --project <path> --app-id <digits>."
        )
    return 0


def _feature(args: argparse.Namespace) -> int:
    lock = claims.FeatureLock(args.project.resolve())
    try:
        return _feature_change(args, lock)
    finally:
        lock.release()


def _reviewer(args: argparse.Namespace) -> int:
    """`joinery reviewer`: the Settings page's Reviewer agent switch and model."""
    from joinery.roles import reviewer_settings, set_reviewer

    project = args.project.resolve()
    try:
        if args.set is not None or args.model is not None:
            set_reviewer(
                project,
                enabled=None if args.set is None else args.set == "on",
                model=args.model,
            )
        settings = reviewer_settings(project)
    except (OSError, ValueError) as exc:
        if args.json:
            print(json.dumps({"error": str(exc)}))
        else:
            print(str(exc), file=sys.stderr)
        return 1
    data = {
        "enabled": settings.enabled,
        "model": settings.model,
        "rounds": settings.rounds,
    }
    if args.json:
        print(json.dumps(data))
    else:
        print(
            "Reviewer agent: "
            + ("on" if settings.enabled else "off")
            + (f" ({settings.model})" if settings.model else " (the author's model)")
        )
    return 0


def _feature_change(args: argparse.Namespace, lock: claims.FeatureLock) -> int:
    project = args.project.resolve()
    try:
        if args.action == "list":
            game = blueprints.project_game(project)
            if args.json:
                print(json.dumps({"game": game}))
            elif game is None:
                print("This project was not started from a blueprint.")
            else:
                print(
                    f"{game.get('blueprint')}: {', '.join(game.get('features', [])) or 'no features'}"
                )
            return 0
        if not args.id:
            raise ValueError("Name the feature.")
        # One change to game features at a time, with the chat's agents too.
        holder = claims.FeatureLock.holder(project)
        if holder:
            raise ValueError(claims.features_refusal(holder))
        if args.plan:
            print(
                json.dumps(
                    game_setup.plan_feature(project, args.action, args.id), indent=2
                )
            )
            return 0
        if game_setup.steam_missing(project, args.action, args.id):
            # Steam is already on but its download failed or was skipped:
            # adding it again fetches the extension, with nothing to review.
            step = game_setup.install_steam(project)
            if args.json:
                print(json.dumps({"steps": [step]}))
            else:
                _print_step(step)
            return 1 if step["state"] == "failed" else 0
        held_by = lock.take(f"{args.action.capitalize()} {args.id}")
        if held_by:
            raise ValueError(claims.features_refusal(held_by))
        tx = game_setup.stage_feature(project, args.action, args.id)
    except (OSError, ValueError) as exc:
        if args.json:
            print(json.dumps({"error": str(exc)}))
        else:
            print(f"feature: {exc}", file=sys.stderr)
        return 1
    task = game_setup.follow_up(tx)
    return _decide(
        tx,
        tx.changes,
        yes=args.yes,
        as_json=args.json,
        after=lambda: game_setup.after_accept(project, task),
    )


def _bible_upgrade(project: Path, yes: bool, as_json: bool) -> int:
    """Upgrade art/bible.md to the second format as a reviewed change."""

    def edit(stage: Path) -> None:
        path = stage / BIBLE_PATH
        if not path.is_file():
            raise ValueError("The project has no art bible.")
        text = path.read_text(encoding="utf-8")
        from joinery.bible_upgrade import upgrade_text

        upgraded = upgrade_text(text)
        if upgraded == text:
            raise ValueError("The art bible is already up to date.")
        path.write_text(upgraded, encoding="utf-8")

    try:
        tx = game_setup.stage_change(project.resolve(), "Upgrade the art bible", edit)
    except (OSError, ValueError) as exc:
        if as_json:
            print(json.dumps({"error": str(exc)}))
        else:
            print(f"bible: {exc}", file=sys.stderr)
        return 1

    def after() -> list[dict[str, Any]]:
        # The bible's bytes changed, so its approval no longer matches.
        return [
            {
                "step": "bible",
                "state": "done",
                "message": "The art bible changed. Review it and approve it again.",
            }
        ]

    return _decide(tx, tx.changes, yes=yes, as_json=as_json, after=after)


def _transaction_decision(project: Path, identifier: str, accept: bool) -> int:
    warning = ""
    try:
        if not identifier:
            raise ValueError("Choose the change with --id.")
        tx = FileTransaction.load(project.resolve(), identifier)
        if accept:
            changes = tx.accept()
            steps = game_setup.after_accept(tx.project, game_setup.follow_up(tx))
        else:
            added = references.created(tx.changes)
            tx.reject()
            changes, steps = [], []
            warning = references.orphan_notice(
                references.users_of(tx.project, added), references.DISCARDED
            )
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({"error": str(exc)}))
        return 1
    outcome: dict[str, object] = {
        "transaction_id": tx.id,
        "state": tx.state,
        "changes": changes,
        "steps": steps,
    }
    if warning:
        outcome["warning"] = warning
    if tx.timeline:
        outcome["timeline"] = tx.timeline
    print(json.dumps(outcome))
    return 0


def _brief(project: Path, questions: bool, as_json: bool = False) -> int:
    if questions:
        print(json.dumps({"pages": brief.grouped()}, indent=2))
        return 0
    if as_json:
        try:
            game = blueprints.project_game(project)
        except ValueError as exc:
            print(json.dumps({"error": str(exc)}))
            return 1
        print(json.dumps(brief.summary(project, game), indent=2))
        return 0
    try:
        record = blueprints.read_record(project)
        described = blueprints.describe(record.blueprint) if record else None
        features = record.features if record else []
        written = brief.write_from_answers_file(project, described, features)
        if written is None and (project / brief.BRIEF_PATH).exists():
            print("brief: design/brief.md already exists; nothing written")
            return 0
        if written is None and record is not None:
            # No setup answers: draft it from the starting point alone.
            written = brief.write_brief(project, {}, described, features)
        # The wizard's design documents, when it had no starting point to apply.
        imports = setup_imports.plan_from_args(
            project, answers=(setup_imports.DOCUMENTS_FILE,)
        )
        for step in setup_imports.apply(project, imports):
            _print_step(step)
    except (ValueError, FileExistsError) as exc:
        print(f"brief: {exc}", file=sys.stderr)
        return 1
    if written is None:
        print("brief: no answers file and no starting point; nothing written")
        return 0
    print(f"brief: wrote {written}")
    return 0


def _bible(project: Path) -> int:
    try:
        record = blueprints.read_record(project)
        preset = (
            blueprints.describe(record.blueprint).get("bible_preset")
            if record
            else None
        )
        written = write_from_answers_file(project, preset or None)
        # The wizard's reference images go on the board before the approval.
        imports = setup_imports.plan_from_args(
            project, answers=(setup_imports.REFERENCES_FILE,)
        )
        for step in setup_imports.apply(project, imports):
            _print_step(step)
    except (ValueError, FileExistsError) as exc:
        print(f"bible: {exc}", file=sys.stderr)
        return 1
    if written is None:
        print("bible: no answers file to read; nothing written")
        return 0
    from joinery.art_approval import APPROVED_AT_CREATION, approve_at_creation

    # Drafted from the user's own answers: approved at creation, if complete.
    if approve_at_creation(project):
        print(f"bible: {APPROVED_AT_CREATION}")
        return 0
    print(f"bible: wrote {written}")
    return 0


def _check_bible(project: Path) -> int:
    try:
        # Imported here: the rules engine is heavy and the hook imports this module.
        from joinery import bible_lint

        summary = bible_lint.summary(project)
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}))
        return 1
    print(json.dumps(summary, indent=2))
    return 1 if summary["problems"] else 0


def _check_catalogue() -> int:
    problems = blueprints.validate_catalogue()
    for problem in problems:
        print(problem)
    print(f"check catalogue: {'FAILED' if problems else 'ok'}")
    return 1 if problems else 0


def _status(args: argparse.Namespace) -> int:
    from joinery import readiness

    argv: list[str] = []
    if args.project is not None:
        argv += ["--project", str(args.project)]
    if args.tool:
        argv += ["--tool", args.tool]
    for flag in ("catalog", "refresh", "fill", "deep", "background", "agents", "text"):
        if getattr(args, flag):
            argv.append(f"--{flag}")
    # JSON is the default; --json only says so explicitly.
    return readiness.main(argv)


def _parts() -> Any:
    """The parts commands, when that module is present."""
    try:
        return importlib.import_module("joinery.parts")
    except ImportError:
        return None


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


def _parser() -> argparse.ArgumentParser:
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
        "transactions",
        help="inspect, accept, reject, recover or revert project changes",
    )
    tx_cmd.add_argument(
        "action", choices=["list", "accept", "reject", "recover", "revert"]
    )
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
    new.add_argument("--blueprint", help="start from this playable blueprint")
    new.add_argument(
        "--features",
        help="comma-separated feature ids; without it, the blueprint's defaults",
    )
    new.add_argument("--steam-app-id", help="Steam app ID (digits); empty to add later")
    new.add_argument(
        "--connect",
        choices=list(game_setup.CONNECT_CHOICES),
        help="with Online play and Steam: connect players through Steam or direct IP",
    )
    _import_arguments(new)
    history.add_policy_arguments(new)
    game_cmd = commands.add_parser(
        "game", help="apply the new-project wizard's game choices to a project"
    )
    game_cmd.add_argument("action", choices=["apply"])
    game_cmd.add_argument("--project", type=Path, required=True)
    game_cmd.add_argument("--blueprint", required=True)
    game_cmd.add_argument("--features")
    game_cmd.add_argument("--steam-app-id")
    game_cmd.add_argument("--connect", choices=list(game_setup.CONNECT_CHOICES))
    game_cmd.add_argument(
        "--no-addon", action="store_true", help="the addon is already in place"
    )
    _import_arguments(game_cmd)
    history.add_policy_arguments(game_cmd)
    references_cmd = commands.add_parser(
        "references",
        help="add reference images and design documents as a reviewed change",
    )
    references_cmd.add_argument("action", choices=["add"])
    references_cmd.add_argument("--project", type=Path, required=True)
    _import_arguments(references_cmd)
    references_cmd.add_argument(
        "--plan", action="store_true", help="show what would change; write nothing"
    )
    references_cmd.add_argument("--json", action="store_true")
    references_cmd.add_argument(
        "--yes", action="store_true", help="accept the reviewed change without asking"
    )
    blueprints_cmd = commands.add_parser(
        "blueprints", help="list the playable blueprints a project can start from"
    )
    blueprints_cmd.add_argument("action", nargs="?", choices=["describe"])
    blueprints_cmd.add_argument("id", nargs="?")
    blueprints_cmd.add_argument(
        "--json", action="store_true", help="the wizard's catalogue, for a UI front end"
    )
    features_cmd = commands.add_parser(
        "features", help="list the features a game project can add"
    )
    features_cmd.add_argument("--json", action="store_true")
    feature_cmd = commands.add_parser(
        "feature", help="add or remove a game feature as a reviewed change"
    )
    feature_cmd.add_argument("action", choices=["add", "remove", "list"])
    feature_cmd.add_argument("id", nargs="?")
    feature_cmd.add_argument("--project", type=Path, required=True)
    feature_cmd.add_argument(
        "--plan", action="store_true", help="show what would change; write nothing"
    )
    feature_cmd.add_argument("--json", action="store_true")
    feature_cmd.add_argument(
        "--yes", action="store_true", help="accept the reviewed change without asking"
    )
    match_cmd = commands.add_parser(
        "match", help="audit the project against the art bible; list what drifts"
    )
    match_cli.add_arguments(match_cmd)
    steam_cmd = commands.add_parser(
        "steam", help="show or set the Steam app ID (it can be added any time)"
    )
    steam_cmd.add_argument("--project", type=Path, required=True)
    steam_cmd.add_argument("--app-id", help="Steam app ID (digits); 0 clears it")
    steam_cmd.add_argument("--connect", choices=["steam", "direct"])
    steam_cmd.add_argument("--json", action="store_true")
    playtest_cmd = commands.add_parser(
        "playtest", help="play the game headless with a bot and report what goes wrong"
    )
    playtest.add_arguments(playtest_cmd)
    share_cmd = commands.add_parser(
        "share", help="build the game for players, make store media, publish"
    )
    share.add_arguments(share_cmd)
    blockout_cmd = commands.add_parser(
        "blockout", help="check, rebuild or walk a level blockout layout"
    )
    blockout_command.add_arguments(blockout_cmd)
    kits_cmd = commands.add_parser(
        "kits", help="list kits, preview one, or make models from a kit for review"
    )
    kits_cli.add_arguments(kits_cmd)
    kit_cmd = commands.add_parser(
        "kit", help="export a shared kit, import one for review, remove one, list them"
    )
    kit_share_cli.add_arguments(kit_cmd)
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
    history.add_policy_arguments(setup)
    tools = commands.add_parser("tools", help="list tools and their MCP status")
    tools.add_argument("--project", type=Path, default=ROOT)
    tools.add_argument(
        "--json", action="store_true", help="machine-readable, for a UI front end"
    )
    agents_cmd = commands.add_parser(
        "agents", help="list supported agents and gate status"
    )
    agents_cmd.add_argument("--json", action="store_true", help="agent menu data")
    reviewer_cmd = commands.add_parser(
        "reviewer", help="show or change the agent that reviews each change"
    )
    reviewer_cmd.add_argument("--project", type=Path, default=Path.cwd())
    reviewer_cmd.add_argument("--set", choices=["on", "off"], default=None)
    reviewer_cmd.add_argument(
        "--model", default=None, help="the reviewer's model; empty for the author's"
    )
    reviewer_cmd.add_argument("--json", action="store_true")
    complex_models.add_arguments(
        commands.add_parser(
            "complex-models", help="show or change who makes complex models"
        )
    )
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
    doctor_mode = doctor.add_mutually_exclusive_group()
    doctor_mode.add_argument(
        "--workers",
        action="store_true",
        help="run the checks the cache cannot answer in the workers (the default)",
    )
    doctor_mode.add_argument(
        "--cached", action="store_true", help="answer from the cache only"
    )
    doctor.add_argument("--refresh", action="store_true", help="run every check again")
    status = commands.add_parser(
        "status", help="what each tool can do in this project, as JSON or text"
    )
    status.add_argument("--project", type=Path)
    status.add_argument("--tool")
    status.add_argument("--catalog", action="store_true", help="every catalog tool")
    status.add_argument("--refresh", action="store_true", help="run every check now")
    status.add_argument(
        "--fill", action="store_true", help="run only the checks the cache lacks"
    )
    status.add_argument("--deep", action="store_true", help="include the slow checks")
    status.add_argument(
        "--agents", action="store_true", help="add each agent's connection"
    )
    status.add_argument(
        "--background",
        action="store_true",
        help="answer from the cache and refresh missing checks in the background",
    )
    status_format = status.add_mutually_exclusive_group()
    status_format.add_argument("--text", action="store_true", help="plain text")
    status_format.add_argument("--json", action="store_true", help="JSON (the default)")
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
    bible.add_argument(
        "--upgrade",
        action="store_true",
        help="bring a first-format bible up to date, as a reviewed change",
    )
    bible.add_argument("--yes", action="store_true", help="accept the upgrade")
    bible.add_argument("--json", action="store_true")
    brief_cmd = commands.add_parser(
        "brief", help="write a project's game brief from the setup answers"
    )
    brief_cmd.add_argument("--project", type=Path, required=True)
    brief_cmd.add_argument(
        "--questions",
        action="store_true",
        help="print the setup questions as JSON, for a UI front end",
    )
    brief_cmd.add_argument(
        "--json",
        action="store_true",
        help="print the brief's summary and suggestions as JSON",
    )
    auth = commands.add_parser("auth", help="show how to sign an agent in")
    auth.add_argument(
        "agent", nargs="?", choices=[a.id for a in all_agents()], default=None
    )
    connect = commands.add_parser("connect", help="connect a local agent account")
    connect.add_argument(
        "action", choices=["status", "login", "use-key", "configure", "verify"]
    )
    connect.add_argument("--agent", choices=[a.id for a in all_agents()])
    connect.add_argument(
        "--project", type=Path, help="project to send the test message from (verify)"
    )
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
    check.add_argument(
        "target",
        choices=["all", "catalog", "deps", "mcp", "assets", "bible", "catalogue"],
    )
    check.add_argument("--project", type=Path, default=ROOT / "testbed")
    history.register(commands)
    timeline.register(commands)
    chats.register(commands)
    parts = _parts()
    if parts is not None:
        parts.register(commands)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    if args.command == "history":
        return history.run(args)

    if args.command == "timeline":
        return timeline.run(args)

    if args.command == "chats":
        return chats.run(args)

    if args.command == "parts":
        parts = _parts()
        assert parts is not None  # The parser only offers it when present.
        result: int = parts.run(args)
        return result

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

    if args.command == "transactions" and args.action in {"accept", "reject"}:
        return _transaction_decision(args.project, args.id, args.action == "accept")

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
                if args.action == "list":
                    from joinery.art_approval import status as bible_status

                    outcome["art_bible"] = bible_status(args.project.resolve())
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
                return int(any(item["state"] in MCP_FAILURES for item in server_states))
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
                result_data: Any = connection.all_statuses()
            elif args.action == "configure":
                try:
                    payload = json.loads(sys.stdin.readline())
                except ValueError:
                    raise ValueError("Could not read connection settings.") from None
                result_data = {"profile": configure_api(payload)}
            elif args.agent is None:
                raise ValueError("Choose an agent first.")
            elif args.action == "verify":
                verified = connection.verify(args.agent, args.project or Path.cwd())
                print(json.dumps(verified))
                return 0 if verified.get("state") == "ok" else 1
            elif args.action == "login":
                result_data = connection.login(args.agent)
            else:
                result_data = connection.use_key(args.agent)
        except (TypeError, ValueError, OSError) as exc:
            print(json.dumps({"error": str(exc)}))
            return 1
        print(json.dumps(result_data))
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
                argv_out = open_asset(
                    args.project, args.asset, args.tool, dry_run=args.dry_run
                )
                print(json.dumps({"command": argv_out, "opened": not args.dry_run}))
        except (ValueError, OSError) as exc:
            print(json.dumps({"error": str(exc)}))
            return 1
        return 0
    if args.command == "hook":
        return hook.main(args.agent)
    if args.command == "new":
        return _new(
            args.path,
            args.name,
            args.tools,
            args.blueprint,
            args.features,
            args.steam_app_id,
            args.connect,
            args,
        )
    if args.command == "game":
        return _game_apply(args)
    if args.command == "references":
        return _references(args)
    if args.command == "blueprints":
        return _blueprints(args)
    if args.command == "features":
        return _features(args.json)
    if args.command == "playtest":
        return playtest.main(args)
    if args.command == "share":
        return share.main(args)
    if args.command == "match":
        return match_cli.main(args)
    if args.command == "blockout":
        return blockout_command.main(args)
    if args.command == "kits":
        return kits_cli.main(args)
    if args.command == "kit":
        return kit_share_cli.main(args)
    if args.command == "feature":
        return _feature(args)
    if args.command == "steam":
        return _steam(args)
    if args.command == "status":
        return _status(args)
    if args.command == "setup":
        try:
            payload_text = (
                args.custom_mcps_file.read_text()
                if args.custom_mcps_file is not None
                else args.custom_mcps
            )
        except OSError as exc:
            print(f"setup: {exc}", file=sys.stderr)
            return 1
        return _setup(args.tools, args.project, payload_text, args)
    if args.command == "reviewer":
        return _reviewer(args)
    if args.command == "complex-models":
        return complex_models.main(args)
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
        text, code = doctor_report(
            args.project / "joinery.toml",
            workers=not args.cached,
            refresh=args.refresh,
        )
        print(text)
        return code
    if args.command == "rebuild":
        return _rebuild(args.project, args.runner, args.record, args.timeout)
    if args.command == "bible":
        if args.questions:
            print(json.dumps({"pages": grouped()}, indent=2))
            return 0
        if args.upgrade:
            return _bible_upgrade(args.project, args.yes, args.json)
        code = _bible(args.project)
        # The wizard writes the bible right after setup: it joins the first entry.
        _timeline_begin(args.project, fresh=False)
        return code
    if args.command == "brief":
        code = _brief(args.project, args.questions, args.json)
        if not (args.questions or args.json):
            _timeline_begin(args.project, fresh=False)
        return code
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
            project = args.project.resolve() if config.is_file() else None
            print(json.dumps(tool_menu(catalog, current, project=project), indent=2))
            return 0
        print(render(catalog, set(selection_ids(catalog, current)), current.custom))
        return 0
    if args.target == "bible":
        return _check_bible(args.project)
    if args.target == "catalogue":
        return _check_catalogue()
    return _check(args.target, args.project)
