"""The playtester: an agent that plays the game headless and writes what it found (ADR 0038).

``/playtest`` in the sidebar (or, with ``[agents] playtest_after_change = true``, an
accepted gameplay change) starts one. It is the chat's own provider (Codex, Claude Code,
Gemini CLI or opencode) in the read-only ``playtester`` role (``joinery.roles``): the
policy hook and its gateway endpoint judge its calls by the same role table, which allows
reads and ``playtest`` and nothing that changes the project. It never edits the game. It calls ``playtest(minutes?, seed?, focus?)``,
reads the report and the screenshots, and writes a short report: bugs with repro steps
and the seed, softlocks, balance notes, each with a severity. Lines it starts with
``Fix:`` become "Ask Journeyman to fix" follow-ups, which the user sends as a normal
request.

Any agent may call ``playtest`` (the lead, a task); every run shows on the sidebar's
playtest card through ``playtest`` events tagged with ``playtest_id``:
``{state: starting|playing|writing|done|failed|stopped, elapsed, seconds, report,
summary, followups, message}``. One playtest runs at a time.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from bridge.instructions import PLAYTEST_PROMPT
from bridge.protocol import Done, Message, MessageDelta, Prompt
from joinery import playtest, playtest_heat

Emit = Callable[[dict[str, Any]], None]
Runner = Callable[
    [playtest.Options, Callable[[float, float], None]], Awaitable[dict[str, Any]]
]
#: Files whose accepted change is gameplay: worth a playtest when the project asks.
GAMEPLAY_SUFFIXES = (".gd", ".tscn", ".tres", ".gdshader", ".cs")
#: The playtester's own words, at most.
REPORT_LIMIT = 12_000
BUSY = "A playtest is already running; wait for its report."


class PlaytestAgent(Protocol):
    def bind_gateway(self, path: Any) -> None: ...

    async def connect(self, project: Any, profile_id: str = "") -> None: ...

    def send(self, message: Any) -> Any: ...

    async def cancel(self) -> None: ...

    async def close(self) -> None: ...


class PlaytestEndpoint(Protocol):
    path: Any
    playtest: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None

    async def start(self) -> None: ...

    async def close(self) -> None: ...


@dataclass
class Run:
    id: str
    origin: str
    focus: str = ""
    state: str = "starting"
    seconds: float = 0.0
    elapsed: float = 0.0
    report: dict[str, Any] | None = None
    summary: str = ""
    followups: list[str] = field(default_factory=list)
    message: str = ""
    job: asyncio.Task[None] | None = None

    def event(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "kind": "playtest",
            "playtest_id": self.id,
            "origin": self.origin,
            "state": self.state,
            "elapsed": round(self.elapsed, 1),
            "seconds": self.seconds,
            "focus": self.focus,
        }
        if self.report is not None:
            data["report"] = self.report
        if self.summary:
            data["summary"] = self.summary
        if self.followups:
            data["followups"] = self.followups
        if self.message:
            data["message"] = self.message
        return data


def gameplay_change(changes: list[dict[str, Any]]) -> bool:
    """Whether an accepted change touched the game's code or scenes."""
    return any(
        str(change.get("path", "")).endswith(GAMEPLAY_SUFFIXES)
        or str(change.get("path", "")) == "project.godot"
        for change in changes
        if isinstance(change, dict)
    )


def split_followups(text: str) -> tuple[str, list[str]]:
    """The report without its ``Fix:`` lines, and those lines as requests."""
    kept: list[str] = []
    followups: list[str] = []
    for line in text.strip().splitlines():
        bare = line.strip().lstrip("-*• ").strip()
        if bare.lower().startswith("fix:"):
            request = bare[4:].strip()
            if request and len(followups) < 6:
                followups.append(request[:600])
            continue
        kept.append(line)
    return "\n".join(kept).strip()[:REPORT_LIMIT], followups


def tool_params(params: dict[str, Any], default_minutes: float) -> dict[str, Any]:
    """``playtest``'s arguments, checked: {minutes, seed, focus, reach}."""
    minutes = params.get("minutes")
    if minutes is None:
        minutes = default_minutes
    if (
        not isinstance(minutes, (int, float))
        or isinstance(minutes, bool)
        or not 0.25 <= minutes <= 10
    ):
        raise ValueError("minutes is between 0.25 and 10.")
    seed = params.get("seed") or 0
    if not isinstance(seed, int) or isinstance(seed, bool) or not 0 <= seed < 2**31:
        raise ValueError("seed is a whole number from 0; leave it out for a new run.")
    focus = params.get("focus") or ""
    if not isinstance(focus, str) or len(focus) > 200:
        raise ValueError("focus is a short phrase (up to 200 characters).")
    reach = params.get("reach") or []
    if (
        not isinstance(reach, list)
        or len(reach) > 8
        or not all(isinstance(r, str) and 0 < len(r.strip()) <= 60 for r in reach)
    ):
        raise ValueError("reach is up to 8 short place names (up to 60 characters).")
    return {
        "minutes": float(minutes),
        "seed": seed,
        "focus": focus.strip(),
        "reach": [r.strip() for r in reach],
    }


