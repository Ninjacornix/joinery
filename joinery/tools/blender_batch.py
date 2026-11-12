"""Several Blender edits of one document in one Blender process.

Each step is the very recipe script the same edit would get on its own
(`Blender.script`): it opens the document the step before saved, applies one
operation, saves, and exports. The driver runs those scripts one after the
other inside one Blender, so the worker and Blender start once. Between steps
it stores the saved document as the next step's preimage (content-named, as
`store_preimage` does), writes that path into the next script, keeps the
step's previous exports and textures to put back if the step fails, and
measures each step's outputs the way `joinery.recipe.fingerprints` would.

A step that raises stops the run: the driver reports which step and why and
exits 1; nothing after it runs. The host (`joinery.authoring`) puts the failed
step's files back and writes one recipe per finished step.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from joinery.recipe import _BLEND_SIGNATURE_FN

#: Prefix of the driver's progress lines.
PREFIX = "JOINERY_BATCH:"

DRIVER = (
    "import bpy, hashlib, json, os, shutil, sys, traceback\n"
    + _BLEND_SIGNATURE_FN
    + r"""
os.chdir('/work')
with open(sys.argv[sys.argv.index('--') + 1]) as _handle:
    _plan = json.load(_handle)


def _report(kind, **values):
    sys.stderr.flush()
    print('JOINERY_BATCH:' + json.dumps(dict(kind=kind, **values)), flush=True)


def _sha(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def _pngs(folder):
    if not os.path.isdir(folder):
        return []
    return sorted(
        folder + '/' + name for name in os.listdir(folder) if name.endswith('.png')
    )


def _preimage(source):
    digest = _sha(source)
    folder = _plan['preimages']
    os.makedirs(folder, exist_ok=True)
    open(os.path.join(folder, '.gdignore'), 'a').close()
    target = folder + '/' + digest + os.path.splitext(source)[1]
    if not os.path.isfile(target) or _sha(target) != digest:
        shutil.copyfile(source, target)
    return target


_source, _textures, _exports = _plan['source'], _plan['textures'], _plan['exports']
_steps = _plan['steps']
for _index, _step in enumerate(_steps):
    _base = _plan['base'] if _index == 0 else _preimage(_source)
    _report('start', step=_index, base=_base)
    # What this step replaces, put back by the host if it fails.
    _backup = os.path.join(_plan['scratch'], str(_index))
    os.makedirs(os.path.join(_backup, 'textures'), exist_ok=True)
    for _number, _export in enumerate(_exports):
        if os.path.isfile(_export):
            shutil.copyfile(_export, os.path.join(_backup, 'export-%d' % _number))
    # Textures are written again by every edit; an image the step drops must
    # not leave its old file behind (as edit_document clears them).
    for _png in _pngs(_textures):
        shutil.move(_png, os.path.join(_backup, 'textures', os.path.basename(_png)))
    _text = _step['text']
    if _index:
        _text = _text.replace(_step['placeholder'], _base)
    with open(_step['script'], 'w') as _handle:
        _handle.write(_text)
    _path = os.path.abspath(_step['script'])
    try:
        exec(compile(_text, _path, 'exec'), {'__name__': '__main__', '__file__': _path})
    except BaseException as _error:
        traceback.print_exc()
        _why = ''.join(traceback.format_exception_only(type(_error), _error)).strip()
        _report('failed', step=_index, error=_why[-1500:])
        sys.stdout.flush()
        os._exit(1)
    _written = _pngs(_textures)
    _expect = {}
    if _index < len(_steps) - 1:
        # The next step overwrites these files, so measure them now.
        for _output in [*_exports, *_written]:
            _expect[_output] = 'sha256:' + _sha(_output)
        bpy.ops.wm.open_mainfile(filepath=os.path.abspath(_source), load_ui=False)
        _expect[_source] = 'blend:' + joinery_signature()
    _report('done', step=_index, textures=_written, expect=_expect)
"""
)


@dataclass
class Progress:
    """What the driver reported: each step's start, its end or its failure."""

    #: step -> the preimage it started from ("" for a new document).
    bases: dict[int, str] = field(default_factory=dict)
    #: step -> {"textures": [...], "expect": {...}} for each finished step.
    done: dict[int, dict[str, Any]] = field(default_factory=dict)
    failed: int | None = None
    error: str = ""
    #: step -> the output lines printed while it ran.
    output: dict[int, str] = field(default_factory=dict)


def read_progress(stdout: str) -> Progress:
    """The driver's progress lines, and each step's own output."""
    found = Progress()
    current: int | None = None
    lines: dict[int, list[str]] = {}
    for line in stdout.splitlines():
        if not line.startswith(PREFIX):
            if current is not None:
                lines[current].append(line)
            continue
        row = json.loads(line.removeprefix(PREFIX))
        step = int(row["step"])
        if row["kind"] == "start":
            found.bases[step] = str(row.get("base") or "")
            current = step
            lines[step] = []
        elif row["kind"] == "done":
            found.done[step] = {
                "textures": [str(t) for t in row.get("textures", [])],
                "expect": {str(k): str(v) for k, v in row.get("expect", {}).items()},
            }
        elif row["kind"] == "failed":
            found.failed, found.error = step, str(row.get("error", ""))
    found.output = {step: "\n".join(rows) for step, rows in lines.items()}
    return found


def placeholder(batch: str, step: int) -> str:
    """Stands for a step's preimage in its script until the step before has run."""
    return f"recipes/inputs/joinery-batch-{batch}-{step}.blend"
