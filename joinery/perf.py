"""`joinery perf`: how well the game runs, measured, against a target machine's budget.

The game is played twice with the same seed by the playtest bot with the probe on
(`joinery.check_runs`): headless in stock Godot (logic, scene census, script costs,
load time, memory over time) and in a window that never takes focus (frame times with
rendering, draw calls, primitives in view, video and texture memory, shader
compilations). Numbers from this machine are scaled to the target: a target's
``speed`` says how fast it is next to the "high" class, and ``[performance] host_speed``
how fast this machine is (1.0, an Apple Silicon or recent desktop, by default), so a
frame that takes 6 ms here is projected at 6 × host_speed / speed there. It is an
estimate and says so; budgets of draw calls, primitives and memory don't scale.

Budgets per target are below and documented in ADR 0050; ``[performance]`` in
joinery.toml picks the default ``target`` and overrides any budget, for every target
(``[performance] frame_ms = 20``) or one (``[performance.web] draw_calls = 300``). The
art bible's ``[models] max_triangles`` caps models where it says so.
"""

from __future__ import annotations

import itertools
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from joinery import check_runs, perf_static
from joinery.checks_core import CheckFix, Report, Tally, config, res

AREAS: dict[str, str] = {
    "frames": "Frame time",
    "rendering": "Rendering",
    "textures": "Textures",
    "lighting": "Lights",
    "physics": "Physics",
    "scripts": "Scripts",
    "loading": "Loading",
    "memory": "Memory",
}


class Budget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    #: How fast the target is next to the "high" class (1.0).
    speed: float
    frame_ms: float
    draw_calls: int
    primitives: int
    texture_mb: float
    nodes: int
    physics_bodies: int
    shadow_lights: int
    load_ms: int
    max_texture: int
    model_triangles: int
    memory_growth_mb_min: float = 5.0


TARGETS: dict[str, Budget] = {
    "high": Budget(
        label="High-end PC",
        speed=1.0,
        frame_ms=16.7,
        draw_calls=4000,
        primitives=6_000_000,
        texture_mb=3072,
        nodes=20000,
        physics_bodies=2000,
        shadow_lights=8,
        load_ms=3000,
        max_texture=4096,
        model_triangles=150_000,
    ),
    "mid": Budget(
        label="Mid-range PC",
        speed=0.5,
        frame_ms=16.7,
        draw_calls=2000,
        primitives=2_000_000,
        texture_mb=1536,
        nodes=10000,
        physics_bodies=1000,
        shadow_lights=4,
        load_ms=5000,
        max_texture=2048,
        model_triangles=60_000,
    ),
    "low": Budget(
        label="Low-end PC",
        speed=0.25,
        frame_ms=33.3,
        draw_calls=800,
        primitives=500_000,
        texture_mb=512,
        nodes=5000,
        physics_bodies=400,
        shadow_lights=1,
        load_ms=8000,
        max_texture=1024,
        model_triangles=20_000,
    ),
    "steam-deck": Budget(
        label="Steam Deck",
        speed=0.35,
        frame_ms=25.0,
        draw_calls=1500,
        primitives=1_500_000,
        texture_mb=1024,
        nodes=8000,
        physics_bodies=800,
        shadow_lights=2,
        load_ms=6000,
        max_texture=2048,
        model_triangles=40_000,
    ),
    "web": Budget(
        label="Web browser",
        speed=0.3,
        frame_ms=33.3,
        draw_calls=500,
        primitives=500_000,
        texture_mb=256,
        nodes=4000,
        physics_bodies=300,
        shadow_lights=1,
        load_ms=10000,
        max_texture=1024,
        model_triangles=20_000,
    ),
}
DEFAULT_TARGET = "mid"
#: What drawing an empty window costs on a target, added back after scaling.
TARGET_OVERHEAD_MS = 1.0


def target_name(project: Path, given: str = "") -> str:
    wanted = given or str(config(project, "performance").get("target", "") or "")
    if wanted and wanted not in TARGETS:
        raise ValueError(
            f"Unknown performance target {wanted!r}; one of {', '.join(TARGETS)}."
        )
    return wanted or DEFAULT_TARGET


