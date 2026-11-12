"""Godot's check of a staged script right after the write (ADR 0078).

The real-Godot tests run the language server and one-shot checks on the host
against a throwaway project, through the same code the worker runs; they need
``JOINERY_GODOT_BIN``. In Joinery itself these runs happen in the offline worker
(``Sandbox``), which ``JOINERY_WORKER_TESTS=1`` covers.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import socket
import sys
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, ClassVar

import pytest

from joinery import metrics
from joinery.write_check import (
    ERROR,
    MAX_REFLECTIONS,
    WARNING,
    ClassNames,
    Diagnostic,
    Outcome,
    Sandbox,
    WriteCheck,
    builtin_scripts,
    checked,
    new_only,
)

GODOT = os.environ.get("JOINERY_GODOT_BIN", "")
REAL = bool(GODOT) and Path(GODOT).is_file()
needs_godot = pytest.mark.skipif(not REAL, reason="needs JOINERY_GODOT_BIN")
WORKER = os.environ.get("JOINERY_WORKER_TESTS") == "1" and shutil.which("docker")
needs_worker = pytest.mark.skipif(not WORKER, reason="needs JOINERY_WORKER_TESTS=1")

GOOD = """extends Node

var speed: int = 3


func _ready() -> void:
\tprint(speed)
"""
BROKEN = """extends Node

var speed: int = 3


func _ready() -> void:
\tprint(speed)
\tif speed > 2
\t\tprint("fast")
"""
UNUSED = """extends Node

var speed: int = 3


