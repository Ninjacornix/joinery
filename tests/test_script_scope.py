"""Raw bpy/Krita/GIMP Python and sclang reach only their document (ADR 0078)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from joinery import metrics
from joinery.gateway import ProjectGateway
from joinery.script_scope import check, raw_script, refusal


def rules(language: str, code: str) -> set[str]:
    return {issue.rule for issue in check(language, code)}


def test_a_plain_blender_edit_is_in_scope() -> None:
    code = (
        "import bpy, bmesh, math\n"
        "from mathutils import Vector\n"
        "import addon_utils\n"
        "from bl_ext.system.mpfb.services import HumanService\n"
        "bpy.ops.mesh.primitive_cube_add(size=1)\n"
        "obj = bpy.context.object\n"
        "obj.location = Vector((0, 0, math.pi))\n"
        "img = bpy.data.images.load('//textures/wood.png')\n"
        "img.save(filepath='art/sources/wood.png')\n"
    )
    assert check("python", code) == []


def test_network_subprocess_and_imports_outside_the_allowlist_are_refused() -> None:
    issues = check(
        "python",
        "import bpy\n"
        "import socket\n"
        "from subprocess import run\n"
        "import os\n"
        "from urllib.request import urlopen\n"
        "import yaml\n"
        "from PyQt5.QtNetwork import QNetworkAccessManager\n",
    )
    assert [(issue.line, issue.rule) for issue in issues] == [
        (2, "scope.network"),
        (3, "scope.subprocess"),
        (4, "scope.subprocess"),
        (5, "scope.network"),
        (6, "scope.import"),
        (7, "scope.network"),
    ]


def test_files_outside_the_stage_are_refused() -> None:
    assert rules("python", "bpy.ops.wm.save_as_mainfile(filepath='/tmp/x.blend')") == {
        "scope.files"
    }
    assert rules("python", "open('../../secrets.txt')") == {"scope.files"}
    assert rules("python", "doc.saveAs('~/Desktop/a.kra')") == {"scope.files"}
    assert rules("python", "open('C:\\\\Users\\\\a.txt')") == {"scope.files"}
    # Blender's "//" is next to the .blend, inside the project.
    assert rules("python", "bpy.data.images.load('//maps/a.png')") == set()


def test_code_made_at_run_time_and_python_internals_are_refused() -> None:
    assert rules("python", "exec('import os')") == {"scope.dynamic"}
    assert rules("python", "__import__('os').system('ls')") == {"scope.dynamic"}
    assert rules("python", "().__class__.__bases__[0].__subclasses__()") == {
        "scope.dynamic"
    }


def test_blender_operators_outside_a_document_edit_are_refused() -> None:
    assert rules("python", "bpy.ops.wm.url_open(url='https://example.com')") == {
        "scope.blender"
    }
    assert rules("python", "bpy.ops.extensions.package_install(pkg_id='x')") == {
        "scope.blender"
    }
    assert rules("python", "bpy.app.handlers.load_post.append(f)") == {"scope.blender"}


def test_gimp_takes_only_its_own_libraries() -> None:
    assert rules("python", "from gi.repository import Gimp, Gegl") == set()
    assert rules("python", "from gi.repository import Gio") == {"scope.import"}


def test_a_script_that_does_not_parse_is_refused_with_its_line() -> None:
    (issue,) = check("python", "import bpy\nbpy.ops.mesh.(\n")
    assert issue.rule == "scope.syntax" and issue.line == 2


def test_an_nrt_patch_is_in_scope() -> None:
    code = (
        "// a short pluck\n"
        'var score = Score([[0.0, ["/s_new", \\pluck, 1000, 0, 0]]]);\n'
        'var addr = NetAddr("127.0.0.1", 57110);\n'
        'score.recordNRT(outputFilePath: "audio/sfx/pluck.wav", duration: 1,\n'
        "  action: { 0.exit });\n"
        '"Pipe dreams".postln; /* "x".unixCmd */\n'
    )
    assert check("sclang", code) == []


def test_sclang_programs_network_files_and_text_code_are_refused() -> None:
    issues = check(
        "sclang",
        '"curl https://x".unixCmd;\n'
        'p = Pipe("ls", "r");\n'
        'NetAddr("10.0.0.2", 57120).sendMsg("/x");\n'
        'Quarks.install("x");\n'
        '"1 + 1".interpret;\n'
        '"/etc/passwd".load;\n'
        'File.delete("audio/x.wav");\n'
        '"~/a.wav".standardizePath;\n',
    )
    found = {(issue.line, issue.rule) for issue in issues}
    assert {
        (1, "scope.subprocess"),
        (2, "scope.subprocess"),
        (3, "scope.network"),
        (4, "scope.network"),
        (5, "scope.dynamic"),
        (6, "scope.dynamic"),
        (6, "scope.files"),
        (7, "scope.files"),
        (8, "scope.files"),
    } <= found


def test_raw_code_is_found_on_each_raw_tool() -> None:
    assert raw_script(
        "joinery-blender-ops", "operate_document", {"tool": "blender", "code": "x"}
    ) == ("python", "x")
    assert raw_script(
        "joinery-sound", "operate_document", {"tool": "supercollider", "code": "x"}
    ) == ("sclang", "x")
    assert raw_script("blender", "execute_blender_code", {"code": "x"}) == (
        "python",
        "x",
    )
    assert raw_script("claude-collider", "cc_execute", {"code": "x"}) == ("sclang", "x")
    # Surge XT's code is its JSON source, not a script; typed tools have no code.
    assert raw_script(
        "joinery-sound", "operate_document", {"tool": "surge-xt", "code": "{}"}
    ) == ("", "{}")
    assert raw_script("godot", "write_file", {"content": "x"}) == ("", "")


def test_the_refusal_is_plain_and_names_lines() -> None:
    text = refusal("python", check("python", "import socket\nimport os\n"))
    assert text.startswith("Not run: this Python script reaches outside")
    assert "line 1: imports socket" in text and "line 2: imports os" in text
    assert "typed operation" in text


def test_the_gateway_refuses_before_the_app_starts_and_records_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    (tmp_path / "joinery.toml").write_text('[tools]\nenabled=["blender"]\n')
    monkeypatch.setattr("joinery.gateway.configured", lambda _: ([], []))
    gateway = ProjectGateway(tmp_path)
    arguments = {
        "tool": "blender",
        "source": "art/sources/a.blend",
        "exports": [],
        "code": "import bpy\nimport subprocess\nsubprocess.run(['ls'])\n",
    }

    async def run(code: str) -> str:
        try:
            await gateway.call(
                "joinery-blender-ops", "operate_document", {**arguments, "code": code}
            )
        except ValueError as error:
            return str(error)
        return ""

    reason = asyncio.run(run(arguments["code"]))  # type: ignore[arg-type]
    assert reason.startswith("Not run:") and "line 2: imports subprocess" in reason
    assert gateway._call_attrs == {
        metrics.SCOPE_LANGUAGE: "python",
        metrics.SCOPE_REFUSED: "scope.subprocess",
    }
    assert gateway.transaction is None  # nothing was staged
    # In scope, the call goes on (and here finds no Blender connected).
    assert "not installed" in asyncio.run(run("import bpy\n"))
    assert gateway._call_attrs == {metrics.SCOPE_LANGUAGE: "python"}
