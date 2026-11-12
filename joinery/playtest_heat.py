"""Playtest heatmaps: where the bot died, got stuck and spent its time, over recent runs.

Each playtest's bot reports where the player was when a level was lost or the game
respawned it, where it was stuck (a softlock or a fall out of the world), and how many
frames it stood in each cell of the coverage grid (``joinery/playtest_bot/bot_cover.gd``).
:func:`run_heat` turns one run's facts into points on the ground plane (x, z in 3D; x, y
in 2D), kept in the run's ``report.json`` as ``heat``. :func:`aggregate` lays the last N
runs of the same scene over one grid (the newest run's coverage grid), so the playtest
card can paint deaths, stuck points and time spent on the same top-down map the coverage
feature draws, and an agent gets the hotspots in world units.
"""

from __future__ import annotations

import json
import math
import tomllib
from pathlib import Path
from typing import Any

#: Runs aggregated by default (``[agents] heatmap_runs``); never more than are kept.
DEFAULT_RUNS = 5
#: A respawn this soon after a lost level is the same death, not another one.
SAME_DEATH_SECONDS = 3.0
#: Hotspots listed per layer.
HOTSPOTS = 3
LAYERS = ("deaths", "stuck", "time")
#: Findings that mean the player was stuck where it stood.
STUCK_KINDS = frozenset({"softlock", "fell"})