def budget(project: Path, target: str) -> Budget:
    """The target's budget with joinery.toml's ``[performance]`` overrides."""
    table = config(project, "performance")
    values = TARGETS[target].model_dump()
    for source in (table, table.get(target, {})):
        if not isinstance(source, dict):
            continue
        for key, value in source.items():
            if key in values and key != "label" and isinstance(value, (int, float)):
                values[key] = type(values[key])(value)
    return Budget.model_validate(values)


def host_speed(project: Path) -> float:
    value = config(project, "performance").get("host_speed", 1.0)
    return float(value) if isinstance(value, (int, float)) and value > 0 else 1.0


def _percentile(values: list[float], share: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * share))]


def metrics(
    headless: check_runs.Run,
    window: check_runs.Run,
    empty: dict[str, float] | None = None,
) -> dict[str, Any]:
    """The numbers the findings judge, from both runs (and an empty scene's frame
    times in the same window, ``empty``)."""
    cpu = headless.perf
    drawn = window.perf
    samples = [s for s in window.probe.get("samples", []) if isinstance(s, dict)]
    head = [s for s in headless.probe.get("samples", []) if isinstance(s, dict)]
    census = (
        headless.probe.get("census", {}) if isinstance(headless.probe, dict) else {}
    )

    def most(key: str, rows: list[dict[str, Any]]) -> float:
        return max((float(s.get(key, 0) or 0) for s in rows), default=0.0)

    growth = 0.0
    if len(head) >= 3:
        span = (int(head[-1]["frame"]) - int(head[0]["frame"])) / 60 / 60
        if span > 0:
            growth = (float(head[-1]["static_mb"]) - float(head[0]["static_mb"])) / span
    compiled = [int(s.get("compilations", 0) or 0) for s in samples]
    late = [b - a for a, b in itertools.pairwise(compiled)][4:]
    return {
        "windowed": bool(samples),
        "frame_ms": {
            "p50": float(drawn.get("frame_ms_p50", 0) or 0),
            "p95": float(drawn.get("frame_ms_p95", 0) or 0),
            "p99": float(drawn.get("frame_ms_p99", 0) or 0),
            "max": float(drawn.get("frame_ms_max", 0) or 0),
        },
        "empty_frame_ms": dict(empty or {}),
        "cpu_frame_ms": {
            "p50": float(cpu.get("frame_ms_p50", 0) or 0),
            "p95": float(cpu.get("frame_ms_p95", 0) or 0),
            "p99": float(cpu.get("frame_ms_p99", 0) or 0),
        },
        "draw_calls": int(_percentile([float(s["draw_calls"]) for s in samples], 0.95)),
        "primitives": int(most("primitives", samples))
        or int(census.get("triangles", 0)),
        "primitives_from": "drawn" if most("primitives", samples) else "scene",
        "texture_mb": most("texture_mb", samples)
        or float(census.get("texture_mb_est", 0)),
        "video_mb": most("video_mb", samples),
        "nodes": int(most("nodes", head)),
        "bodies": int(census.get("bodies", 0)),
        "rigid": int(census.get("rigid", 0)),
        "lights": census.get("lights", []),
        "load_ms": int(headless.probe.get("load_ms", -1)),
        "memory_growth_mb_min": round(growth, 2),
        "late_compilations": sum(x for x in late if x > 0),
        "spikes": drawn.get("spikes", []),
        "hot": headless.probe.get("hot", []),
        "census": census,
    }


