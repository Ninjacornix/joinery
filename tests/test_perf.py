"""`joinery perf` without Godot: budgets, the scaled judgement, the file checks, the plan.

The fixture (tests/fixtures/perf_game) is a heavy scene planted on purpose: 24 copies of
one mesh with no visibility range, six shadow-casting omni lights and a script that loops
300 000 times a frame; each test adds an oversized texture or a model with LODs off.
"""

from __future__ import annotations

import json
import shutil
import struct
import zlib
from pathlib import Path
from typing import Any

import pytest

from joinery import check_runs, checks_core, perf, perf_static
from joinery.checks_core import Tally

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "perf_game"


def _png(path: Path, width: int, height: int) -> None:
    """A plain grey PNG (test data made in code, not art)."""
    row = b"\x00" + b"\x80\x80\x80" * width
    data = zlib.compress(row * height, 9)

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body))
            + kind
            + body
            + struct.pack(">I", zlib.crc32(kind + body))
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", data)
        + chunk(b"IEND", b"")
    )


def _glb(path: Path, triangles: int) -> None:
    """A binary glTF with one mesh of ``triangles`` triangles (a strip of quads)."""
    positions = b"".join(
        struct.pack("<3f", float(i), float(i % 2), 0.0) for i in range(triangles + 2)
    )
    indices = b"".join(struct.pack("<3I", i, i + 1, i + 2) for i in range(triangles))
    binary = positions + indices
    document = {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"name": "Strip", "mesh": 0}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(positions)},
            {"buffer": 0, "byteOffset": len(positions), "byteLength": len(indices)},
        ],
        "accessors": [
            {
                "bufferView": 0,
                "componentType": 5126,
                "count": triangles + 2,
                "type": "VEC3",
                "min": [0, 0, 0],
                "max": [float(triangles + 1), 1, 0],
            },
            {
                "bufferView": 1,
                "componentType": 5125,
                "count": triangles * 3,
                "type": "SCALAR",
            },
        ],
    }
    text = json.dumps(document).encode()
    text += b" " * (-len(text) % 4)
    body = (
        struct.pack("<II", len(text), 0x4E4F534A)
        + text
        + struct.pack("<II", len(binary), 0x004E4942)
        + binary
    )
    path.write_bytes(struct.pack("<III", 0x46546C67, 2, 12 + len(body)) + body)


@pytest.fixture
def game(tmp_path: Path) -> Path:
    project = tmp_path / "perf"
    shutil.copytree(FIXTURE, project)
    (project / "art").mkdir()
    _png(project / "art" / "ground.png", 4096, 4096)
    (project / "art" / "ground.png.import").write_text(
        '[remap]\n\nimporter="texture"\ntype="CompressedTexture2D"\n\n'
        "[params]\n\ncompress/mode=0\nprocess/size_limit=0\n"
    )
    _glb(project / "art" / "rock.glb", 80_000)
    (project / "art" / "rock.glb.import").write_text(
        '[remap]\n\nimporter="scene"\n\n[params]\n\nmeshes/generate_lods=false\n'
    )
    return project


def _files(project: Path) -> list[str]:
    return perf_static.all_files(project)


def test_targets_have_budgets_and_joinery_toml_overrides_them(tmp_path: Path) -> None:
    assert set(perf.TARGETS) == {"high", "mid", "low", "steam-deck", "web"}
    assert perf.TARGETS["web"].draw_calls < perf.TARGETS["high"].draw_calls
    (tmp_path / "joinery.toml").write_text(
        '[performance]\ntarget = "web"\nframe_ms = 20\nhost_speed = 0.8\n'
        "[performance.web]\ndraw_calls = 300\n"
    )
    assert perf.target_name(tmp_path) == "web"
    goal = perf.budget(tmp_path, "web")
    assert goal.draw_calls == 300 and goal.frame_ms == 20.0
    assert perf.budget(tmp_path, "high").frame_ms == 20.0
    assert perf.host_speed(tmp_path) == 0.8
    with pytest.raises(ValueError, match="Unknown performance target"):
        perf.target_name(tmp_path, "console")


def _numbers(**over: Any) -> dict[str, Any]:
    numbers: dict[str, Any] = {
        "windowed": True,
        "frame_ms": {"p50": 6.0, "p95": 9.0, "p99": 12.0, "max": 30.0},
        "cpu_frame_ms": {"p50": 1.0, "p95": 2.0, "p99": 3.0},
        "draw_calls": 900,
        "primitives": 400_000,
        "texture_mb": 300.0,
        "nodes": 1200,
        "bodies": 40,
        "rigid": 3,
        "lights": [],
        "load_ms": 900,
        "memory_growth_mb_min": 0.5,
        "late_compilations": 0,
        "spikes": [],
        "hot": [],
        "census": {"all_layers": []},
    }
    numbers.update(over)
    return numbers


