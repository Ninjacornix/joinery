"""`joinery kit`: export a shared kit, import one, remove one, list them.

Only the user runs import, export and remove (the shared hook refuses them for
every agent): the Kits section's Export kit… and Import kit… buttons, or a
terminal. Import and remove are one change each, left for review unless
`--yes` accepts it at once; `--plan` shows what would happen and changes nothing.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from joinery import kit_archive, shared_kits
from joinery.transactions import FileTransaction

ACTIONS = ("list", "export", "import", "remove")


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("action", choices=ACTIONS)
    parser.add_argument("file", nargs="?", default="", help="import: the .joinerykit")
    parser.add_argument("--project", type=Path)
    parser.add_argument(
        "--name", default="", help="export: the kit's name; remove: which"
    )
    parser.add_argument(
        "--include", action="append", default=[], help="export: a path, folder or glob"
    )
    parser.add_argument(
        "--generator", action="append", default=[], help="export: a kit generator id"
    )
    parser.add_argument(
        "--preset",
        action="append",
        default=[],
        help='export: NAME=GENERATOR:{"param": value}',
    )
    parser.add_argument("--licence", default="", help="export: default the game's")
    parser.add_argument("--version", dest="kit_version", default="1.0.0")
    parser.add_argument("--description", default="")
    parser.add_argument(
        "--custom",
        action="store_true",
        help="export: include assets made with a custom tool (marked as such)",
    )
    parser.add_argument("--out", type=Path, help="export: the .joinerykit to write")
    parser.add_argument("--into", default="", help="import: a folder of kits/")
    parser.add_argument("--plan", action="store_true", help="show it; change nothing")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--yes", action="store_true", help="accept without review")


def main(args: argparse.Namespace) -> int:
    try:
        data = _run(args)
    except (OSError, ValueError, RuntimeError) as exc:
        if args.json or args.action == "list":
            print(json.dumps({"error": str(exc)}))
        else:
            print(f"kit: {exc}", file=sys.stderr)
        return 1
    if args.json or args.action == "list":
        print(json.dumps(data))
        return 0
    print(data.get("summary") or f"{data.get('name')} {data.get('version')}")
    for row in data.get("changes", []) or data.get("files", []):
        print(f"  {row.get('change', row.get('kind', ''))}: {row['path']}")
    if data.get("file"):
        print(f"Wrote {data['file']} ({data['bytes']} bytes).")
    if data.get("state") == "review":
        print(
            "Waiting for your review. Accept with: joinery transactions accept "
            f"--project {args.project} --id {data['transaction_id']}"
        )
    return 0


def _run(args: argparse.Namespace) -> dict[str, Any]:
    if args.project is None:
        raise ValueError("Name the project with --project.")
    project = args.project.resolve()
    if args.action == "list":
        return shared_kits.listing(project)
    if args.action == "export":
        if not args.name.strip():
            raise ValueError("Name the kit with --name.")
        manifest, files = shared_kits.export_plan(
            project,
            args.name,
            include=args.include,
            generators=args.generator,
            presets=args.preset,
            licence=args.licence,
            version=args.kit_version,
            description=args.description,
            allow_custom=args.custom,
        )
        if args.plan:
            return shared_kits.describe(manifest)
        if args.out is None:
            raise ValueError("Say where to save the kit with --out.")
        return shared_kits.export(project, args.out.expanduser(), manifest, files)
    if args.action == "import":
        if not args.file:
            raise ValueError("Name the .joinerykit file to import.")
        archive = kit_archive.read(Path(args.file).expanduser())
        plan = shared_kits.plan_import(project, archive, args.into)
        if args.plan:
            return plan.as_json()
        staged = _stage(
            project,
            plan.summary(),
            lambda stage: shared_kits.apply_import(stage, plan),
            yes=args.yes,
        )
        return {**plan.as_json(), **staged}
    manifest, record = shared_kits.find_installed(project, args.name)
    used = shared_kits.users(project, record)
    if used:
        raise ValueError(
            f"The {manifest.name} kit is still used by " + ", ".join(used[:5]) + "."
            " Take its files out of those first."
        )
    summary = f"Remove the {manifest.name} kit {manifest.version}"
    data: dict[str, Any] = {
        "summary": summary,
        "changes": [{"path": f.path, "change": "removed"} for f in record.files],
    }
    if args.plan:
        return data
    return {
        **data,
        **_stage(
            project,
            summary,
            lambda stage: shared_kits.apply_remove(stage, record),
            yes=args.yes,
        ),
    }


def _stage(project: Path, summary: str, work: Any, *, yes: bool) -> dict[str, Any]:
    tx = FileTransaction(project)
    tx.note_tool("joinery")
    tx.begin(summary)
    try:
        work(tx.stage)
        changes = tx.review()
    except BaseException:
        if tx.state in {"staging", "review"}:
            tx.reject()
        raise
    if yes:
        tx.accept()
    return {"transaction_id": tx.id, "state": tx.state, "reviewed": len(changes)}
