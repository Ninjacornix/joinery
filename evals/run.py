"""Run an eval suite and write its report.

    uv run python evals/run.py --suite benchmark --list
    uv run python evals/run.py --suite benchmark --live --agent claude --model <id>

The benchmark (evals/benchmark.toml) runs typical requests on fresh copies of
the testbed with a pinned agent and reports median time, tokens and rounds
(joinery.benchmark). Nothing runs an agent without --live: a live run spends
that agent's real tokens.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from joinery import benchmark, idle_runs
from joinery.routing import routing_settings

SUITES = {"benchmark": ROOT / "evals/benchmark.toml"}


def off_peak(
    pin: benchmark.Pin,
    project: Path,
    *,
    explicit_model: bool,
    now: Callable[[], datetime] = datetime.now,
    sleep: Callable[[float], None] = time.sleep,
) -> benchmark.Pin:
    """Wait until ``[agents] idle_hours`` of ``project``, and run on its light model
    (ADR 0061) unless a model was pinned on the command line."""
    config = idle_runs.settings(project)
    if not idle_runs.in_hours(now(), config.hours):
        print(f"Waiting for off-peak hours ({config.hours})…", flush=True)
        while not idle_runs.in_hours(now(), config.hours):
            sleep(60)
    light = routing_settings(project).light_model(pin.agent)
    if light and not explicit_model:
        print(f"Running on the light model: {light}", flush=True)
        return replace(pin, model=light)
    return pin


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--suite", choices=sorted(SUITES), required=True)
    parser.add_argument("--list", action="store_true", help="list the cases and stop")
    parser.add_argument("--live", action="store_true", help="run the real agent")
    parser.add_argument("--agent", default="", help="pin this agent (claude, codex, …)")
    parser.add_argument("--model", default="", help="pin this model")
    parser.add_argument("--effort", default="")
    parser.add_argument("--repeats", type=int, default=0)
    parser.add_argument("--case", action="append", default=[], help="only this case")
    parser.add_argument("--out", type=Path, help="write the Markdown report here")
    parser.add_argument(
        "--off-peak",
        action="store_true",
        help="wait for the testbed's idle_hours, then run on its light model "
        "unless --model pins one (no agent CLI offers a batch API: ADR 0072)",
    )
    args = parser.parse_args(argv)
    suite = benchmark.load(SUITES[args.suite])
    if args.list or not args.live:
        for case in suite.cases:
            print(f"{case.id:18} {case.kind:18} {case.request}")
        if not args.list:
            print("\nNothing was run: add --live (spends the agent's tokens).")
        return 0
    pin = benchmark.Pin(
        agent=args.agent or suite.pin.agent,
        model=args.model or suite.pin.model,
        effort=args.effort or suite.pin.effort,
    )
    if not pin.agent:
        print("Pin an agent with --agent for a live run.", file=sys.stderr)
        return 2
    if args.off_peak:
        pin = off_peak(pin, ROOT / "testbed", explicit_model=bool(args.model))
    with tempfile.TemporaryDirectory(prefix="joinery-benchmark-") as folder:
        report = asyncio.run(
            benchmark.run(
                suite,
                benchmark.LiveDriver(),
                Path(folder),
                pin=pin,
                repeats=args.repeats,
                only=tuple(args.case),
            )
        )
    text = benchmark.render(report)
    if args.out is not None:
        args.out.write_text(text, encoding="utf-8")
        args.out.with_suffix(".json").write_text(json.dumps(report, indent=2) + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
