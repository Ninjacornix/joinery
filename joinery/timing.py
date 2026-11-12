"""Local latency evidence: fixed labels and durations, never prompts or payloads."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from joinery.metrics import PHASE_KINDS, Trace


class TurnTiming:
    def __init__(self) -> None:
        self.id = uuid.uuid4().hex
        self.started = time.monotonic()
        self.phases: dict[str, float] = {}
        self.first_response: float | None = None
        self.model = ""
        self.effort = ""
        self.mode = ""
        #: The same work as spans, for the change's cost (joinery.metrics).
        self.trace = Trace(self.id)

    @contextmanager
    def span(
        self, phase: str, name: str = "", attributes: dict[str, Any] | None = None
    ) -> Iterator[dict[str, Any]]:
        """Time a phase; it is also one span of the change, whose attributes the
        yielded dict takes."""
        start = time.monotonic()
        try:
            with self.trace.span(
                PHASE_KINDS.get(phase, phase), name or phase, attributes
            ) as attrs:
                yield attrs
        finally:
            self.phases[phase] = self.phases.get(phase, 0.0) + time.monotonic() - start

    def response(self) -> None:
        if self.first_response is None:
            self.first_response = time.monotonic() - self.started

    def report(self, outcome: str) -> dict[str, Any]:
        return {
            "kind": "timing",
            "id": self.id,
            "outcome": outcome,
            "model": self.model,
            "effort": self.effort,
            "mode": self.mode,
            "total_ms": round((time.monotonic() - self.started) * 1000),
            "first_response_ms": None
            if self.first_response is None
            else round(self.first_response * 1000),
            "phases_ms": {
                key: round(value * 1000) for key, value in self.phases.items()
            },
        }

    def save(self, project: Path, outcome: str) -> dict[str, Any]:
        report = self.report(outcome)
        target = project / ".joinery/latency.jsonl"
        if target.is_symlink() or not target.resolve().is_relative_to(
            project.resolve()
        ):
            raise ValueError("Latency evidence must stay inside this project.")
        target.parent.mkdir(parents=True, exist_ok=True)
        lines = (
            target.read_text().splitlines()[-199:]
            if target.exists() and target.stat().st_size < 2_000_000
            else []
        )
        target.write_text("\n".join([*lines, json.dumps(report)]) + "\n")
        return report
