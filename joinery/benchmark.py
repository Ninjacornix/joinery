"""A fixed set of typical requests, run on a copy of the testbed, to compare harness
changes by median time, tokens and rounds (ADR 0054).

The suite is a TOML file (``evals/benchmark.toml``); each case is one user
request with optional seed files written into the copy first (a script with a
bug to fix, a HUD label to reword). A *driver* runs one request in a prepared
project; every run is measured by the change metrics the chat host already
records (``joinery.metrics``), so the benchmark and a real session read the
same numbers. :class:`LiveDriver` runs the real chat host with a pinned agent
and model; tests run fake agents through the same turn instead.
"""

from __future__ import annotations

import shutil
import statistics
import tomllib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from joinery import headless, metrics

#: The agent and model every run uses, so runs compare.
Pin = headless.Pin

ROOT = Path(__file__).resolve().parent.parent
SUITE = ROOT / "evals/benchmark.toml"
#: Never copied into a run's project: caches and earlier runs' records.
SKIP = shutil.ignore_patterns(".godot", ".joinery", "__pycache__")


@dataclass(frozen=True)
class Case:
    id: str
    kind: str
    request: str
    #: Files written into the copy before the request: {project path: text}.
    seed: dict[str, str] = field(default_factory=dict)
    #: Project paths a good change is expected to touch (for the reader, not scored).
    expect: tuple[str, ...] = ()


@dataclass(frozen=True)
class Suite:
    name: str
    project: Path
    repeats: int
    pin: Pin
    cases: tuple[Case, ...]


def _relative(path: str) -> str:
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts or not candidate.parts:
        raise ValueError(f"A seed path is relative to the project: {path}")
    return candidate.as_posix()


def load(path: Path = SUITE) -> Suite:
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    head = raw.get("suite", {})
    pin = raw.get("pin", {})
    cases: list[Case] = []
    for entry in raw.get("case", []):
        seed = entry.get("seed", {})
        cases.append(
            Case(
                id=str(entry["id"]),
                kind=str(entry["kind"]),
                request=str(entry["request"]).strip(),
                seed={_relative(str(k)): str(v) for k, v in seed.items()},
                expect=tuple(str(p) for p in entry.get("expect", [])),
            )
        )
    ids = [case.id for case in cases]
    if len(set(ids)) != len(ids):
        raise ValueError("Each benchmark case needs its own id.")
    return Suite(
        name=str(head.get("name", path.stem)),
        project=(path.parent / str(head.get("project", "../testbed"))).resolve(),
        repeats=max(int(head.get("repeats", 1)), 1),
        pin=Pin(
            agent=str(pin.get("agent", "")),
            model=str(pin.get("model", "")),
            effort=str(pin.get("effort", "")),
        ),
        cases=tuple(cases),
    )


def prepare(suite: Suite, case: Case, folder: Path) -> Path:
    """A fresh copy of the suite's project with the case's seed files in it."""
    target = folder / case.id
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(suite.project, target, ignore=SKIP)
    for relative, text in case.seed.items():
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return target


Driver = Callable[[Path, Case, Pin], Awaitable[None]]


def measured(project: Path) -> dict[str, Any]:
    """One run's cost: every change its request made, added up."""
    found = metrics.changes(project)
    reported = [c["tokens"] for c in found if c["tokens"] is not None]
    return {
        "changes": len(found),
        "total_ms": sum(c["total_ms"] for c in found),
        "tokens": {
            "input": sum(t["input"] for t in reported),
            "output": sum(t["output"] for t in reported),
            "cached": sum(t["cached"] for t in reported),
        }
        if reported
        else None,
        "rounds": {
            "agent_turns": sum(c["rounds"]["agent_turns"] for c in found),
            "send_backs": sum(c["rounds"]["send_backs"] for c in found),
        },
        "outcome": ",".join(c["decision"] or c["outcome"] for c in found),
    }


