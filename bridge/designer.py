"""The design check in the chat host: Joinery's checks, and the designer agent's pass.

``/design`` in the sidebar runs ``joinery design`` itself, with no model. "Look
deeper" on its card asks the host for the agent pass: the chat's own provider in the
read-only ``designer`` role (``joinery.roles``), whose policy hook and gateway
endpoint allow reads and ``report_design`` and nothing that changes the project. It is
told what Joinery's checks already found, reads the brief, design documents, scenes
and scripts, and reports judgement calls with evidence; ``joinery.design`` drops any
whose evidence doesn't hold. It never edits the game.

With ``[agents] design_check_every = N`` in joinery.toml, every N-th accepted change runs the
checks (no model) and the card shows when they find something. The count is this
chat session's.

Events go to the sidebar as ``design`` events tagged with ``design_id``:
``{state: checking|reading|done|failed|stopped, origin, report, dropped, message}``.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bridge.instructions import DESIGN_PROMPT
from bridge.playtester import PlaytestAgent, PlaytestEndpoint
from bridge.protocol import Done, Prompt
from joinery import design
from joinery.roles import spec

Emit = Callable[[dict[str, Any]], None]
Checker = Callable[[Path], design.Report]
BUSY = "The design check is already running."
#: Joinery's findings the agent is told about, at most.
TOLD = 20


@dataclass
class Run:
    id: str
    origin: str
    deep: bool
    state: str = "checking"
    report: design.Report | None = None
    dropped: int = 0
    message: str = ""
    job: asyncio.Task[None] | None = None

    def event(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "kind": "design",
            "design_id": self.id,
            "origin": self.origin,
            "deep": self.deep,
            "state": self.state,
        }
        if self.report is not None:
            data["report"] = self.report.model_dump()
        if self.dropped:
            data["dropped"] = self.dropped
        if self.message:
            data["message"] = self.message
        return data


def told(report: design.Report) -> str:
    """Joinery's own findings, one line each, for the agent not to repeat."""
    if not report.findings:
        return "- nothing"
    lines = [
        f"- {f.title} ({f.evidence[0].where() if f.evidence else ''})"
        for f in report.findings[:TOLD]
    ]
    if len(report.findings) > TOLD:
        lines.append(f"- and {len(report.findings) - TOLD} more")
    return "\n".join(lines)


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9_]{3,}", text.lower()) if w not in _COMMON}


_COMMON = frozenset({"the", "and", "never", "isn", "can", "has", "not", "with", "from"})


def _same(item: design.Finding, found: design.Finding) -> bool:
    """The agent re-reported a finding of Joinery's: a shared place and a like title."""
    places = {(e.path, e.line) for e in found.evidence}
    if not any((e.path, e.line) in places for e in item.evidence):
        return False
    mine, theirs = _words(item.title), _words(found.title)
    return bool(mine and theirs) and len(mine & theirs) / len(mine | theirs) >= 0.5


def merged(
    report: design.Report, reports: list[dict[str, Any]]
) -> tuple[design.Report, int]:
    """The check's report with the agent's findings added, and how many were dropped.
    An agent finding that repeats one of Joinery's (or its own) is left out."""
    added: list[design.Finding] = []
    dropped = 0
    for entry in reports:
        dropped += len(entry.get("dropped", []))
        for raw in entry.get("findings", []):
            item = design.Finding.model_validate(raw)
            if any(_same(item, f) for f in report.findings + added):
                continue
            added.append(item)
    findings = sorted(
        report.findings + added,
        key=lambda f: (design.SEVERITY_ORDER[f.severity], f.source != "check"),
    )
    return (
        report.model_copy(
            update={"findings": findings, "summary": design.summary(findings)}
        ),
        dropped,
    )


class Designer:
    """The design check for one chat: on request, and every N accepted changes."""

    def __init__(
        self,
        project: Path,
        emit: Emit,
        *,
        agent: str,
        profile: str,
        bridge: Callable[[str], PlaytestAgent],
        endpoint: Callable[[], PlaytestEndpoint],
        checker: Checker | None = None,
    ) -> None:
        self.project = project
        self.emit = emit
        self.agent = agent
        self.profile = profile
        self.bridge = bridge
        self.endpoint = endpoint
        self.checker: Checker = checker or design.check
        self.current: Run | None = None
        #: Accepted changes since the last automatic check.
        self.accepted = 0

    def busy(self) -> bool:
        return self.current is not None and self.current.state in {
            "checking",
            "reading",
        }

    def start(self, deep: bool = True, origin: str = "user") -> Run:
        """Check now; ``deep`` adds the designer agent's pass after Joinery's checks."""
        if self.busy():
            raise ValueError(BUSY)
        run = Run(id=uuid.uuid4().hex[:12], origin=origin, deep=deep)
        self.current = run
        self.emit(run.event())
        run.job = asyncio.ensure_future(self._run(run))
        return run

    def after_decision(self, decision: dict[str, Any]) -> None:
        """``[agents] design_check_every``: the checks after every N-th accepted change."""
        if not decision.get("accepted"):
            return
        every = design.check_every(self.project)
        if every <= 0:
            self.accepted = 0
            return
        self.accepted += 1
        if self.accepted < every or self.busy():
            return
        self.accepted = 0
        self.start(deep=False, origin="after_change")

    async def _run(self, run: Run) -> None:
        try:
            run.report = await asyncio.to_thread(self.checker, self.project)
            if run.deep:
                run.state = "reading"
                self.emit(run.event())
                limit = spec("designer").seconds
                reports = await asyncio.wait_for(self._agent(run.report), limit)
                run.report, run.dropped = merged(run.report, reports)
            run.state = "done"
        except asyncio.CancelledError:
            run.state = "stopped"
            raise
        except TimeoutError:
            run.state = "done" if run.report is not None else "failed"
            run.message = (
                "The design agent ran out of time; Joinery's checks are shown."
            )
        except (OSError, RuntimeError, ValueError) as exc:
            if run.report is not None:
                run.state = "done"
                run.message = f"The design agent couldn't finish: {exc}"
            else:
                run.state = "failed"
                run.message = str(exc) or "The design check didn't run."
        finally:
            self.emit(run.event())

    async def _agent(self, report: design.Report) -> list[dict[str, Any]]:
        child: PlaytestAgent | None = None
        endpoint: PlaytestEndpoint | None = None
        try:
            child = self.bridge(self.agent)
            endpoint = self.endpoint()
            await endpoint.start()
            child.bind_gateway(endpoint.path)
            await child.connect(self.project, self.profile)
            parts = DESIGN_PROMPT.fill(found=told(report))
            prompt = Prompt(
                text=parts["text"],
                rules=parts["rules"],
                project_dir=str(self.project),
            )
            async for event in child.send(prompt):
                if isinstance(event, Done):
                    break
            return list(getattr(endpoint, "reports", []))
        finally:
            for closer in (child, endpoint):
                if closer is not None:
                    with contextlib.suppress(Exception):
                        await closer.close()

    async def stop(self, design_id: str = "") -> None:
        run = self.current
        if run is None or (design_id and run.id != design_id) or not self.busy():
            return
        if run.job is not None and not run.job.done():
            run.job.cancel()
            await asyncio.gather(run.job, return_exceptions=True)

    async def close(self) -> None:
        await self.stop()
