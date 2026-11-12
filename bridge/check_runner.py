"""The accessibility and performance checks in the chat host (ADR 0049, ADR 0050).

The sidebar's Accessibility and Performance cards run the checks themselves (`joinery
access --json`, `joinery perf --json`); Fix all, or one area's Fix, sends ``check_fix``.
The host takes the card's report (or checks again when it is old), plans the changes
(`joinery.checks_core.plan`) and starts them beside the chat's own request:

* typed fixes as scripted tasks of the crew, as "Make it match" does (ADR 0045);
* fixes that need code: the check's read-only agent (the ``accessibility`` or
  ``performance`` role, `joinery.roles`) reads the findings and the files and words the
  change; a worker task makes it (its words are the request, or the check's own when the
  agent can't say). Every change is checked, read by the reviewer and decided by the user.

Adding a feature is the user's own step, so the card offers those itself.

The same host serves ``access_check`` and ``perf_check`` to every agent that may call
them, and with ``[agents] access_check_every`` / ``perf_check_every = N`` checks again
after every N accepted changes (off by default). Events: ``kind`` "access" or "perf",
``state`` auditing, fixing (with the report and the planned tasks), done, failed,
stopped, or checked (a periodic check's report).
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Protocol

from bridge.crew import WORKING, Crew, ScriptedCall, Task
from bridge.instructions import CHECK_PROMPT
from bridge.models import Selection
from bridge.protocol import Done, Message, MessageDelta, Prompt
from joinery import access, checks_core, perf
from joinery.roles import spec
from joinery.routing import routing_settings

Emit = Callable[[dict[str, Any]], None]
Audit = Callable[[str, dict[str, Any]], Awaitable[checks_core.Report]]
POLL = 0.25
#: A report this recent (seconds) is fixed as it is; an older one is measured again.
FRESH = 300.0
ROLES = {"access": "accessibility", "perf": "performance"}
LABELS = {"access": access.AREAS, "perf": perf.AREAS}


class RoleAgent(Protocol):
    def bind_gateway(self, path: Any) -> None: ...

    async def connect(self, project: Any, profile_id: str = "") -> None: ...

    def send(self, message: Any) -> Any: ...

    async def close(self) -> None: ...


class RoleEndpoint(Protocol):
    path: Any
    checks: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]] | None

    async def start(self) -> None: ...

    async def close(self) -> None: ...


async def run_audit(
    project: Path, kind: str, params: dict[str, Any]
) -> checks_core.Report:
    """One check, off the event loop; its report is kept as the check's last."""
    if kind == "access":
        report = await asyncio.to_thread(access.audit, project)
    else:
        report = await asyncio.to_thread(
            perf.audit, project, str(params.get("target", "") or "")
        )
    checks_core.save(project, report)
    return report


def for_agent(report: checks_core.Report, limit: int = 25) -> dict[str, Any]:
    """A report as the tools return it: scores, facts and the first findings."""
    data = report.model_dump()
    facts = dict(data.get("facts", {}))
    facts.pop("runs", None)
    measured = facts.get("measured")
    if isinstance(measured, dict):
        facts["measured"] = {
            k: v for k, v in measured.items() if k not in {"spikes", "hot"}
        }
    data["facts"] = facts
    data["findings"] = data["findings"][:limit]
    data["note"] = (
        "Each finding says what was measured, the goal, where it was seen and how it is "
        "fixed: typed (Joinery's own operation), agent (a code change) or feature (the "
        "user adds a game feature). The user starts fixes from the card (/access, /perf)."
    )
    return data