def judge(
    tally: Tally, numbers: dict[str, Any], target: Budget, factor: float, seed: int
) -> None:
    """The measured numbers against the budget (frame and load times scaled)."""
    frame = numbers["frame_ms"] if numbers["windowed"] else numbers["cpu_frame_ms"]
    kind = "frame" if numbers["windowed"] else "CPU frame"
    empty = numbers.get("empty_frame_ms", {}) if numbers["windowed"] else {}
    for name in ("p95", "p99"):
        # What the window costs empty (and an editor binary more than a game) stays
        # here: only the game's own share is scaled to the target.
        own = max(0.0, frame[name] - float(empty.get(name, 0.0)))
        projected = (TARGET_OVERHEAD_MS if empty else 0.0) + own * factor
        ok = projected <= target.frame_ms * (1.0 if name == "p95" else 1.5)
        tally.check("frames", name, ok)
        if not ok:
            tally.add(
                "frames",
                "project.godot",
                f"frame_{name}",
                "high" if projected > 2 * target.frame_ms else "medium",
                f"{projected:.1f} ms",
                f"≤ {target.frame_ms * (1 if name == 'p95' else 1.5):.1f} ms",
                f"{name} {kind} time would be {projected:.1f} ms on a {target.label} "
                f"({frame[name]:.1f} ms here, {own:.1f} ms of it the game's own), "
                f"over its {target.frame_ms:.1f} ms budget.",
                subject=name,
                needs_you="Fix what makes the frames slow below; Joinery measures again "
                "after each accepted change.",
                evidence={"seed": seed},
            )
    for key, area, limit, words in (
        ("draw_calls", "rendering", target.draw_calls, "draw calls a frame"),
        ("primitives", "rendering", target.primitives, "triangles in view"),
        ("texture_mb", "textures", target.texture_mb, "MB of textures"),
        ("nodes", "memory", target.nodes, "nodes"),
        ("bodies", "physics", target.physics_bodies, "physics bodies"),
    ):
        value = float(numbers[key])
        if key == "draw_calls" and not numbers["windowed"]:
            continue
        tally.check(area, key, value <= limit)
        if value > limit:
            tally.add(
                area,
                "project.godot",
                key,
                "high" if value > 2 * limit else "medium",
                f"{value:g} {words}",
                f"≤ {limit:g}",
                f"The game reaches {value:g} {words}; a {target.label} affords {limit:g}.",
                subject=key,
                fix=_agent_for(key, numbers, value, limit),
                evidence={"seed": seed},
            )
    load = numbers["load_ms"] * factor
    if numbers["load_ms"] >= 0:
        tally.check("loading", "load_ms", load <= target.load_ms)
        if load > target.load_ms:
            tally.add(
                "loading",
                "project.godot",
                "load_ms",
                "medium",
                f"{load / 1000:.1f} s",
                f"≤ {target.load_ms / 1000:.1f} s",
                f"The first scene would take {load / 1000:.1f} s to load on a {target.label}.",
                fix=CheckFix(
                    kind="agent",
                    label="Load the first scene faster",
                    request="Make the main scene load faster: load big resources in the "
                    "background with ResourceLoader.load_threaded_request behind a short "
                    "loading screen, and preload only what the first frame needs.",
                    targets=[],
                ),
                evidence={"seed": seed},
            )
    growth = numbers["memory_growth_mb_min"]
    tally.check("memory", "growth", growth <= target.memory_growth_mb_min)
    if growth > target.memory_growth_mb_min:
        tally.add(
            "memory",
            "project.godot",
            "memory_growth",
            "medium",
            f"+{growth:.1f} MB/min",
            f"≤ {target.memory_growth_mb_min:g} MB/min",
            f"Memory grew {growth:.1f} MB a minute while the bot played and never came back.",
            fix=CheckFix(
                kind="agent",
                label="Stop memory growing while playing",
                request="Memory grows steadily during play: find nodes or resources made "
                "every frame or on every event and never freed (queue_free, signals left "
                f"connected, arrays that only grow). Replay with seed {seed} to check.",
            ),
            evidence={"seed": seed},
        )
    if numbers["windowed"]:
        compiled = int(numbers["late_compilations"])
        tally.check("rendering", "compilations", compiled == 0)
        if compiled:
            spike: dict[str, Any] = next(iter(numbers["spikes"]), {})
            tally.add(
                "rendering",
                "project.godot",
                "shader_compilations",
                "medium",
                f"{compiled} compilations in play",
                "0 after the start",
                f"Godot compiled {compiled} shader pipelines after the game started; each "
                "can stall a frame the first time something new is drawn.",
                needs_you="Show new materials and effects once while loading (a warm-up "
                "frame behind the loading screen), or use fewer shader variants.",
                evidence={"seed": seed, "t": round(int(spike.get("frame", 0)) / 60, 2)}
                if spike
                else {"seed": seed},
            )
    shadowed = [
        light
        for light in numbers["lights"]
        if isinstance(light, dict) and light.get("shadow")
    ]
    tally.check("lighting", "shadowed", len(shadowed) <= target.shadow_lights + 1)


