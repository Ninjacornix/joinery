"""`joinery tidy`: what the game never uses, and taking it out as one reviewed change.

    joinery tidy --project P                  # one line per finding, sure ones first
    joinery tidy --project P --json           # everything, for the editor's tidy card
    joinery tidy --project P --plan [--id X]  # what removing would delete and edit
    joinery tidy --project P --remove [--id X] [--yes]
                                              # stage the removal for review (--yes: accept)
    joinery tidy --project P --keep KEY       # leave it alone ([tidy] keep); --unkeep KEY
    joinery tidy --project P --clear-caches   # free Joinery's own caches (no review)

Without ``--id``, ``--plan`` and ``--remove`` take every sure finding; "maybe used" ones
only by id. The check itself never changes the project.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from joinery import tidy, tidy_caches, tidy_remove, tidy_report
from joinery.tidy import human_size
from joinery.transactions import DIRECTORY, FileTransaction

#: Findings the plain report lists before "and N more".
SHOWN = 20


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--id", action="append", default=[], help="a finding's id")
    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "--plan", action="store_true", help="show what removing changes"
    )
    action.add_argument(
        "--remove", action="store_true", help="stage the removal for review"
    )
    action.add_argument(
        "--keep", metavar="KEY", help="leave a finding alone ([tidy] keep)"
    )
    action.add_argument("--unkeep", metavar="KEY", help="take a key out of [tidy] keep")
    action.add_argument(
        "--clear-caches", action="store_true", help="free Joinery's caches"
    )
    parser.add_argument("--yes", action="store_true", help="accept the removal at once")


def _out(args: argparse.Namespace, data: dict[str, Any], text: str) -> None:
    print(json.dumps(data) if args.json else text)


def main(args: argparse.Namespace) -> int:
    project = args.project.resolve()
    if not (project / "project.godot").is_file():
        message = f"{project} is not a Godot project."
        _out(args, {"error": message}, message)
        return 1
    try:
        return _run(args, project)
    except (OSError, ValueError) as exc:
        _out(args, {"error": str(exc)}, f"tidy: {exc}")
        return 1


def _run(args: argparse.Namespace, project: Path) -> int:
    if args.clear_caches:
        cleared = tidy_caches.clear(project)
        total = sum(c.size for c in cleared)
        text = (
            f"Freed {human_size(total)} of Joinery's caches: "
            + "; ".join(f"{c.what} ({human_size(c.size)})" for c in cleared)
            if cleared
            else "Joinery's caches are already clear."
        )
        _out(
            args,
            {
                "cleared": [c.model_dump() for c in cleared],
                "size": total,
                "message": text,
            },
            text,
        )
        return 0
    if args.keep or args.unkeep:
        report = tidy_report.check(project)
        key = tidy_remove.keep_key(report, args.keep or args.unkeep)
        listed = tidy.set_keep(project, key, keep=bool(args.keep))
        text = (
            f"Kept {key}: tidy leaves it alone."
            if args.keep
            else f"{key} is checked again."
        )
        _out(args, {"keep": listed, "key": key, "message": text}, text)
        return 0
    graph = tidy.build(project)
    report = tidy_report.check(project, graph)
    if args.plan or args.remove:
        removal = tidy_remove.plan(graph, report, args.id)
        if args.plan or removal.problems:
            data = removal.model_dump() | {
                "summary": removal.summary(),
                "words": tidy_remove.describe(removal),
            }
            data["edits"] = sorted(removal.edits)
            text = "\n".join(
                tidy_remove.describe(removal)
                + [f"  delete {c.path} ({c.what})" for c in removal.delete]
                + [f"  problem: {p}" for p in removal.problems]
            )
            _out(args, data, text)
            return 1 if removal.problems and args.remove else 0
        if not removal.ids:
            _out(args, {"error": "Nothing to remove."}, "Nothing to remove.")
            return 1
        return _stage(args, project, removal)
    if args.json:
        print(json.dumps(report.model_dump()))
        return 0
    print(report.summary + ".")
    for finding in report.findings[:SHOWN]:
        size = f" {human_size(finding.size)}" if finding.size else ""
        mark = "" if finding.confidence == "unused" else " (maybe used)"
        print(f"  {finding.id}  {finding.title}{size}{mark}: {finding.why}")
    if len(report.findings) > SHOWN:
        print(f"  … and {len(report.findings) - SHOWN} more (--json for all)")
    if report.cache_size:
        print(
            f"Joinery's caches: {human_size(report.cache_size)} "
            "(joinery tidy --clear-caches frees them)."
        )
    return 0


def stage(project: Path, removal: tidy_remove.Removal) -> FileTransaction:
    """The removal as a change waiting for review: nothing in the project moves yet."""
    tx = FileTransaction(project)
    tx.note_tool("joinery")
    tx.begin(removal.summary())
    try:
        tidy_remove.apply(tx.stage, removal)
        tx.review()
    except BaseException:
        if tx.state in {"staging", "review"}:
            tx.reject()
        raise
    return tx


def _stage(
    args: argparse.Namespace, project: Path, removal: tidy_remove.Removal
) -> int:
    tx = stage(project, removal)
    if args.yes:
        tx.accept()
    files = [
        c["path"] for c in tx.changes if DIRECTORY not in {c["before"], c["after"]}
    ]
    data = {
        "transaction_id": tx.id,
        "summary": tx.summary,
        "state": tx.state,
        "files": files,
        "size": removal.size,
        "words": tidy_remove.describe(removal),
    }
    text = "\n".join(tidy_remove.describe(removal))
    if tx.state == "review":
        text += (
            "\nWaiting for your review. Accept with: joinery transactions accept "
            f"--project {project} --id {tx.id}"
        )
    _out(args, data, text)
    return 0