async def run(
    suite: Suite,
    driver: Driver,
    folder: Path,
    *,
    pin: Pin | None = None,
    repeats: int = 0,
    only: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Run every case ``repeats`` times; returns each case's runs and medians."""
    chosen = pin or suite.pin
    times = repeats or suite.repeats
    report: dict[str, Any] = {
        "suite": suite.name,
        "agent": chosen.agent,
        "model": chosen.model,
        "effort": chosen.effort,
        "repeats": times,
        "cases": [],
    }
    for case in suite.cases:
        if only and case.id not in only:
            continue
        runs = []
        for _ in range(times):
            project = prepare(suite, case, folder)
            try:
                await driver(project, case, chosen)
                runs.append(measured(project))
            except (OSError, RuntimeError, ValueError, TimeoutError) as exc:
                runs.append({"error": str(exc) or type(exc).__name__})
        good = [r for r in runs if "error" not in r]
        report["cases"].append(
            {"id": case.id, "kind": case.kind, "runs": runs, "median": medians(good)}
        )
    report["median"] = medians(
        [r for c in report["cases"] for r in c["runs"] if "error" not in r]
    )
    return report


def medians(runs: list[dict[str, Any]]) -> dict[str, Any]:
    if not runs:
        return {"runs": 0}
    tokens = [r["tokens"] for r in runs if r["tokens"] is not None]

    def middle(values: list[float]) -> float:
        return round(statistics.median(values), 1)

    return {
        "runs": len(runs),
        "total_ms": middle([r["total_ms"] for r in runs]),
        "input_tokens": middle([t["input"] for t in tokens]) if tokens else None,
        "output_tokens": middle([t["output"] for t in tokens]) if tokens else None,
        "cached_tokens": middle([t["cached"] for t in tokens]) if tokens else None,
        "agent_turns": middle([r["rounds"]["agent_turns"] for r in runs]),
        "send_backs": middle([r["rounds"]["send_backs"] for r in runs]),
    }


def render(report: dict[str, Any]) -> str:
    """The report as a Markdown table, for ``evals/results/<name>.md``."""

    def cell(value: Any, scale: float = 1.0, unit: str = "") -> str:
        if value is None:
            return "n/a"
        return f"{value / scale:.1f}{unit}" if scale != 1.0 else f"{value:g}{unit}"

    pin = report["agent"] + (f" / {report['model']}" if report["model"] else "")
    lines = [
        f"# {report['suite']}: {pin}, {report['repeats']} run(s) per case",
        "",
        "| case | kind | runs | median time | input tokens | cached | output | agent turns | send-backs |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    rows = [(c["id"], c["kind"], c["median"]) for c in report["cases"]]
    rows.append(("**all**", "", report["median"]))
    for case_id, kind, m in rows:
        if not m.get("runs"):
            lines.append(f"| {case_id} | {kind} | 0 | | | | | | |")
            continue
        lines.append(
            f"| {case_id} | {kind} | {m['runs']} | {cell(m['total_ms'], 1000, ' s')}"
            f" | {cell(m['input_tokens'])} | {cell(m['cached_tokens'])}"
            f" | {cell(m['output_tokens'])} | {cell(m['agent_turns'])}"
            f" | {cell(m['send_backs'])} |"
        )
    errors = [
        f"- {c['id']}: {r['error']}"
        for c in report["cases"]
        for r in c["runs"]
        if "error" in r
    ]
    if errors:
        lines += ["", "Failed runs:", *errors]
    return "\n".join(lines) + "\n"


def chat_command(project: Path, pin: Pin) -> list[str]:
    """The chat host the editor runs, for ``project`` with the pinned agent. A
    benchmark accepts each change, so nothing is kept waiting for review."""
    return headless.chat_command(project, pin, keep_reviews=False)


class LiveDriver:
    """Runs one request through the real chat host (``joinery chat``) with the pinned
    agent and model, accepting the change it puts in review (``joinery.headless``,
    the driver ``joinery run`` uses, without the accept).

    There is no editor in a benchmark run: a live scene edit is answered as
    failed, so the agent stages saved files instead. This spends the agent's
    real tokens; run it only on purpose.
    """

    def __init__(
        self,
        timeout: float = 900,
        command: Callable[[Path, Pin], list[str]] | None = None,
    ) -> None:
        self.timeout = timeout
        self.command = command or chat_command

    async def __call__(self, project: Path, case: Case, pin: Pin) -> None:
        if not pin.agent:
            raise ValueError("Pin an agent (--agent) for a live run.")
        await headless.drive(
            self.command(project, pin),
            [headless.prompt_message(case.request, pin)],
            accept=True,
            timeout=self.timeout,
        )
