"""The chat host's "when idle" queue (ADR 0072): unhurried runs start when you're away.

A Fix or Check the sidebar sends while ``[agents] unhurried = "idle"`` (or one that
asks ``when: "idle"`` itself) is queued in ``.joinery/idle-queue.json`` rather than
started (joinery.idle_runs). The host looks every half minute: when nothing is running,
the editor has been quiet for ``idle_minutes`` and the clock is inside ``idle_hours``,
the oldest queued run starts, on the project's light model when one is set (ADR 0061).
Its changes are reviewed in the Review tab like any other. ``/idle`` lists the queue,
``/idle now`` starts it at once and ``/idle clear`` empties it.

Nothing here knows which provider runs the agents.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from joinery import idle_runs

Emit = Callable[[dict[str, Any]], None]
#: Starts one run: (the job, the model to use or "" for the chat's).
Dispatch = Callable[[idle_runs.Job, str], None]
#: Seconds between looks.
POLL = 30.0


class IdleQueue:
    def __init__(
        self,
        project: Path,
        emit: Emit,
        dispatch: Dispatch,
        busy: Callable[[], bool],
        light_model: Callable[[], str],
        *,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = datetime.now,
        poll: float = POLL,
    ) -> None:
        self.project = project
        self.emit = emit
        self.dispatch = dispatch
        self.busy = busy
        self.light_model = light_model
        self.clock = clock
        self.now = now
        self.poll = poll
        self._last = clock()
        self._job: asyncio.Task[None] | None = None

    # --- Asking ------------------------------------------------------------------------

    def touch(self) -> None:
        """The user did something: the quiet time starts again."""
        self._last = self.clock()

    def event(self) -> dict[str, Any]:
        return {
            "kind": "idle_queue",
            "jobs": [j.event() for j in idle_runs.queued(self.project)],
        }

    def queue(self, kind: str, data: dict[str, Any]) -> idle_runs.Job:
        job = idle_runs.add(self.project, kind, data)
        config = idle_runs.settings(self.project)
        model = self.light_model()
        on = f" on the light model ({model})" if model else ""
        self.emit(
            {
                "kind": "notice",
                "level": "info",
                "text": f"{idle_runs.LABELS[kind]} waits for when you're away: it runs "
                f"{idle_runs.hours_words(config)}{on}. Type /idle now to run it now.",
            }
        )
        self.emit(self.event())
        return job

    def status(self) -> str:
        waiting = idle_runs.queued(self.project)
        config = idle_runs.settings(self.project)
        if not waiting:
            return "Nothing is waiting for when you're away."
        names = ", ".join(idle_runs.LABELS[j.kind] for j in waiting)
        return (
            f"Waiting for when you're away: {names}. They run "
            f"{idle_runs.hours_words(config)}. Type /idle now to run them now, or "
            "/idle clear to drop them."
        )

    # --- Running -----------------------------------------------------------------------

    def tick(self) -> idle_runs.Job | None:
        """Start the oldest waiting run if it is time; returns it."""
        if not idle_runs.queued(self.project) or self.busy():
            return None
        config = idle_runs.settings(self.project)
        if not idle_runs.due(config, self.now(), self.clock() - self._last):
            return None
        return self._start()

    def run_now(self) -> int:
        """``/idle now``: start every waiting run at once (each kind runs once)."""
        started = 0
        while idle_runs.queued(self.project):
            if self._start() is None:
                break
            started += 1
        return started

    def clear(self) -> int:
        count = idle_runs.remove(self.project)
        self.emit(self.event())
        return count

    def _start(self) -> idle_runs.Job | None:
        job = idle_runs.take(self.project)
        if job is None:
            return None
        model = self.light_model()
        idle_runs.record(self.project, job, model)
        try:
            self.dispatch(job, model)
        except (OSError, ValueError, RuntimeError) as exc:
            self.emit(
                {
                    "kind": "notice",
                    "level": "warn",
                    "text": f"{idle_runs.LABELS[job.kind]} couldn't start: {exc}",
                }
            )
        self.emit(self.event())
        return job

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.poll)
            with contextlib.suppress(OSError, ValueError):
                self.tick()

    def start(self) -> None:
        if self._job is None or self._job.done():
            self._job = asyncio.ensure_future(self._loop())

    async def close(self) -> None:
        if self._job is not None:
            self._job.cancel()
            await asyncio.gather(self._job, return_exceptions=True)
            self._job = None
