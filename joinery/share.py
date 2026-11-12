"""`joinery share …`: build the game for players, make store media, draft the page, publish.

    joinery share status --project P --json
    joinery share build  --project P [--targets web,windows,macos,linux] [--download-templates]
    joinery share media  --project P [--only screenshots,trailer,cover] [--seed N | --new-seed]
    joinery share steam-art --project P
    joinery share page   --project P [--redraft]
    joinery share itch   --project P --target user/game [--channel web,windows] [--dry-run]
                         [--install-butler] [--yes]

With ``--json`` progress goes to stdout as ``{"progress": …}`` lines and the
result is the last line, which is how the Share page reads it. Network use
happens only on the user's explicit flags (``--download-templates``,
``--install-butler``) and in ``itch`` itself, which no agent may run (ADR 0046).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from joinery import (
    export_templates,
    share_build,
    share_itch,
    share_media,
    share_page,
    share_steam,
)
from joinery.pinned import PinnedDownloadError

DEFAULT_TARGETS = "web,windows,macos,linux"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    actions = parser.add_subparsers(dest="share_action", required=True)
    status = actions.add_parser("status", help="what is built and made so far")
    build = actions.add_parser("build", help="export, zip and check the game")
    build.add_argument("--targets", default=DEFAULT_TARGETS)
    build.add_argument(
        "--download-templates",
        action="store_true",
        help="download Godot's pinned export templates (1.2 GB) if they aren't here",
    )
    build.add_argument("--no-playtest", action="store_true")
    media = actions.add_parser("media", help="screenshots, trailer and cover")
    media.add_argument("--only", default=",".join(share_media.KINDS))
    seed = media.add_mutually_exclusive_group()
    seed.add_argument("--seed", type=int, default=0, help="replay this run")
    seed.add_argument("--new-seed", action="store_true", help="play a new run")
    steam = actions.add_parser(
        "steam-art", help="Steam capsules and library images from the screenshots"
    )
    page = actions.add_parser("page", help="draft the itch.io page text")
    page.add_argument("--redraft", action="store_true", help="replace share/page.md")
    itch = actions.add_parser("itch", help="publish the builds to itch.io (you only)")
    itch.add_argument("--target", required=True, help="user/game on itch.io")
    itch.add_argument(
        "--channel", default="", help="channels or targets, comma-separated"
    )
    itch.add_argument(
        "--dry-run", action="store_true", help="show what would be pushed"
    )
    itch.add_argument(
        "--install-butler",
        action="store_true",
        help="allow installing the pinned butler",
    )
    itch.add_argument("--yes", action="store_true", help="don't ask before pushing")
    for sub in (status, build, media, steam, page, itch):
        sub.add_argument("--project", type=Path, required=True)
        sub.add_argument("--json", action="store_true")


def status(project: Path) -> dict[str, Any]:
    project = project.resolve()
    pin = export_templates.load_pin()
    needed = sorted({f for t in share_build.TARGETS.values() for f in t.templates()})
    page = project / share_page.PAGE
    return {
        "name": share_build.game_name(project),
        "host": share_build.host_target(),
        "targets": [
            {"id": t.id, "label": t.label, "channel": t.channel}
            for t in share_build.TARGETS.values()
        ],
        "templates": {
            "version": pin.version,
            "ready": export_templates.available(needed),
            "download_gb": round(pin.size / 1e9, 1),
        },
        "build": share_build.last_report(project),
        "media": share_media.listing(project),
        "steam": share_steam.listing(project),
        "page": page.read_text(encoding="utf-8") if page.is_file() else None,
        "itch": {**share_itch.status(), **share_itch.settings(project)},
    }


def _emit(args: argparse.Namespace, data: dict[str, Any]) -> None:
    print(json.dumps(data) if args.json else json.dumps(data, indent=2), flush=True)


def _progress(args: argparse.Namespace) -> share_build.Progress:
    def say(text: str) -> None:
        if args.json:
            print(json.dumps({"progress": text}), flush=True)
        else:
            print(text + "…", file=sys.stderr, flush=True)

    return say


def _confirmed(args: argparse.Namespace, steps: list[dict[str, Any]]) -> bool:
    if args.yes:
        return True
    if not sys.stdin.isatty():
        return False
    for step in steps:
        print(f"  {step['file']} -> {args.target}:{step['channel']}", file=sys.stderr)
    answer = input(f"Push {len(steps)} build(s) to itch.io as {args.target}? [y/N] ")
    return answer.strip().lower() in {"y", "yes"}


def main(args: argparse.Namespace) -> int:
    project: Path = args.project
    action = args.share_action
    say = _progress(args)
    try:
        if action == "status":
            _emit(args, status(project))
        elif action == "build":
            targets = share_build.parse_targets(args.targets)
            report = share_build.build(
                project,
                targets,
                download_templates=args.download_templates,
                play=not args.no_playtest,
                progress=say,
            )
            _emit(args, report)
            return 0 if all(t.get("ok") for t in report["targets"].values()) else 1
        elif action == "media":
            only = [k.strip() for k in args.only.split(",") if k.strip()]
            if not only or any(k not in share_media.KINDS for k in only):
                raise ValueError("--only takes screenshots, trailer and cover.")
            recipe = asyncio.run(
                share_media.make(
                    project, only, seed=args.seed, new_seed=args.new_seed, progress=say
                )
            )
            _emit(
                args,
                {"recipe": recipe, "media": share_media.listing(project.resolve())},
            )
        elif action == "steam-art":
            entry = asyncio.run(share_steam.make(project, progress=say))
            _emit(
                args,
                {"steam_art": entry, "steam": share_steam.listing(project.resolve())},
            )
            checks = entry.get("checks", [])
            return 0 if all(c.get("status") != "failed" for c in checks) else 1
        elif action == "page":
            _emit(args, share_page.write(project, redraft=args.redraft))
        elif action == "itch":
            wanted = [c.strip() for c in args.channel.split(",") if c.strip()] or None
            if not args.dry_run:
                steps = share_itch.plan(project.resolve(), args.target, wanted)
                if not _confirmed(args, steps):
                    raise share_itch.PublishError("Nothing was pushed (not confirmed).")
            result = share_itch.publish(
                project,
                args.target,
                wanted,
                dry_run=args.dry_run,
                install_butler=args.install_butler,
            )
            _emit(args, result)
            return 0 if args.dry_run or result.get("ok") else 1
    except export_templates.TemplatesMissing as exc:
        _emit(args, {"error": str(exc), "needs": "templates"})
        return 1
    except (
        share_itch.PublishError,
        share_media.MediaError,
        PinnedDownloadError,
        ValueError,
        OSError,
    ) as exc:
        _emit(args, {"error": str(exc)})
        return 1
    return 0