def test_frame_times_are_scaled_to_the_target_and_counts_are_not() -> None:
    tally = Tally(perf.AREAS)
    # 9 ms p95 here is 36 ms on a low-end PC (speed 0.25): over its 33.3 ms.
    perf.judge(tally, _numbers(), perf.TARGETS["low"], 1.0 / 0.25, seed=7)
    found = {f.check: f for f in tally.findings}
    assert found["frame_p95"].measured == "36.0 ms"
    assert found["frame_p95"].evidence == {"seed": 7}
    assert "draw_calls" in found  # 900 > 800, unscaled
    tally = Tally(perf.AREAS)
    perf.judge(tally, _numbers(), perf.TARGETS["high"], 1.0, seed=7)
    assert tally.findings == []
    assert all(score.score == 100 for score in tally.scores())


def test_growing_memory_late_shader_compilations_and_slow_loads_are_found() -> None:
    tally = Tally(perf.AREAS)
    numbers = _numbers(
        memory_growth_mb_min=12.0,
        late_compilations=5,
        spikes=[{"frame": 300, "ms": 80.0}],
        load_ms=4000,
    )
    perf.judge(tally, numbers, perf.TARGETS["mid"], 2.0, seed=3)
    found = {f.check: f for f in tally.findings}
    assert (
        found["memory_growth"].fix is not None
        and found["memory_growth"].fix.kind == "agent"
    )
    assert found["shader_compilations"].evidence == {"seed": 3, "t": 5.0}
    assert found["load_ms"].measured == "8.0 s"


def test_heavy_files_are_found_with_typed_fixes(game: Path) -> None:
    tally = Tally(perf.AREAS)
    files = _files(game)
    perf_static.textures(tally, game, files, 2048, {})
    perf_static.models(tally, game, files, 60_000, {})
    perf_static.scenes(tally, game, files, 4)
    found = {(f.path, f.check): f for f in tally.findings}
    size = found[("art/ground.png", "texture_size")]
    assert size.measured == "4096×4096" and size.severity == "high"
    assert size.fix is not None and size.fix.source == "art/ground.png.import"
    assert size.fix.arguments["edits"] == [
        {"ini": "params", "values": {"process/size_limit": 2048}}
    ]
    compression = found[("art/ground.png", "texture_compression")]
    assert compression.measured == "lossless"
    lods = found[("art/rock.glb", "lods")]
    assert lods.fix is not None and lods.fix.label == "Make LODs on import in Godot"
    heavy = found[("art/rock.glb", "model_triangles")]
    assert heavy.measured == "80000 triangles" and heavy.fix is None
    assert "imported" in heavy.needs_you
    shadows = found[("main.tscn", "shadow_lights")]
    assert shadows.measured == "6 shadowed lights"
    assert shadows.fix is not None
    assert [e["node"] for e in shadows.fix.arguments["edits"]] == [
        ["Lamp5", "Lights"],
        ["Lamp6", "Lights"],
    ]
    repeats = found[("main.tscn", "repeats")]
    assert repeats.fix is not None and repeats.fix.kind == "agent"
    assert "MultiMeshInstance3D" in repeats.fix.request
    ranged = found[("main.tscn", "visibility_range")]
    assert ranged.fix is not None and len(ranged.fix.arguments["edits"]) == 24


def test_a_texture_made_in_krita_is_resized_in_krita(game: Path) -> None:
    from joinery.match import Owner

    tally = Tally(perf.AREAS)
    (game / "art" / "ground.kra").write_bytes(b"krita")
    owners = {
        "art/ground.png": Owner(
            tool="krita", source="art/ground.kra", exports=["art/ground.png"]
        )
    }
    perf_static.textures(tally, game, _files(game), 1024, owners)
    fix = next(f.fix for f in tally.findings if f.check == "texture_size")
    assert fix is not None
    assert (fix.server, fix.tool) == ("joinery-paint", "edit_image")
    assert fix.arguments["ops"] == [{"op": "resize", "width": 1024, "height": 1024}]


def test_a_script_hot_loop_is_found_and_its_measured_cost_judged(game: Path) -> None:
    hot = perf_static.hot_code("hot.gd", (game / "hot.gd").read_text())
    assert hot == [("_process", 9, "loops 300000 times every frame")]
    tally = Tally(perf.AREAS)
    perf_static.scripts(tally, game, _files(game), {"res://hot.gd": 12.0}, 16.7, 2.0)
    (finding,) = tally.findings
    assert finding.severity == "high" and finding.measured == "24.0 ms a frame"
    assert finding.fix is not None and finding.fix.targets == ["hot.gd"]
    assert "loops 300000 times every frame" in finding.message


