"""GIMP 3: native .xcf images edited through Python-Fu in the offline worker.

GIMP runs headless as `gimp-console` with the python-fu-eval batch interpreter.
An operation opens the .xcf (or starts from nothing), runs the agent's code
against `image`, saves the .xcf and exports PNGs through the PNG exporter with
its timestamp and metadata switched off, so a replay is byte-identical.

GIMP's stderr is always noisy (theme and plug-in locale warnings), so a failure
is reported from its "batch command experienced ..." line and the traceback,
never from the presence of stderr.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from joinery.tools.base import (
    CREATIVE_WORKER,
    ImageCheck,
    McpProbe,
    Run,
    ToolModule,
    WorkerExec,
)

#: GIMP keeps its profile in a throwaway folder, so a read-only home or a warm
#: worker's earlier run never changes what an operation does.
PROFILE = "GIMP3_DIRECTORY=/tmp/joinery-gimp"

#: Headless GIMP with the Python batch interpreter. Brushes, patterns,
#: gradients and fonts stay loaded so painting and text layers work.
BATCH = (
    "env",
    PROFILE,
    "gimp-console",
    "-i",
    "--batch-interpreter=python-fu-eval",
)

#: GIMP's own verdict on a batch command; it is the useful line in the noise.
OUTCOME = "batch command experienced"


def batch(code: str) -> list[str]:
    """Run Python-Fu `code` once in headless GIMP and quit."""
    # exec() keeps multi-line code intact through the single -b argument.
    return [*BATCH, "-b", f"exec({code!r})", "--quit"]


#: Runs a command in the worker and prints its useful line first: the marker on
#: success, otherwise GIMP's verdict joined with the Python error.
_VERDICT_FIRST = f"""
import re, subprocess, sys
done = subprocess.run(sys.argv[1:], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace')
lines = done.stdout.splitlines()
head = [line for line in lines if line.startswith('JOINERY_GIMP_OK')]
verdict = next((line.rstrip(': ') for line in lines if {OUTCOME!r} in line), '')
error = next((line for line in reversed(lines) if re.match(r'[A-Za-z_][\\w.]*(Error|Exception)\\b', line)), '')
if not head and verdict:
    head = [verdict + (': ' + error if error else '')]
print('\\n'.join(head + ([] if done.returncode == 0 else lines[-20:])))
sys.exit(done.returncode)
"""


def _verdict_first(argv: list[str]) -> tuple[str, ...]:
    """Run `argv` with GIMP's own verdict as the first output line.

    Readiness reports the first output line of a failed check; for GIMP that
    must be "batch command experienced ...", not a theme warning.
    """
    return ("python3", "-c", _VERDICT_FIRST, *argv)


#: Proves the adapter's own path works: GIMP starts headless and runs Python.
PYTHON_FU = _verdict_first(
    batch('from gi.repository import Gimp\nprint("JOINERY_GIMP_OK", Gimp.version())')
)

# Helpers every operation script gets. `image` is the document; `layer_exports`
# maps an export path to the one layer it should contain (the composite
# otherwise). find_layer searches inside layer groups too.
PRELUDE = """
import json, os
from gi.repository import Gimp, Gio, Gegl
os.chdir('/work')
NONINTERACTIVE = Gimp.RunMode.NONINTERACTIVE

def _joinery_file(path):
    return Gio.File.new_for_path(os.path.abspath(path))

def _joinery_tree(items):
    for item in items:
        yield item
        if item.is_group():
            yield from _joinery_tree(item.get_children())

def find_layer(image, name):
    for item in _joinery_tree(image.get_layers()):
        if item.get_name() == name:
            return item
    raise KeyError(f'No layer named {name!r}; inspect the document for layer names')

def find_channel(image, name):
    channel = image.get_channel_by_name(name)
    if channel is None:
        raise KeyError(f'No channel named {name!r}; inspect the document for channel names')
    return channel

def _joinery_isolate(items, target):
    found = False
    for item in items:
        if item.get_id() == target:
            item.set_visible(True)
            found = True
        elif item.is_group() and _joinery_isolate(item.get_children(), target):
            item.set_visible(True)
            found = True
        else:
            item.set_visible(False)
    return found

def _joinery_export_png(image, path, layer=None):
    copy = image.duplicate()
    try:
        if layer is not None:
            _joinery_isolate(copy.get_layers(), find_layer(copy, layer).get_id())
        assert any(item.get_visible() for item in copy.get_layers()), 'Nothing visible to export to ' + path
        copy.merge_visible_layers(Gimp.MergeType.CLIP_TO_IMAGE)
        procedure = Gimp.get_pdb().lookup_procedure('file-png-export')
        config = procedure.create_config()
        for key, value in (
            ('run-mode', NONINTERACTIVE),
            ('image', copy),
            ('file', _joinery_file(path)),
            ('options', None),
            ('time', False),
            ('include-exif', False),
            ('include-xmp', False),
            ('include-iptc', False),
            ('include-comment', False),
            ('include-thumbnail', False),
        ):
            config.set_property(key, value)
        status = procedure.run(config).index(0)
        assert status == Gimp.PDBStatusType.SUCCESS, f'PNG export of {path} failed: {status}'
    finally:
        copy.delete()
"""

# Reports what an edit can target by name: the layer tree (with groups, masks,
# modes and offsets), saved selection channels and paths.
INSPECT_XCF = """
import json, os
from gi.repository import Gimp, Gio
def _nick(value):
    return getattr(value, 'value_nick', str(value))
def _layer(item):
    row = {
        'name': item.get_name(),
        'visible': item.get_visible(),
        'opacity': round(item.get_opacity(), 3),
        'mode': _nick(item.get_mode()),
        'offsets': list(item.get_offsets())[-2:],
        'size': [item.get_width(), item.get_height()],
        'mask': item.get_mask() is not None,
    }
    if item.is_group():
        row['children'] = [_layer(child) for child in item.get_children()]
    return row
image = Gimp.file_load(Gimp.RunMode.NONINTERACTIVE, Gio.File.new_for_path(os.path.abspath(SOURCE)))
print('JOINERY_DOCUMENT:' + json.dumps({
    'size': [image.get_width(), image.get_height()],
    'base_type': _nick(image.get_base_type()),
    'precision': _nick(image.get_precision()),
    'layers': [_layer(item) for item in image.get_layers()],
    'channels': [{'name': c.get_name(), 'visible': c.get_visible()} for c in image.get_channels()],
    'paths': [p.get_name() for p in image.get_paths()],
}), flush=True)
image.delete()
"""


def failure(output: str) -> str:
    """GIMP's verdict and the Python error, without its start-up noise."""
    lines = output.splitlines()
    picked = [line for line in lines if OUTCOME in line]
    start = next(
        (i for i, line in enumerate(lines) if line.startswith("Traceback")), None
    )
    if start is not None:
        picked += lines[start:]
    return "\n".join(picked) if picked else output[-2000:]


class Gimp(ToolModule):
    id = "gimp"
    name = "GIMP"
    family = "joinery-paint"
    suffix = ".xcf"
    script_ext = ".py"
    authoring = True
    binary = "gimp-console"
    desktop = "gimp"
    ready_summary = (
        "{name} {version} ready: edits .xcf layers and channels offline; exports PNG"
    )
    broken = "GIMP failed to start"
    checks = (
        ImageCheck(CREATIVE_WORKER),
        WorkerExec(
            CREATIVE_WORKER,
            ("gimp-console", "--version"),
            expect=r"GNU Image Manipulation Program",
            version=r"version (\d+\.\d+(?:\.\d+)?)",
            on_failure="needs_image",
            failure="GIMP is missing from the creative worker; rebuild it",
        ),
        WorkerExec(
            CREATIVE_WORKER,
            PYTHON_FU,
            expect=r"JOINERY_GIMP_OK",
            version=r"JOINERY_GIMP_OK (\d+\.\d+(?:\.\d+)?)",
            tier=2,
            timeout=180,
        ),
        McpProbe(
            "joinery-paint",
            "document_capabilities",
            capability="gimp",
            image=CREATIVE_WORKER,
        ),
    )

    def check_destinations(self, source: str, exports: list[str]) -> None:
        if Path(source).parts[0] != "art":
            raise ValueError("Keep native GIMP images under art/.")
        for path in exports:
            if Path(path).suffix != ".png" or Path(path).parts[0] != "art":
                raise ValueError(
                    "GIMP exports are PNG files under art/; name each one in exports."
                )
            if path == source:
                raise ValueError("An export cannot replace its .xcf source.")

    def script(self, source: str, base: str, code: str, exports: list[str]) -> str:
        return "\n".join(
            [
                PRELUDE,
                (
                    f"image = Gimp.file_load(NONINTERACTIVE, _joinery_file({base!r}))"
                    if base
                    else "image = None"
                ),
                "layer_exports = {}",
                code,
                "assert image is not None, 'Create a GIMP image as `image` (Gimp.Image.new) or edit an existing .xcf'",
                f"_joinery_exports = {json.dumps(exports)}",
                "assert set(layer_exports) <= set(_joinery_exports), 'Every layer_exports path must also be listed in exports'",
                # Refuse a wrong layer name before anything is written.
                "for _joinery_layer in layer_exports.values():",
                "    find_layer(image, _joinery_layer)",
                f"assert Gimp.file_save(NONINTERACTIVE, image, _joinery_file({source!r}), None), {'GIMP could not save ' + source!r}",
                "for _joinery_path in _joinery_exports:",
                "    _joinery_export_png(image, _joinery_path, layer_exports.get(_joinery_path))",
                "image.delete()",
                "",
            ]
        )

    def replay_argv(self, script: str) -> list[str]:
        return [
            *BATCH,
            "-b",
            f"import runpy; runpy.run_path({script!r}, run_name='__main__')",
            "--quit",
        ]

    async def inspect(
        self, project: Path, path: Path, source: str, run: Run
    ) -> dict[str, Any] | None:
        code = f"SOURCE = {source!r}\n" + INSPECT_XCF
        got = await run(batch(code), project, readonly=True)
        for line in got.output.splitlines():
            if line.startswith("JOINERY_DOCUMENT:"):
                data: dict[str, Any] = json.loads(
                    line.removeprefix("JOINERY_DOCUMENT:")
                )
                return data
        raise ValueError("GIMP could not inspect this image: " + failure(got.output))


MODULE = Gimp()
