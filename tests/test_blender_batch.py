"""Several typed Blender operations in one call and one Blender process.

Blender is replaced by a stand-in `bpy` module (a document is a JSON list of
the operations applied to it), so the real recipe scripts and the real batch
driver run here under plain Python: a batch must leave exactly the recipes,
preimages, scripts and fingerprints that the same edits leave one by one, stop
at a failing step and put its files back, and refuse an invalid step before
Blender starts. tests/integration/blender_batch.py runs it in the worker.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from joinery import authoring, parts
from joinery.authoring import DocumentStep, StepsOutcome
from joinery.recipe import HISTORY_DIR, load_recipe, recipe_paths
from joinery.recipe import record as real_record
from joinery.runner import Result
from servers.blender_ops import batch

SOURCE = "art/sources/box.blend"
MODEL = "art/models/box.glb"
TEXTURES = "art/textures/box"

# A stand-in for Blender's Python module: the document is the list of
# operations applied to it, saved as JSON; a GLB export writes the same list.
STUB_BPY = r"""
import json, os
_chdir = os.chdir
os.chdir = lambda path: None if path == '/work' else _chdir(path)
STATE = {'log': [], 'images': {}}

class _Empty(list):
    def get(self, name, default=None):
        return default

class _Data:
    def __getattr__(self, name):
        return _Empty()

data = _Data()

class _Anything:
    def __getattr__(self, name):
        return _Anything()
    def __call__(self, *args, **kwargs):
        return _Anything()
    def __iter__(self):
        return iter(())
    def __bool__(self):
        return False

context = _Anything()
app = _Anything()
types = _Anything()
props = _Anything()

class path:
    @staticmethod
    def clean_name(name):
        return name

class _Op:
    def __init__(self, name=()):
        self.name = name
    def __getattr__(self, part):
        return _Op(self.name + (part,))
    def __call__(self, **kwargs):
        name = '.'.join(self.name)
        if name == 'wm.read_factory_settings':
            STATE.update(log=[], images={})
        elif name == 'wm.open_mainfile':
            with open(kwargs['filepath']) as handle:
                STATE.update(json.load(handle))
        elif name == 'wm.save_as_mainfile':
            with open(kwargs['filepath'], 'w') as handle:
                json.dump(STATE, handle, sort_keys=True)
        elif name == 'export_scene.gltf':
            with open(kwargs['filepath'], 'w') as handle:
                json.dump({'glb': STATE['log']}, handle)
        return {'FINISHED'}

ops = _Op()
"""
STUB_MATHUTILS = """
class Vector(tuple):
    pass
class Matrix:
    pass
class Euler(tuple):
    pass
class Quaternion(tuple):
    pass
"""


def apply(label: str) -> str:
    """Operation code; like Blender, every edit writes all the model's images."""
    return (
        f"bpy.STATE['log'].append({label!r})\n"
        f"os.makedirs({TEXTURES!r}, exist_ok=True)\n"
        "for _name, _pixels in bpy.STATE['images'].items():\n"
        f"    open({TEXTURES!r} + '/' + _name + '.png', 'wb').write(_pixels.encode())\n"
    )


def paint(name: str, label: str) -> str:
    """Operation code that also changes an image, as a map edit would."""
    return f"bpy.STATE['images'][{name!r}] = {label!r}\n" + apply(label)


def _stage(tmp_path: Path, name: str) -> Path:
    stage = tmp_path / name / "stage"
    stage.mkdir(parents=True)
    (stage.parent / "journal.json").write_text(json.dumps({"state": "staging"}))
    (stage / "joinery.toml").write_text('[tools]\nenabled=["blender"]\n')
    return stage


def _python(stubs: Path, argv: list[str], cwd: Path) -> Result:
    start = time.monotonic()
    got = subprocess.run(
        [sys.executable, *argv],
        cwd=cwd,
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(stubs), "PATH": "/usr/bin:/bin"},
        check=False,
    )
    return Result(got.returncode == 0, got.stdout, got.stderr, time.monotonic() - start)


class _Runner:
    """Runs a fingerprint's Blender command against the stand-in module."""

    name = "stub"

    def __init__(self, stubs: Path) -> None:
        self.stubs = stubs

    def available(self) -> bool:
        return True

    def run(self, argv: list[str], project: Path, timeout: int = 0) -> Result:
        script = argv[argv.index("--python-expr") + 1]
        opened = argv[2] if not argv[2].startswith("-") else ""
        code = "import bpy\n"
        if opened:
            code += f"bpy.ops.wm.open_mainfile(filepath={opened!r})\n"
        return _python(self.stubs, ["-c", code + script], project)


