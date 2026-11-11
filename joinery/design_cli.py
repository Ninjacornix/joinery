"""`joinery design`: check the game's design against its brief, read-only.

    joinery design --project P            # one line per problem, most serious first
    joinery design --project P --json     # everything, for the editor's design card
    joinery design --project P --every 5  # check after every 5 accepted changes (0: off)
    joinery design --project P --settings --json   # read that setting

This command never changes the game. A finding's fix is a normal request the user
sends to Journeyman from the editor ("Ask agent to fix").
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from joinery import design

#: Findings the plain report lists before "and N more".
SHOWN = 10


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--every",
        type=int,
        metavar="N",
        help="check the design after every N accepted changes (0: off)",
    )
    parser.add_argument(
        "--settings",
        action="store_true",
        help="print the design_check_every setting only",
    )


def main(args: argparse.Namespace) -> int:
    project = args.project.resolve()
    if args.every is not None or args.settings:
        try:
            every = (
                design.set_check_every(project, args.every)
                if args.every is not None
                else design.check_every(project)
            )
        except (OSError, ValueError) as exc:
            print(json.dumps({"error": str(exc)}) if args.json else str(exc))
            return 1
        print(
            json.dumps({"check_every": every, "choices": list(design.EVERY_CHOICES)})
            if args.json
            else f"Design check after every {every} accepted changes."
            if every
            else "The design is checked only when you ask (/design)."
        )
        return 0
    if not (project / "project.godot").is_file():
        message = f"{project} is not a Godot project."
        print(json.dumps({"error": message}) if args.json else message)
        return 1
    report = design.check(project)
    if args.json:
        print(json.dumps(report.model_dump()))
        return 0
    print(report.summary + ".")
    for finding in report.findings[:SHOWN]:
        where = finding.evidence[0].where() if finding.evidence else ""
        print(f"  {finding.severity:<6} {finding.title}  [{where}]")
    if len(report.findings) > SHOWN:
        print(f"  … and {len(report.findings) - SHOWN} more (--json for all)")
    for line in report.skipped:
        print("  not checked: " + line)
    return 0
