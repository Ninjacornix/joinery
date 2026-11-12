"""What a playtest found, judged: console parsing, severity and balance notes.

The playtest bot (``joinery/playtest_bot``) reports what it saw as raw facts: log
entries, findings, events, timings and samples. Godot's console tells how the process
ended. This module turns both into one report the CLI prints, the gateway's
``playtest`` tool returns and the sidebar's playtest card shows: items with a
severity, a plain title, the place in the code where there is one, and repro steps
with the seed.

Severities, most serious first: ``critical`` (the game crashed, froze or never
started), ``high`` (a script error, a softlock, the player lost out of the world,
saves that don't load), ``medium`` (engine errors, frame hitches, memory that keeps
growing, a pause that does nothing), ``low`` (warnings, text that doesn't fit) and
``note`` (balance: what a random first-time player's timings and deaths suggest).
"""

from __future__ import annotations

import itertools
import re
from collections.abc import Iterable
from typing import Any

SEVERITIES = ("critical", "high", "medium", "low", "note")

#: Console lines Godot starts an error or warning with.
_CONSOLE = re.compile(
    r"^(?P<kind>SCRIPT ERROR|USER SCRIPT ERROR|ERROR|USER ERROR|WARNING|USER WARNING):\s*(?P<message>.*)$"
)
_AT = re.compile(r"^\s+at:\s*(?:(?P<function>[^(]*?)\s*)?\((?P<where>[^)]*)\)\s*$")
_CRASH = re.compile(
    r"(Program crashed with signal \d+|CrashHandlerException|handle_crash:|Segmentation fault)"
)
_RES_LINE = re.compile(r"(res://[^\s:()]+):(\d+)")
#: Engine noise every headless run prints; never a finding.
_NOISE = (
    "ObjectDB instances leaked at exit",
    "resources still in use at exit",
    "Pages in use exist at exit",
)

_KIND_SEVERITY = {
    "crash": "critical",
    "freeze": "critical",
    "boot": "critical",
    "script_error": "high",
    "shader_error": "high",
    "softlock": "high",
    "fell": "high",
    "physics": "high",
    "save": "high",
    "quit": "medium",
    "error": "medium",
    "pause": "medium",
    "hitch": "medium",
    "memory": "medium",
    "warning": "low",
    "text": "low",
    "balance": "note",
    "coverage": "note",
}

_TITLES = {
    "crash": "The game crashed",
    "freeze": "The game froze",
    "boot": "The game didn't start",
    "script_error": "Script error",
    "shader_error": "Shader error",
    "softlock": "Softlock",
    "fell": "Fell out of the world",
    "physics": "Physics went wrong",
    "save": "Saving or loading is broken",
    "quit": "The game quit by itself",
    "error": "Engine error",
    "pause": "Pause doesn't work",
    "hitch": "Frame hitches",
    "memory": "Memory keeps growing",
    "warning": "Warning",
    "text": "Text doesn't fit",
    "balance": "Balance",
    "coverage": "Parts of the level unreached",
}


def console_entries(output: str) -> list[dict[str, Any]]:
    """Godot's console errors and warnings: {kind, message, where, function}.

    The message line is followed by an ``at:`` line naming the function and place;
    a script error's backtrace names the script line, which wins when present.
    """
    entries: list[dict[str, Any]] = []
    lines = output.splitlines()
    for index, line in enumerate(lines):
        found = _CONSOLE.match(line)
        if found is None:
            continue
        message = found.group("message").strip()
        if any(noise in message for noise in _NOISE):
            continue
        label = found.group("kind")
        kind = (
            "script_error"
            if "SCRIPT" in label or label == "USER ERROR"
            else "warning"
            if "WARNING" in label
            else "error"
        )
        where, function = "", ""
        for follow in lines[index + 1 : index + 6]:
            at = _AT.match(follow)
            if at is not None and not where:
                where = at.group("where")
                function = (at.group("function") or "").strip()
            script = _RES_LINE.search(follow)
            if script is not None and "res://" not in where:
                where = f"{script.group(1)}:{script.group(2)}"
                break
            if _CONSOLE.match(follow):
                break
        entries.append(
            {"kind": kind, "message": message, "where": where, "function": function}
        )
    return entries


