"""Typed Material Maker operations in joinery-materialmaker.

`list_graph` reads a .ptex: its nodes, their parameters with the definitions
they are checked against, and its connections. `set_node_parameters` changes
one node's parameters after checking each against the node's definition, and
`wrap_image` brings one of the project's own images into the graph as an image
node. Both write the whole graph through `edit_graph`, which exports it through
Material Maker and records a recipe that replays it exactly.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from joinery import authoring
from joinery.recipe import load_recipe, recipe_paths
from joinery.tools import material_maker, mm_graph
from servers.errors import tool_errors


def _graph(project: Path, source: str) -> tuple[str, dict[str, Any]]:
    source = source.removeprefix("res://")
    if Path(source).suffix != ".ptex" or Path(source).parts[:1] != ("art",):
        raise ValueError("Material Maker graphs are .ptex files under art/.")
    path = authoring.confined(project, source)
    if not path.is_file():
        raise ValueError(f"{source} is missing; make it with operate_graph first.")
    if path.stat().st_size > 2_000_000:
        raise ValueError(f"{source} is over 2 MB; change it in Material Maker.")
    graph = mm_graph.lenient_json(path.read_text(encoding="utf-8"))
    if not isinstance(graph, dict):
        raise ValueError(f"{source} is not a Material Maker graph.")  # noqa: TRY004 - shown to the agent
    return source, graph


async def _library(project: Path, graph: dict[str, Any]) -> dict[str, Any]:
    """Material Maker's own definitions of the graph's library node types."""
    types = mm_graph.library_types(graph)
    if not types:
        return {}
    got = await authoring.run_offline(
        mm_graph.worker_argv(types), project, 60, readonly=True
    )
    if not got.ok:
        raise RuntimeError(
            "Material Maker's node library could not be read in the worker: "
            + got.output[-400:]
        )
    return mm_graph.read_worker_output(got.stdout)


def _output_directory(project: Path, source: str, given: str | None) -> str:
    if given:
        return given.removeprefix("res://")
    for path in recipe_paths(project):
        try:
            recipe = load_recipe(path)
        except (OSError, ValueError):
            continue
        if recipe.tool == "material-maker" and source in recipe.outputs:
            return mm_graph.export_directory(recipe.outputs, source)
    raise ValueError(f"{source} has no recorded exports; name the output_directory.")


async def list_graph(project: Path, source: str) -> dict[str, Any]:
    source, graph = _graph(project, source)
    return {
        "source": source,
        **mm_graph.describe(graph, await _library(project, graph)),
    }


async def set_node_parameters(
    project: Path,
    source: str,
    node: str,
    parameters: dict[str, Any],
    output_directory: str | None = None,
    note: str = "",
) -> dict[str, Any]:
    source, graph = _graph(project, source)
    directory = _output_directory(project, source, output_directory)
    changed, checked = mm_graph.set_parameters(
        graph, node, parameters, await _library(project, graph)
    )
    result = await authoring.edit_graph(
        project,
        source,
        changed,
        directory,
        f"Set {node} of {source}: {json.dumps(checked, sort_keys=True)}."
        + (f" {note}" if note else ""),
    )
    return {**result, "node": node, "parameters": checked}


async def wrap_image(
    project: Path,
    source: str,
    image: str,
    name: str = "",
    connect: list[dict[str, Any]] | None = None,
    output_directory: str | None = None,
    note: str = "",
) -> dict[str, Any]:
    source, graph = _graph(project, source)
    directory = _output_directory(project, source, output_directory)
    if not isinstance(image, str):
        raise ValueError("image is a project path.")  # noqa: TRY004 - shown to the agent
    # The same checks the export makes, before anything changes: an image under
    # art/ in this project, outside the export folder.
    relative = material_maker._image_input(project, image, source, directory)
    changed, made = mm_graph.wrap_image(
        graph, relative, await _library(project, graph), name, connect
    )
    wired = ", ".join(f"{c['node']} port {c['port']}" for c in connect or [])
    result = await authoring.edit_graph(
        project,
        source,
        changed,
        directory,
        f"Image {relative} as node {made} of {source}"
        + (f", into {wired}" if wired else "")
        + "."
        + (f" {note}" if note else ""),
    )
    return {**result, "node": made, "image": relative}


def register(server: MCPServer[Any], project: Path) -> None:
    """Add the typed graph operations to joinery-materialmaker."""

    @server.tool(name="list_graph", annotations=ToolAnnotations(read_only_hint=True))
    @tool_errors
    async def list_graph_tool(source: str) -> dict[str, Any]:
        """List a Material Maker graph (.ptex under art/): each node's name, type and parameters, the parameter definitions they are checked against (type, range, enum choices, help), its input and output port counts, and every connection (from, from_port, to, to_port). Changes nothing."""
        return await list_graph(project, source)

    @server.tool(name="set_node_parameters")
    @tool_errors
    async def set_node_parameters_tool(
        source: str,
        node: str,
        parameters: dict[str, Any],
        output_directory: str | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        """Set parameters of one node of a Material Maker graph, each checked against the node's definition (list_graph shows them): float within its range, size as 2^n (whole number in its range), enum as an index or choice name, boolean, color [r, g, b, a?] (0-1), gradient {interpolation, points: [{pos, r, g, b, a}]}, curve {points: [{x, y, ls, rs}]}, polygon {points: [{x, y}]}. Re-exports the graph through Material Maker (to its recorded export folder unless output_directory is given) with a replayable recipe. Requires approved art direction and user review."""
        return await set_node_parameters(
            project, source, node, parameters, output_directory, note
        )

    @server.tool(name="wrap_image")
    @tool_errors
    async def wrap_image_tool(
        source: str,
        image: str,
        name: str = "",
        connect: list[dict[str, Any]] | None = None,
        output_directory: str | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        """Bring one of this project's own images (under art/, with a joinery.lock entry or a recipe, outside the export folder) into a Material Maker graph as an image node. connect: [{node, port}] wires its output into those input ports (replacing what fed them; list_graph shows the ports). Re-exports the graph with a replayable recipe. Requires approved art direction and user review."""
        return await wrap_image(
            project, source, image, name, connect, output_directory, note
        )
