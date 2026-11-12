"""Native-document authoring with offline execution and measured replay records."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from joinery import tools
from joinery.catalog import load_catalog, load_project_tools, selection_ids
from joinery.locking import write_lock
from joinery.mcp_runtime import validate_arguments
from joinery.recipe import (
    HISTORY_DIR,
    PREIMAGE_DIR,
    Recipe,
    load_recipe,
    recipe_paths,
    recipe_text,
    record,
    store_preimage,
)
from joinery.runner import Result, Runner, SandboxRunner
from joinery.tools import (
    blender,
    blender_anim,
    blender_batch,
    blender_export,
    inkscape,
    material_maker,
)
from joinery.tools.base import ToolModule
from joinery.transactions import copy_file
from joinery.worker_identity import docker_owner_args

IMAGE = "joinery-creative-worker"
#: Tools whose native documents Joinery edits (script, graph or vector adapter).
SUPPORTED = tools.authoring_ids()
SUFFIXES = {tool: tools.get(tool).suffix for tool in SUPPORTED}
#: Native formats inspect_document returns as text.
TEXT_SUFFIXES = {".scd", ".sc", ".ptex", ".svg", ".json"}
#: Kept for callers that still import these from here.
texture_dir = blender.texture_dir
material_export_driver = material_maker.export_driver


def confined(project: Path, relative: str) -> Path:
    path = Path(relative.removeprefix("res://"))
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("Use a path relative to this project.")
    target = project / path
    if not target.resolve().is_relative_to(project.resolve()):
        raise ValueError("The document is outside this project.")
    return target


async def run_offline(
    argv: list[str],
    project: Path,
    timeout: int = 600,
    *,
    readonly: bool = False,
    image: str = IMAGE,
) -> Result:
    if os.environ.get("JOINERY_WORKER_SCOPE"):
        from joinery.warm_workers import execute

        return await execute(argv, project, timeout, readonly, image)
    name = "joinery-mcp-" + uuid.uuid4().hex
    start = asyncio.get_running_loop().time()
    process = await asyncio.create_subprocess_exec(
        "docker",
        "run",
        "--rm",
        *docker_owner_args(),
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
        image,
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
    # The app reads a project path, never Godot's res:// form.
    source = source.removeprefix("res://")
    path = confined(project, source)
    if not path.is_file():
        raise ValueError("The source document is missing.")
    module = tools.REGISTRY.get(tool)
    if module is not None and module.suffix == path.suffix:
        described = await module.inspect(project, path, source, run_offline)
        if described is not None:
            return described
    if path.suffix in TEXT_SUFFIXES:
        if path.stat().st_size > 2_000_000:
            raise ValueError(
                "Read specific graph sections; this document exceeds the inspection text limit."
            )
        return {"source": source, "content": path.read_text()}
    raise ValueError("Use the owning app's native inspection tool for this format.")


def require_transaction(project: Path) -> None:
    """Refuse unless `project` is the stage of a transaction that is staging."""
    journal = project.parent / "journal.json"
    if (
        project.name != "stage"
        or not journal.is_file()
        or json.loads(journal.read_text()).get("state") != "staging"
    ):
        raise PermissionError(
            "Native authoring requires a project transaction; open it through the project gateway."
        )


def enabled(project: Path, tool: str) -> None:
    require_transaction(project)
    if tool not in SUPPORTED:
        raise ValueError("No native document adapter for this tool.")
    if tool not in selection_ids(
        load_catalog(), load_project_tools(project / "joinery.toml")
    ):
        raise PermissionError(f"{tool} is not selected for this project.")


async def edit_document(
    project: Path,
    tool: str,
    source: str,
    code: str,
    exports: list[str],
    note: str = "",
    *,
    keep_macro_shape_keys: bool | None = None,
    shape_keys: blender_export.ExportShapeKeys | None = None,
    inputs: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Apply `code` to a native document, export it and record the recipe.

    `inputs` are further project files the code reads (a painted map), listed
    as recipe sources. For Blender, `shape_keys` chooses the GLB's morph
    targets and `keep_macro_shape_keys` keeps MPFB2's body-shape keys as morph
    targets; with neither, the model keeps its recorded choice.
    """
    module, source, exports, target = _checked_document(
        project, tool, source, exports, (code,)
    )
    inputs = tuple(dict.fromkeys((*inputs, *module.script_inputs(code))))
    textures: tuple[str, ...] = ()
    if tool == "blender":
        # Texture files are regenerated on every edit; clear the old set so an
        # image removed from the model does not leave a stale file behind.
        textures = _textures(project, source)
    _refuse_dropping_audio_edits(project, source, (source, *exports))
    _replacements(project, (source, *exports, *textures))
    identifier = uuid.uuid4().hex
    recipe_dir = project / "recipes"
    recipe_dir.mkdir(exist_ok=True)
    # An immutable preimage makes an incremental edit replayable without
    # depending on the future version of the very document it modifies.
    # Named by content, so edits from the same state share one copy.
    keep = target.is_file() and module.uses_preimage
    base = store_preimage(project, target) if keep else ""
    for output in [source, *exports]:
        confined(project, output).parent.mkdir(parents=True, exist_ok=True)
    for extra in inputs:
        if not confined(project, extra).is_file():
            raise ValueError(f"{extra} is not a file in this project.")
    script = recipe_dir / (identifier + module.script_ext)
    if isinstance(module, blender.Blender):
        chosen = _export_shape_keys(project, source, keep_macro_shape_keys, shape_keys)
        script.write_text(module.script(source, base, code, exports, shape_keys=chosen))
    else:
        script.write_text(module.script(source, base, code, exports))
    if module.text_source:
        target.write_text(code)
    recipe = Recipe(
        tool=tool,
        script=script.relative_to(project).as_posix(),
        outputs=(source, *exports),
        sources=tuple(dict.fromkeys(((base,) if base else ()) + inputs)),
        note=note,
    )
    # Keep the old maps in memory: a failed or refused edit puts them back, so
    # the next edit still finds every output of the recipe it replaces.
    kept = {old: (project / old).read_bytes() for old in textures}
    for old in textures:
        (project / old).unlink(missing_ok=True)
    try:
        argv = module.replay_argv(recipe.script)
        if module.replay_image:
            # A tool with its own worker image (Audacity) replays there.
            result = await run_offline(argv, project, image=module.replay_image)
        else:
            result = await run_offline(argv, project)
    except BaseException:
        _restore(project, kept)
        raise
    if not result.ok:
        _restore(project, kept)
    if tool == "blender" and result.ok:
        folder = confined(project, texture_dir(source))
        written = tuple(
            p.relative_to(project).as_posix() for p in sorted(folder.glob("*.png"))
        )
        recipe = recipe.model_copy(update={"outputs": (*recipe.outputs, *written)})
    if not result.ok:
        raise RuntimeError(
            f"{tool} did not complete the document operation: {result.output[-2000:]}"
        )
    if tool == "blender":
        # Godot imports portable GLB exports. Keep native .blend documents
        # without requiring a Blender install on every game developer's host.
        (target.parent / ".gdignore").write_text("")
    recorded = await _record(project, recipe, recipe_dir / (identifier + ".toml"))
    if tool == "blender":
        recorded.update(_export_facts(project, exports, result.output))
    return recorded


