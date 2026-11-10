"""Blender: native .blend sources edited through bpy in the offline worker."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from joinery.tools.base import (
    BLENDER_WORKER,
    CREATIVE_WORKER,
    SANDBOX_ENTRYPOINT,
    Feature,
    ImageCheck,
    InstallCheck,
    McpProbe,
    Run,
    ToolModule,
    WorkerExec,
)

# Reports what an edit needs to target by name: objects with their parents,
# modifiers, vertex groups and shape keys, armatures with their bones, and
# actions. Large rigs (Rigify, MPFB2) list deform bones only, with a total.
INSPECT_BLEND = """
import bpy, json
MAX_BONES = 256
def bones(armature):
    every = list(armature.bones)
    shown = every if len(every) <= MAX_BONES else [b for b in every if b.use_deform]
    return {
        'bone_count': len(every),
        'deform_bone_count': sum(b.use_deform for b in every),
        'bones': [
            {'name': b.name, 'parent': b.parent.name if b.parent else None, 'deform': b.use_deform}
            for b in shown
        ],
    }
def describe(o):
    row = {
        'name': o.name,
        'type': o.type,
        'parent': o.parent.name if o.parent else None,
        'position': list(o.location),
        'scale': list(o.scale),
        'rotation': list(o.rotation_euler),
        'vertices': len(o.data.vertices) if o.type == 'MESH' else 0,
        'modifiers': [{'name': m.name, 'type': m.type} for m in o.modifiers],
    }
    if o.type == 'MESH':
        row['vertex_groups'] = len(o.vertex_groups)
        keys = o.data.shape_keys
        row['shape_keys'] = [k.name for k in keys.key_blocks] if keys else []
    if o.type == 'ARMATURE':
        row.update(bones(o.data))
    if o.animation_data and o.animation_data.action:
        row['action'] = o.animation_data.action.name
    return row
print('JOINERY_DOCUMENT:' + json.dumps({
    'objects': [describe(o) for o in bpy.data.objects],
    'materials': [m.name for m in bpy.data.materials],
    'actions': [
        {'name': a.name, 'frames': [a.frame_range[0], a.frame_range[1]]}
        for a in bpy.data.actions
    ],
}))
"""

# Writes each image that has pixels as its own PNG. The .blend keeps its packed
# copy, so a recipe still replays from the .blend alone.
EXPORT_TEXTURES = """
def _joinery_export_textures(directory):
    os.makedirs(directory, exist_ok=True)
    used = set()
    for image in sorted(bpy.data.images, key=lambda i: i.name):
        if image.type != 'IMAGE' or image.size[0] == 0 or image.size[1] == 0:
            continue
        name = bpy.path.clean_name(image.name) or 'texture'
        while name in used:
            name += '_'
        used.add(name)
        width, height = image.size
        pixels = [0.0] * (width * height * 4)
        image.pixels.foreach_get(pixels)
        copy = bpy.data.images.new('joinery_texture_export', width, height, alpha=True)
        copy.colorspace_settings.name = image.colorspace_settings.name
        copy.pixels.foreach_set(pixels)
        copy.filepath_raw = os.path.join(directory, name + '.png')
        copy.file_format = 'PNG'
        copy.save()
        bpy.data.images.remove(copy)
"""


# Blender prints an error raised by a --python script and still exits 0, so a
# failed operation would look like a successful one that changed nothing. An
# error that reaches the top of the recipe exits 1 instead. Errors reported
# from elsewhere (a driver, a handler) only print, as before.
FAIL_ON_ERROR = """
import sys, traceback
def _joinery_fail(kind, value, tb, _top=globals()):
    traceback.print_exception(kind, value, tb)
    if tb is not None and tb.tb_frame.f_globals is _top:
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(1)
sys.excepthook = _joinery_fail
"""

# Packs every image whose pixels changed in memory: a bake target made with
# images.new (source GENERATED), or a packed image that was painted or baked
# again. pack_all() packs only images read from files, and never repacks one
# that is already packed, so without this the pixels are lost on save and the
# texture PNG is never written. Packing turns a generated image into a packed
# IMAGE, which the texture export then writes. Images that never changed
# regenerate identically and stay as they are.
PACK_CHANGED_IMAGES = """
for _joinery_image in bpy.data.images:
    if _joinery_image.source in {'GENERATED', 'FILE'} and _joinery_image.has_data and _joinery_image.is_dirty:
        _joinery_image.pack()
"""

# Runs after the source is saved, so it shapes only the export. Godot cannot
# run Blender modifiers, so the GLB is exported with them applied
# (export_apply). The glTF exporter drops every shape key of a mesh whose
# modifiers it applies, so such a mesh gets its modifiers applied here first,
# once per shape key, keeping every key. An MPFB2 human is the common case: a
# Mask modifier hides its helper geometry and it carries shape keys; without
# this, the helpers would reach the game as visible, unweighted geometry.
# Armature modifiers stay for the exporter's skinning.
BAKE_FOR_EXPORT = """
def _joinery_evaluated_coordinates(obj):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    depsgraph.update()
    mesh = bpy.data.meshes.new_from_object(obj.evaluated_get(depsgraph), depsgraph=depsgraph)
    coordinates = [0.0] * (len(mesh.vertices) * 3)
    mesh.vertices.foreach_get('co', coordinates)
    bpy.data.meshes.remove(mesh)
    return coordinates

