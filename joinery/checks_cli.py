"""`joinery access` and `joinery perf`: check the game, no model needed.

    joinery access --project P                 # scores and the top findings
    joinery access --project P --json          # everything, for the editor's card
    joinery perf --project P --target web      # against a web browser's budget
    joinery perf --project P --plan            # also the changes Fix all would start

Both play the game with the playtest bot (headless, then in a window that never takes
focus) and never change it. The fixes run in the editor as reviewed changes.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from joinery import access, access_xag, checks_core, perf

SHOWN = 8


def add_access(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--area", action="append", choices=list(access.AREAS))
    parser.add_argument(
        "--plan", action="store_true", help="list the changes Fix all would start"
    )
    parser.add_argument(
        "--no-window",
        action="store_true",
        help="skip the windowed run (no flash check)",
    )


def add_perf(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--target", choices=list(perf.TARGETS), default="")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--area", action="append", choices=list(perf.AREAS))
    parser.add_argument(
        "--plan", action="store_true", help="list the changes Fix all would start"
    )
    parser.add_argument(
        "--no-window", action="store_true", help="measure headless only (no drawing)"
    )


def main(args: argparse.Namespace) -> int:
    project = args.project.resolve()
    if not (project / "project.godot").is_file():
        print(f"{project} is not a Godot project.")
        return 2
    try:
        if args.command == "access":
            report = access.audit(project, window=not args.no_window)
            names = access.AREAS
        else:
            report = perf.audit(project, args.target, window=not args.no_window)
            names = perf.AREAS
    except ValueError as exc:
        print(str(exc))
        return 2
    if args.area:
        report.findings = [f for f in report.findings if f.area in args.area]
        report.areas = [a for a in report.areas if a.id in args.area]
        if report.kind == "access":
            report.facts["xag"] = access_xag.coverage(report.model_dump())
    checks_core.save(project, report)
    planned = (
        checks_core.plan(project, report, areas=args.area, labels=names)
        if args.plan
        else []
    )
    if args.json:
        data = report.model_dump()
        if args.plan:
            data["plan"] = [task.model_dump() for task in planned]
        print(json.dumps(data))
        return 0
    if report.kind == "perf":
        facts = report.facts
        measured = facts.get("measured", {})
        frame = (
            measured.get("frame_ms")
            if measured.get("windowed")
            else measured.get("cpu_frame_ms")
        )
        print(
            f"Target: {facts.get('target_label')} (frame times here × {facts.get('factor')})"
        )
        if frame:
            print(
                f"Frame time here: p50 {frame['p50']:.1f} ms · p95 {frame['p95']:.1f} ms · "
                f"p99 {frame['p99']:.1f} ms"
                + (
                    ""
                    if measured.get("windowed")
                    else " (headless: scripts and physics only)"
                )
            )
    print(report.summary or "Nothing was measured.")
    for finding in report.findings[:SHOWN]:
        how = (
            f"{finding.fix.kind}: {finding.fix.label}"
            if finding.fix
            else "needs you: " + finding.needs_you
        )
        print(f"  {finding.severity:<6} {finding.path}: {finding.message} ({how})")
    if len(report.findings) > SHOWN:
        print(f"  … and {len(report.findings) - SHOWN} more")
    for task in planned:
        print(f"  change: {task.title} ({task.kind}, {', '.join(task.apps)})")
    for error in report.errors:
        print("  not measured: " + error)
    return 0
