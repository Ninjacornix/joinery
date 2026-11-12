"""``joinery puzzle check``: can each puzzle level be won, and how hard is it?

``joinery puzzle check --project P`` checks every level in ``design/puzzles/`` and
the memory-card deals; ``joinery puzzle check design/puzzles/pack.toml`` checks one
file; ``--json`` prints the whole report.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from joinery.puzzle.check import Report, check_level_file, check_project


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("action", choices=["check"])
    parser.add_argument(
        "files", nargs="*", type=Path, help="puzzle files (default: the whole project)"
    )
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--json", action="store_true")


def main(args: argparse.Namespace) -> int:
    project: Path = args.project.resolve()
    if args.files:
        report = Report()
        for path in args.files:
            full = path if path.is_absolute() else Path.cwd() / path
            if not full.is_file():
                full = project / path
            try:
                relative = full.resolve().relative_to(project).as_posix()
                root = project
            except ValueError:
                relative, root = full.name, full.resolve().parent
            check_level_file(root, relative, report)
    else:
        report = check_project(project)
    if args.json:
        print(json.dumps(report.as_dict(), indent=2))
    else:
        for level in report.levels:
            print(f"{level['file']}: {level['summary']}")
            if level.get("solution"):
                print(f"  shortest: {level['solution']}")
        if report.cards is not None:
            print(f"{report.cards['summary']}")
        for finding in report.findings:
            print(f"[{finding.severity}] {finding.file}: {finding.text}")
        if not report.checked and not report.findings:
            print("No puzzle levels (design/puzzles/*.toml) and no memory-card deals.")
    return 0 if report.ok else 1
