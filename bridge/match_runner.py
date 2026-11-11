""" "Make it match" in the chat host: audit, plan, then the fixes as parallel tasks.

The sidebar's Fix all (or one area's Fix) sends ``match_fix``. The host audits the
project again (the card's numbers may be minutes old), plans the fixes into small
changes (`joinery.match_plan`) and starts each as a scripted task of the crew: as
many at once as the project's ``[agents] parallel`` leaves free beside the chat's
own request, the rest queued. Apps still take turns per worker (WorkerSlots). Each
change is checked, read by the reviewer and decided by the user in the Review tab;
nothing is accepted here.

Events, all ``kind: "match"``: ``auditing``, then ``fixing`` with the audit and the
planned tasks, an update as each task starts or ends, then ``done`` (or ``failed``
or ``stopped``).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from bridge.crew import WORKING, Crew, ScriptedCall, Task
from bridge.protocol import Prompt
from joinery import match, match_plan
from joinery.bible_rules import Measure

Emit = Callable[[dict[str, Any]], None]
#: Seconds between looks at the running tasks.
POLL = 0.25


class MatchRun:
    """One project's "Make it match": at most one run at a time."""

    def __init__(
        self,
        crew: Crew,
        project: Path,
        emit: Emit,
        measure: Measure,
        prompt: Callable[[], Prompt],
    ) -> None:
        self.crew = crew
        self.project = project
        self.emit = emit
        self.measure = measure
        self.prompt = prompt
        self.state = "idle"
        self.rows: list[dict[str, Any]] = []
        self.job: asyncio.Task[None] | None = None
        self._tasks: dict[str, Task] = {}

    def busy(self) -> bool:
        return self.job is not None and not self.job.done()

    def start(self, areas: list[str], findings: list[str]) -> None:
        if self.busy():
            raise ValueError("Journeyman is already making the project match.")
        unknown = set(areas) - set(match.AREAS)
        if unknown:
            raise ValueError("Unknown area: " + ", ".join(sorted(unknown)) + ".")
        self.rows = []
        self._tasks = {}
        self.job = asyncio.create_task(self._run(areas, findings))

    async def stop(self) -> None:
        """Stop queued fixes and the ones still working; changes in review stay."""
        job = self.job
        if job is not None and not job.done():
            job.cancel()
            await asyncio.gather(job, return_exceptions=True)
        for task in list(self._tasks.values()):
            if task.state == WORKING:
                with contextlib.suppress(ValueError):
                    await self.crew.stop(task.id)

    def _event(self, state: str, **extra: Any) -> None:
        self.state = state
        self.emit({"kind": "match", "state": state, "tasks": self.rows, **extra})

    async def _run(self, areas: list[str], findings: list[str]) -> None:
        try:
            self._event("auditing")
            audit = await match.audit(self.project, self.measure)
            planned = match_plan.plan(
                self.project, audit, areas=areas or None, findings=findings or None
            )
            self.rows = [
                {
                    "key": fix.key,
                    "title": fix.title,
                    "area": fix.area,
                    "apps": fix.apps,
                    "task_id": "",
                    "state": "queued",
                    "message": "",
                }
                for fix in planned
            ]
            self._event("fixing", audit=audit.model_dump())
            if not planned:
                self._event("done", message="Nothing Joinery can fix by itself.")
                return
            await self._schedule(planned)
            self._event("done")
        except asyncio.CancelledError:
            for row in self.rows:
                if row["state"] == "queued":
                    row["state"] = "stopped"
            self._event("stopped")
            raise
        except (OSError, RuntimeError, ValueError, TimeoutError) as exc:
            self._event("failed", message=str(exc))

    def _free(self) -> int:
        """Turns free for fixes: the chat's own request keeps its slot."""
        return max(1, self.crew.limit - 1) - len(self.crew.working())

    async def _schedule(self, planned: list[match_plan.FixTask]) -> None:
        queue = list(planned)
        rows = {row["key"]: row for row in self.rows}
        running: dict[str, Task] = {}
        while queue or running:
            while queue and self._free() > 0:
                fix = queue.pop(0)
                row = rows[fix.key]
                try:
                    task = await self.crew.start_scripted(
                        fix.title,
                        fix.request,
                        [
                            ScriptedCall(s.server, s.tool, s.arguments, s.label)
                            for s in fix.steps
                        ],
                        prompt=self.prompt(),
                        targets=fix.targets,
                        facts=fix.facts,
                    )
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