class Checks:
    """The two checks of one chat host: tools, fixes and periodic runs."""

    def __init__(
        self,
        crew: Crew,
        project: Path,
        emit: Emit,
        prompt: Callable[[], Prompt],
        *,
        agent: str = "",
        profile: str = "",
        bridge: Callable[[str, str], RoleAgent] | None = None,
        endpoint: Callable[[str], RoleEndpoint] | None = None,
        audit: Audit | None = None,
    ) -> None:
        self.crew = crew
        self.project = project
        self.emit = emit
        self.prompt = prompt
        self.agent = agent
        self.profile = profile
        self.bridge = bridge
        self.endpoint = endpoint
        self.audit: Audit = audit or (
            lambda kind, params: run_audit(project, kind, params)
        )
        self.runs: dict[str, CheckRun] = {kind: CheckRun(self, kind) for kind in ROLES}
        self._locks = {kind: asyncio.Lock() for kind in ROLES}
        self._accepted = 0
        self._periodic: set[asyncio.Task[None]] = set()

    async def tool(self, kind: str, params: dict[str, Any]) -> dict[str, Any]:
        """``access_check`` and ``perf_check`` for whichever agent may call them."""
        if kind not in ROLES:
            raise ValueError("Unknown check.")
        target = str(params.get("target", "") or "")
        if target and target not in perf.TARGETS:
            raise ValueError(f"target is one of {', '.join(perf.TARGETS)}.")
        async with self._locks[kind]:
            report = await self.audit(kind, params)
        areas = [str(a) for a in params.get("areas") or []]
        if areas:
            report.findings = [f for f in report.findings if f.area in areas]
        return for_agent(report)

    def handle(self, data: dict[str, Any]) -> bool:
        """A sidebar message for the checks; False when it is not one."""
        kind = str(data.get("check", ""))
        if data.get("kind") == "check_fix" and kind in self.runs:
            self.runs[kind].start(
                [str(a) for a in data.get("areas") or []],
                [str(f) for f in data.get("findings") or []],
                {"target": str(data.get("target", "") or "")},
            )
            return True
        return False

    async def cancel(self, data: dict[str, Any]) -> None:
        run = self.runs.get(str(data.get("check", "")))
        if run is not None:
            await run.stop()

    def after_decision(self, decision: dict[str, Any]) -> None:
        """``[agents] access_check_every`` / ``perf_check_every``: check again."""
        if not decision.get("accepted"):
            return
        self._accepted += 1
        for kind in ROLES:
            every = checks_core.every(self.project, f"{kind}_check_every")
            if every and self._accepted % every == 0 and not self.runs[kind].busy():
                job = asyncio.ensure_future(self._check(kind))
                self._periodic.add(job)
                job.add_done_callback(self._periodic.discard)

    async def _check(self, kind: str) -> None:
        try:
            async with self._locks[kind]:
                report = await self.audit(kind, {})
        except (OSError, RuntimeError, ValueError) as exc:
            self.emit(
                {"kind": kind, "state": "failed", "message": str(exc), "tasks": []}
            )
            return
        self.emit(
            {
                "kind": kind,
                "state": "checked",
                "audit": report.model_dump(),
                "tasks": [],
            }
        )

    async def advise(self, kind: str, task: checks_core.CheckTask) -> str:
        """The check's read-only agent words the change; "" when it can't.

        With ``[agents] light_model`` the light model words it first; when it can't
        say (no words, NONE, an error or no time) the main model tries again."""
        light = routing_settings(self.project).light_model(self.agent)
        if light:
            words = await self._advise(kind, task, light)
            if words:
                return words
        return await self._advise(kind, task, "")

    async def _advise(self, kind: str, task: checks_core.CheckTask, model: str) -> str:
        if self.bridge is None or self.endpoint is None:
            return ""
        role = ROLES[kind]
        child: RoleAgent | None = None
        point: RoleEndpoint | None = None
        try:
            child = self.bridge(self.agent, role)
            point = self.endpoint(role)
            point.checks = self.tool
            await point.start()
            child.bind_gateway(point.path)
            await child.connect(self.project, self.profile)
            parts = CHECK_PROMPT.fill(
                role=role, tool=f"{kind}_check", findings=task.request
            )
            prompt = Prompt(
                text=parts["text"],
                rules=parts["rules"],
                project_dir=str(self.project),
                selection=Selection(model=model),
            )
            messages: list[str] = []

            async def read() -> None:
                assert child is not None
                async for event in child.send(prompt):
                    if isinstance(event, MessageDelta):
                        if event.new_message or not messages:
                            messages.append("")
                        messages[-1] += event.text
                    elif isinstance(event, Message) and event.level != "refuse":
                        messages.append(event.text)
                    elif isinstance(event, Done):
                        break

            await asyncio.wait_for(read(), spec(role).seconds)
            words = next((m for m in reversed(messages) if m.strip()), "").strip()
            return "" if words.upper().rstrip(".") == "NONE" else words[:4000]
        except (OSError, RuntimeError, ValueError, TimeoutError):
            return ""
        finally:
            for closer in (child, point):
                if closer is not None:
                    with contextlib.suppress(Exception):
                        await closer.close()

    async def close(self) -> None:
        for run in self.runs.values():
            await run.stop()
        for job in list(self._periodic):
            job.cancel()
        await asyncio.gather(*self._periodic, return_exceptions=True)


