"""/remember and "Remember this", as a reviewed change without an agent (ADR 0077).

The sidebar sends ``remember``::

    {"kind": "remember", "text": "Signals are snake_case."}

The host checks the line (one line, short, not there already, the file not past
what Journeyman reads: ``joinery.memory``) and starts a scripted task of the crew
that adds it under "## Remembered" in JOURNEYMAN.md on the staged copy. The change
goes through the checks and the Review tab like any other; nothing is written
before the user accepts it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bridge.crew import Crew, Task
from bridge.protocol import Prompt
from joinery import memory


def plan(project: Path, data: dict[str, Any]) -> str:
    """The line to add, checked against the file as it is now."""
    item = memory.entry(str(data.get("text", "")))
    memory.remembered(memory.read(project), item)
    return item


async def start(
    crew: Crew, project: Path, data: dict[str, Any], prompt: Prompt
) -> Task:
    """Start the addition as a scripted task; its change waits in Review."""
    item = plan(project, data)
    return await crew.start_scripted(
        memory.summary(item),
        f"Add to {memory.FILE}: {item}",
        [],
        prompt=prompt,
        targets=[memory.FILE],
        facts=[f"Adds one line to {memory.FILE}", item[:120]],
        edit=lambda stage: memory.append(stage, item),
    )
