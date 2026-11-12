"""`joinery kits`: the asset workspace's Kits section, and the same from a terminal.

`list` and `preview` change nothing. `make` and `edit` stage the new models as a
change waiting for review, exactly like an agent's kit_make or kit_edit, unless
`--yes` accepts it at once.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from joinery import kit_groups, kit_preview, kits
from joinery.art_approval import approved
from joinery.transactions import FileTransaction

ACTIONS = ("list", "preview", "make", "edit", "from-blend")
ART_GATE = "Approve the art bible first: in chat, type /art and approve it."


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("action", choices=ACTIONS)
    parser.add_argument(
        "blend", nargs="?", type=Path, help="from-blend: your own .blend file"
    )
    parser.add_argument(
        "--group", default="", help="from-blend: the Geometry Nodes group to wrap"
    )
    parser.add_argument("--id", default="", help="from-blend: the new kit's id")
    parser.add_argument("--label", default="", help="from-blend: the kit's label")
    parser.add_argument(
        "--base",
        choices=kit_groups.BASES,
        default="none",
        help="from-blend: the mesh the group runs on",
    )
    parser.add_argument(
        "--licence",
        default="the user's own work",
        help="from-blend: the .blend's licence, for the lock",
    )
    parser.add_argument("--project", type=Path)
    parser.add_argument("--kit", default="")
    parser.add_argument("--params", default="{}", help="kit parameters as JSON")
    parser.add_argument("--colours", default="{}", help="material colours as JSON")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--name", default="")
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--vary", type=float, default=None)
    parser.add_argument("--folder", default="")
    parser.add_argument("--lods", type=int, default=0)
    parser.add_argument("--asset", default="", help="edit: a model made from a kit")
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--yes", action="store_true", help="accept the change without review"
    )


def _object(text: str, what: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except ValueError:
        raise ValueError(f"{what} is a JSON object.") from None
    if not isinstance(value, dict):
        raise ValueError(f"{what} is a JSON object.")  # noqa: TRY004 - user input
    return value


def main(args: argparse.Namespace) -> int:
    try:
        data = _run(args)
    except (OSError, ValueError, RuntimeError) as exc:
        if args.json or args.action in {"list", "preview"}:
            print(json.dumps({"error": str(exc)}))
        else:
            print(f"kits: {exc}", file=sys.stderr)
        return 1
    if args.json or args.action in {"list", "preview"}:
        print(json.dumps(data))
    else:
        print(data.get("summary", ""))
        if data.get("group"):
            print(
                f"Kit {data['kit']}: parameters "
                + (", ".join(data.get("params", [])) or "none")
                + (
                    "; fixed at the group's values: " + ", ".join(data["fixed"])
                    if data.get("fixed")
                    else ""
                )
            )
        for path in data.get("files", []):
            print("  " + path)
        if data.get("state") == "review":
            print(
                "Waiting for your review. Accept with: joinery transactions accept "
                f"--project {args.project} --id {data['transaction_id']}"
            )
    return 0


def _run(args: argparse.Namespace) -> dict[str, Any]:
    project = args.project.resolve() if args.project else None
    if args.action == "list":
        return kits.describe(project)
    if project is None:
        raise ValueError("Name the project with --project.")
    params = _object(args.params, "--params")
    colours = _object(args.colours, "--colours")
    seed = 1 if args.seed is None else args.seed
    if args.action == "from-blend":
        if args.blend is None:
            raise ValueError("Name your .blend: joinery kits from-blend <file.blend>.")
        blend = args.blend.expanduser().resolve()

        async def wrap(stage: Path) -> dict[str, Any]:
            return await kit_groups.import_group(
                stage,
                blend,
                args.group,
                kit=args.id,
                label=args.label,
                folder=args.folder or "art/props",
                base=args.base,
                licence=args.licence,
            )

        return _stage(
            project, f"Make a kit from the node group {args.group}", wrap, args.yes
        )
    if args.action == "preview":
        return asyncio.run(
            kit_preview.preview(project, args.kit, params, seed=seed, colours=colours)
        )
    if not approved(project):
        raise ValueError(ART_GATE)
    if args.action == "make":
        kit = kits.get(args.kit, project)
        label = kit.label.lower()
        summary = (
            f"Make {args.count} {label}s from the kit"
            if args.count > 1
            else f"Make a {label} from the kit"
        )

        async def make(stage: Path) -> dict[str, Any]:
            name = args.name or kits.free_name(stage, kit.id, args.count, args.folder)
            made = kits.plan(
                stage,
                kit.id,
                params,
                seed=seed,
                name=name,
                count=args.count,
                vary=args.vary,
                folder=args.folder,
                lods=args.lods,
                colours=colours,
            )
            return await kits.make(stage, made)

        return _stage(project, summary, make, args.yes)
    if not args.asset:
        raise ValueError("Name the model to change with --asset.")
    asset = args.asset.removeprefix("res://")

    async def edit(stage: Path) -> dict[str, Any]:
        made, _marker = kits.edit_plan(
            stage, asset, params, seed=args.seed, colours=colours
        )
        return await kits.make(stage, made, replacing=asset)

    name = Path(asset).stem.replace("_", " ")
    return _stage(project, f"Change {name} with its kit", edit, args.yes)


def _stage(project: Path, summary: str, work: Any, yes: bool) -> dict[str, Any]:
    """Run `work` on a staged copy and leave it for review (or accept it)."""
    tx = FileTransaction(project)
    tx.note_tool("blender")
    tx.begin(summary)
    try:
        made: dict[str, Any] = asyncio.run(work(tx.stage))
        changes = tx.review()
    except BaseException:
        if tx.state in {"staging", "review"}:
            tx.reject()
        raise
    if yes:
        tx.accept()
    return {
        "transaction_id": tx.id,
        "summary": tx.summary,
        "state": tx.state,
        "changes": changes,
        "files": [
            c["path"] for c in changes if c.get("after") not in {"", "directory"}
        ],
        "kit": made.get("kit"),
        "models": made.get("models", []),
        "seconds": made.get("seconds"),
        **{
            key: made[key]
            for key in ("group", "params", "fixed", "materials", "seed_input")
            if key in made
        },
    }