def _joinery_bake_for_export():
    for obj in [o for o in bpy.data.objects if o.type == 'MESH']:
        keys = obj.data.shape_keys
        applied = [m for m in obj.modifiers if m.type != 'ARMATURE' and m.show_viewport]
        if keys is None or len(keys.key_blocks) < 2 or not applied:
            continue
        armatures = [m for m in obj.modifiers if m.type == 'ARMATURE' and m.show_viewport]
        for modifier in armatures:
            modifier.show_viewport = False
        blocks = list(keys.key_blocks)
        settings = [(k.name, k.value, k.mute, k.slider_min, k.slider_max) for k in blocks]
        obj.show_only_shape_key = True
        shapes = []
        for index in range(len(blocks)):
            obj.active_shape_key_index = index
            shapes.append(_joinery_evaluated_coordinates(obj))
        if len({len(shape) for shape in shapes}) != 1:
            raise RuntimeError(
                f'{obj.name}: its modifiers change the vertex count per shape key, so the '
                'GLB cannot keep both. Apply the modifiers or remove the shape keys first.'
            )
        obj.active_shape_key_index = 0
        depsgraph = bpy.context.evaluated_depsgraph_get()
        depsgraph.update()
        baked = bpy.data.meshes.new_from_object(
            obj.evaluated_get(depsgraph), preserve_all_data_layers=True, depsgraph=depsgraph
        )
        obj.show_only_shape_key = False
        obj.data = baked
        for modifier in applied:
            obj.modifiers.remove(modifier)
        for modifier in armatures:
            modifier.show_viewport = True
        for (name, value, mute, low, high), shape in zip(settings, shapes):
            block = obj.shape_key_add(name=name, from_mix=False)
            block.data.foreach_set('co', shape)
            block.slider_min, block.slider_max = low, high
            block.value, block.mute = value, mute
        print('JOINERY_EXPORT_BAKED', obj.name, len(settings) - 1, 'shape keys')
"""

#: Extensions a recipe turns on when its code names them: (word in the code,
#: module, label). Recipes start from factory settings, which enable neither.
EXTENSIONS = (
    ("rigify", "rigify", "Rigify"),
    ("mpfb", "bl_ext.system.mpfb", "MPFB2"),
)

#: How an agent turns MPFB2 on. Shown wherever Blender code is written.
MPFB_HINT = (
    "Blender runs in the background from factory settings, so enable extensions "
    "first: MPFB2 is addon_utils.enable('bl_ext.system.mpfb', default_set=True) "
    "and Rigify is addon_utils.enable('rigify', default_set=True)."
)

# Builds one MPFB2 human. Passes when MPFB2 is absent (the tier-2 check reports
# that as an old image), so this only fails when MPFB2 loads but cannot work.
MPFB_HUMAN = """
import addon_utils
if not addon_utils.enable('bl_ext.system.mpfb', default_set=True):
    print('JOINERY_MPFB_ABSENT')
else:
    from bl_ext.system.mpfb.services.humanservice import HumanService
    human = HumanService.create_human()
    print('JOINERY_MPFB_HUMAN', len(human.data.vertices))
"""


def texture_dir(source: str) -> str:
    """Where a Blender source's textures are written as separate image files."""
    return f"art/textures/{Path(source).stem}"


def _blender_expr(expr: str) -> tuple[str, ...]:
    return (
        "blender",
        "-b",
        "--factory-startup",
        "--python-exit-code",
        "1",
        "--python-expr",
        expr,
    )


def _enable(addon: str, marker: str) -> tuple[str, ...]:
    return _blender_expr(
        "import addon_utils; "
        f"assert addon_utils.enable({addon!r}, default_set=True); "
        f"print({marker!r})"
    )


def extension_lines(code: str) -> list[str]:
    """Enable each extension the operation code names, or stop with a clear reason."""
    lines: list[str] = []
    for word, module, label in EXTENSIONS:
        if re.search(word, code, re.IGNORECASE):
            reason = (
                f"{label} is not available in this Blender worker. "
                "Run Joinery setup to rebuild the worker image."
            )
            lines += [
                "import addon_utils",
                f"if not addon_utils.enable({module!r}, default_set=True):",
                f"    raise RuntimeError({reason!r})",
            ]
    return lines


