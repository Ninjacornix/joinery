"""Material Maker: native .ptex graphs exported to Godot materials offline."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import tomllib
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import Any

from joinery.tools.base import (
    CREATIVE_WORKER,
    ImageCheck,
    McpProbe,
    ToolModule,
    WorkerExec,
)

HOME = "/opt/material-maker"


def export_driver(source: str, directory: str, name: str = "") -> str:
    """Godot script that loads the graph and exports every material node.

    Exports are named after `name` (the native source), defaulting to `source`.
    """
    return """extends SceneTree
func _initialize() -> void:
    run.call_deferred()
func run() -> void:
    await process_frame
    var loader: Node = root.get_node("mm_loader")
    var graph = await loader.load_gen(SOURCE)
    if graph == null:
        quit(1)
        return
    root.add_child(graph)
    var count := 0
    for node in graph.get_children():
        if node.has_method("export_material"):
            # Material Maker 1.8's command-line export otherwise skips existing
            # files marked prompt_overwrite. This document is already isolated
            # in transaction staging; refresh its declared native exports.
            var profile: Dictionary = node.get_export("Godot/Godot 4 Standard")
            for file: Dictionary in profile.get("files", []):
                file["prompt_overwrite"] = false
            await node.export_material(PREFIX, "Godot/Godot 4 Standard", 0, true)
            count += 1
    graph.free()
    quit(0 if count > 0 else 1)
""".replace("SOURCE", json.dumps("/work/" + source)).replace(
        "PREFIX",
        json.dumps("/work/" + directory.rstrip("/") + "/" + Path(name or source).stem),
    )


def graph_code(
    source: str,
    graph: dict[str, Any],
    output_directory: str,
    runtime: dict[str, Any] | None = None,
) -> str:
    """The recipe script: write the graph, then export it through Material Maker.

    `runtime` is the same graph with image inputs as /work paths. The native
    .ptex keeps project-relative paths; the worker loads a copy under recipes/.
    """
    stem = "recipes/" + hashlib.sha256(source.encode()).hexdigest()[:16]
    driver = stem + ".export.gd"
    loaded = source
    lines = [
        "import json, pathlib, subprocess\n",
        f"pathlib.Path({source!r}).write_text({json.dumps(json.dumps(graph))})\n",
    ]
    if runtime is not None and runtime != graph:
        loaded = stem + ".run.ptex"
        lines.append(
            f"pathlib.Path({loaded!r}).write_text({json.dumps(json.dumps(runtime))})\n"
        )
    return "".join(
        [
            *lines,
            f"driver = pathlib.Path({driver!r})\n",
            f"driver.write_text({export_driver(loaded, output_directory, source)!r})\n",
            f"subprocess.run(['{HOME}/joinery-godot', '--main-pack', '{HOME}/material_maker.x86_64', '--rendering-method', 'mobile', '--script', str(driver)], check=True)\n",
        ]
    )


#: Immutable copies recipes start from (joinery.recipe.PREIMAGE_DIR).
PREIMAGES = "recipes/inputs"
#: Material Maker's image node and the parameter holding its file.
IMAGE_NODE = "image"
IMAGE_PARAMETER = "image"


def _nodes(graph: Any) -> Iterator[dict[str, Any]]:
    """Every node, including those inside nested subgraphs."""
    if isinstance(graph, dict):
        for node in graph.get("nodes") or ():
            if isinstance(node, dict):
                yield node
                yield from _nodes(node)


def _known_assets(project: Path) -> set[str]:
    """Files with provenance: a joinery.lock entry or a recipe that makes them."""
    from joinery.locking import claimed_outputs

    known = set(claimed_outputs(project))
    lock = project / "joinery.lock"
    if lock.is_file():
        for entry in tomllib.loads(lock.read_text()).get("asset", []):
            if isinstance(entry, dict) and isinstance(entry.get("path"), str):
                known.add(entry["path"])
    return known


def _image_input(project: Path, value: Any, source: str, output_directory: str) -> str:
    """The project-relative path of one image node's file, after checking it."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Each Material Maker image node needs an image file.")
    text = value.strip().replace("\\", "/")
    for prefix in ("res://", "/work/"):
        text = text.removeprefix(prefix)
    path = PurePosixPath(text)
    if (
        path.is_absolute()
        or re.match(r"^[A-Za-z]:/", text)
        or "://" in text
        or ".." in path.parts
        or not path.parts
        or (path.parts[0] != "art" and path.parent != PurePosixPath(PREIMAGES))
    ):
        raise ValueError(
            f"Image input {value!r} must be a file under art/ in this project."
        )
    relative = path.as_posix()
    target = project / relative
    if not target.resolve().is_relative_to(project.resolve()) or not target.is_file():
        raise ValueError(f"Image input {relative} is not a file in this project.")
    if relative == source or path.is_relative_to(PurePosixPath(output_directory)):
        raise ValueError(
            f"Image input {relative} is inside the export directory "
            f"{output_directory}; the export would overwrite its own input."
        )
    return relative


