"""The blueprint x feature matrix: every starting point, played for real.

For each blueprint the matrix makes three kinds of project through
`joinery.blueprints` (via `joinery.project.create_project`): the blueprint
alone, the blueprint with each feature it supports, and the blueprint with
every compatible feature. Each project is imported in stock Godot, its main
menu booted, its blueprint play test run, and every applied feature's own
headless test run. Where online play is applied, its two-process host and join
test runs too (plus the blueprint's and save-load's own online tests).

`godot_run` gives each project its own HOME, so `user://` (settings, saves,
logs) never leaks between cells or into the real user folder, and cells can
run in parallel processes (`-k` per blueprint).

`python -m tests.blueprints.matrix <report dir>` prints the table of a run.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from functools import partial
from pathlib import Path

from joinery import blueprints
from tests.blueprints.godot_run import (
    Completed,
    boot,
    free_port,
    import_project,
    make_project,
    run_godot,
    run_pair,
)

ONLINE = "online-play"
PLAY_TEST = "res://tests/game/play_test.gd"
PAIR_TEST = "res://tests/features/online-play/pair_test.gd"
LOBBY_TEST = "res://tests/features/online-play/lobby_test.gd"
CARDS_ONLINE_TEST = "res://tests/game/online_test.gd"
SAVE_ONLINE_TEST = "res://tests/features/save-load/online_test.gd"
ONLINE_CHECKS = "online: starting-point checks passed"
#: Blueprints that ship their own two-process online test.
BLUEPRINT_ONLINE: dict[str, tuple[str, str, str]] = {
    "puzzle-cards": (CARDS_ONLINE_TEST, "cards_host", "cards_client"),
}
_DETAIL_CHARS = 4000


# --- the cells -----------------------------------------------------------------


@dataclass(frozen=True)
class Cell:
    """One project of the matrix: a blueprint and the features asked for."""

    blueprint: str
    features: tuple[str, ...]
    kind: str  # "alone" | "single" | "all"

    @property
    def column(self) -> str:
        if self.kind == "alone":
            return "alone"
        if self.kind == "all":
            return "all"
        return self.features[0]

    @property
    def id(self) -> str:
        return f"{self.blueprint}+{self.column}"


def supported_features(blueprint: str) -> list[str]:
    return sorted(f.id for f in blueprints.list_features() if f.supports(blueprint))


def all_compatible(blueprint: str) -> tuple[str, ...]:
    """The largest feature set that resolves, adding features in id order."""
    chosen: list[str] = []
    for feature in supported_features(blueprint):
        if not blueprints.resolve(blueprint, [*chosen, feature]).problems:
            chosen.append(feature)
    return tuple(chosen)


def cells() -> list[Cell]:
    """Every blueprint: alone, with each supported feature, with all of them."""
    found: list[Cell] = []
    for blueprint in sorted(b.id for b in blueprints.list_blueprints()):
        found.append(Cell(blueprint, (), "alone"))
        found += [Cell(blueprint, (f,), "single") for f in supported_features(blueprint)]
        found.append(Cell(blueprint, all_compatible(blueprint), "all"))
    return found


# --- the steps -----------------------------------------------------------------


@dataclass(frozen=True)
class Step:
    """One check run against a cell's project."""

    name: str
    run: Callable[[Path], Completed | tuple[Completed, Completed] | None]


@dataclass
class StepResult:
    name: str
    seconds: float
    ok: bool
    detail: str = ""


@dataclass
class CellResult:
    cell: str
    blueprint: str
    column: str
    requested: list[str]
    applied: list[str]
    added: dict[str, str]
    steps: list[StepResult] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return all(step.ok for step in self.steps)

    def failures(self) -> list[StepResult]:
        return [step for step in self.steps if not step.ok]


def _expect(outputs: Sequence[Completed], *needles: str) -> None:
    for result in outputs:
        missing = [n for n in needles if n not in result.output]
        if missing:
            tail = "\n".join(result.output.splitlines()[-40:])
            raise AssertionError(f"missing {missing!r} in {' '.join(result.args)}\n{tail}")


def _lan() -> list[str]:
    return [f"--lan-port={free_port()}", "--lan-broadcast=127.0.0.1"]


