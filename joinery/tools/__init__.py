"""Per-tool modules: one place per catalog tool for its adapter and readiness checks.

`joinery.authoring`, `servers.authoring`, `joinery.server_install` and
`joinery.readiness` dispatch through `REGISTRY` rather than branching on tool
ids, so adding a tool means adding a module here and a catalog entry.
"""

from __future__ import annotations

from joinery.tools import (
    audacity,
    blender,
    gimp,
    godot,
    inkscape,
    krita,
    material_maker,
    supercollider,
    surge_xt,
)
from joinery.tools.base import ToolModule

REGISTRY: dict[str, ToolModule] = {
    module.id: module
    for module in (
        godot.MODULE,
        blender.MODULE,
        krita.MODULE,
        gimp.MODULE,
        inkscape.MODULE,
        material_maker.MODULE,
        supercollider.MODULE,
        surge_xt.MODULE,
        audacity.MODULE,
    )
}

#: Joinery's own authoring servers and the package under servers/ that runs each.
FAMILY_PACKAGES: dict[str, str] = {
    "joinery-blender-ops": "blender_ops",
    "joinery-paint": "paint",
    "joinery-sound": "sound",
    "joinery-materialmaker": "materialmaker",
}


def get(tool_id: str) -> ToolModule:
    try:
        return REGISTRY[tool_id]
    except KeyError:
        raise ValueError(f"No tool module for {tool_id!r}.") from None


def authoring_ids() -> set[str]:
    """Tools whose native documents Joinery edits through some adapter."""
    return {m.id for m in REGISTRY.values() if m.authoring}


def family_tools(family: str) -> list[ToolModule]:
    """Modules owned by one of Joinery's authoring servers, in registry order."""
    return [m for m in REGISTRY.values() if m.family == family]


def script_tools(family: str) -> set[str]:
    """Tools the family's operate_document accepts (they have a script adapter)."""
    return {m.id for m in family_tools(family) if m.script_ext}


def families_for(selected: set[str]) -> list[str]:
    """Authoring servers to start for the selected tools, in a stable order."""
    wanted = {REGISTRY[t].family for t in selected if t in REGISTRY}
    return [family for family in FAMILY_PACKAGES if family in wanted]


__all__ = [
    "FAMILY_PACKAGES",
    "REGISTRY",
    "ToolModule",
    "authoring_ids",
    "families_for",
    "family_tools",
    "get",
    "script_tools",
]
