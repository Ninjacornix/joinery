"""`joinery match`: audit the project against the art bible, no model needed.

    joinery match --project P            # scores and the top findings
    joinery match --project P --json     # everything, for the editor's Match card
    joinery match --project P --plan     # also the reviewed changes "Fix all" would start
    joinery match --project P --final art/props/crate.glb   # never touch it

The fixes themselves run in the editor, as parallel tasks whose changes are
reviewed there (the chat host's ``match_fix``); this command never changes an
asset.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from joinery import match, match_plan
from joinery.authoring import run_offline

#: Findings the plain report lists before "and N more".
SHOWN = 8


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--area",
        action="append",
        choices=list(match.AREAS),
        help="only this area (repeat for more)",
    )
    parser.add_argument(
        "--plan", action="store_true", help="list the changes Fix all would start"
    )
    parser.add_argument(
        "--final", metavar="PATH", help="mark an asset final: match never changes it"
    )
    parser.add_argument(
        "--not-final", metavar="PATH", help="let match change an asset again"
    )


def main(args: argparse.Namespace) -> int:
    project = args.project.resolve()
    if args.final or args.not_final:
        final = match.mark_final(
            project, args.final or args.not_final, bool(args.final)
        )
        print(
            json.dumps({"final": final})
            if args.json
            else "Final: " + (", ".join(final) or "none")
        )
        return 0
    audit = asyncio.run(
        match.audit(
            project,
            lambda argv, root: run_offline(argv, root, 600, readonly=True),
            areas=args.area,
        )
    )
    planned = match_plan.plan(project, audit, areas=args.area) if args.plan else []
    if args.json:
        data = audit.model_dump()
        if args.plan:
            data["plan"] = [task.model_dump() for task in planned]
        print(json.dumps(data))
        return 0
    if not audit.rules:
        print("The art bible has no checked rules, so there is nothing to match yet.")
    print(audit.summary or "Nothing the art bible checks is in this project yet.")
    for finding in audit.findings[:SHOWN]:
        how = finding.fix.label if finding.fix else "needs you: " + finding.needs_you
        print(f"  {finding.severity:<6} {finding.path}: {finding.message} ({how})")
    if len(audit.findings) > SHOWN:
        print(f"  … and {len(audit.findings) - SHOWN} more")
    needs = [f for f in audit.findings if f.fix is None]
    if needs:
        print(
            f"  {len(needs)} need you: "
            + ", ".join(dict.fromkeys(f.path for f in needs))
        )
    for task in planned:
        print(f"  change: {task.title} ({', '.join(task.apps)})")
    for error in audit.errors:
        print("  not measured: " + error)
    return 0