def crashed(output: str, returncode: int | None) -> bool:
    """Whether the Godot process crashed (a signal, or the crash handler's lines)."""
    if returncode is not None and returncode < 0:
        return True
    return returncode not in (0, None) and _CRASH.search(output) is not None


def stamp(seconds: float) -> str:
    """0:42, 12:05."""
    whole = max(0, int(seconds))
    return f"{whole // 60}:{whole % 60:02d}"


def repro(options: dict[str, Any], t: float | None, where: str = "") -> str:
    """Steps to see it again: the exact command (the seed replays the same run), when, where."""
    command = f"joinery playtest --project {options.get('project', '.')} --seed {options['seed']}"
    command += f" --seconds {int(options['seconds'])}"
    if options.get("scene"):
        command += f" --scene {options['scene']}"
    steps = [f"Run `{command}`"]
    if t is not None:
        steps.append(f"watch around {stamp(t)} of play")
    if where:
        steps.append(f"see {where}")
    return "; ".join(steps) + "."


def _merge_logs(
    bot_log: Iterable[dict[str, Any]], console: Iterable[dict[str, Any]], fps: int
) -> list[dict[str, Any]]:
    """One entry per distinct problem, counted, with the first time it happened.

    The bot's logger knows the frame; the console knows what happened before the bot was
    listening (a script that failed to compile) or after (exit), so its entries count only
    when the logger has nothing like them.
    """
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in bot_log:
        message = str(entry.get("message", "")).strip()
        if not message or any(noise in message for noise in _NOISE):
            continue
        key = (message, str(entry.get("where", "")))
        if key in merged:
            merged[key]["count"] += 1
            continue
        merged[key] = {
            "kind": str(entry.get("kind", "error")),
            "message": message,
            "where": str(entry.get("where", "")),
            "function": str(entry.get("function", "")),
            "t": int(entry.get("frame", 0)) / max(fps, 1),
            "count": 1,
        }
    known = {message for message, _ in merged}
    for entry in console:
        message = str(entry["message"])
        if message in known:
            continue
        key = (message, str(entry.get("where", "")))
        if key in merged:
            merged[key]["count"] += 1
            continue
        merged[key] = {**entry, "t": None, "count": 1}
    return list(merged.values())


def _memory(samples: list[dict[str, Any]], fps: int) -> dict[str, Any] | None:
    """Node or memory counts that only ever grow, by a lot: a leak worth a look."""
    if len(samples) < 4:
        return None
    body = samples[1:]  # the first sample is often still loading
    for key, unit, floor, ratio in (
        ("nodes", "nodes", 300, 1.5),
        ("static_mb", "MB", 48, 1.6),
    ):
        values = [float(s.get(key, 0)) for s in body]
        rises = sum(1 for a, b in itertools.pairwise(values) if b > a)
        if (
            values[-1] - values[0] >= floor
            and values[-1] >= values[0] * ratio
            and rises >= len(values) - 2
        ):
            minutes = (
                (int(body[-1]["frame"]) - int(body[0]["frame"])) / max(fps, 1) / 60
            )
            return {
                "kind": "memory",
                "detail": (
                    f"{key.replace('static_mb', 'Memory').replace('nodes', 'Node count')} "
                    f"grew from {values[0]:g} to {values[-1]:g} {unit} in "
                    f"{minutes:.1f} min of play and never came back down."
                ),
                "t": int(body[-1]["frame"]) / max(fps, 1),
            }
    return None