def _checked_document(
    project: Path, tool: str, source: str, exports: list[str], codes: tuple[str, ...]
) -> tuple[ToolModule, str, list[str], Path]:
    """The checks every document edit passes before its app starts.

    Returns the tool module, the source and exports as project paths, and the
    source's path.
    """
    enabled(project, tool)
    module = tools.get(tool)
    if not module.script_ext:
        raise ValueError("Use edit_graph or edit_vector for this application.")
    for code in codes:
        validate_arguments({"code": code})
    # Agents name files the way Godot shows them (res://art/models/x.glb, as a
    # part's resource reads); the recipe and every check use project paths.
    source = source.removeprefix("res://")
    exports = [output.removeprefix("res://") for output in exports]
    target = confined(project, source)
    if target.suffix != module.suffix or Path(source).parts[0] not in {"art", "audio"}:
        raise ValueError("Use the owning tool's native format under art/ or audio/.")
    module.check_destinations(source, exports)
    for output in exports:
        confined(project, output)
        if Path(output).parts[0] not in {"art", "audio"}:
            raise ValueError("Exports belong under art/ or audio/.")
    return module, source, exports, target


def _textures(project: Path, source: str) -> tuple[str, ...]:
    """The texture files a Blender source's last edit wrote."""
    folder = confined(project, texture_dir(source))
    return tuple(
        p.relative_to(project).as_posix() for p in sorted(folder.glob("*.png"))
    )


