"""The sidebar's "/variant" on the scene open in the editor, as a reviewed change.

Typed bare, ``/variant`` sends ``variant_make``::

    {"kind": "variant_make", "base": "res://enemies/goblin.tscn", "path": ""}

The host writes a new inherited scene of ``base`` (``joinery.scene_variants``: beside it
as ``<name>_variant.tscn`` unless ``path`` names one) and starts a scripted task of the
crew that writes it through Godot's ``write_file``: no agent is needed, the change goes
through the checks and the Review tab like any other, and nothing is written before the
user accepts. ``/variant <what differs>`` is a request to the agent instead
(``bridge.instructions.SCENE_VARIANT_REQUEST``), which calls ``make_variant``.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

from bridge.crew import Crew, ScriptedCall, Task
from bridge.protocol import Prompt
from joinery import scene_variants


def plan(project: Path, data: dict[str, Any]) -> tuple[str, str, ScriptedCall, list[str]]:
    """(title, request, the write, facts) for a ``variant_make`` message."""
    base = str(data.get("base", "") or "")
    if not base:
        raise ValueError("Open the scene to make a variant of first.")
    made = scene_variants.make(project, base, str(data.get("path", "") or ""))
    name = PurePosixPath(made.variant.base.removeprefix("res://")).stem.replace("_", " ")
    call = ScriptedCall(
        "godot",
        "write_file",
        {"projectPath": str(project), "filePath": made.path, "content": made.text},
        "Write the variant in Godot",
    )
    request = (
        f"Make a variant of {made.variant.base} as res://{made.path}: a Godot "
        "inherited scene that keeps only what differs."
    )
    return f"Variant of {name}", request, call, [made.variant.fact()]


async def start(crew: Crew, project: Path, data: dict[str, Any], prompt: Prompt) -> Task:
    """Start the variant as a scripted task of the crew; its change waits in Review."""
    title, request, call, facts = plan(project, data)
    return await crew.start_scripted(
        title,
        request,
        [call],
        prompt=prompt,
        targets=[str(call.arguments["filePath"])],
        facts=facts,
    )
