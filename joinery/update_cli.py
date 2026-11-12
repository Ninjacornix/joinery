"""`joinery update` and `joinery verify` (joinery/updater.py, ADR 0083).

joinery update --check [--force] [--json]        # at most once a day unless --force
joinery update --install [--after-exit PID] [--project P] [--progress] [--json]
joinery update --restart | --cancel | --rollback [--after-exit PID]
joinery update --prereleases on|off
joinery update --dismiss 0.6.0                   # the daily check stops offering it
joinery verify Joinery-0.6.0-macos-arm64.zip [--offline] [--json]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from joinery import updater


def add_update(parser: argparse.ArgumentParser) -> None:
    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "--check", action="store_true", help="look for a newer release (default)"
    )
    action.add_argument(
        "--install", action="store_true", help="download, verify and stage it"
    )
    action.add_argument(
        "--restart", action="store_true", help="start Joinery again after the swap"
    )
    action.add_argument(
        "--cancel", action="store_true", help="drop an update waiting for restart"
    )
    action.add_argument(
        "--rollback", action="store_true", help="go back to the kept version"
    )
    action.add_argument(
        "--prereleases", choices=["on", "off"], help="offer alpha, beta and rc"
    )
    action.add_argument(
        "--dismiss", metavar="VERSION", help="stop offering this version daily"
    )
    parser.add_argument(
        "--force", action="store_true", help="check now, not once a day"
    )
    parser.add_argument(
        "--after-exit",
        type=int,
        default=0,
        metavar="PID",
        help="swap once this process exits",
    )
    parser.add_argument(
        "--project",
        action="append",
        default=[],
        type=Path,
        help="update this project's addon",
    )
    parser.add_argument(
        "--progress", action="store_true", help="print progress as JSON lines"
    )
    parser.add_argument("--json", action="store_true")


def add_verify(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("archive", type=Path)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="use Sigstore's built-in trust root, no network",
    )
    parser.add_argument("--json", action="store_true")


def _say(result: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(result))
        return
    if result.get("error"):
        print(result["error"])
    elif "available" in result:
        release = result.get("release") or {}
        if result.get("offline"):
            print(result.get("message", "Offline."))
        elif result["available"]:
            print(
                f"Joinery {release['version']} is available (you have {result['current']})."
            )
            if not release.get("signed"):
                print(
                    "It is not signed, so it can only be downloaded by hand: "
                    + release["url"]
                )
            else:
                print("Install it with: joinery update --install")
        else:
            print(f"Joinery {result['current']} is up to date.")
        if result.get("previous"):
            print(f"Go back to {result['previous']} with: joinery update --rollback")
    elif result.get("verified"):
        print(
            f"{Path(result['archive']).name}: SHA-256 and signature match {result['signed_by']}"
        )
    else:
        print(result.get("message") or json.dumps(result))


def update(args: argparse.Namespace) -> int:
    def progress(stage: str, done: float) -> None:
        if args.progress:
            print(json.dumps({"progress": round(done, 3), "stage": stage}), flush=True)

    projects = [str(p.resolve()) for p in args.project]
    try:
        if args.install:
            result = updater.install(
                wait_pid=args.after_exit, projects=projects, progress=progress
            )
        elif args.rollback:
            result = updater.rollback(wait_pid=args.after_exit, projects=projects)
        elif args.restart:
            result = updater.restart()
        elif args.cancel:
            result = updater.cancel()
        elif args.prereleases:
            result = updater.set_prereleases(args.prereleases == "on")
        elif args.dismiss:
            result = updater.dismiss(args.dismiss)
        else:
            result = updater.check(force=args.force)
    except (OSError, ValueError) as error:
        result = {"error": str(error), "offline": isinstance(error, updater.Offline)}
        _say(result, args.json)
        return 1
    _say(result, args.json)
    return 0


def verify(args: argparse.Namespace) -> int:
    try:
        result = updater.verify(args.archive.resolve(), offline=args.offline)
    except (OSError, ValueError) as error:
        _say({"error": str(error), "verified": False}, args.json)
        return 1
    _say(result, args.json)
    return 0
