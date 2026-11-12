"""Typed Material Maker graph operations: read a .ptex, set node parameters, add an image.

A node's parameters are checked against its definition: the node's own
``shader_model`` or subgraph when it carries one, otherwise Material Maker's
library definition (``nodes/<type>.mmg``) read in the offline worker, where
Material Maker is installed. Every change is written back as the whole graph
through ``edit_graph``, so the recipe holds the exact document and replays it.
"""

from __future__ import annotations

import copy
import inspect
import json
import math
import re
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any

from joinery.tools.material_maker import HOME, IMAGE_NODE, IMAGE_PARAMETER

#: Material Maker's library of node definitions in the worker.
NODES_DIR = f"{HOME}/nodes"
NODE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
#: Parameter kinds set_node_parameters can check; the rest need Material Maker itself.
SETTABLE = (
    "float",
    "size",
    "enum",
    "boolean",
    "color",
    "gradient",
    "curve",
    "polygon",
    "polyline",
)
IMAGE_TYPES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".exr"})
MAX_POINTS = 256


def lenient_json(text: str) -> Any:
    """JSON as Material Maker writes it (Godot allows trailing commas)."""
    try:
        return json.loads(text)
    except ValueError:
        return json.loads(re.sub(r",(\s*[\]}])", r"\1", text))


def _parameter(raw: dict[str, Any]) -> dict[str, Any]:
    kind = str(raw.get("type", ""))
    row: dict[str, Any] = {
        "type": kind,
        "label": raw.get("label") or raw.get("shortdesc") or raw.get("name", ""),
    }
    for key in ("min", "max", "step", "first", "last", "default"):
        if key in raw:
            row[key] = raw[key]
    if kind == "enum":
        row["values"] = [
            str(v.get("name", "")) for v in raw.get("values", []) if isinstance(v, dict)
        ]
    if raw.get("longdesc"):
        row["help"] = raw["longdesc"]
    return row


def normalise_definition(raw: Any, load: Callable[[str], Any]) -> dict[str, Any] | None:
    """A node definition as {label, parameters, inputs, outputs}, or None.

    `raw` is an .mmg document or a graph node carrying its own definition;
    `load(type)` reads another library definition (for a subgraph's linked
    controls), returning None when there is none.
    """
    if not isinstance(raw, dict):
        return None
    model = raw.get("shader_model")
    if isinstance(model, dict):
        return {
            "label": model.get("name") or raw.get("name", ""),
            "parameters": {
                str(p["name"]): _parameter(p)
                for p in model.get("parameters", [])
                if isinstance(p, dict) and "name" in p
            },
            "inputs": len(model.get("inputs") or []),
            "outputs": len(model.get("outputs") or []),
        }
    nodes = raw.get("nodes")
    if not isinstance(nodes, list):
        return None
    inner = {str(n.get("name")): n for n in nodes if isinstance(n, dict)}
    parameters: dict[str, Any] = {}
    remote = next(
        (n for n in nodes if isinstance(n, dict) and n.get("type") == "remote"), None
    )
    for widget in (remote or {}).get("widgets", []):
        if not isinstance(widget, dict) or "name" not in widget:
            continue
        label = widget.get("label") or widget.get("shortdesc") or widget["name"]
        if widget.get("type") == "config_control":
            choices = list((widget.get("configurations") or {}).keys())
            parameters[str(widget["name"])] = {
                "type": "enum",
                "label": label,
                "values": choices,
            }
            continue
        found: dict[str, Any] = {"type": "", "label": label}
        for link in widget.get("linked_widgets", []):
            target = inner.get(str(link.get("node")))
            if target is None:
                continue
            definition = normalise_definition(target, load)
            if definition is None:
                definition = normalise_definition(load(str(target.get("type"))), load)
            linked = (
                (definition or {}).get("parameters", {}).get(str(link.get("widget")))
            )
            if linked:
                found = {**linked, "label": label}
                break
        if widget.get("longdesc"):
            found["help"] = widget["longdesc"]
        parameters[str(widget["name"])] = found
    ports = {
        str(n.get("name")): len(n.get("ports") or [])
        for n in nodes
        if isinstance(n, dict) and n.get("type") == "ios"
    }
    return {
        "label": raw.get("label") or raw.get("shortdesc") or raw.get("name", ""),
        "parameters": parameters,
        "inputs": ports.get("gen_inputs", 0),
        "outputs": ports.get("gen_outputs", 0),
    }