def feature_steps(
    feature: str, blueprint: str, applied: Sequence[str], work: Path, *, alone: bool
) -> list[Step]:
    """The headless checks a feature ships, as its own pytest file runs them."""

    def settings(name: str) -> str:
        return f"--settings-path={work / 'settings' / name}.cfg"

    if feature == "controller":
        test = "res://tests/features/controller/controller_test.gd"
        args = ("--autostart", settings("controller"))
        return [
            Step("controller", lambda p: run_godot(p, test, args, 240, marker="controller")),
            Step(
                "controller:verify",
                lambda p: run_godot(
                    p, test, (*args, "--controller-phase=verify"), 180, marker="controller"
                ),
            ),
        ]
    if feature == "save-load":
        test = "res://tests/features/save-load/save_load_test.gd"
        args = ("--autostart", settings("save-load"))
        steps = [Step("save-load", lambda p: run_godot(p, test, args, 240, marker="save-load"))]
        if ONLINE in applied:
            steps.append(
                Step(
                    "save-load:online-pair",
                    lambda p: run_pair(
                        p,
                        ("--host", "--port={port}", "--autostart", settings("save-host"), *_lan()),
                        ("--join=127.0.0.1:{port}", "--autostart", settings("save-client")),
                        timeout=180,
                        script=SAVE_ONLINE_TEST,
                        host_marker="save-load host",
                        client_marker="save-load client",
                    ),
                )
            )
        return steps
    if feature == "steam":
        test = "res://tests/features/steam/steam_test.gd"
        extra = _lan() if ONLINE in applied else []

        def steam(p: Path) -> Completed:
            result = run_godot(
                p, test, ("--autostart", settings("steam"), *extra), 180, marker="steam"
            )
            if ONLINE in applied:
                _expect([result], "steam bridge: checked")
            return result

        return [Step("steam", steam)]
    if feature == "translations":
        test = "res://tests/features/translations/translations_test.gd"
        return [
            Step(
                "translations",
                lambda p: run_godot(p, test, (settings("translations"),), 180, marker="translations"),
            )
        ]
    if feature == "vr":
        test = "res://tests/features/vr/vr_test.gd"
        return [
            Step(
                "vr:desktop",
                lambda p: run_godot(p, test, ("--autostart", settings("vr")), 240, marker="vr"),
            ),
            Step(
                "vr:simulated",
                lambda p: run_godot(
                    p, test, ("--autostart", settings("vr-rig"), "--vr-simulate"), 240,
                    marker="vr",
                ),
            ),
        ]
    if feature == ONLINE:
        return online_steps(blueprint, work, dedicated=alone)
    return []


def online_steps(blueprint: str, work: Path, *, dedicated: bool) -> list[Step]:
    """Online play's lobby page in one process, then real two-process games."""

    def settings(name: str) -> str:
        return f"--settings-path={work / 'settings' / name}.cfg"

    def lobby(p: Path) -> Completed:
        args = (f"--port={free_port()}", *_lan(), settings("lobby"))
        return run_godot(p, LOBBY_TEST, args, 180, marker="online_lobby")

    def pair(p: Path) -> tuple[Completed, Completed]:
        lan = _lan()
        host, client = run_pair(
            p,
            ["--host", "--port={port}", "--autostart", *lan, settings("net-host")],
            ["--join=127.0.0.1:{port}", "--autostart", *lan, settings("net-client")],
            timeout=180,
            script=PAIR_TEST,
        )
        _expect([host], "online_host: ok", ONLINE_CHECKS)
        _expect([client], "online_client: ok", ONLINE_CHECKS)
        return host, client

    def server(p: Path) -> tuple[Completed, Completed]:
        host, client = run_pair(
            p,
            ["--server", "--port={port}", *_lan(), settings("net-server")],
            ["--join=127.0.0.1:{port}", "--autostart", settings("net-joiner")],
            timeout=180,
            script=PAIR_TEST,
        )
        _expect([host], "online_server: ok", "Online: dedicated server listening on port")
        _expect([host, client], ONLINE_CHECKS)
        return host, client

    steps = [Step("online-play:lobby", lobby), Step("online-play:pair", pair)]
    if dedicated:
        steps.append(Step("online-play:dedicated", server))
    if blueprint in BLUEPRINT_ONLINE:
        script, host_marker, client_marker = BLUEPRINT_ONLINE[blueprint]
        steps.append(
            Step(
                f"{blueprint}:online-pair",
                lambda p: run_pair(
                    p,
                    ("--host", "--port={port}", "--autostart", *_lan(), settings("bp-host")),
                    ("--join=127.0.0.1:{port}", "--autostart", settings("bp-client")),
                    timeout=180,
                    script=script,
                    host_marker=host_marker,
                    client_marker=client_marker,
                ),
            )
        )
    return steps


def cell_steps(cell: Cell, applied: Sequence[str], work: Path) -> list[Step]:
    play_args = ("--autostart", f"--settings-path={work / 'settings' / 'play'}.cfg")
    steps = [
        Step("import", lambda p: import_project(p, timeout=300)),
        Step(
            "menu",
            lambda p: boot(p, 60, (f"--settings-path={work / 'settings' / 'menu'}.cfg",)),
        ),
        Step(
            "play",
            lambda p: run_godot(p, PLAY_TEST, play_args, 300, marker=cell.blueprint),
        ),
    ]
    for feature in applied:
        steps += feature_steps(
            feature, cell.blueprint, applied, work, alone=cell.kind == "single"
        )
    return steps