def _agent_for(
    key: str, numbers: dict[str, Any], value: float, limit: float
) -> CheckFix | None:
    census = numbers.get("census", {})
    if key == "bodies":
        return CheckFix(
            kind="agent",
            label="Use fewer physics bodies",
            request=f"The game runs {value:g} physics bodies ({census.get('rigid', 0)} "
            f"rigid); a target affords {limit:g}. Merge static colliders into fewer "
            "StaticBody nodes, put sleeping props to sleep, and give each kind of body "
            "its own collision layer and mask so they don't test against everything.",
        )
    if key == "nodes":
        return CheckFix(
            kind="agent",
            label="Use fewer nodes",
            request=f"The game reaches {value:g} nodes; a target affords {limit:g}. "
            "Merge decorative children, free what leaves the screen, and pool what is "
            "spawned often.",
        )
    return None


def physics_layers(tally: Tally, numbers: dict[str, Any], seed: int) -> None:
    every = numbers["census"].get("all_layers", []) if numbers.get("census") else []
    tally.check("physics", "layers", len(every) < 10)
    if len(every) >= 10:
        first = every[0]
        tally.add(
            "physics",
            res(str(first.get("scene", ""))) or "project.godot",
            "physics_layers",
            "medium",
            f"{len(every)} bodies test every layer",
            "layers and masks per kind",
            f"{len(every)} bodies collide with every physics layer, so each tests against "
            "all the others.",
            fix=CheckFix(
                kind="agent",
                label="Give physics bodies their own layers",
                request="Name physics layers in project.godot (player, world, enemies, "
                "pickups, …) and set each body's collision_layer and collision_mask so it "
                f"only tests what it can touch. {len(every)} bodies use every layer now, "
                f"such as {first.get('node', '')} in {first.get('scene', '')}.",
                targets=sorted(
                    {res(str(b.get("scene", ""))) for b in every if b.get("scene")}
                ),
            ),
            evidence={
                "scene": first.get("scene", ""),
                "node": first.get("node", ""),
                "seed": seed,
            },
        )


def audit(
    project: Path,
    target: str = "",
    *,
    window: bool = True,
    seconds: float = check_runs.HEADLESS_SECONDS,
) -> Report:
    """Play, measure and judge; never changes a file."""
    project = project.resolve()
    name = target_name(project, target)
    goal = budget(project, name)
    factor = host_speed(project) / goal.speed
    errors: list[str] = []
    headless = check_runs.headless(project, "perf", seconds=seconds)
    if headless.error:
        errors.append(headless.error)
    drawn = (
        check_runs.windowed(project, "perf")
        if window and not headless.error
        else check_runs.Run(error="Not measured in a window (--no-window).")
    )
    if drawn.error and window and not headless.error:
        errors.append(drawn.error)
    tally = Tally(AREAS)
    empty = check_runs.baseline(project) if drawn.probe else {}
    numbers = metrics(headless, drawn, empty)
    if not headless.error:
        judge(tally, numbers, goal, factor, headless.seed)
        physics_layers(tally, numbers, headless.seed)
    files = perf_static.all_files(project)
    owners = perf_static.owners(project)
    perf_static.textures(tally, project, files, goal.max_texture, owners)
    perf_static.models(tally, project, files, goal.model_triangles, owners)
    perf_static.scenes(tally, project, files, goal.shadow_lights)
    measured = {
        str(h.get("script", "")): float(h.get("ms", 0) or 0)
        for h in numbers["hot"]
        if isinstance(h, dict)
    }
    perf_static.scripts(tally, project, files, measured, goal.frame_ms, factor)
    report = tally.report("perf", project, errors)
    report.facts = {
        "target": name,
        "target_label": goal.label,
        "targets": list(TARGETS),
        "budget": goal.model_dump(),
        "factor": round(factor, 2),
        "seed": headless.seed,
        "measured": {k: v for k, v in numbers.items() if k not in {"census", "lights"}},
        "runs": {
            "headless": headless.report.get("out", ""),
            "window": drawn.report.get("out", ""),
        },
    }
    return report