#: Runs in the worker: prints the library definitions of the node types it is given.
WORKER_SCRIPT = "\n".join(
    [
        "from __future__ import annotations",
        "import json, re, sys",
        inspect.getsource(lenient_json),
        inspect.getsource(_parameter),
        inspect.getsource(normalise_definition),
        f"NODES = {NODES_DIR!r}",
        "def load(kind):",
        "    if not re.fullmatch(r'[A-Za-z0-9_]+', kind or ''):",
        "        return None",
        "    try:",
        "        return lenient_json(open(f'{NODES}/{kind}.mmg', encoding='utf-8').read())",
        "    except (OSError, ValueError):",
        "        return None",
        "found = {}",
        "for kind in json.loads(sys.argv[1]):",
        "    definition = normalise_definition(load(kind), load)",
        "    if definition is not None:",
        "        found[kind] = definition",
        "print('JOINERY_MM_DEFS:' + json.dumps(found, sort_keys=True))",
        "",
    ]
)


def worker_argv(types: list[str]) -> list[str]:
    return ["python3", "-c", WORKER_SCRIPT, json.dumps(sorted(set(types)))]


def read_worker_output(output: str) -> dict[str, Any]:
    for line in output.splitlines():
        if line.startswith("JOINERY_MM_DEFS:"):
            found = json.loads(line.removeprefix("JOINERY_MM_DEFS:"))
            return found if isinstance(found, dict) else {}
    raise RuntimeError("Material Maker's node library did not answer: " + output[-400:])


# --- reading a graph ------------------------------------------------------------------


def nodes(graph: dict[str, Any]) -> list[dict[str, Any]]:
    found = graph.get("nodes")
    if not isinstance(found, list):
        raise ValueError("This .ptex has no node list.")  # noqa: TRY004 - shown to the agent
    return [n for n in found if isinstance(n, dict)]


def node(graph: dict[str, Any], name: str) -> dict[str, Any]:
    for item in nodes(graph):
        if item.get("name") == name:
            return item
    raise ValueError(
        f"The graph has no node {name!r}. Nodes: "
        + ", ".join(str(n.get("name")) for n in nodes(graph))
        + "."
    )


def library_types(graph: dict[str, Any]) -> list[str]:
    """Node types whose definition lives in Material Maker's library."""
    return sorted(
        {
            str(n.get("type"))
            for n in nodes(graph)
            if normalise_definition(n, lambda _kind: None) is None and n.get("type")
        }
    )


def definition_of(
    item: dict[str, Any], library: dict[str, Any]
) -> dict[str, Any] | None:
    """A node's definition: its own when it carries one, else the library's."""
    own = normalise_definition(item, lambda kind: None)
    return own if own is not None else library.get(str(item.get("type")))


def describe(graph: dict[str, Any], library: dict[str, Any]) -> dict[str, Any]:
    """The graph's nodes (with their definitions) and connections, for list_graph."""
    rows = []
    for item in nodes(graph):
        definition = definition_of(item, library)
        row: dict[str, Any] = {
            "name": item.get("name"),
            "type": item.get("type"),
            "parameters": item.get("parameters", {}),
        }
        if definition is not None:
            row["label"] = definition["label"]
            row["inputs"] = definition["inputs"]
            row["outputs"] = definition["outputs"]
            row["definitions"] = definition["parameters"]
        elif item.get("type") == IMAGE_NODE:
            row["note"] = "An image node: bring images in with wrap_image."
        else:
            row["note"] = "No definition found; change it in Material Maker."
        rows.append(row)
    return {
        "name": graph.get("name", ""),
        "nodes": rows,
        "connections": [
            {k: c.get(k) for k in ("from", "from_port", "to", "to_port")}
            for c in graph.get("connections", [])
            if isinstance(c, dict)
        ],
    }


# --- checking a parameter -----------------------------------------------------------


def _number(name: str, value: Any) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ValueError(f"{name} is a number.")
    return float(value)


def _unit(name: str, value: Any) -> float:
    number = _number(name, value)
    if not 0 <= number <= 1:
        raise ValueError(f"{name} is from 0 to 1.")
    return number


