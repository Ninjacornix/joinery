"""The warm Blender's request loop and its fall-backs, with a stand-in ``bpy``.

The loop itself (``joinery.warm_blender.SERVER``) runs in a plain Python here,
with a tiny fake ``bpy`` module; the real Blender is exercised by
``tests/integration/warm_blender.py``.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest

from joinery import warm_blender
from joinery.warm_blender import WarmBlender, request_for

FAKE_BPY = """
import types as _t
calls = []
class _Handlers:
    def __init__(self):
        self.load_post = []
        self.depsgraph_update_post = []
app = _t.SimpleNamespace(handlers=_Handlers(), version_string="4.0 fake")
class _Types:
    Object = object
types = _Types()
def _factory(use_empty=False):
    calls.append(("factory", use_empty))
def _open(filepath):
    calls.append(("open", filepath))
    print("Read blend: " + filepath)
ops = _t.SimpleNamespace(
    wm=_t.SimpleNamespace(read_factory_settings=_factory, open_mainfile=_open)
)
"""


@pytest.fixture
def warm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> WarmBlender:
    fake = tmp_path / "fake"
    fake.mkdir()
    (fake / "bpy.py").write_text(FAKE_BPY)
    work = tmp_path / "work"
    (work / "recipes").mkdir(parents=True)
    monkeypatch.setenv("PYTHONPATH", str(fake))
    monkeypatch.setenv("JOINERY_WARM_WORKDIR", str(work))
    return WarmBlender("test", [sys.executable, "-c", warm_blender.SERVER])


def run(step: Any) -> Any:
    return asyncio.run(step())


def test_only_the_typed_operation_and_fingerprint_commands_run_warm() -> None:
    assert request_for(
        ["blender", "--background", "--factory-startup", "--python", "recipes/a.py"]
    ) == {"script": "recipes/a.py"}
    assert request_for(
        ["blender", "--background", "art/x.blend", "--python-expr", "print(1)"]
    ) == {"open": "art/x.blend", "expr": "print(1)"}
    assert request_for(
        ["blender", "--background", "--factory-startup", "--python-expr", "x"]
    ) == {"expr": "x"}
    for cold in (
        # A batch driver takes its own arguments; renders, other programs.
        [
            "blender",
            "--background",
            "--factory-startup",
            "--python",
            "d.py",
            "--",
            "p",
        ],
        ["blender", "--background", "/etc/x.blend", "--python-expr", "x"],
        ["blender", "--background", "../x.blend", "--python-expr", "x"],
        ["blender", "--background", "--factory-startup", "--python", "/tmp/a.py"],
        ["ffmpeg", "-i", "a.png"],
    ):
        assert request_for(cold) is None


def test_scripts_run_like_a_fresh_blender(warm: WarmBlender, tmp_path: Path) -> None:
    work = tmp_path / "work"
    (work / "recipes/ok.py").write_text(
        "import bpy, os, sys\n"
        "print('name', __name__, __file__)\n"
        "print('to stderr', file=sys.stderr)\n"
        "os.write(1, b'C-level line\\n')\n"
        "LEAK = 1\n"
    )
    (work / "recipes/leak.py").write_text("print('LEAK' in globals())\n")
    (work / "recipes/hooked.py").write_text(
        "import sys, os\n"
        "def _fail(*a):\n    os._exit(1)\n"
        "sys.excepthook = _fail\n"
        "raise ValueError('missing object Nope')\n"
    )
    (work / "recipes/plain.py").write_text("raise ValueError('printed only')\n")
    (work / "recipes/exits.py").write_text("import sys\nsys.exit(3)\n")

    async def go() -> list[Any]:
        try:
            got = []
            for script in ("ok", "leak", "plain", "hooked", "exits"):
                got.append(await warm.run({"script": f"recipes/{script}.py"}, 30))
            got.append(
                await warm.run({"open": "a.blend", "expr": "print('SIG:x')"}, 30)
            )
            return [got, warm.starts]
        finally:
            await warm.close()

    (ok, leak, plain, hooked, exits, read), starts = run(go)
    assert ok.ok and "name __main__ recipes/ok.py" in ok.stdout
    assert "C-level line" in ok.stdout and "to stderr" in ok.stderr
    # Fresh globals each time.
    assert leak.ok and leak.stdout.strip() == "False"
    # Blender exits 0 on an error it only prints; a recipe's hook makes it 1.
    assert plain.ok and "printed only" in plain.stderr
    assert not hooked.ok and "missing object Nope" in hooked.stderr
    assert not exits.ok
    assert read.ok and "Read blend: a.blend" in read.stdout and "SIG:x" in read.stdout
    # A failed request is followed by a fresh process (hooked, exits).
    assert starts == 3


def test_a_request_that_changes_blender_gets_a_fresh_process(
    warm: WarmBlender, tmp_path: Path
) -> None:
    work = tmp_path / "work"
    (work / "recipes/handler.py").write_text(
        "import bpy\nbpy.app.handlers.load_post.append(print)\n"
    )
    (work / "recipes/ok.py").write_text("print('fine')\n")

    async def go() -> int:
        try:
            first = await warm.run({"script": "recipes/handler.py"}, 30)
            assert first is not None and first.ok
            second = await warm.run({"script": "recipes/ok.py"}, 30)
            assert second is not None and second.ok
            return warm.starts
        finally:
            await warm.close()

    assert run(go) == 2


def test_restarts_after_max_ops_and_when_the_process_dies(
    warm: WarmBlender, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(warm_blender, "MAX_OPS", 2)
    work = tmp_path / "work"
    (work / "recipes/ok.py").write_text("print('fine')\n")
    (work / "recipes/dies.py").write_text("import os\nos._exit(9)\n")

    async def go() -> tuple[int, Any]:
        try:
            for _ in range(3):
                got = await warm.run({"script": "recipes/ok.py"}, 30)
                assert got is not None and got.ok
            assert warm.starts == 2
            # The process died mid-request: no answer, so the caller runs it cold.
            died = await warm.run({"script": "recipes/dies.py"}, 30)
            after = await warm.run({"script": "recipes/ok.py"}, 30)
            assert after is not None and after.ok
            return warm.starts, died
        finally:
            await warm.close()

    starts, died = run(go)
    assert died is None and starts == 3


def test_a_request_that_hangs_times_out_like_a_cold_run(
    warm: WarmBlender, tmp_path: Path
) -> None:
    (tmp_path / "work/recipes/hangs.py").write_text("import time\ntime.sleep(60)\n")

    async def go() -> None:
        try:
            with pytest.raises(TimeoutError):
                await warm.run({"script": "recipes/hangs.py"}, 2)
            assert not warm.alive
        finally:
            await warm.close()

    run(go)


def test_a_process_that_does_not_start_falls_back_to_cold(tmp_path: Path) -> None:
    broken = WarmBlender("test", [sys.executable, "-c", "print('no protocol')"])

    async def go() -> Any:
        return await broken.run({"script": "recipes/x.py"}, 10)

    assert run(go) is None


def test_the_worker_falls_back_to_a_cold_exec_when_warm_cannot_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from joinery import warm_workers
    from joinery.runner import Result

    monkeypatch.setenv("JOINERY_WORKER_SCOPE", "a" * 32)
    commands: list[list[str]] = []

    async def command(argv: list[str], _timeout: int) -> Result:
        commands.append(argv)
        return Result(True, "cold", "", 0.1)

    async def no_answer(self: WarmBlender, _request: Any, _timeout: float) -> None:
        return None

    monkeypatch.setattr(warm_workers, "_command", command)
    monkeypatch.setattr(WarmBlender, "run", no_answer)
    argv = ["blender", "--background", "--factory-startup", "--python", "r/a.py"]

    async def go() -> Result:
        return await warm_workers.execute(argv, tmp_path, 30, False, "image")

    try:
        got = run(go)
    finally:
        warm_workers._containers.clear()
        warm_workers._blenders.clear()
        warm_workers._locks.clear()
    assert got.stdout == "cold"
    assert commands[0][:2] == ["docker", "run"]
    assert commands[1][:2] == ["docker", "exec"] and commands[1][-5:] == argv