class Blender(ToolModule):
    id = "blender"
    name = "Blender"
    family = "joinery-blender-ops"
    suffix = ".blend"
    script_ext = ".py"
    authoring = True
    binary = "blender"
    desktop = "blender"
    parts_provider = "blender"
    ready_summary = "{name} {version} ready (offline worker)"
    needs_image = "Blender worker image not built. Run Joinery setup."
    broken = "Blender failed to start"
    mcp_notes = (("execute_blender_code", "Joinery: " + MPFB_HINT),)
    features = (
        Feature(
            "rigify",
            "Rigify",
            needs_image="Rigify unavailable: the worker image is out of date. Run Joinery setup.",
            broken="Rigify failed to load",
        ),
        Feature(
            "mpfb",
            "MPFB2 humans",
            needs_image=(
                "MPFB2 humans unavailable: worker image predates the fix. Run Joinery "
                "setup to rebuild (about 1 h, about 6 GB)."
            ),
            broken="MPFB2 humans failed to load",
        ),
        Feature(
            "mcp",
            "official Blender MCP connected",
            needs_image=(
                "Blender MCP worker not built. Run Joinery setup. Editing still works "
                "through Joinery's Blender adapter."
            ),
            needs_install=(
                "Blender MCP not installed. Run Joinery setup. Editing still works "
                "through Joinery's Blender adapter."
            ),
            broken="Blender MCP error",
        ),
    )
    checks = (
        ImageCheck(CREATIVE_WORKER),
        WorkerExec(
            CREATIVE_WORKER,
            ("blender", "--version"),
            expect=r"Blender \d",
            version=r"Blender (\d+\.\d+(?:\.\d+)?)",
            on_failure="needs_image",
            failure="Blender is missing from the creative worker",
        ),
        WorkerExec(
            CREATIVE_WORKER,
            _enable("rigify", "JOINERY_RIGIFY_OK"),
            expect="JOINERY_RIGIFY_OK",
            tier=2,
            timeout=120,
            feature="rigify",
            on_failure="needs_image",
        ),
        WorkerExec(
            CREATIVE_WORKER,
            _enable("bl_ext.system.mpfb", "JOINERY_MPFB_OK"),
            expect="JOINERY_MPFB_OK",
            tier=2,
            timeout=120,
            feature="mpfb",
            on_failure="needs_image",
        ),
        WorkerExec(
            CREATIVE_WORKER,
            _blender_expr(MPFB_HUMAN),
            expect=r"JOINERY_MPFB_(ABSENT|HUMAN [1-9]\d*)",
            tier="deep",
            timeout=300,
            feature="mpfb",
            failure="could not build a human",
        ),
        ImageCheck(BLENDER_WORKER, feature="mcp"),
        WorkerExec(
            BLENDER_WORKER,
            ("blender", "--version"),
            expect=r"Blender \d",
            feature="mcp",
            entrypoint=SANDBOX_ENTRYPOINT,
            on_failure="needs_image",
        ),
        InstallCheck("blender", feature="mcp"),
        McpProbe(
            "blender",
            "get_blendfile_summary_datablocks",
            feature="mcp",
            image=BLENDER_WORKER,
        ),
    )

    def check_destinations(self, source: str, exports: list[str]) -> None:
        if not source.startswith("art/sources/") or any(
            Path(path).is_relative_to(Path(source).parent) for path in exports
        ):
            raise ValueError(
                "Keep native Blender sources under art/sources/ and exports outside that source directory."
            )

    def script(self, source: str, base: str, code: str, exports: list[str]) -> str:
        lines = [
            "import bpy, os, math",
            FAIL_ON_ERROR,
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
            *extension_lines(code),
            code,
        ]
        lines += [
            "assert all(all(math.isfinite(x) for row in o.matrix_world for x in row) for o in bpy.data.objects), 'Nonfinite object transform'",
            PACK_CHANGED_IMAGES,
            "bpy.ops.file.pack_all()",
            f"bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath({source!r}))",
            EXPORT_TEXTURES,
            f"_joinery_export_textures(os.path.abspath({texture_dir(source)!r}))",
        ]
        if exports:
            # A generated Rigify rig carries hundreds of control and mechanism
            # bones; the game needs only its deform bones. Hand-made rigs keep
            # every bone, since non-deform bones there are often sockets.
            lines += [
                "_joinery_deform_only = any('rig_id' in a for a in bpy.data.armatures)",
                BAKE_FOR_EXPORT,
                "_joinery_bake_for_export()",
            ]
        for export in exports:
            if Path(export).suffix != ".glb":
                raise ValueError(
                    "The automatic Blender export uses GLB. Render images explicitly in the operation script."
                )
            lines.append(
                f"bpy.ops.export_scene.gltf(filepath=os.path.abspath({export!r}), export_format='GLB', export_yup=True, export_apply=True, export_def_bones=_joinery_deform_only)"
            )
        return "\n".join(lines) + "\n"

    def replay_argv(self, script: str) -> list[str]:
        # --factory-startup so a user's preferences cannot change the result.
        return ["blender", "--background", "--factory-startup", "--python", script]

    async def inspect(
        self, project: Path, path: Path, source: str, run: Run
    ) -> dict[str, Any] | None:
        got = await run(
            ["blender", "--background", source, "--python-expr", INSPECT_BLEND],
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


MODULE = Blender()