def _colour(name: str, value: Any) -> dict[str, Any]:
    if isinstance(value, list) and len(value) in (3, 4):
        value = dict(zip("rgba", value, strict=False))
    if (
        not isinstance(value, dict)
        or not {"r", "g", "b"} <= set(value)
        or set(value) - {"r", "g", "b", "a", "type"}
    ):
        raise ValueError(
            f"{name} is a colour: [r, g, b, a?] or {{r, g, b, a?}} from 0 to 1."
        )
    return {
        "a": _unit(f"{name}.a", value.get("a", 1.0)),
        "b": _unit(f"{name}.b", value["b"]),
        "g": _unit(f"{name}.g", value["g"]),
        "r": _unit(f"{name}.r", value["r"]),
        "type": "Color",
    }


def _points(
    name: str, value: Any, keys: tuple[str, ...], extra: tuple[str, ...] = ()
) -> list[dict[str, float]]:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_POINTS:
        raise ValueError(f"{name} has 1 to {MAX_POINTS} points.")
    made = []
    for point in value:
        if (
            not isinstance(point, dict)
            or not set(keys) <= set(point)
            or set(point) - set(keys) - set(extra)
        ):
            raise ValueError(f"Each point of {name} has {', '.join(keys)}.")
        made.append(
            {k: _number(f"{name} {k}", point[k]) for k in (*keys, *extra) if k in point}
        )
    return made


def check_parameter(name: str, spec: dict[str, Any], value: Any, current: Any) -> Any:
    """`value` as the node stores it, or a ValueError saying what the parameter takes."""
    kind = spec.get("type", "")
    if kind == "float":
        number = _number(name, value)
        low, high = spec.get("min"), spec.get("max")
        if (isinstance(low, (int, float)) and number < low) or (
            isinstance(high, (int, float)) and number > high
        ):
            raise ValueError(f"{name} is from {low:g} to {high:g}; got {number:g}.")
        return int(number) if number.is_integer() and isinstance(value, int) else number
    if kind == "size":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{name} is a whole number (a power of two: 2^n pixels).")
        first, last = spec.get("first", 0), spec.get("last", 13)
        if not first <= value <= last:
            raise ValueError(
                f"{name} is from {first} to {last} ({2**first} to {2**last} pixels)."
            )
        return value
    if kind == "enum":
        values = list(spec.get("values", []))
        if isinstance(value, str) and value in values:
            return values.index(value)
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 0 <= value < len(values)
        ):
            raise ValueError(
                f"{name} is one of: "
                + ", ".join(f"{i} ({v})" for i, v in enumerate(values))
                + "."
            )
        return value
    if kind == "boolean":
        if not isinstance(value, bool):
            raise ValueError(f"{name} is true or false.")
        return value
    if kind == "color":
        return _colour(name, value)
    if kind == "gradient":
        if not isinstance(value, dict) or set(value) - {
            "interpolation",
            "points",
            "type",
        }:
            raise ValueError(
                f"{name} is a gradient: {{interpolation (0-3), points: [{{pos, r, g, b, a}}]}}."
            )
        interpolation = value.get("interpolation", 1)
        if isinstance(interpolation, bool) or interpolation not in (0, 1, 2, 3):
            raise ValueError(f"{name} interpolation is 0, 1, 2 or 3.")
        points = _points(name, value.get("points"), ("pos", "r", "g", "b"), ("a",))
        for point in points:
            for key in point:
                _unit(f"{name} {key}", point[key])
            point.setdefault("a", 1.0)
        return {"interpolation": interpolation, "points": points, "type": "Gradient"}
    if kind == "curve":
        if not isinstance(value, dict) or set(value) - {"points", "type"}:
            raise ValueError(f"{name} is a curve: {{points: [{{x, y, ls, rs}}]}}.")
        return {
            "points": _points(name, value.get("points"), ("x", "y"), ("ls", "rs")),
            "type": "Curve",
        }
    if kind in ("polygon", "polyline"):
        if not isinstance(value, dict) or set(value) - {"points", "type"}:
            raise ValueError(f"{name} is a polygon: {{points: [{{x, y}}]}}.")
        return {
            "points": _points(name, value.get("points"), ("x", "y")),
            "type": "Polygon",
        }
    if not kind and current is not None:
        # A subgraph control whose target has no definition: keep its kind.
        if isinstance(current, bool):
            return check_parameter(name, {"type": "boolean"}, value, None)
        if isinstance(current, (int, float)):
            return _number(name, value)
    raise ValueError(
        f"{name} is a {kind or 'kind of'} parameter Joinery cannot check; change it in Material Maker."
    )