def runs_wanted(project: Path) -> int:
    """``[agents] heatmap_runs``: how many recent playtests the heatmaps cover (1 to 10)."""
    from joinery.playtest import KEEP

    try:
        raw = tomllib.loads((project / "joinery.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    agents = raw.get("agents", {})
    value = agents.get("heatmap_runs", DEFAULT_RUNS) if isinstance(agents, dict) else 0
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        value = DEFAULT_RUNS
    return min(value, KEEP)


def _plane(at: Any, mode: str) -> list[float] | None:
    """A bot position [x, y, z] on the ground plane: [x, z] in 3D, [x, y] in 2D."""
    if not isinstance(at, (list, tuple)) or len(at) < 3:
        return None
    try:
        x, y, z = (float(at[0]), float(at[1]), float(at[2]))
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in (x, y, z)):
        return None
    return [round(x, 2), round(z if mode == "3d" else y, 2)]


def run_heat(bot: dict[str, Any] | None) -> dict[str, Any]:
    """One run's heat: {mode, cell, deaths: [{at, t, reason}], stuck: [{at, t, kind}],
    time: [{at, seconds}]}; {} when the bot mapped nothing."""
    if not bot:
        return {}
    coverage = bot.get("coverage") or {}
    mode = str(coverage.get("mode", "") or "")
    if not mode:
        return {}
    fps = int(bot.get("fps", 60) or 60)
    deaths: list[dict[str, Any]] = []
    lost: list[float] = []
    for death in bot.get("deaths", []) or []:
        if not isinstance(death, dict):
            continue
        point = _plane(death.get("at"), mode)
        if point is None:
            continue
        t = float(death.get("t", 0) or 0)
        lost.append(t)
        deaths.append({"at": point, "t": t, "reason": str(death.get("reason", ""))})
    for respawn in (bot.get("perf") or {}).get("respawn_at", []) or []:
        if not isinstance(respawn, dict):
            continue
        point = _plane(respawn.get("at"), mode)
        if point is None:
            continue
        t = int(respawn.get("frame", 0) or 0) / max(fps, 1)
        if any(0 <= t - when <= SAME_DEATH_SECONDS for when in lost):
            continue
        deaths.append({"at": point, "t": round(t, 2), "reason": "respawned"})
    stuck = []
    for finding in bot.get("findings", []) or []:
        if not isinstance(finding, dict) or finding.get("kind") not in STUCK_KINDS:
            continue
        point = _plane(finding.get("at"), mode)
        if point is not None:
            stuck.append(
                {
                    "at": point,
                    "t": float(finding.get("t", 0) or 0),
                    "kind": str(finding["kind"]),
                }
            )
    cell = float(coverage.get("cell", 1) or 1)
    origin = coverage.get("origin") or [0, 0]
    time = []
    for entry in coverage.get("dwell", []) or []:
        if not isinstance(entry, (list, tuple)) or len(entry) < 3:
            continue
        col, row, frames = int(entry[0]), int(entry[1]), int(entry[2])
        if frames <= 0:
            continue
        time.append(
            {
                "at": [
                    round(float(origin[0]) + (col + 0.5) * cell, 2),
                    round(float(origin[1]) + (row + 0.5) * cell, 2),
                ],
                "seconds": round(frames / max(fps, 1), 2),
            }
        )
    return {"mode": mode, "cell": cell, "deaths": deaths, "stuck": stuck, "time": time}


def _recent(project: Path) -> list[dict[str, Any]]:
    """The kept playtest reports, newest first, each with its run's folder name."""
    from joinery.playtest import FOLDER

    root = project / FOLDER
    if not root.is_dir():
        return []
    reports = []
    for folder in sorted((p for p in root.iterdir() if p.is_dir()), reverse=True):
        try:
            data = json.loads((folder / "report.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            reports.append({**data, "_run": folder.name})
    return reports


def _cell_of(at: list[float], base: dict[str, Any]) -> tuple[int, int] | None:
    cell = float(base["cell"])
    col = math.floor((at[0] - float(base["origin"][0])) / cell)
    row = math.floor((at[1] - float(base["origin"][1])) / cell)
    if 0 <= col < int(base["cols"]) and 0 <= row < int(base["rows"]):
        return col, row
    return None


def aggregate(
    project: Path, *, last: int | None = None, base: dict[str, Any] | None = None
) -> dict[str, Any]:
    """The last ``last`` playtests of one scene laid over one grid.

    ``base`` is the report whose coverage grid the layers are drawn on (the run just
    played); without it, the newest kept run that mapped the level. Runs of another
    scene or another kind of level (3D, top-down, side-on) are left out. Returns
    {runs, run_ids, mode, cell, origin, cols, rows, start, grid, deaths, stuck, time,
    totals, hotspots}: each layer a list of [col, row, value] (deaths and stuck as counts,
    time as seconds summed over the runs), hotspots the biggest cells of each layer in
    world units; {} when no run mapped the level.
    """
    count = last if last is not None else runs_wanted(project)
    reports = _recent(project)
    if base is None:
        base = next((r for r in reports if (r.get("coverage") or {}).get("grid")), None)
    if base is None:
        return {}
    grid_of = base.get("coverage") or {}
    if not grid_of.get("grid"):
        return {}
    mode = str(grid_of.get("mode", ""))
    rows_text = [str(line) for line in grid_of["grid"]]
    grid: dict[str, Any] = {
        "cell": float(grid_of.get("cell", 1) or 1),
        "origin": list(grid_of.get("origin") or [0, 0]),
        "cols": int(
            grid_of.get("cols", 0) or max((len(r) for r in rows_text), default=0)
        ),
        "rows": int(grid_of.get("rows", 0) or len(rows_text)),
    }
    base_out = str(base.get("out", ""))
    chosen: list[dict[str, Any]] = [base]
    for report in reports:
        if len(chosen) >= count:
            break
        if base_out and str(report.get("out", "")) == base_out:
            continue
        if report.get("_run") and report.get("_run") == base.get("_run"):
            continue
        heat = report.get("heat") or {}
        if heat.get("mode") != mode or report.get("scene", "") != base.get("scene", ""):
            continue
        chosen.append(report)
    layers: dict[str, dict[tuple[int, int], float]] = {name: {} for name in LAYERS}
    for report in chosen:
        heat = report.get("heat") or {}
        if heat.get("mode") != mode:
            continue
        for name, key in (("deaths", ""), ("stuck", ""), ("time", "seconds")):
            for point in heat.get(name, []) or []:
                if not isinstance(point, dict):
                    continue
                at = point.get("at")
                if not isinstance(at, list) or len(at) != 2:
                    continue
                where = _cell_of([float(at[0]), float(at[1])], grid)
                if where is None:
                    continue
                amount = float(point.get(key, 0) or 0) if key else 1.0
                layers[name][where] = layers[name].get(where, 0.0) + amount
    out: dict[str, Any] = {
        "runs": sum(1 for r in chosen if (r.get("heat") or {}).get("mode") == mode),
        "run_ids": [
            str(r.get("_run") or Path(str(r.get("out", ""))).name) for r in chosen
        ],
        "mode": mode,
        **grid,
        "start": grid_of.get("start", [-1, -1]),
        "grid": rows_text,
    }
    totals: dict[str, float] = {}
    hotspots: dict[str, list[dict[str, Any]]] = {}
    for name, cells in layers.items():
        ordered = sorted(cells.items(), key=lambda item: (-item[1], item[0]))
        precision = 1 if name == "time" else 0
        out[name] = [
            [col, row, round(value, precision) if precision else int(value)]
            for (col, row), value in ordered
        ]
        totals[name] = round(sum(cells.values()), 1)
        hotspots[name] = [
            {
                "at": [
                    round(float(grid["origin"][0]) + (col + 0.5) * grid["cell"], 1),
                    round(float(grid["origin"][1]) + (row + 0.5) * grid["cell"], 1),
                ],
                "value": round(value, 1),
            }
            for (col, row), value in ordered[:HOTSPOTS]
        ]
    out["totals"] = totals
    out["hotspots"] = hotspots
    return out


def for_agent(heat: dict[str, Any]) -> dict[str, Any]:
    """The aggregate without its grids: runs, totals and hotspots in world units."""
    if not heat:
        return {}
    return {
        "runs": heat.get("runs", 0),
        "cell": heat.get("cell"),
        "totals": heat.get("totals", {}),
        "hotspots": heat.get("hotspots", {}),
        "note": "deaths and stuck are counts, time is seconds the bot stood there, summed "
        "over the runs; places are on the ground plane (x, z in 3D; x, y in 2D).",
    }


def summary_lines(heat: dict[str, Any]) -> list[str]:
    """Plain lines for the CLI: one per layer with its hotspots."""
    if not heat:
        return ["No playtest mapped the level yet."]
    totals = heat.get("totals", {})
    lines = [f"Over the last {heat.get('runs', 0)} playtests:"]
    words = {"deaths": "deaths", "stuck": "stuck points", "time": "seconds played"}
    for name in LAYERS:
        spots = ", ".join(
            f"({s['at'][0]:g}, {s['at'][1]:g}) {s['value']:g}"
            for s in heat.get("hotspots", {}).get(name, [])
        )
        lines.append(
            f"- {float(totals.get(name, 0)):g} {words[name]}"
            + (f"; most at {spots}" if spots else "")
        )
    return lines