class Playtester:
    """Runs playtests for one chat: the engine for any agent's tool call, and the role."""

    def __init__(
        self,
        project: Path,
        emit: Emit,
        *,
        agent: str,
        profile: str,
        bridge: Callable[[str], PlaytestAgent],
        endpoint: Callable[[], PlaytestEndpoint],
        runner: Runner | None = None,
    ) -> None:
        self.project = project
        self.emit = emit
        self.agent = agent
        self.profile = profile
        self.bridge = bridge
        self.endpoint = endpoint
        self.runner: Runner = runner or playtest.run_async
        self.current: Run | None = None
        self._playing = False

    def busy(self) -> bool:
        return self.current is not None and self.current.state not in {
            "done",
            "failed",
            "stopped",
        }

    def start(self, focus: str = "", origin: str = "user") -> Run:
        """``/playtest``: the playtester plays and writes its report, in the background."""
        if self.busy():
            raise ValueError(BUSY)
        run = Run(id=uuid.uuid4().hex[:12], origin=origin, focus=focus.strip()[:200])
        run.seconds = playtest.config(self.project)["playtest_minutes"] * 60
        self.current = run
        self.emit(run.event())
        run.job = asyncio.ensure_future(self._role(run))
        return run

    async def tool(self, params: dict[str, Any]) -> dict[str, Any]:
        """The ``playtest`` tool, for whichever agent calls it."""
        if self._playing:
            raise ValueError(BUSY)
        checked = tool_params(params, playtest.config(self.project)["playtest_minutes"])
        run = self.current
        if run is None or not self.busy():
            # Another agent asked for one: it gets its own card, without a write-up.
            run = Run(id=uuid.uuid4().hex[:12], origin="agent", focus=checked["focus"])
            self.current = run
        report = await self._play(run, checked)
        if run.origin == "agent":
            run.state = "done"
            self.emit(run.event())
        data = {**playtest.for_agent(report), "note_for_you": _TOOL_NOTE}
        heat = playtest_heat.for_agent((run.report or {}).get("heatmap") or {})
        if heat:
            data["heatmap"] = heat
        return data

    async def _play(self, run: Run, checked: dict[str, Any]) -> dict[str, Any]:
        self._playing = True
        run.state = "playing"
        run.seconds = checked["minutes"] * 60
        run.elapsed = 0.0
        if not run.focus:
            run.focus = checked["focus"]
        self.emit(run.event())

        def progress(t: float, _total: float) -> None:
            if run.state == "playing" and t - run.elapsed >= 1:
                run.elapsed = t
                self.emit(run.event())

        try:
            report = await self.runner(
                playtest.Options(
                    project=self.project,
                    seconds=run.seconds,
                    seed=checked["seed"],
                    focus=checked["focus"],
                    screenshots=True,
                    reach=list(checked.get("reach", [])),
                ),
                progress,
            )
        finally:
            self._playing = False
        run.report = playtest.for_agent(report)
        # The card draws the map itself: the whole grid, and the recent runs' heat on it.
        if report.get("coverage"):
            run.report["coverage"] = report["coverage"]
        heat = playtest_heat.aggregate(self.project, base=report)
        if heat:
            run.report["heatmap"] = heat
        run.elapsed = float(report.get("played_seconds", run.seconds))
        run.state = "writing"
        self.emit(run.event())
        return report

    async def _role(self, run: Run) -> None:
        child: PlaytestAgent | None = None
        endpoint: PlaytestEndpoint | None = None
        try:
            child = self.bridge(self.agent)
            endpoint = self.endpoint()
            endpoint.playtest = self.tool
            await endpoint.start()
            child.bind_gateway(endpoint.path)
            await child.connect(self.project, self.profile)
            minutes = run.seconds / 60
            parts = PLAYTEST_PROMPT.fill(
                minutes=f"{minutes:g}", focus=run.focus or "the whole game"
            )
            prompt = Prompt(
                text=parts["text"],
                rules=parts["rules"],
                project_dir=str(self.project),
            )
            # Only the last message is the report; earlier ones narrate the run.
            messages: list[str] = []
            async for event in child.send(prompt):
                if isinstance(event, MessageDelta):
                    if event.new_message or not messages:
                        messages.append("")
                    messages[-1] += event.text
                elif isinstance(event, Message) and event.level != "refuse":
                    messages.append(event.text)
                elif isinstance(event, Done):
                    break
            written = next((m for m in reversed(messages) if m.strip()), "")
            if run.report is None:
                # The agent wrote without playing: play anyway, so the card has facts.
                await self._play(
                    run, {"minutes": minutes, "seed": 0, "focus": run.focus}
                )
            run.summary, run.followups = split_followups(written)
            if not run.summary and run.report is not None:
                run.summary = str(run.report.get("summary", ""))
            run.state = "done"
        except asyncio.CancelledError:
            run.state = "stopped"
            raise
        except (OSError, RuntimeError, ValueError, TimeoutError) as exc:
            if run.report is not None:
                run.state = "done"
                run.message = f"The playtester couldn't write its report: {exc}"
                run.summary = str(run.report.get("summary", ""))
            else:
                run.state = "failed"
                run.message = str(exc) or "The playtest didn't run."
        finally:
            for closer in (child, endpoint):
                if closer is not None:
                    with contextlib.suppress(Exception):
                        await closer.close()
            self.emit(run.event())

    async def stop(self, playtest_id: str = "") -> None:
        run = self.current
        if run is None or (playtest_id and run.id != playtest_id) or not self.busy():
            return
        if run.job is not None and not run.job.done():
            run.job.cancel()
            await asyncio.gather(run.job, return_exceptions=True)
        else:
            run.state = "stopped"
            self.emit(run.event())

    async def close(self) -> None:
        await self.stop()


_TOOL_NOTE = (
    "Each item has a severity, the place in the code where there is one, and repro "
    "steps with the seed. Screenshots are context only, never assets. Critical and "
    "high items are bugs; note items are balance hints from a bot."
)