func _ready() -> void:
\tvar unused := 2
\tprint(speed)
"""


class LocalRunner:
    """Godot on this machine against a throwaway test project (tests only)."""

    def __init__(self, stage: Path, binary: str) -> None:
        self.root = str(stage)
        self.godot = binary
        self.python = sys.executable
        self._helpers = Path(tempfile.mkdtemp(prefix="joinery-check-"))

    async def start(self) -> None:
        return None

    def port(self) -> int:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            return int(probe.getsockname()[1])

    def command(self, argv: list[str], *, stdin: bool = False) -> list[str]:
        return list(argv)

    async def put(self, name: str, text: str) -> str:
        target = self._helpers / name
        target.write_text(text)
        return str(target)

    async def close(self) -> None:
        shutil.rmtree(self._helpers, ignore_errors=True)


def stage(tmp_path: Path) -> Path:
    (tmp_path / "project.godot").write_text(
        'config_version=5\n\n[application]\nconfig/name="check"\n'
    )
    (tmp_path / "player.gd").write_text(GOOD)
    return tmp_path


@pytest.fixture
def godot_stage(tmp_path: Path) -> Iterator[tuple[Path, LocalRunner]]:
    root = stage(tmp_path)
    yield root, LocalRunner(root, GODOT)


# --- without Godot ------------------------------------------------------------------


def test_only_problems_the_write_added_are_new() -> None:
    old = [
        Diagnostic("a.gd", 3, WARNING, "unused"),
        Diagnostic("a.gd", 9, ERROR, "x not declared"),
    ]
    new = [
        Diagnostic("a.gd", 5, WARNING, "unused"),  # moved two lines down
        Diagnostic("a.gd", 11, ERROR, "x not declared"),
        Diagnostic("a.gd", 12, ERROR, "x not declared"),  # one more of the same
        Diagnostic("a.gd", 2, ERROR, "Expected ':'"),
    ]
    assert new_only(old, new) == new[2:]


def test_which_files_are_checked() -> None:
    assert all(checked(p) for p in ("a.gd", "s/a.tscn", "a.tres", "x.gdshader"))
    assert not any(checked(p) for p in ("a.png", "a.glb", "a.gd.uid", "a.json"))


def test_built_in_scripts_are_read_with_their_lines() -> None:
    scene = (
        '[gd_scene load_steps=2 format=3]\n\n[sub_resource type="GDScript" '
        'id="GDScript_a1"]\nscript/source = "extends Node\n\nfunc _ready() -> void:'
        '\n\tprint(\\"hi \\\\\\\\ there\\")\n"\n\n[node name="Root" type="Node"]\n'
        'script = SubResource("GDScript_a1")\n'
    )
    ((identifier, line, source),) = builtin_scripts(scene)
    assert identifier == "GDScript_a1" and line == 4
    assert (
        source == 'extends Node\n\nfunc _ready() -> void:\n\tprint("hi \\\\ there")\n'
    )
    assert builtin_scripts('[gd_scene format=3]\n[node name="A" type="Node"]\n') == []


def test_class_names_are_reread_only_when_a_file_changes(tmp_path: Path) -> None:
    (tmp_path / "a.gd").write_text("class_name Alpha\nextends Node\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub/b.gd").write_text("@tool\nclass_name Beta extends Node\n")
    (tmp_path / ".godot").mkdir()
    (tmp_path / ".godot/c.gd").write_text("class_name Hidden\n")
    names = ClassNames(tmp_path)
    assert names.current() == {"Alpha", "Beta"}
    (tmp_path / "a.gd").write_text("class_name Gamma\nextends Node\n")
    assert names.current() == {"Gamma", "Beta"}


def test_the_message_lists_new_problems_and_the_streak() -> None:
    outcome = Outcome(
        "player.gd",
        [Diagnostic("player.gd", 8, ERROR, 'Expected ":" after "if" condition.')],
        backend="language-server",
        streak=1,
    )
    assert outcome.message() == (
        "Godot check of player.gd: 1 new problem from this write. Fix them now, "
        'before anything else:\n- player.gd:8 error: Expected ":" after "if" condition.'
    )
    outcome.streak = MAX_REFLECTIONS
    assert f"write {MAX_REFLECTIONS} of player.gd in a row" in outcome.message()
    assert Outcome("player.gd", backend="language-server").message() == ""
    assert Outcome("player.gd", note="Docker is not available").message() == (
        "Godot could not check player.gd now (Docker is not available); precheck will."
    )


def test_a_failed_check_never_fails_the_write(tmp_path: Path) -> None:
    class Broken(LocalRunner):
        async def start(self) -> None:
            raise RuntimeError("no worker")

    async def run() -> Outcome:
        check = WriteCheck(tmp_path, Broken(tmp_path, "godot"))
        try:
            return await check.check("a.gdshader", None, "shader_type spatial;")
        finally:
            await check.close()

    outcome = asyncio.run(run())
    assert outcome.backend == "" and outcome.note == "no worker"
    assert "could not check a.gdshader" in outcome.message()


def test_the_gateway_checks_a_staged_script_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mcp.types import CallToolResult, TextContent

    from joinery.gateway import ProjectGateway

    (tmp_path / "project.godot").write_text("config_version=5\n")
    (tmp_path / "joinery.toml").write_text('[tools]\nenabled=["godot"]\n')
    (tmp_path / "player.gd").write_text(GOOD)
    monkeypatch.setattr("joinery.gateway.configured", lambda _: ([], []))
    gateway = ProjectGateway(tmp_path)
    seen: list[tuple[str, str | None, str]] = []

    class FakeCheck:
        def __init__(self, stage: Path, **_kwargs: object) -> None:
            self.stage = stage

        def warm(self) -> None:
            pass

        async def check(self, path: str, before: str | None, after: str) -> Outcome:
            seen.append((path, before, after))
            return Outcome(
                path,
                [Diagnostic(path, 8, ERROR, "Expected end of statement")],
                backend="language-server",
                seconds=0.01,
                streak=1,
            )

        async def close(self) -> None:
            pass

    class FakeGodot:
        tools: ClassVar[dict[str, object]] = {"write_file": object()}

        async def call(
            self, tool: str, arguments: dict[str, Any], timeout: float = 60
        ) -> CallToolResult:
            target = gateway.transaction.stage / "player.gd"  # type: ignore[union-attr]
            target.write_text(arguments["content"])
            return CallToolResult(content=[TextContent(type="text", text="Written")])

        async def connect(self) -> None:
            pass

        async def close(self) -> None:
            pass

    async def connection(_server: str) -> Any:
        return FakeGodot()

    async def no_import(_paths: list[str]) -> None:
        return None

    monkeypatch.setattr("joinery.gateway.WriteCheck", FakeCheck)
    monkeypatch.setattr(gateway, "connection", connection)
    monkeypatch.setattr(gateway, "_import_for", no_import)
    gateway.staging()
    gateway.staged["godot"] = FakeGodot()  # type: ignore[assignment]

    result = asyncio.run(
        gateway.call(
            "godot",
            "write_file",
            {
                "filePath": "res://player.gd",
                "patch": [{"find": "speed)", "replace": "speed"}],
            },
        )
    )
    assert seen == [("player.gd", GOOD, GOOD.replace("speed)", "speed"))]
    texts = [getattr(block, "text", "") for block in result.content]
    assert texts[0] == "Written"
    assert texts[-1].startswith("Godot check of player.gd: 1 new problem")
    assert "player.gd:8 error: Expected end of statement" in texts[-1]
    assert gateway._call_attrs[metrics.WRITE_CHECK] == "language-server"
    assert gateway._call_attrs[metrics.WRITE_CHECK_ERRORS] == 1
    assert gateway._call_attrs[metrics.WRITE_CHECK_STREAK] == 1
    # A file nobody checks, and a write that failed, are left alone.
    asyncio.run(
        gateway.call("godot", "write_file", {"filePath": "a.txt", "content": "x"})
    )
    assert len(seen) == 1


# --- with real Godot -----------------------------------------------------------------


@needs_godot
def test_a_broken_script_reports_its_new_error_and_the_fix_clears_it(
    godot_stage: tuple[Path, LocalRunner],
) -> None:
    root, runner = godot_stage

    async def run() -> list[Outcome]:
        check = WriteCheck(root, runner)
        check.warm()
        try:
            broken = await check.check("player.gd", GOOD, BROKEN)
            fixed = await check.check(
                "player.gd", BROKEN, BROKEN.replace("> 2", "> 2:")
            )
            unused = await check.check("player.gd", GOOD, UNUSED)
            again = await check.check("player.gd", UNUSED, UNUSED + "\n")
            return [broken, fixed, unused, again]
        finally:
            await check.close()

    broken, fixed, unused, again = asyncio.run(run())
    assert broken.backend == "language-server"
    assert [(d.line, d.severity) for d in broken.new] == [(8, ERROR)]
    assert 'after "if" condition' in broken.new[0].message
    assert broken.streak == 1
    assert fixed.new == [] and fixed.streak == 0
    # A warning is new once; a write that keeps it does not repeat it.
    assert [(d.line, d.severity) for d in unused.new] == [(7, WARNING)]
    assert "UNUSED_VARIABLE" in unused.new[0].message
    assert again.new == []
    # Warm, a check is fast.
    assert fixed.seconds < 2 and again.seconds < 2, (fixed.seconds, again.seconds)


@needs_godot
def test_a_class_added_in_the_stage_is_known_to_the_check(
    godot_stage: tuple[Path, LocalRunner],
) -> None:
    root, runner = godot_stage

    async def run() -> Outcome:
        check = WriteCheck(root, runner)
        try:
            first = await check.check("player.gd", GOOD, GOOD + "\n")
            assert first.backend == "language-server"
            (root / "enemy.gd").write_text(
                "class_name Enemy\nextends Node\n\n\nfunc hit() -> int:\n\treturn 1\n"
            )
            uses = (
                GOOD
                + "\n\nfunc spawn() -> int:\n\tvar e := Enemy.new()\n\treturn e.hit()\n"
            )
            return await check.check("player.gd", GOOD, uses)
        finally:
            await check.close()

    outcome = asyncio.run(run())
    assert outcome.backend == "language-server"
    assert outcome.new == []


@needs_godot
def test_the_fallback_reports_the_files_parse_error(
    godot_stage: tuple[Path, LocalRunner],
) -> None:
    root, runner = godot_stage
    (root / "player.gd").write_text(BROKEN)

    async def run() -> Outcome:
        check = WriteCheck(root, runner)
        check._server_failed = "language server unavailable"
        try:
            return await check.check("player.gd", GOOD, BROKEN)
        finally:
            await check.close()

    outcome = asyncio.run(run())
    assert outcome.backend == "check-only" and not outcome.compared
    assert [(d.file, d.line, d.severity) for d in outcome.new] == [
        ("player.gd", 8, ERROR)
    ]
    assert "problems in the file" in outcome.message()


@needs_godot
def test_a_broken_shader_and_its_fix(godot_stage: tuple[Path, LocalRunner]) -> None:
    root, runner = godot_stage
    good = "shader_type spatial;\n\nvoid fragment() {\n\tALBEDO = vec3(1.0);\n}\n"
    bad = "shader_type spatial;\n\nvoid fragment() {\n\tALBEDO = vec3(1.0) + nope;\n}\n"

    async def run() -> tuple[Outcome, Outcome, Outcome]:
        check = WriteCheck(root, runner)
        try:
            return (
                await check.check("water.gdshader", good, bad),
                await check.check("water.gdshader", bad, good),
                await check.check("water.gdshader", bad, bad.replace("1.0)", "0.5)")),
            )
        finally:
            await check.close()

    broken, fixed, still = asyncio.run(run())
    assert broken.backend == "shader"
    assert [(d.line, d.severity) for d in broken.new] == [(4, ERROR)]
    assert "nope" in broken.new[0].message
    assert fixed.new == []
    assert still.new == []  # the error was there before this write


@needs_godot
def test_a_scenes_built_in_script_is_checked_at_its_scene_line(
    godot_stage: tuple[Path, LocalRunner],
) -> None:
    root, runner = godot_stage

    def scene(body: str) -> str:
        source = body.replace("\\", "\\\\").replace('"', '\\"')
        return (
            '[gd_scene load_steps=2 format=3]\n\n[sub_resource type="GDScript" '
            f'id="GDScript_a1"]\nscript/source = "{source}"\n\n'
            '[node name="Root" type="Node"]\nscript = SubResource("GDScript_a1")\n'
        )

    async def run() -> Outcome:
        check = WriteCheck(root, runner)
        try:
            return await check.check("level.tscn", scene(GOOD), scene(BROKEN))
        finally:
            await check.close()

    outcome = asyncio.run(run())
    assert outcome.backend == "language-server"
    # Line 8 of the script; its source starts on line 4 of the scene.
    assert [(d.file, d.line, d.severity) for d in outcome.new] == [
        ("level.tscn", 11, ERROR)
    ]


@needs_worker
def test_the_worker_checks_a_script_in_the_offline_container(tmp_path: Path) -> None:
    root = stage(tmp_path)

    async def run() -> tuple[Outcome, Outcome, Outcome, Outcome, float]:
        check = WriteCheck(root, Sandbox(root))
        check.warm()
        try:
            started = time.monotonic()
            broken = await check.check("player.gd", GOOD, BROKEN)
            cold = time.monotonic() - started
            fixed = await check.check("player.gd", BROKEN, GOOD)
            shader = await check.check(
                "a.gdshader",
                None,
                "shader_type spatial;\nvoid fragment() {\n\tALBEDO = nope;\n}\n",
            )
            (root / "player.gd").write_text(BROKEN)
            check._server_failed = "off for this test"
            fallback = await check.check("player.gd", GOOD, BROKEN)
            return broken, fixed, shader, fallback, cold
        finally:
            await check.close()

    broken, fixed, shader, fallback, cold = asyncio.run(run())
    print(
        f"worker: first check {cold:.2f}s ({broken.backend}), warm check "
        f"{fixed.seconds:.3f}s, shader {shader.seconds:.2f}s, check-only "
        f"{fallback.seconds:.2f}s"
    )
    assert broken.backend == "language-server"
    assert [(d.line, d.severity) for d in broken.new] == [(8, ERROR)]
    assert fixed.new == []
    assert [(d.line, d.severity) for d in shader.new] == [(3, ERROR)]
    assert fallback.backend == "check-only"
    assert [(d.line, d.severity) for d in fallback.new] == [(8, ERROR)]