def _hitches(perf: dict[str, Any], fps: int) -> dict[str, Any] | None:
    spikes = [s for s in perf.get("spikes", []) if isinstance(s, dict)]
    if not spikes:
        return None
    worst = max(float(s.get("ms", 0)) for s in spikes)
    if worst < 250 and len(spikes) < 4:
        return None
    first = min(int(s.get("frame", 0)) for s in spikes)
    return {
        "kind": "hitch",
        "detail": (
            f"{len(spikes)} frame{'s' if len(spikes) != 1 else ''} took far longer than "
            f"usual (worst {worst:.0f} ms; typical {float(perf.get('frame_ms_p50', 0)):.2f} ms)."
        ),
        "t": first / max(fps, 1),
    }


def balance_notes(bot: dict[str, Any]) -> list[dict[str, Any]]:
    """What a random first-time player's run suggests about difficulty. Only notes."""
    goals = bot.get("goals", {}) if isinstance(bot.get("goals"), dict) else {}
    played = float(bot.get("seconds_played", 0) or 0)
    completed = [c for c in goals.get("completed", []) if isinstance(c, dict)]
    failed = [f for f in goals.get("failed", []) if isinstance(f, dict)]
    respawns = int((bot.get("perf") or {}).get("respawns", 0) or 0)
    notes: list[dict[str, Any]] = []
    if completed:
        fastest = min(float(c.get("level_time", c.get("t", 0)) or 0) for c in completed)
        line = f"A bot pressing keys at random finished the level in {stamp(fastest)}"
        if fastest < 20:
            notes.append(
                {"detail": line + ": it may be too easy.", "t": completed[0]["t"]}
            )
        else:
            notes.append({"detail": line + ".", "t": completed[0]["t"]})
    deaths = len(failed) + respawns
    rate = deaths / max(played / 60, 0.5)
    if played >= 30 and deaths >= 4 and rate >= 3 and not completed:
        notes.append(
            {
                "detail": (
                    f"The bot died or respawned {deaths} times in {stamp(played)} without "
                    f"finishing ({rate:.1f} a minute): it may be too hard, or hazards may be "
                    "too easy to walk into."
                ),
                "t": None,
            }
        )
    progress = (
        int(goals.get("score_changes", 0) or 0)
        + int(goals.get("checkpoints", 0) or 0)
        + int(goals.get("world_changes", 0) or 0)
        + len(completed)
    )
    if played >= 60 and progress == 0 and bot.get("core") and bot.get("interactive"):
        notes.append(
            {
                "detail": (
                    f"In {stamp(played)} the bot scored nothing, reached no checkpoint and "
                    "changed nothing: the goals may be hard to find or reach."
                ),
                "t": None,
            }
        )
    return [{"kind": "balance", **note} for note in notes]