@dataclass(frozen=True)
class DocumentStep:
    """One edit of a batch: its operation code, its recipe note, and files it reads."""

    code: str
    note: str
    inputs: tuple[str, ...] = ()


@dataclass
class StepsOutcome:
    """What a batch of edits did: a result per finished step, and the failure."""

    #: As edit_document returns for each step, in order.
    results: list[dict[str, Any]] = field(default_factory=list)
    #: The step that failed (0-based), or None when every step finished.
    failed: int | None = None
    error: str = ""
    #: Wall-clock seconds of the one Blender run.
    seconds: float = 0.0


#: A batch runs inside one gateway call, which waits at most 900 s.
BATCH_TIMEOUT = 840


async def edit_document_steps(
    project: Path,
    source: str,
    steps: list[DocumentStep],
    exports: list[str],
    *,
    keep_macro_shape_keys: bool | None = None,
    shape_keys: blender_export.ExportShapeKeys | None = None,
) -> StepsOutcome:
    """Apply several Blender edits to one document in one Blender process.

    Every step gets the recipe script, preimage, note and recorded fingerprints
    it would get from its own edit_document call, in order: the earlier steps'
    recipes go to recipes/history (where the next edit would move them) and the
    last finished step's recipe is the current one, so `joinery rebuild`
    replays the same thing either way. A failing step stops the batch; its
    files are put back as the step before left them, and nothing after it runs.
    """
    if not steps:
        raise ValueError("A batch has at least one step.")
    tool = "blender"
    module, source, exports, target = _checked_document(
        project, tool, source, exports, tuple(step.code for step in steps)
    )
    if not isinstance(module, blender.Blender):
        raise TypeError("Batched edits run in Blender.")
    textures = _textures(project, source)
    _refuse_dropping_audio_edits(project, source, (source, *exports))
    _replacements(project, (source, *exports, *textures))
    for step in steps:
        for extra in step.inputs:
            if not confined(project, extra).is_file():
                raise ValueError(f"{extra} is not a file in this project.")
    recipe_dir = project / "recipes"
    recipe_dir.mkdir(exist_ok=True)
    base = store_preimage(project, target) if target.is_file() else ""
    for output in [source, *exports]:
        confined(project, output).parent.mkdir(parents=True, exist_ok=True)
    chosen = _export_shape_keys(project, source, keep_macro_shape_keys, shape_keys)
    batch = uuid.uuid4().hex
    ids = [uuid.uuid4().hex for _ in steps]
    scripts = [f"recipes/{identifier}{module.script_ext}" for identifier in ids]
    marks = [blender_batch.placeholder(batch, index) for index in range(len(steps))]
    scratch = f"recipes/.batch-{batch}"
    plan = {
        "source": source,
        "base": base,
        "exports": exports,
        "textures": texture_dir(source),
        "preimages": PREIMAGE_DIR,
        "scratch": scratch,
        "steps": [
            {
                "script": script,
                "placeholder": mark,
                "text": module.script(
                    source,
                    base if index == 0 else mark,
                    step.code,
                    exports,
                    shape_keys=chosen,
                ),
            }
            for index, (step, script, mark) in enumerate(
                zip(steps, scripts, marks, strict=True)
            )
        ],
    }
    folder = project / scratch
    folder.mkdir(parents=True)
    try:
        (folder / "plan.json").write_text(json.dumps(plan))
        (folder / "driver.py").write_text(blender_batch.DRIVER)
        try:
            ran = await run_offline(
                [
                    "blender",
                    "--background",
                    "--factory-startup",
                    "--python",
                    f"{scratch}/driver.py",
                    "--",
                    f"{scratch}/plan.json",
                ],
                project,
                BATCH_TIMEOUT,
            )
        except BaseException:
            _undo_step(project, source, exports, scratch, 0, base)
            for script in scripts:
                (project / script).unlink(missing_ok=True)
            raise
        progress = blender_batch.read_progress(ran.stdout)
        finished = 0
        while finished in progress.done:
            finished += 1
        outcome = StepsOutcome(seconds=ran.seconds)
        if finished < len(steps):
            outcome.failed = finished
            outcome.error = progress.error or (
                "Blender stopped without saying why: " + ran.output[-1500:]
            )
            if finished in progress.bases:
                _undo_step(
                    project,
                    source,
                    exports,
                    scratch,
                    finished,
                    progress.bases[finished],
                )
            for script in scripts[finished:]:
                (project / script).unlink(missing_ok=True)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    if finished:
        # Godot imports portable GLB exports, never the .blend beside them.
        (target.parent / ".gdignore").write_text("")
    history = project / HISTORY_DIR
    for index in range(finished):
        done = progress.done[index]
        step = steps[index]
        step_base = progress.bases.get(index, "")
        recipe = Recipe(
            tool=tool,
            script=scripts[index],
            outputs=(source, *exports, *done["textures"]),
            sources=tuple(
                dict.fromkeys(((step_base,) if step_base else ()) + step.inputs)
            ),
            note=step.note,
        )
        output = progress.output.get(index, "")
        if index < finished - 1:
            # The next step's edit would move this recipe to history.
            history.mkdir(parents=True, exist_ok=True)
            path = history / (ids[index] + ".toml")
            path.write_text(
                recipe_text(recipe.model_copy(update={"expect": done["expect"]}))
            )
            result: dict[str, Any] = {
                "source": source,
                "outputs": list(recipe.outputs),
                "recipe": path.relative_to(project).as_posix(),
                "status": "staged",
                "requires_review": True,
            }
            result.update(_export_facts(project, [], output))
        else:
            result = await _record(project, recipe, recipe_dir / (ids[index] + ".toml"))
            result.update(_export_facts(project, exports, output))
        outcome.results.append(result)
    return outcome