def test_typed_fixes_of_one_file_fold_into_one_write_and_apply(game: Path) -> None:
    tally = Tally(perf.AREAS)
    files = _files(game)
    perf_static.scenes(tally, game, files, 4)
    perf_static.textures(tally, game, files, 2048, {})
    report = tally.report("perf", game, [])
    tasks = checks_core.plan(game, report, labels=perf.AREAS)
    scripted = [t for t in tasks if t.kind == "scripted"]
    agents = [t for t in tasks if t.kind == "agent"]
    # One task per file: the scene's lights and ranges fold into one write of it.
    assert {t.area for t in scripted} == {"rendering", "textures"}
    scene_writes = [
        s for t in scripted for s in t.steps if s.arguments["filePath"] == "main.tscn"
    ]
    (scene,) = scene_writes
    text = scene.arguments["content"]
    assert text.count("shadow_enabled = false") == 2
    assert text.count("visibility_range_end = 60.0") == 24
    texture = next(t for t in scripted if t.area == "textures")
    (step,) = texture.steps
    content = step.arguments["content"]
    assert "process/size_limit=2048" in content and "compress/mode=2" in content
    assert texture.targets == ["art/ground.png.import"]
    assert agents and agents[0].request.endswith("naming the change.")


def test_node_and_ini_edits_keep_the_rest_of_the_file() -> None:
    scene = (
        '[gd_scene format=3]\n\n[node name="Root" type="Node3D"]\n\n'
        '[node name="Lamp" type="OmniLight3D" parent="."]\nshadow_enabled = true\n\n'
        '[node name="Lamp" type="OmniLight3D" parent="Other"]\nshadow_enabled = true\n'
    )
    edited = checks_core.set_node_properties(
        scene, "Lamp", "Other", {"shadow_enabled": False}
    )
    assert edited.count("shadow_enabled = true") == 1
    assert edited.endswith('parent="Other"]\nshadow_enabled = false\n')
    with pytest.raises(ValueError, match="no longer in the scene"):
        checks_core.set_node_properties(scene, "Gone", ".", {"x": 1})
    ini = '[remap]\n\npath="x"\n\n[params]\n\ncompress/mode=0\n'
    out = checks_core.set_ini_values(
        ini, "params", {"compress/mode": 2, "process/size_limit": 1024}
    )
    assert (
        out
        == '[remap]\n\npath="x"\n\n[params]\n\ncompress/mode=2\nprocess/size_limit=1024\n'
    )


def test_metrics_read_both_runs() -> None:
    headless = check_runs.Run(
        probe={
            "samples": [
                {"frame": 0, "static_mb": 30.0, "nodes": 100},
                {"frame": 1800, "static_mb": 31.0, "nodes": 120},
                {"frame": 3600, "static_mb": 32.0, "nodes": 120},
            ],
            "census": {
                "triangles": 5000,
                "bodies": 12,
                "rigid": 2,
                "texture_mb_est": 40.0,
            },
            "load_ms": 250,
            "hot": [{"script": "res://hot.gd", "ms": 7.5}],
        },
        perf={"frame_ms_p50": 1.0, "frame_ms_p95": 2.0, "frame_ms_p99": 4.0},
    )
    window = check_runs.Run(
        probe={
            "samples": [
                {
                    "frame": 30,
                    "draw_calls": 120,
                    "primitives": 9000,
                    "texture_mb": 60.0,
                    "compilations": 10,
                },
                {
                    "frame": 60,
                    "draw_calls": 140,
                    "primitives": 9100,
                    "texture_mb": 61.0,
                    "compilations": 10,
                },
            ]
        },
        perf={
            "frame_ms_p50": 8.0,
            "frame_ms_p95": 10.0,
            "frame_ms_p99": 12.0,
            "frame_ms_max": 40.0,
        },
    )
    numbers = perf.metrics(headless, window)
    assert numbers["windowed"] and numbers["frame_ms"]["p99"] == 12.0
    assert numbers["draw_calls"] == 140 and numbers["primitives"] == 9100
    assert numbers["texture_mb"] == 61.0 and numbers["nodes"] == 120
    assert numbers["memory_growth_mb_min"] == 2.0  # 2 MB in one minute
    headless_only = perf.metrics(headless, check_runs.Run())
    assert not headless_only["windowed"]
    assert headless_only["primitives"] == 5000 and headless_only["texture_mb"] == 40.0