def image_inputs(
    project: Path, graph: dict[str, Any], source: str, output_directory: str
) -> tuple[dict[str, Any], dict[str, Any], tuple[str, ...]]:
    """Check a graph's image inputs and return (stored, runtime, inputs).

    Each image must be a project file under art/ with provenance (a lock entry
    or a recipe), outside the export directory. The stored graph keeps paths
    project-relative so the .ptex still opens in desktop Material Maker; the
    runtime graph points the worker at /work.
    """
    stored = copy.deepcopy(graph)
    runtime = copy.deepcopy(graph)
    inputs: list[str] = []
    known: set[str] | None = None
    for kept, loaded in zip(_nodes(stored), _nodes(runtime), strict=True):
        if kept.get("type") != IMAGE_NODE:
            continue
        parameters = kept.get("parameters")
        relative = _image_input(
            project,
            parameters.get(IMAGE_PARAMETER) if isinstance(parameters, dict) else None,
            source,
            output_directory,
        )
        assert isinstance(parameters, dict)
        if known is None:
            known = _known_assets(project)
        # A preimage is an immutable copy named by its content, so it needs
        # no lock entry of its own (a map's pixels taken from its model).
        preimage = relative.startswith(PREIMAGES + "/") and (
            hashlib.sha256((project / relative).read_bytes()).hexdigest()
            == PurePosixPath(relative).stem
        )
        if relative not in known and not preimage:
            raise ValueError(
                f"{relative} has no joinery.lock entry, so its origin is unknown. "
                "Ask the user to declare it as a human import, or make it with a "
                "project tool first."
            )
        parameters[IMAGE_PARAMETER] = relative
        loaded["parameters"][IMAGE_PARAMETER] = "/work/" + relative
        inputs.append(relative)
    return stored, runtime, tuple(dict.fromkeys(inputs))


def _version() -> str:
    versions = Path(__file__).resolve().parents[2] / "sandbox/versions.env"
    try:
        for line in versions.read_text().splitlines():
            if line.startswith("MATERIALMAKER_VERSION="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


class MaterialMaker(ToolModule):
    id = "material-maker"
    name = "Material Maker"
    family = "joinery-materialmaker"
    suffix = ".ptex"
    authoring = True
    binary = "material-maker"
    #: The export runs this Godot build on Material Maker's pack.
    requires = (f"{HOME}/material_maker.x86_64", f"{HOME}/joinery-godot")
    desktop = "material-maker"
    version = _version()
    ready_summary = "{name} {version} ready (runs offline)"
    broken = "Material Maker failed its check"
    checks = (
        ImageCheck(CREATIVE_WORKER),
        WorkerExec(
            CREATIVE_WORKER,
            (
                "sh",
                "-c",
                (
                    f"test -x {HOME}/material_maker.x86_64"
                    f" && test -x {HOME}/joinery-godot && echo JOINERY_MM_OK"
                ),
            ),
            expect="JOINERY_MM_OK",
            on_failure="needs_image",
            failure=(
                "Material Maker worker found but its exporter is missing; rebuild the "
                "creative worker."
            ),
        ),
        McpProbe(
            "joinery-materialmaker",
            "document_capabilities",
            capability="material-maker",
            image=CREATIVE_WORKER,
            failure=(
                "Material Maker worker found but its exporter is missing; rebuild the "
                "creative worker."
            ),
        ),
    )

    def replay_argv(self, script: str) -> list[str]:
        return ["python3", script]


MODULE = MaterialMaker()