@pytest.fixture
def blender(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Blender replaced by the stand-in; returns the command of every run."""
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    (stubs / "bpy.py").write_text(STUB_BPY)
    (stubs / "mathutils.py").write_text(STUB_MATHUTILS)
    (stubs / "numpy.py").write_text("")
    runs: list[list[str]] = []

    async def run_offline(
        argv: list[str], project: Path, timeout: int = 600, *, readonly: bool = False
    ) -> Result:
        runs.append(argv)
        index = argv.index("--python")
        return _python(stubs, argv[index + 1 :], project)

    def record(path: Path, project: Path, _runner: Any) -> Any:
        return real_record(path, project, _Runner(stubs))

    monkeypatch.setattr(authoring, "run_offline", run_offline)
    monkeypatch.setattr(authoring, "record", record)
    monkeypatch.setattr(authoring, "write_lock", lambda _project: [])
    return runs


EDITS = [
    ("Scale the box", apply("scale")),
    ("Paint the lid", paint("lid", "lid")),
    ("Move the pivot", apply("pivot")),
    ("Paint the side", paint("side", "side")),
]


def _start(stage: Path) -> None:
    asyncio.run(
        authoring.edit_document(
            stage, "blender", SOURCE, apply("make"), [MODEL], "Make the box"
        )
    )


def _log(stage: Path) -> list[dict[str, Any]]:
    """Every recipe, current and replaced, with its script, as comparable rows."""
    rows = []
    for path in [*recipe_paths(stage), *sorted((stage / HISTORY_DIR).glob("*.toml"))]:
        recipe = load_recipe(path)
        rows.append(
            {
                "current": path.parent.name == "recipes",
                "note": recipe.note,
                "outputs": recipe.outputs,
                "sources": recipe.sources,
                "expect": recipe.expect,
                "script": (stage / recipe.script).read_text(),
            }
        )
    return sorted(rows, key=lambda row: (row["current"], row["note"]))


def _files(stage: Path) -> dict[str, bytes]:
    """The model, its exports, its textures and every preimage."""
    return {
        p.relative_to(stage).as_posix(): p.read_bytes()
        for p in sorted(stage.rglob("*"))
        if p.is_file() and p.suffix in {".blend", ".glb", ".png"}
    }


def _outputs(stage: Path) -> dict[str, bytes]:
    return {k: v for k, v in _files(stage).items() if not k.startswith("recipes/")}


def _kept(stage: Path) -> list[str]:
    """Recipe files and scripts (a preimage of a failed edit may stay, as alone)."""
    return sorted(
        p.relative_to(stage).as_posix()
        for p in (stage / "recipes").rglob("*")
        if p.is_file() and p.suffix in {".toml", ".py"}
    )


def test_a_batch_records_exactly_what_the_same_edits_record_one_by_one(
    tmp_path: Path, blender: list[list[str]]
) -> None:
    single, batched = _stage(tmp_path, "single"), _stage(tmp_path, "batched")
    _start(single)
    _start(batched)
    blender.clear()
    for note, code in EDITS:
        asyncio.run(
            authoring.edit_document(single, "blender", SOURCE, code, [MODEL], note)
        )
    assert len(blender) == len(EDITS)
    blender.clear()
    outcome = asyncio.run(
        authoring.edit_document_steps(
            batched, SOURCE, [DocumentStep(code, note) for note, code in EDITS], [MODEL]
        )
    )
    assert len(blender) == 1, "one Blender process for the whole batch"
    assert outcome.failed is None and len(outcome.results) == len(EDITS)
    assert _log(batched) == _log(single)
    assert _files(batched) == _files(single)
    assert json.loads((batched / SOURCE).read_text())["log"] == [
        "make",
        "scale",
        "lid",
        "pivot",
        "side",
    ]
    # The earlier steps went where the next edit puts a replaced recipe.
    assert [r["recipe"].split("/")[1] for r in outcome.results] == [
        "history",
        "history",
        "history",
        outcome.results[-1]["recipe"].split("/")[1],
    ]
    assert outcome.results[-1]["recipe"].count("/") == 1
    assert not list((batched / "recipes").glob(".batch-*")), "scratch left behind"


def test_a_failing_step_stops_the_batch_and_puts_its_files_back(
    tmp_path: Path, blender: list[list[str]]
) -> None:
    stage = _stage(tmp_path, "failing")
    _start(stage)
    steps = [
        DocumentStep(paint("lid", "lid"), "Paint the lid"),
        DocumentStep(
            paint("side", "side") + "raise RuntimeError('no object named Lid')",
            "Break",
        ),
        DocumentStep(apply("never"), "Never runs"),
    ]
    outcome = asyncio.run(authoring.edit_document_steps(stage, SOURCE, steps, [MODEL]))
    assert outcome.failed == 1
    assert "RuntimeError: no object named Lid" in outcome.error
    assert len(outcome.results) == 1
    assert json.loads((stage / SOURCE).read_text())["log"] == ["make", "lid"]
    assert json.loads((stage / MODEL).read_text())["glb"] == ["make", "lid"]
    assert sorted(p.name for p in (stage / TEXTURES).glob("*.png")) == ["lid.png"]
    assert (stage / TEXTURES / "lid.png").read_bytes() == b"lid"
    current = [load_recipe(p) for p in recipe_paths(stage)]
    assert [r.note for r in current] == ["Paint the lid"]
    scripts = {p.name for p in (stage / "recipes").glob("*.py")}
    assert len(scripts) == 2, "only the finished steps keep a script"
    assert not list((stage / "recipes").glob(".batch-*"))


def test_a_failing_first_step_changes_nothing(
    tmp_path: Path, blender: list[list[str]]
) -> None:
    stage = _stage(tmp_path, "first")
    _start(stage)
    asyncio.run(
        authoring.edit_document(
            stage, "blender", SOURCE, paint("lid", "lid"), [MODEL], "Paint the lid"
        )
    )
    before, recipes = _outputs(stage), _kept(stage)
    outcome = asyncio.run(
        authoring.edit_document_steps(
            stage,
            SOURCE,
            [DocumentStep("raise RuntimeError('broken')", "Break")],
            [MODEL],
        )
    )
    assert outcome.failed == 0 and outcome.results == []
    assert _outputs(stage) == before
    assert _kept(stage) == recipes


def test_batch_codes_pass_the_same_policy_check(
    tmp_path: Path, blender: list[list[str]]
) -> None:
    from joinery.mcp_runtime import PolicyStop

    stage = _stage(tmp_path, "policy")
    _start(stage)
    with pytest.raises(PolicyStop):
        asyncio.run(
            authoring.edit_document_steps(
                stage,
                SOURCE,
                [
                    DocumentStep(apply("fine"), "Fine"),
                    DocumentStep("import replicate\n", "Generative"),
                ],
                [MODEL],
            )
        )
    assert len(blender) == 1, "only the starting model ran"


# --- the blender_batch tool --------------------------------------------------


def _project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, AsyncMock]:
    stage = _stage(tmp_path, "tool")
    (stage / SOURCE).parent.mkdir(parents=True)
    (stage / SOURCE).write_text("{}")
    (stage / "recipes").mkdir()
    (stage / "recipes/box.toml").write_text(
        'version = 1\ntool = "blender"\nscript = "recipes/box.py"\n'
        f'outputs = ["{SOURCE}", "{MODEL}"]\n'
    )

    async def steps(
        project: Path,
        source: str,
        given: list[DocumentStep],
        exports: list[str],
        **_kw: Any,
    ) -> StepsOutcome:
        return StepsOutcome(
            results=[
                {
                    "source": source,
                    "outputs": [source, *exports],
                    "recipe": f"recipes/history/{n}.toml",
                    "status": "staged",
                }
                for n, _step in enumerate(given)
            ],
            seconds=4.2,
        )

    run = AsyncMock(side_effect=steps)
    monkeypatch.setattr(batch, "edit_document_steps", run)
    return stage, run


def _call(stage: Path, arguments: dict[str, Any]) -> Any:
    from mcp import Client

    from servers.authoring import make_server

    async def go() -> Any:
        async with Client(make_server(stage, "joinery-blender-ops")) as client:
            return await client.call_tool("blender_batch", arguments)

    return asyncio.run(go())


STEPS = [
    {"op": "apply_scale", "arguments": {"objects": ["Box"]}},
    {"op": "set_pivot", "arguments": {"pivot": "bottom-centre"}},
    {"op": "reduce_triangles", "arguments": {"max_triangles": 400}},
    {
        "op": "set_material_value",
        "arguments": {"material": "Wood", "socket": "Roughness", "value": 0.6},
    },
    {
        "op": "edit_bone",
        "arguments": {
            "armature": "Rig",
            "bone": "Lid",
            "op": "rename",
            "params": {"name": "Hinge"},
        },
    },
]


def test_the_tool_runs_typed_steps_in_order_and_answers_briefly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage, run = _project(tmp_path, monkeypatch)
    result = _call(stage, {"source": "res://" + SOURCE, "steps": STEPS})
    assert not result.is_error, result.content[0].text
    found = json.loads(result.content[0].text)
    assert found["steps"] == [
        "1. apply_scale: Apply scale on Box",
        "2. set_pivot: Move the model's pivot to its bottom-centre",
        "3. reduce_triangles: Decimate to at most 400 triangles",
        "4. set_material_value: Set Roughness of Wood to 0.6",
        "5. edit_bone: Bone Lid of Rig: rename {'name': 'Hinge'}",
    ]
    assert found["changed"] == [MODEL, SOURCE]
    assert found["seconds"] == 4.2
    _project_path, source, given, exports = run.await_args.args
    assert source == SOURCE and exports == [MODEL]
    assert given[0].note == "Apply scale on Box"
    assert "_joinery_apply_scale(['Box'])" in given[0].code
    assert "_joinery_edit_bone('Rig', 'Lid', 'rename'" in given[4].code
    assert len(result.content[0].text) < 900, "one short result"


@pytest.mark.parametrize(
    ("steps", "reason"),
    [
        ([], "1 to 16 operations"),
        (
            [STEPS[0], {"op": "split_material", "arguments": {}}],
            "Step 2 (split_material)",
        ),
        ([{"op": "set_pivot", "arguments": {"pivot": "top"}}], "bottom-centre"),
        ([STEPS[0], {"op": "apply_scale", "arguments": {"objects": "Box"}}], "Step 2"),
        (
            [{"op": "reduce_triangles", "arguments": {"max_triangles": "lots"}}],
            "integer",
        ),
        ([{"op": "set_pivot", "arguments": {"pivotal": 1}}], "pivotal"),
        ([{"op": "reduce_triangles", "arguments": {}}], "max_triangles"),
        (
            [{"op": "set_pivot", "arguments": {"source": "art/sources/other.blend"}}],
            "not a Blender model",
        ),
        (
            [{"op": "set_pivot", "arguments": {"exports": ["art/models/b.glb"]}}],
            "exports once",
        ),
        ([{"op": "set_pivot", "extra": 1}], '{"op": name'),
    ],
)
def test_an_invalid_step_refuses_the_whole_batch_before_blender(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, steps: list[Any], reason: str
) -> None:
    stage, run = _project(tmp_path, monkeypatch)
    result = _call(stage, {"source": SOURCE, "steps": steps})
    assert result.is_error
    assert reason in result.content[0].text, result.content[0].text
    run.assert_not_awaited()


def test_a_step_outside_the_part_selection_refuses_the_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage, run = _project(tmp_path, monkeypatch)
    parts.write_targets(
        stage,
        [
            {
                "part_kind": "material",
                "owner_source": SOURCE,
                "label": "Wood",
                "material": "Wood",
            }
        ],
    )
    other = dict(STEPS[3], arguments={**STEPS[3]["arguments"], "material": "Metal"})
    result = _call(stage, {"source": SOURCE, "steps": [STEPS[3], other]})
    assert result.is_error
    text = result.content[0].text
    assert "Step 2 (set_material_value)" in text and "selection" in text, text
    run.assert_not_awaited()


def test_a_failed_step_names_the_step_and_what_stayed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage, run = _project(tmp_path, monkeypatch)

    async def stops(project: Path, source: str, given: Any, exports: Any, **_kw: Any):
        return StepsOutcome(
            results=[{"outputs": [source], "recipe": "recipes/a.toml"}],
            failed=1,
            error="RuntimeError: There is no object named 'Box' in this model.",
        )

    run.side_effect = stops
    result = _call(stage, {"source": SOURCE, "steps": STEPS[:3]})
    assert result.is_error
    text = result.content[0].text
    assert "Step 2 of 3 (set_pivot) failed: RuntimeError: There is" in text, text
    assert "Step 1 is applied and staged (1. apply_scale" in text
    assert "steps 2-3 did not change anything" in text