# --- running a cell ------------------------------------------------------------


def _detail(exc: BaseException) -> str:
    text = str(exc)
    if isinstance(exc, subprocess.TimeoutExpired):
        text = f"timed out after {exc.timeout} s: {' '.join(map(str, exc.cmd))}"
    return text[-_DETAIL_CHARS:]


def _timed(name: str, action: Callable[[], object]) -> StepResult:
    start = time.monotonic()
    try:
        action()
    except (AssertionError, subprocess.TimeoutExpired, ValueError, OSError) as exc:
        return StepResult(name, round(time.monotonic() - start, 1), False, _detail(exc))
    return StepResult(name, round(time.monotonic() - start, 1), True)


def run_cell(cell: Cell, work: Path) -> CellResult:
    """Apply, import and run every check of one cell; never stops at a failure.

    A failed import stops the Godot steps (nothing else can run without it).
    """
    started = time.monotonic()
    resolution = blueprints.resolve(cell.blueprint, cell.features)
    applied = [f.id for f in resolution.features]
    result = CellResult(
        cell=cell.id,
        blueprint=cell.blueprint,
        column=cell.column,
        requested=list(cell.features),
        applied=applied,
        added=dict(resolution.added),
    )
    if resolution.problems:
        result.steps.append(StepResult("resolve", 0.0, False, "; ".join(resolution.problems)))
        return result
    projects: list[Path] = []

    def apply() -> None:
        project = make_project(work, cell.blueprint, cell.features)
        plan = blueprints.plan(project, cell.blueprint, cell.features)
        assert plan.conflicts == [], f"applying again conflicts: {plan.conflicts}"
        assert plan.changes == [], f"applying again changes: {plan.changes}"
        projects.append(project)

    result.steps.append(_timed("apply", apply))
    if projects:
        project = projects[0]
        for step in cell_steps(cell, applied, work):
            outcome = _timed(step.name, partial(step.run, project))
            result.steps.append(outcome)
            if step.name == "import" and not outcome.ok:
                break
    result.seconds = round(time.monotonic() - started, 1)
    return result


# --- the report ----------------------------------------------------------------


def write_result(result: CellResult, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{result.cell}.json"
    data = asdict(result) | {"ok": result.ok}
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def load_results(directory: Path) -> list[dict[str, object]]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(directory.glob("*.json"))
    ]


def render(results: Sequence[dict[str, object]]) -> str:
    """A markdown pass/fail table (rows: blueprints; columns: feature sets)."""
    feature_ids = sorted(f.id for f in blueprints.list_features())
    columns = ["alone", *feature_ids, "all"]
    table: dict[str, dict[str, dict[str, object]]] = {}
    for row in results:
        table.setdefault(str(row["blueprint"]), {})[str(row["column"])] = row
    lines = [
        "| blueprint | " + " | ".join(columns) + " |",
        "|---|" + "---|" * len(columns),
    ]
    total = 0.0
    for blueprint in sorted(table):
        cells_text: list[str] = []
        for column in columns:
            entry = table[blueprint].get(column)
            if entry is None:
                cells_text.append("-")
                continue
            seconds = float(str(entry["seconds"]))
            total += seconds
            verdict = "pass" if entry["ok"] else "**FAIL**"
            cells_text.append(f"{verdict} {seconds:.0f} s")
        lines.append(f"| {blueprint} | " + " | ".join(cells_text) + " |")
    lines.append("")
    lines.append(f"Cells: {len(results)}, failed: "
                 f"{sum(1 for r in results if not r['ok'])}, Godot time: {total:.0f} s")
    failures: list[str] = []
    for row in results:
        steps = row["steps"]
        assert isinstance(steps, list)
        for step in steps:
            if not step["ok"]:
                first = str(step["detail"]).strip().splitlines()[:3]
                failures.append(f"- {row['cell']} {step['name']}: " + " / ".join(first))
    if failures:
        lines += ["", "Failures:", *failures]
    lines += ["", "| cell | applied | steps (s) |", "|---|---|---|"]
    for row in results:
        steps = row["steps"]
        assert isinstance(steps, list)
        timing = ", ".join(
            f"{s['name']} {s['seconds']}" + ("" if s["ok"] else " FAIL") for s in steps
        )
        applied = row["applied"]
        assert isinstance(applied, list)
        lines.append(f"| {row['cell']} | {', '.join(applied) or '-'} | {timing} |")
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str]) -> int:
    if len(argv) != 1:
        print("usage: python -m tests.blueprints.matrix <report dir>")
        return 2
    print(render(load_results(Path(argv[0]))), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
