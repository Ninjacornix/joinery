"""Native-document authoring with offline execution and measured replay records."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

from joinery.catalog import load_catalog, load_project_tools, selection_ids
from joinery.locking import write_lock
from joinery.mcp_runtime import validate_arguments
from joinery.recipe import Recipe, _tool_argv, load_recipe, recipe_paths, record
from joinery.runner import Result, SandboxRunner

IMAGE = "joinery-creative-worker"
SUPPORTED = {
    "blender",
    "krita",
    "aseprite",
    "supercollider",
    "material-maker",
    "inkscape",
}
SUFFIXES = {
    "blender": ".blend",
    "krita": ".kra",
    "aseprite": ".aseprite",
    "supercollider": ".scd",
    "material-maker": ".ptex",
    "inkscape": ".svg",
}


def confined(project: Path, relative: str) -> Path:
    path = Path(relative.removeprefix("res://"))
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("Use a path relative to this project.")
    target = project / path
    if not target.resolve().is_relative_to(project.resolve()):
        raise ValueError("The document is outside this project.")
    return target


async def run_offline(
    argv: list[str], project: Path, timeout: int = 600, *, readonly: bool = False
) -> Result:
    name = "joinery-mcp-" + uuid.uuid4().hex
    start = asyncio.get_running_loop().time()
    process = await asyncio.create_subprocess_exec(
        "docker",
        "run",
        "--rm",
        "--platform",
        "linux/amd64",
        "--network",
        "none",
        "--name",
        name,
        "--label",
        "joinery.scope="
        + os.environ.get("JOINERY_WORKER_SCOPE", name.removeprefix("joinery-mcp-")),
        "--pids-limit",
        "512",
        "--memory",
        "4g",
        "-v",
        f"{project.resolve()}:/work" + (":ro" if readonly else ""),
        "-w",
        "/work",
        IMAGE,
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
        return Result(
            process.returncode == 0,
            stdout.decode(errors="replace"),
            stderr.decode(errors="replace"),
            asyncio.get_running_loop().time() - start,
        )
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
        cleanup = await asyncio.create_subprocess_exec(
            "docker",
            "rm",
            "--force",
            name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(cleanup.wait(), 10)
        if cleanup.returncode is None:
            cleanup.kill()
            await cleanup.wait()


async def inspect_document(project: Path, tool: str, source: str) -> dict[str, Any]:
    if tool not in selection_ids(
        load_catalog(), load_project_tools(project / "joinery.toml")
    ):
        raise PermissionError("Select this owning application in project setup first.")
    path = confined(project, source)
    if not path.is_file():
        raise ValueError("The source document is missing.")
    if path.suffix == ".blend":
        code = "import bpy,json; print('JOINERY_DOCUMENT:' + json.dumps({'objects':[{'name':o.name,'type':o.type,'position':list(o.location),'scale':list(o.scale),'rotation':list(o.rotation_euler),'vertices':len(o.data.vertices) if o.type=='MESH' else 0} for o in bpy.data.objects], 'materials':[m.name for m in bpy.data.materials]}))"
        got = await run_offline(
            ["blender", "--background", source, "--python-expr", code],
            project,
            readonly=True,
        )
        for line in got.output.splitlines():
            if line.startswith("JOINERY_DOCUMENT:"):
                data: dict[str, Any] = json.loads(
                    line.removeprefix("JOINERY_DOCUMENT:")
                )
                return data
        raise ValueError(
            "Blender could not inspect this document: " + got.output[-800:]
        )
    if path.suffix == ".kra":
        import zipfile
        from xml.etree import ElementTree

        with zipfile.ZipFile(path) as archive:
            document = ElementTree.fromstring(archive.read("maindoc.xml"))
        return {
            "layers": [
                dict(node.attrib)
                for node in document.iter()
                if node.tag.split("}")[-1].lower() == "layer"
            ]
        }
    if path.suffix in {".scd", ".sc", ".ptex", ".svg", ".json"}:
        if path.stat().st_size > 2_000_000:
            raise ValueError(
                "Read specific graph sections; this document exceeds the inspection text limit."
            )
        return {"source": source, "content": path.read_text()}
    raise ValueError("Use the owning app's native inspection tool for this format.")


def enabled(project: Path, tool: str) -> None:
    journal = project.parent / "journal.json"
    if (
        project.name != "stage"
        or not journal.is_file()
        or json.loads(journal.read_text()).get("state") != "staging"
    ):
        raise PermissionError(
            "Native authoring requires a project transaction; open it through the project gateway."
        )
    if tool not in SUPPORTED:
        raise ValueError("No native document adapter for this tool.")
    if tool not in selection_ids(
        load_catalog(), load_project_tools(project / "joinery.toml")
    ):
        raise PermissionError(f"{tool} is not selected for this project.")


def _script(tool: str, source: str, base: str, code: str, exports: list[str]) -> str:
    if tool == "blender":
        lines = [
            "import bpy, os, math",
            "os.chdir('/work')",
            "bpy.ops.wm.read_factory_settings(use_empty=True)",
            "bpy.context.preferences.filepaths.save_version = 0",
        ]
        if base:
            lines.append(
                f"bpy.ops.wm.open_mainfile(filepath=os.path.abspath({base!r}))"
            )
        lines += [
            "bpy.context.scene.unit_settings.system = 'METRIC'",
            "bpy.context.scene.unit_settings.scale_length = 1.0",
            code,
        ]
        lines += [
            "assert all(all(math.isfinite(x) for row in o.matrix_world for x in row) for o in bpy.data.objects), 'Nonfinite object transform'",
            "bpy.ops.file.pack_all()",
            f"bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath({source!r}))",
        ]
        for export in exports:
            if Path(export).suffix != ".glb":
                raise ValueError(
                    "The automatic Blender export uses GLB. Render images explicitly in the operation script."
                )
            lines.append(
                f"bpy.ops.export_scene.gltf(filepath=os.path.abspath({export!r}), export_format='GLB', export_yup=True)"
            )
        return "\n".join(lines) + "\n"
    if tool == "krita":
        return "\n".join(
            [
                "from krita import Krita, InfoObject",
                "import os",
                "os.chdir('/work')",
                "app = Krita.instance()",
                f"document = app.openDocument(os.path.abspath({base!r}))"
                if base
                else "document = None",
                code,
                "assert document is not None, 'Create or open a native Krita document'",
                "document.setBatchmode(True)",
                "document.waitForDone()",
                f"assert document.saveAs(os.path.abspath({source!r}))",
                *[
                    f"assert document.exportImage(os.path.abspath({path!r}), InfoObject())"
                    for path in exports
                ],
                "document.setModified(False)",
                "document.close()",
                "",
            ]
        )
    if tool == "aseprite":
        return "\n".join(
            [
                f"local sprite = app.open({json.dumps(base)})"
                if base
                else "local sprite = nil",
                code,
                "assert(sprite ~= nil, 'Create or open a native sprite')",
                f"sprite:saveAs({json.dumps(source)})",
                *[f"sprite:saveCopyAs({json.dumps(path)})" for path in exports],
                "",
            ]
        )
    if tool == "supercollider":
        # The log recreates the exact native patch before interpreting it, so a
        # clean replay never depends on the previous source output remaining.
        # Rendering is NRT with a completion action; no sound device is needed.
        return (
            "(\nFile.use("
            + json.dumps(source, ensure_ascii=False)
            + ', "w", { |file| file.write('
            + json.dumps(code, ensure_ascii=False)
            + "); });\n"
            + json.dumps(code, ensure_ascii=False)
            + ".interpret;\n)\n"
        )
    raise ValueError("Use the graph or vector document operation for this application.")


async def edit_document(
    project: Path, tool: str, source: str, code: str, exports: list[str], note: str = ""
) -> dict[str, Any]:
    enabled(project, tool)
    validate_arguments({"code": code})
    target = confined(project, source)
    if target.suffix != SUFFIXES[tool] or Path(source).parts[0] not in {"art", "audio"}:
        raise ValueError("Use the owning tool's native format under art/ or audio/.")
    if tool == "blender" and (
        not source.startswith("art/sources/")
        or any(Path(path).is_relative_to(Path(source).parent) for path in exports)
    ):
        raise ValueError(
            "Keep native Blender sources under art/sources/ and exports outside that source directory."
        )
    for output in exports:
        confined(project, output)
        if Path(output).parts[0] not in {"art", "audio"}:
            raise ValueError("Exports belong under art/ or audio/.")
    _replacements(project, (source, *exports))
    identifier = uuid.uuid4().hex
    recipe_dir = project / "recipes"
    recipe_dir.mkdir(exist_ok=True)
    base = ""
    if target.is_file():
        # An immutable preimage makes an incremental edit replayable without
        # depending on the future version of the very document it modifies.
        input_path = recipe_dir / "inputs" / (identifier + target.suffix)
        input_path.parent.mkdir(exist_ok=True)
        (input_path.parent / ".gdignore").write_text("")
        shutil.copy2(target, input_path)
        base = input_path.relative_to(project).as_posix()
    for output in [source, *exports]:
        confined(project, output).parent.mkdir(parents=True, exist_ok=True)
    extension = {
        "blender": ".py",
        "krita": ".py",
        "aseprite": ".lua",
        "supercollider": ".scd",
    }.get(tool)
    if extension is None:
        raise ValueError("Use edit_graph or edit_vector for this application.")
    script = recipe_dir / (identifier + extension)
    script.write_text(_script(tool, source, base, code, exports))
    if tool == "supercollider":
        target.write_text(code)
    recipe = Recipe(
        tool=tool,
        script=script.relative_to(project).as_posix(),
        outputs=(source, *exports),
        sources=(base,) if base else (),
        note=note,
    )
    result = await run_offline(_tool_argv(recipe), project)
    if not result.ok:
        raise RuntimeError(
            f"{tool} did not complete the document operation: {result.output[-2000:]}"
        )
    if tool == "blender":
        # Godot imports portable GLB exports. Keep native .blend documents
        # without requiring a Blender install on every game developer's host.
        (target.parent / ".gdignore").write_text("")
    return await _record(project, recipe, recipe_dir / (identifier + ".toml"))


def _replacements(project: Path, outputs: tuple[str, ...]) -> list[Path]:
    replacements = []
    for previous in recipe_paths(project):
        old = load_recipe(previous)
        if set(old.outputs) & set(outputs):
            if not set(old.outputs) <= set(outputs):
                raise ValueError(
                    "Refresh every output of the existing source recipe: "
                    + ", ".join(old.outputs)
                )
            replacements.append(previous)
    return replacements


async def _record(project: Path, recipe: Recipe, path: Path) -> dict[str, Any]:
    replacements = [p for p in _replacements(project, recipe.outputs) if p != path]
    rows = [
        "version = 1",
        f"tool = {json.dumps(recipe.tool)}",
        f"script = {json.dumps(recipe.script)}",
        f"outputs = {json.dumps(recipe.outputs)}",
        f"sources = {json.dumps(recipe.sources)}",
        f"note = {json.dumps(recipe.note)}",
    ]
    path.write_text("\n".join(rows) + "\n")
    await asyncio.to_thread(record, path, project, SandboxRunner(image=IMAGE))
    if replacements:
        history = project / "recipes/history"
        history.mkdir(exist_ok=True)
        for previous in replacements:
            previous.rename(history / previous.name)
    problems = write_lock(project)
    if problems:
        raise ValueError("Provenance recording failed: " + "; ".join(problems))
    return {
        "source": recipe.outputs[0],
        "outputs": list(recipe.outputs),
        "recipe": path.relative_to(project).as_posix(),
        "status": "staged",
        "requires_review": True,
    }


async def edit_graph(
    project: Path, source: str, graph: dict[str, Any], output_directory: str
) -> dict[str, Any]:
    enabled(project, "material-maker")
    target = confined(project, source)
    output = confined(project, output_directory)
    if (
        target.suffix != ".ptex"
        or Path(source).parts[0] != "art"
        or Path(output_directory).parts[0] != "art"
    ):
        raise ValueError(
            "Material Maker uses a native .ptex under art/ and an art/ export directory."
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    code = (
        "import json, pathlib, subprocess\n"
        + f"pathlib.Path({source!r}).write_text({json.dumps(json.dumps(graph))})\n"
        + f"driver = pathlib.Path({('recipes/' + hashlib.sha256(source.encode()).hexdigest()[:16] + '.export.gd')!r})\n"
        + f"driver.write_text({material_export_driver(source, output_directory)!r})\n"
        + "subprocess.run(['/opt/material-maker/joinery-godot', '--main-pack', '/opt/material-maker/material_maker.x86_64', '--rendering-method', 'mobile', '--script', str(driver)], check=True)\n"
    )
    return await _scripted_export(
        project, "material-maker", source, code, output_directory
    )


def material_export_driver(source: str, directory: str) -> str:
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
        "PREFIX", json.dumps("/work/" + directory.rstrip("/") + "/" + Path(source).stem)
    )


async def edit_vector(
    project: Path, source: str, svg: str, export: str
) -> dict[str, Any]:
    enabled(project, "inkscape")
    for relative in (source, export):
        if Path(relative).parts[0] != "art":
            raise ValueError("Vector source and exports belong under art/.")
        confined(project, relative).parent.mkdir(parents=True, exist_ok=True)
    if Path(source).suffix != ".svg" or Path(export).suffix != ".png":
        raise ValueError("Use native SVG and a PNG export.")
    validate_arguments(svg)
    if re.search(
        r"(?:file:|<!ENTITY|<script|(?:href|url)\s*[=(]\s*['\"]?https?:)",
        svg,
        re.IGNORECASE,
    ):
        raise ValueError(
            "Vector documents must be self-contained; external assets and scripts are not accepted."
        )
    code = (
        "import pathlib, subprocess\n"
        + f"pathlib.Path({source!r}).write_text({svg!r})\n"
        + f"subprocess.run(['inkscape', {source!r}, '--export-filename=' + {export!r}], check=True)\n"
    )
    return await _scripted_export(
        project, "inkscape", source, code, str(Path(export).parent), [export]
    )


async def _scripted_export(
    project: Path,
    tool: str,
    source: str,
    code: str,
    directory: str,
    outputs: list[str] | None = None,
) -> dict[str, Any]:
    before = _export_inventory(project, directory) if outputs is None else {}
    previous_outputs: set[str] = set()
    if outputs is None:
        for path in recipe_paths(project):
            previous = load_recipe(path)
            if previous.tool == tool and source in previous.outputs:
                previous_outputs.update(previous.outputs)
    identifier = hashlib.sha256((source + code).encode()).hexdigest()[:32]
    script = project / "recipes" / (identifier + ".py")
    script.parent.mkdir(exist_ok=True)
    script.write_text(code)
    result = await run_offline(
        ["python3", script.relative_to(project).as_posix()], project
    )
    if not result.ok:
        raise RuntimeError(result.output[-2000:])
    if outputs is None:
        after = _export_inventory(project, directory)
        outputs = sorted(
            relative
            for relative, digest in after.items()
            if relative != source
            and (before.get(relative) != digest or relative in previous_outputs)
        )
    if not outputs:
        raise ValueError("The application produced no exports.")
    recipe = Recipe(
        tool=tool,
        script=script.relative_to(project).as_posix(),
        outputs=(source, *outputs),
    )
    return await _record(project, recipe, script.with_suffix(".toml"))


def _export_inventory(project: Path, directory: str) -> dict[str, str]:
    outputs = {}
    for path in confined(project, directory).rglob("*"):
        if path.is_file():
            relative = path.relative_to(project).as_posix()
            target = confined(project, relative)
            with target.open("rb") as output:
                outputs[relative] = hashlib.file_digest(output, "sha256").hexdigest()
    return outputs