class CheckRun:
    """One check's Fix: at most one at a time."""

    def __init__(self, owner: Checks, kind: str) -> None:
        self.owner = owner
        self.kind = kind
        self.state = "idle"
        self.rows: list[dict[str, Any]] = []
        self.job: asyncio.Task[None] | None = None
        self._tasks: dict[str, Task] = {}

    def busy(self) -> bool:
        return self.job is not None and not self.job.done()

    def start(
        self, areas: list[str], findings: list[str], options: dict[str, Any]
    ) -> None:
        if self.busy():
            raise ValueError(
                f"Journeyman is already fixing what the {ROLES[self.kind]} check found."
            )
        unknown = set(areas) - set(LABELS[self.kind])
        if unknown:
            raise ValueError("Unknown area: " + ", ".join(sorted(unknown)) + ".")
        self.rows = []
        self._tasks = {}
        self.job = asyncio.create_task(self._run(areas, findings, options))

    async def stop(self) -> None:
        job = self.job
        if job is not None and not job.done():
            job.cancel()
            await asyncio.gather(job, return_exceptions=True)
        for task in list(self._tasks.values()):
            if task.state == WORKING:
                with contextlib.suppress(ValueError):
                    await self.owner.crew.stop(task.id)

    def _event(self, state: str, **extra: Any) -> None:
        self.state = state
        self.owner.emit(
            {"kind": self.kind, "state": state, "tasks": self.rows, **extra}
        )

    async def _report(self, options: dict[str, Any]) -> checks_core.Report:
        project = self.owner.project
        saved = checks_core.last(project, self.kind)
        path = project / checks_core.FOLDER / f"{self.kind}.json"
        target = str(options.get("target", "") or "")
        fresh = saved is not None and time.time() - path.stat().st_mtime < FRESH
        if (
            saved is not None
            and fresh
            and (not target or saved.facts.get("target") == target)
        ):
            return saved
        return await self.owner.audit(self.kind, options)

    async def _run(
        self, areas: list[str], findings: list[str], options: dict[str, Any]
    ) -> None:
        try:
            self._event("auditing")
            report = await self._report(options)
            planned = [
                t
                for t in checks_core.plan(
                    self.owner.project,
                    report,
                    areas=areas or None,
                    findings=findings or None,
                    labels=LABELS[self.kind],
                )
                if t.kind != "feature"
            ]
            self.rows = [
                {
                    "key": t.key,
                    "title": t.title,
                    "area": t.area,
                    "apps": t.apps,
                    "kind": t.kind,
                    "task_id": "",
                    "state": "queued",
                    "message": "",
                }
                for t in planned
            ]
            self._event("fixing", audit=report.model_dump())
            if not planned:
                self._event("done", message="Nothing Joinery can change by itself.")
                return
            await self._schedule(planned)
            self._event("done")
        except asyncio.CancelledError:
            for row in self.rows:
                if row["state"] in {"queued", "planning"}:
                    row["state"] = "stopped"
            self._event("stopped")
            raise
        except (OSError, RuntimeError, ValueError, TimeoutError) as exc:
            self._event("failed", message=str(exc))

    def _free(self) -> int:
        crew = self.owner.crew
        return max(1, crew.limit - 1) - len(crew.working())

    async def _start(self, task: checks_core.CheckTask, row: dict[str, Any]) -> Task:
        crew = self.owner.crew
        if task.kind == "scripted":
            return await crew.start_scripted(
                task.title,
                task.request,
                [
                    ScriptedCall(s.server, s.tool, s.arguments, s.label)
                    for s in task.steps
                ],
                prompt=self.owner.prompt(),
                targets=task.targets,
                facts=task.facts,
            )
        row.update(state="planning")
        self._event("fixing")
        words = await self.owner.advise(self.kind, task)
        request = (
            f"{words}\n\n(From Joinery's {ROLES[self.kind]} check:)\n{task.request}"
            if words
            else task.request
        )
        return await crew.start(
            task.title,
            request,
            origin="user",
            prompt=self.owner.prompt(),
            targets=task.targets,
        )

    async def _schedule(self, planned: list[checks_core.CheckTask]) -> None:
        queue = list(planned)
        rows = {row["key"]: row for row in self.rows}
        running: dict[str, Task] = {}
        while queue or running:
            while queue and self._free() > 0:
                fix = queue.pop(0)
                row = rows[fix.key]
                try:
                    task = await self._start(fix, row)
                except ValueError as exc:
                    row.update(state="failed", message=str(exc))
                    self._event("fixing")
                    continue
                running[fix.key] = self._tasks[fix.key] = task
                row.update(task_id=task.id, state=task.state)
                self._event("fixing")
            await asyncio.sleep(POLL)
            for key, task in list(running.items()):
                if task.state == WORKING:
                    continue
                running.pop(key)
                rows[key].update(state=task.state, message=task.message)
                self._event("fixing")