def set_parameters(
    graph: dict[str, Any], name: str, given: dict[str, Any], library: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """A copy of the graph with node `name`'s parameters changed, and the checked values."""
    if not isinstance(given, dict) or not given:
        raise ValueError("parameters is an object of one or more {name: value}.")
    changed = copy.deepcopy(graph)
    item = node(changed, name)
    if item.get("type") == IMAGE_NODE:
        raise ValueError(f"{name} is an image node; bring an image in with wrap_image.")
    definition = definition_of(item, library)
    if definition is None:
        raise ValueError(
            f"{name} ({item.get('type')}) has no definition Joinery can check; change it in Material Maker."
        )
    known = definition["parameters"]
    stored = item.setdefault("parameters", {})
    if not isinstance(stored, dict):
        raise ValueError(f"{name} has no parameter table.")  # noqa: TRY004 - shown to the agent
    checked = {}
    for key, value in given.items():
        if key not in known:
            raise ValueError(
                f"{name} ({item.get('type')}) has no parameter {key!r}. It takes: {', '.join(known) or 'nothing'}."
            )
        checked[key] = check_parameter(key, known[key], value, stored.get(key))
    stored.update(checked)
    return changed, checked


# --- bringing in an image -------------------------------------------------------------


def wrap_image(
    graph: dict[str, Any],
    image: str,
    library: dict[str, Any],
    name: str = "",
    connect: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], str]:
    """A copy of the graph with a new image node for `image` (a project path), and its name.

    Each of `connect` ({node, port}) wires the image's output into that input
    port, replacing whatever fed the port before.
    """
    if Path(image).suffix.lower() not in IMAGE_TYPES:
        raise ValueError("The image is a " + ", ".join(sorted(IMAGE_TYPES)) + " file.")
    changed = copy.deepcopy(graph)
    existing = {str(n.get("name")) for n in nodes(changed)}
    if name:
        if not NODE_NAME.match(name):
            raise ValueError(
                "The node name is letters, digits and '_', starting with a letter."
            )
        if name in existing:
            raise ValueError(f"The graph already has a node {name!r}.")
    else:
        stem = re.sub(r"[^A-Za-z0-9_]", "_", PurePosixPath(image).stem) or "image"
        base = stem if stem[0].isalpha() else "image_" + stem
        name = base[:56]
        number = 2
        while name in existing:
            name = f"{base[:56]}_{number}"
            number += 1
    positions = [n.get("node_position") for n in nodes(changed)]
    xs = [
        p.get("x", 0)
        for p in positions
        if isinstance(p, dict) and isinstance(p.get("x", 0), (int, float))
    ]
    ys = [
        p.get("y", 0)
        for p in positions
        if isinstance(p, dict) and isinstance(p.get("y", 0), (int, float))
    ]
    changed["nodes"].append(
        {
            "name": name,
            "type": IMAGE_NODE,
            "parameters": {IMAGE_PARAMETER: image},
            "node_position": {
                "x": (min(xs) if xs else 0) - 400,
                "y": max(ys) + 200 if ys else 0,
            },
        }
    )
    links = changed.setdefault("connections", [])
    for target in connect or []:
        if not isinstance(target, dict) or set(target) != {"node", "port"}:
            raise ValueError(
                "Each connection is {node, port}: the input the image feeds."
            )
        into = node(changed, str(target["node"]))
        port = target["port"]
        definition = definition_of(into, library)
        inputs = definition["inputs"] if definition else None
        if (
            isinstance(port, bool)
            or not isinstance(port, int)
            or port < 0
            or (inputs is not None and port >= inputs)
        ):
            raise ValueError(
                f"{target['node']} has input ports 0 to {(inputs or 1) - 1}."
            )
        links[:] = [
            c
            for c in links
            if not (
                isinstance(c, dict)
                and c.get("to") == into["name"]
                and c.get("to_port") == port
            )
        ]
        links.append(
            {"from": name, "from_port": 0, "to": into["name"], "to_port": port}
        )
    return changed, name


def export_directory(outputs: tuple[str, ...], source: str) -> str:
    """The export folder a graph's recipe already uses: where its exports sit."""
    folders = {str(PurePosixPath(o).parent) for o in outputs if o != source}
    if not folders:
        raise ValueError(
            f"{source} has no recorded exports; name the output_directory."
        )
    common = PurePosixPath(min(folders, key=len))
    while not all(PurePosixPath(f).is_relative_to(common) for f in folders):
        common = common.parent
    return common.as_posix()
