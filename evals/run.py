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
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from joinery import benchmark

SUITES = {"benchmark": ROOT / "evals/benchmark.toml"}


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