def _undo_step(
    project: Path,
    source: str,
    exports: list[str],
    scratch: str,
    index: int,
    base: str,
) -> None:
    """Put back what a failed batch step replaced: its document, exports and textures."""
    backup = project / scratch / str(index)
    if not backup.is_dir():
        return
    if base:
        copy_file(project / base, project / source)
    else:
        (project / source).unlink(missing_ok=True)
    for number, export in enumerate(exports):
        saved = backup / f"export-{number}"
        if saved.is_file():
            copy_file(saved, project / export)
        else:
            (project / export).unlink(missing_ok=True)
    folder = confined(project, texture_dir(source))
    for png in folder.glob("*.png"):
        png.unlink()
    for png in sorted((backup / "textures").glob("*.png")):
        copy_file(png, folder / png.name)


def recorded_shape_keys(project: Path, source: str) -> blender_export.ExportShapeKeys:
    """The shape-key choice of the recipe that last made `source` (the default if none)."""
    for path in recipe_paths(project):
        recipe = load_recipe(path)
        script = project / recipe.script
        if source in recipe.outputs and script.is_file():
            found = blender_export.recorded_shape_keys(script.read_text())
            return found or blender_export.ExportShapeKeys()
    return blender_export.ExportShapeKeys()


def _export_shape_keys(
    project: Path,
    source: str,
    keep_macros: bool | None,
    chosen: blender_export.ExportShapeKeys | None,
) -> blender_export.ExportShapeKeys:
    """The export's shape-key choice: the one given, else the model's own, with
    `keep_macros` changed when it is given."""
    if chosen is not None:
        return chosen
    recorded = recorded_shape_keys(project, source)
    if keep_macros is None:
        return recorded
    return blender_export.ExportShapeKeys(keep_macros=keep_macros, keep=recorded.keep)


