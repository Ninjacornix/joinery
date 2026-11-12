"""/tidy's Remove, as a reviewed change without an agent.

The tidy card sends ``tidy_remove``::

    {"kind": "tidy_remove", "ids": ["3fa2c1…", …]}   # [] or absent: every sure finding

The host checks the project again, plans the removal (``joinery.tidy_remove``) and
starts a scripted task of the crew that makes it on the staged copy: the files with
their sidecars, sources, recipes and ``joinery.lock`` entries, and the edits to
``project.godot`` and scenes. The change goes through the checks and the Review tab
like any other; Discard restores everything and the Timeline can revert it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bridge.crew import Crew, Task
from bridge.protocol import Prompt
from joinery import tidy, tidy_remove, tidy_report
from joinery.tidy import human_size


def plan(project: Path, data: dict[str, Any]) -> tidy_remove.Removal:
    ids = data.get("ids") or []
    if not isinstance(ids, list) or any(not isinstance(i, str) for i in ids):
        raise TypeError("ids is a list of finding ids.")
    graph = tidy.build(project)
    removal = tidy_remove.plan(graph, tidy_report.check(project, graph), ids or None)
    if removal.problems:
        raise ValueError(" ".join(removal.problems))
    if not removal.ids:
        raise ValueError("Nothing unused to remove: check again.")
    return removal


def facts(removal: tidy_remove.Removal) -> list[str]:
    """The review card's facts: what goes and what it frees."""
    found = [f"Frees {human_size(removal.size)}"] if removal.size else []
    found.append(f"{len(removal.delete)} files deleted")
    if removal.edits:
        found.append("edits " + ", ".join(sorted(removal.edits))[:60])
    return found + removal.lines[:3]


async def start(
    crew: Crew, project: Path, data: dict[str, Any], prompt: Prompt
) -> Task:
    """Start the removal as a scripted task of the crew; its change waits in Review."""
    removal = plan(project, data)
    request = "\n".join(tidy_remove.describe(removal))
    targets = [c.path for c in removal.delete] + sorted(removal.edits)
    return await crew.start_scripted(
        removal.summary(),
        request,
        [],
        prompt=prompt,
        targets=targets,
        facts=facts(removal),
        edit=lambda stage: tidy_remove.apply(stage, removal),
    )