def coverage_summary(raw: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
    """The bot's coverage grid with the unreached areas found in it.

    ``unreached`` lists the biggest groups of navigable cells nobody stood in (up to five),
    each with its size in cells and its centre in world units on the ground plane (x, z in
    3D; x, y in 2D), so an agent can say where they are. ``map`` names the picture, which
    ``joinery.playtest`` fills in with the run's folder.
    """
    grid = raw.get("grid")
    if not isinstance(grid, list) or not grid:
        return {}
    rows = [str(line) for line in grid]
    cell = float(raw.get("cell", 1) or 1)
    origin = raw.get("origin") or [0, 0]
    seen: set[tuple[int, int]] = set()
    groups: list[list[tuple[int, int]]] = []
    for y, line in enumerate(rows):
        for x, mark in enumerate(line):
            if mark != "." or (x, y) in seen:
                continue
            group: list[tuple[int, int]] = []
            stack = [(x, y)]
            seen.add((x, y))
            while stack:
                cx, cy = stack.pop()
                group.append((cx, cy))
                for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                    if (
                        0 <= ny < len(rows)
                        and 0 <= nx < len(rows[ny])
                        and rows[ny][nx] == "."
                        and (nx, ny) not in seen
                    ):
                        seen.add((nx, ny))
                        stack.append((nx, ny))
            groups.append(group)
    groups.sort(key=lambda g: (-len(g), min(g)))
    unreached = []
    for group in groups[:5]:
        mx = sum(x for x, _ in group) / len(group)
        my = sum(y for _, y in group) / len(group)
        unreached.append(
            {
                "cells": len(group),
                "at": [
                    round(float(origin[0]) + (mx + 0.5) * cell, 1),
                    round(float(origin[1]) + (my + 0.5) * cell, 1),
                ],
            }
        )
    return {
        "mode": str(raw.get("mode", "")),
        "cell": cell,
        "origin": origin,
        "cols": int(raw.get("cols", 0) or 0),
        "rows": int(raw.get("rows", 0) or 0),
        "start": raw.get("start", [-1, -1]),
        "navigable": int(raw.get("navigable", 0) or 0),
        "visited": int(raw.get("visited", 0) or 0),
        "percent": float(raw.get("percent", 0) or 0),
        "grid": rows,
        "unreached": unreached,
        "map": "",
    }


def coverage_note(coverage: dict[str, Any], played: float) -> dict[str, Any] | None:
    """A note when a run of a minute or more left much of the level unseen."""
    if not coverage or played < 60 or coverage["percent"] >= 60:
        return None
    where = ""
    if coverage["unreached"]:
        x, y = coverage["unreached"][0]["at"]
        where = f"; the biggest unreached area is around ({x:g}, {y:g})"
    return {
        "kind": "coverage",
        "detail": (
            f"In {stamp(played)} the bot reached {coverage['percent']:g}% of the level's "
            f"floor{where}. Those places may be hard to find or get to; the map marks them."
        ),
        "t": None,
    }


def build(
    bot: dict[str, Any] | None,
    output: str,
    returncode: int | None,
    *,
    options: dict[str, Any],
    froze_at: float | None = None,
    last_t: float = 0.0,
) -> dict[str, Any]:
    """The report: {status, counts, items, goals, checks, perf, played_seconds, …}."""
    fps = int((bot or {}).get("fps", 60) or 60)
    raw: list[dict[str, Any]] = []
    status = "played"
    played = float((bot or {}).get("seconds_played", last_t) or last_t)
    if froze_at is not None:
        status = "froze"
        raw.append(
            {
                "kind": "freeze",
                "detail": (
                    f"The game stopped answering at {stamp(froze_at)} (no frame for "
                    "a long time); it may be stuck in a loop."
                ),
                "t": froze_at,
            }
        )
    elif crashed(output, returncode):
        status = "crashed"
        raw.append(
            {
                "kind": "crash",
                "detail": f"Godot exited with code {returncode} at about {stamp(played)} of play.",
                "t": played,
            }
        )
    elif bot is None:
        status = "failed_to_start"
        raw.append(
            {
                "kind": "boot",
                "detail": f"The game didn't run (Godot exit code {returncode}).",
                "t": None,
            }
        )
    elif not bot.get("finished"):
        status = "quit"
        raw.append(
            {
                "kind": "quit",
                "detail": f"The game closed itself at {stamp(played)} of play.",
                "t": played,
            }
        )
    for entry in _merge_logs((bot or {}).get("log", []), console_entries(output), fps):
        count = int(entry["count"])
        detail = entry["message"] + (f" (×{count})" if count > 1 else "")
        raw.append(
            {
                "kind": entry["kind"] if entry["kind"] in _KIND_SEVERITY else "error",
                "detail": detail,
                "where": entry.get("where", ""),
                "t": entry.get("t"),
                "count": count,
            }
        )
    for finding in (bot or {}).get("findings", []):
        if isinstance(finding, dict):
            raw.append({**finding, "t": finding.get("t")})
    perf = (bot or {}).get("perf", {}) or {}
    for extra in (_hitches(perf, fps), _memory(perf.get("samples", []), fps)):
        if extra is not None:
            raw.append(extra)
    coverage = coverage_summary((bot or {}).get("coverage") or {}, options)
    if bot is not None and status == "played" and not bot.get("route"):
        raw.extend(balance_notes(bot))
        note = coverage_note(coverage, played)
        if note is not None:
            raw.append(note)
    items = [_item(index, entry, options) for index, entry in enumerate(raw)]
    items.sort(key=lambda item: (SEVERITIES.index(item["severity"]), item["t"] or 0))
    counts = {severity: 0 for severity in SEVERITIES}
    for item in items:
        counts[item["severity"]] += 1
    goals = (bot or {}).get("goals", {}) or {}
    return {
        "version": 1,
        "status": status,
        "seed": options["seed"],
        "seconds": options["seconds"],
        "played_seconds": played,
        "scene": options.get("scene", ""),
        "focus": options.get("focus", ""),
        "style": (bot or {}).get("style", ""),
        "player": (bot or {}).get("player", ""),
        "core": bool((bot or {}).get("core", False)),
        "counts": counts,
        "items": items,
        "goals": {
            "levels_started": goals.get("levels_started", 0),
            "completed": goals.get("completed", []),
            "failed": goals.get("failed", []),
            "checkpoints": goals.get("checkpoints", 0),
            "score_changes": goals.get("score_changes", 0),
            "last_score": goals.get("last_score"),
            "goals_reached": (bot or {}).get("goals_reached", 0),
            "respawns": perf.get("respawns", 0),
            "coverage_cells": perf.get("cells", 0),
        },
        "checks": (bot or {}).get("checks", {}),
        "perf": {
            key: perf.get(key)
            for key in ("frame_ms_p50", "frame_ms_p95", "frame_ms_max")
            if key in perf
        },
        "moments": (bot or {}).get("moments", []),
        "route": (bot or {}).get("route", []) or [],
        "coverage": coverage,
        "reach": [r for r in (bot or {}).get("reach", []) or [] if isinstance(r, dict)],
        "states": _states((bot or {}).get("states")),
        "screenshots": [],
    }


def _states(raw: object) -> dict[str, Any]:
    """What game nodes said about themselves at the end (group `playtest_state`)."""
    if not isinstance(raw, dict):
        return {}
    return {str(path): state for path, state in raw.items() if isinstance(state, dict)}


def _item(index: int, entry: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
    kind = str(entry.get("kind", "error"))
    t = entry.get("t")
    t_value = float(t) if isinstance(t, (int, float)) else None
    where = str(entry.get("where", "") or "")
    if "res://" not in where and "user://" not in where:
        # An engine source file means nothing to the user.
        where = ""
    frame = entry.get("frame")
    return {
        "id": f"p{index + 1}",
        "severity": _KIND_SEVERITY.get(kind, "medium"),
        "kind": kind,
        "title": _TITLES.get(kind, kind.replace("_", " ").capitalize()),
        "detail": str(entry.get("detail", "")),
        "where": where,
        "t": t_value,
        "frame": int(frame) if isinstance(frame, int) else None,
        "at": entry.get("at"),
        "count": int(entry.get("count", 1) or 1),
        "seed": options["seed"],
        "repro": repro(options, t_value, where),
        "shot": "",
    }


def summary_line(report: dict[str, Any]) -> str:
    """One line: "Played 2:00 (seed 7): 1 high, 2 low, 1 note." """
    counts = report.get("counts", {})
    parts = [f"{counts[s]} {s}" for s in SEVERITIES if counts.get(s)]
    found = ", ".join(parts) if parts else "nothing to report"
    verb = {
        "played": "Played",
        "crashed": "Crashed after",
        "froze": "Froze after",
        "quit": "The game quit after",
        "failed_to_start": "Couldn't start; played",
    }.get(str(report.get("status")), "Played")
    return f"{verb} {stamp(float(report.get('played_seconds', 0)))} (seed {report.get('seed')}): {found}."