def _export_facts(project: Path, exports: list[str], output: str) -> dict[str, Any]:
    """Shape keys baked or kept per mesh, and each GLB's meshes and morph targets."""
    facts: dict[str, Any] = {}
    rows = blender_export.shape_rows(output)
    if rows:
        facts["shape_keys"] = rows
    # What a typed operation reports of itself (a refitted rig, values set).
    done = []
    for line in output.splitlines():
        for prefix in ("JOINERY_PART_OP:", "JOINERY_MODEL_OP:"):
            if line.startswith(prefix):
                with contextlib.suppress(ValueError):
                    done.append(json.loads(line.removeprefix(prefix)))
    if done:
        facts["operation"] = done
    animations = blender_anim.animation_rows(output)
    if animations:
        facts["animations"] = animations
    models = {}
    for path in exports:
        if Path(path).suffix == ".glb" and (project / path).is_file():
            with contextlib.suppress(ValueError, KeyError, IndexError):
                models[path] = blender_export.glb_meshes(project / path)
    if models:
        facts["models"] = models
    return facts


def _restore(project: Path, files: dict[str, bytes]) -> None:
    """Write back files removed before a tool run that did not complete."""
    for relative, data in files.items():
        path = project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def _refuse_dropping_audio_edits(
    project: Path, source: str, outputs: tuple[str, ...]
) -> None:
    """Refuse a plain render over a sound whose recipe carries audio edits.

    The new recipe would replace the edited one and silently lose the trims,
    fades and loudness changes. The sound tools re-apply them instead.
    """
    from servers.sound.ops import recipe_edits

    for path in recipe_paths(project):
        old = load_recipe(path)
        if not set(old.outputs) & set(outputs):
            continue
        script = project / old.script
        if script.is_file() and recipe_edits(script.read_text()):
            raise ValueError(
                f"{source} has audio edits that a new render would drop. "
                "Use set_surge_parameters to change a Surge XT patch and keep "
                "them, or process_audio to edit the sound."
            )


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


def _fingerprint_runner() -> Runner:
    """Where the recipe's fingerprints are measured: the warm worker and its
    running Blender when this server has one (joinery.warm_blender), else a
    fresh container."""
    from joinery import warm_blender

    if os.environ.get("JOINERY_WORKER_SCOPE") and warm_blender.enabled():
        from joinery.warm_workers import WarmRunner

        return WarmRunner(asyncio.get_running_loop(), IMAGE)
    return SandboxRunner(image=IMAGE)


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
    await asyncio.to_thread(record, path, project, _fingerprint_runner())
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
    project: Path,
    source: str,
    graph: dict[str, Any],
    output_directory: str,
    note: str = "",
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
    validate_arguments(graph)
    stored, runtime, inputs = material_maker.image_inputs(
        project, graph, source, output_directory
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    code = material_maker.graph_code(source, stored, output_directory, runtime)
    return await _scripted_export(
        project,
        "material-maker",
        source,
        code,
        output_directory,
        sources=inputs,
        note=note,
    )


async def edit_vector(
    project: Path, source: str, svg: str, export: str, note: str = ""
) -> dict[str, Any]:
    enabled(project, "inkscape")
    for relative in (source, export):
        if Path(relative).parts[0] != "art":
            raise ValueError("Vector source and exports belong under art/.")
        confined(project, relative).parent.mkdir(parents=True, exist_ok=True)
    if Path(source).suffix != ".svg" or Path(export).suffix != ".png":
        raise ValueError("Use native SVG and a PNG export.")
    validate_arguments(svg)
    inkscape.check_svg(svg)
    code = inkscape.vector_code(source, svg, export)
    return await _scripted_export(
        project, "inkscape", source, code, str(Path(export).parent), [export], note=note
    )


async def _scripted_export(
    project: Path,
    tool: str,
    source: str,
    code: str,
    directory: str,
    outputs: list[str] | None = None,
    sources: tuple[str, ...] = (),
    note: str = "",
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
        sources=sources,
        note=note,
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
