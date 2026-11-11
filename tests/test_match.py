"""Make everything match: the audit's measurements, fix planning, exclusions and
the parallel scheduling of fixes."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from bridge.crew import ScriptedCall
from bridge.match_runner import MatchRun
from joinery import match, match_look, match_plan
from joinery.bible_rules import _MEASURE, _SAMPLE_IMAGES, parse_rules
from joinery.cli import main as cli_main
from joinery.runner import Result
from tests.test_bible_rules import GlbBuilder, png, wav
from tests.test_crew import CrewTest
from tests.test_crew import project as crew_project  # noqa: F401 - the fixture

BIBLE = """# Art Bible

## 18. Checked rules

```toml
schema = 2
[palette]
colours = ["#203040", "#c08040", "#e0d0b0"]
max_delta_e = 12.0
max_off_palette = 0.05
[textures]
power_of_two = true
[textures.max_size]
"art" = 128
[models]
applied_scale = true
[models.pivot]
"art/models" = "bottom-centre"
[models.max_triangles]
"art/models" = 500
[models.texel_density]
"art" = { px_per_m = 256, tolerance = 0.2 }
[materials]
roughness = [0.4, 0.95]
metallic_requires_name = "(?i)metal"
[audio]
true_peak_max_dbtp = -1.0
[audio.lufs]
"audio/sfx" = -16.0
[environment]
tonemap = "agx"
exposure = [0.9, 1.1]
fog = false
saturation = [0.9, 1.1]
[ui]
fonts = ["ui/fonts/body.ttf"]
palette_colours = true
[accessibility]
min_text_px = 18
ui_folders = ["ui"]
```
"""

WORLD = """[gd_resource type="Environment" format=3]

[resource]
background_mode = 1
tonemap_mode = 2
tonemap_exposure = 1.6
fog_enabled = true
"""

LEVEL = """[gd_scene load_steps=2 format=3]

[sub_resource type="Environment" id="Environment_sky"]
tonemap_mode = 4
adjustment_enabled = true
adjustment_saturation = 1.5

[node name="Level" type="Node3D"]

[node name="World" type="WorldEnvironment" parent="."]
environment = SubResource("Environment_sky")
"""

THEME = """[gd_resource type="Theme" load_steps=2 format=3]

[ext_resource type="FontFile" uid="uid://abc" path="res://ui/fonts/comic.ttf" id="1_font"]

[resource]
default_font = ExtResource("1_font")
Label/colors/font_color = Color(1, 0, 1, 1)
Label/font_sizes/font_size = 12
"""


def recipe(project: Path, name: str, tool: str, outputs: list[str]) -> None:
    (project / "recipes").mkdir(exist_ok=True)
    (project / f"recipes/{name}.py").write_text("# replay\n")
    listed = ", ".join(json.dumps(o) for o in outputs)
    (project / f"recipes/{name}.toml").write_text(
        f'tool = "{tool}"\nscript = "recipes/{name}.py"\noutputs = [{listed}]\n'
    )


def crate() -> bytes:
    builder = GlbBuilder()
    texture = builder.texture(png(256, 256))
    wood = builder.material(
        "Crate_Wood",
        baseColorTexture={"index": texture},
        roughnessFactor=0.2,
        metallicFactor=0.0,
    )
    builder.quad("Crate", wood, scale=[2.0, 2.0, 2.0], translation=[0.0, 0.5, 0.0])
    return builder.build()


@pytest.fixture
def planted(tmp_path: Path) -> Path:
    """A project with one of every mismatch, and two things the user protects."""
    root = tmp_path / "game"
    files: dict[str, bytes | str] = {
        "project.godot": "config_version=5\n[display]\nwindow/size/viewport_height=1080\n",
        "joinery.toml": (
            '[tools]\nenabled = ["godot", "krita", "blender", "surge-xt"]\n\n'
            '[match]\nexclude = ["art/wip"]\nfinal = ["art/textures/final.png"]\n'
        ),
        "art/bible.md": BIBLE,
        "art/textures/wall.kra": b"kra",
        "art/textures/wall.png": png(64, 64),
        "art/textures/floor.kra": b"kra",
        "art/textures/floor.png": png(512, 512),
        "art/textures/final.png": png(512, 512),
        "art/wip/sketch.png": png(300, 300),
        "art/reference/board.png": png(300, 300),
        "art/sources/crate.blend": b"blend",
        "art/models/crate.glb": crate(),
        "audio/sources/beep.json": "{}",
        "audio/sfx/beep.wav": wav(),
        "audio/sfx/imported.wav": wav(),
        "env/world.tres": WORLD,
        "levels/level.tscn": LEVEL,
        "ui/theme.tres": THEME,
    }
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, str):
            path.write_text(data)
        else:
            path.write_bytes(data)
    recipe(root, "wall", "krita", ["art/textures/wall.kra", "art/textures/wall.png"])
    recipe(root, "floor", "krita", ["art/textures/floor.kra", "art/textures/floor.png"])
    recipe(
        root, "crate", "blender", ["art/sources/crate.blend", "art/models/crate.glb"]
    )
    recipe(root, "beep", "surge-xt", ["audio/sources/beep.json", "audio/sfx/beep.wav"])
    return root


def loudness(path: str, lufs: float, peak: float) -> str:
    return (
        f"JOINERY_FILE:{path}\n[Parsed_ebur128_0] Summary:\n\n  Integrated loudness:\n"
        f"    I:         {lufs:.1f} LUFS\n\n  True peak:\n    Peak:      {peak:.1f} dBFS\n"
    )


class FakeWorker:
    """The offline worker: known pixel samples and loudness for the planted files."""

    def __init__(self, magenta: bool = True, loud: bool = True) -> None:
        self.magenta = magenta
        self.loud = loud
        self.calls: list[list[str]] = []

    async def __call__(self, argv: list[str], _root: Path) -> Result:
        self.calls.append(argv)
        files = argv[4:]
        if argv[2] == _SAMPLE_IMAGES:
            out = ""
            for path in files:
                pixel = (
                    "ff00ffff"
                    if path.endswith("wall.png") and self.magenta
                    else "203040ff"
                )
                out += f"JOINERY_FILE:{path}\n{pixel * 64}\n"
            return Result(True, out, "", 0.1)
        if argv[2] == _MEASURE:
            out = "".join(
                loudness(p, -6.0 if self.loud else -16.2, -0.2 if self.loud else -2.0)
                for p in files
            )
            return Result(True, out, "", 0.1)
        return Result(False, "", "unexpected", 0.1)


def run_audit(
    project: Path, worker: FakeWorker | None = None, **kwargs: Any
) -> match.Audit:
    return asyncio.run(match.audit(project, worker or FakeWorker(), **kwargs))


def by(audit: match.Audit, area: str) -> dict[str, list[match.Finding]]:
    found: dict[str, list[match.Finding]] = {}
    for finding in audit.findings:
        if finding.area == area:
            found.setdefault(finding.path, []).append(finding)
    return found


# --- Rules ---------------------------------------------------------------------------


def test_environment_and_ui_tables_parse_and_refuse_bad_values() -> None:
    rules = parse_rules(
        '[environment]\ntonemap = "agx"\nexposure = [0.9, 1.1]\n[ui]\nfonts = ["a.ttf"]\n'
    )
    assert rules.environment.tonemap == "agx"
    assert rules.ui.fonts == ["a.ttf"]
    with pytest.raises(ValueError, match="environment.exposure"):
        parse_rules("[environment]\nexposure = [2.0, 1.0]\n")
    with pytest.raises(ValueError, match="tonemap"):
        parse_rules('[environment]\ntonemap = "sepia"\n')


# --- The audit -----------------------------------------------------------------------


def test_audit_measures_every_area_with_known_values(planted: Path) -> None:
    audit = run_audit(planted)
    scores = {a.id: a.score for a in audit.areas}
    # Two textures sampled, the magenta wall is off: 50 %.
    assert scores["palette"] == 50
    assert audit.summary.startswith("Palette 50% · Textures ")
    palette = by(audit, "palette")["art/textures/wall.png"][0]
    assert (palette.measured, palette.target, palette.severity) == (
        "100% off-palette",
        "≤ 5%",
        "high",
    )
    assert palette.fix is not None and palette.fix.tool == "edit_image"
    assert palette.fix.arguments["source"] == "art/textures/wall.kra"
    assert palette.fix.arguments["ops"][0]["op"] == "palette_map"
    assert palette.fix.arguments["exports"] == ["art/textures/wall.png"]
    textures = by(audit, "textures")
    floor = textures["art/textures/floor.png"][0]
    assert (floor.measured, floor.target) == ("512×512", "128×128")
    assert floor.fix is not None
    assert floor.fix.arguments["ops"] == [{"op": "resize", "width": 128, "height": 128}]
    texel = textures["art/models/crate.glb"][0]
    assert texel.check == "texel_density" and texel.measured == "128 px/m"
    assert texel.fix is not None and texel.fix.tool == "set_texel_density"
    assert texel.fix.arguments["objects"] == ["Crate"]
    models = {f.check: f for f in by(audit, "models")["art/models/crate.glb"]}
    assert set(models) == {"scale", "pivot"}
    assert models["scale"].fix is not None
    assert models["scale"].fix.arguments == {
        "source": "art/sources/crate.blend",
        "objects": ["Crate"],
    }
    assert models["pivot"].measured == "0.50 m off"
    assert models["pivot"].fix is not None and models["pivot"].fix.tool == "set_pivot"
    material = by(audit, "materials")["art/models/crate.glb"][0]
    assert material.subject == "Crate_Wood"
    assert material.fix is not None and material.fix.arguments["value"] == 0.4
    sound = by(audit, "sound")
    beep = sound["audio/sfx/beep.wav"][0]
    assert (beep.measured, beep.target, beep.severity) == (
        "-6.0 LUFS",
        "-16 LUFS",
        "high",
    )
    assert beep.fix is not None
    assert beep.fix.arguments["steps"] == [
        {"op": "normalise", "lufs": -16.0, "true_peak": -1.0}
    ]
    assert beep.fix.apps == ["surge-xt"]
    imported = sound["audio/sfx/imported.wav"][0]
    assert imported.fix is None and "imported" in imported.needs_you
    assert scores["sound"] == 0


def test_lighting_and_ui_findings_carry_godot_fixes(planted: Path) -> None:
    audit = run_audit(planted)
    world = {f.check: f for f in by(audit, "lighting")["env/world.tres"]}
    assert set(world) == {"tonemap", "exposure", "fog"}
    assert world["tonemap"].measured == "filmic" and world["tonemap"].target == "agx"
    exposure = world["exposure"].fix
    assert exposure is not None and exposure.tool == "manage_resource"
    assert exposure.arguments["properties"] == {"tonemap_exposure": 1.1}
    level = by(audit, "lighting")["levels/level.tscn"][0]
    assert level.check == "saturation" and level.measured == "1.5"
    assert level.fix is not None and level.fix.tool == "write_file"
    ui = {f.check: f for f in by(audit, "ui")["ui/theme.tres"]}
    assert set(ui) == {"font", "theme_colour", "font_size"}
    assert ui["font"].measured == "ui/fonts/comic.ttf"
    assert (ui["font_size"].measured, ui["font_size"].target) == ("12 px", "18 px")
    assert ui["theme_colour"].fix is not None
    assert (
        ui["theme_colour"]
        .fix.arguments["properties"]["Label/colors/font_color"]
        .startswith("Color(")
    )
    lighting = next(a for a in audit.areas if a.id == "lighting")
    assert (lighting.checked, lighting.failing, lighting.score) == (2, 2, 0)


def test_what_the_user_protects_is_never_measured(planted: Path) -> None:
    worker = FakeWorker()
    audit = run_audit(planted, worker)
    paths = {f.path for f in audit.findings}
    assert "art/textures/final.png" not in paths
    assert "art/wip/sketch.png" not in paths
    assert "art/reference/board.png" not in paths
    assert {"art/textures/final.png", "art/wip/sketch.png"} <= set(audit.excluded)
    assert "art/reference/board.png" not in audit.excluded
    sampled = [p for call in worker.calls for p in call[4:]]
    assert "art/textures/final.png" not in sampled
    # Marking a source final protects what it exports.
    match.mark_final(planted, "art/textures/wall.kra")
    again = run_audit(planted)
    assert "art/textures/wall.png" in again.excluded
    assert "art/textures/wall.png" not in {f.path for f in again.findings}
    assert match.settings(planted).final == [
        "art/textures/final.png",
        "art/textures/wall.kra",
    ]
    assert match.mark_final(planted, "art/textures/wall.kra", False) == [
        "art/textures/final.png"
    ]


def test_a_matching_project_scores_full_marks(planted: Path) -> None:
    for name in (
        "env/world.tres",
        "levels/level.tscn",
        "ui/theme.tres",
        "art/models/crate.glb",
    ):
        (planted / name).unlink()
    (planted / "art/textures/floor.png").write_bytes(png(128, 128))
    audit = run_audit(planted, FakeWorker(magenta=False, loud=False))
    assert {a.id: a.score for a in audit.areas if a.id != "sound"} == {
        "palette": 100,
        "textures": 100,
    }
    assert [f.path for f in audit.findings] == [
        "audio/sfx/imported.wav"
    ] or not audit.findings


def test_no_docker_is_reported_not_passed(planted: Path) -> None:
    async def broken(_argv: list[str], _root: Path) -> Result:
        raise OSError("Docker is not running")

    audit = asyncio.run(match.audit(planted, broken))
    assert any("Docker is not running" in e for e in audit.errors)
    assert "art/textures/wall.png" not in {f.path for f in audit.findings}


def test_area_filter_and_cli_json(
    planted: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def offline(argv: list[str], root: Path, *_a: Any, **_k: Any) -> Result:
        return await FakeWorker()(argv, root)

    monkeypatch.setattr("joinery.match_cli.run_offline", offline)
    assert (
        cli_main(
            ["match", "--project", str(planted), "--json", "--area", "sound", "--plan"]
        )
        == 0
    )
    data = json.loads(capsys.readouterr().out)
    assert [a["id"] for a in data["areas"]] == ["sound"]
    assert {f["area"] for f in data["findings"]} == {"sound"}
    assert [t["title"] for t in data["plan"]] == ["Normalise 1 sound to −16 LUFS"]
    assert cli_main(["match", "--project", str(planted)]) == 0
    text = capsys.readouterr().out
    assert text.startswith("Palette 50% · ")
    assert "1 need you: audio/sfx/imported.wav" in text


# --- Godot text ------------------------------------------------------------------------


def test_set_properties_replaces_and_adds_lines_in_one_section() -> None:
    patched = match_look.set_properties(
        LEVEL,
        match_look.Section("sub_resource", "Environment_sky"),
        {"adjustment_saturation": 1.1, "tonemap_exposure": 1.0, "fog_enabled": False},
    )
    assert (
        "adjustment_saturation = 1.1\ntonemap_exposure = 1.0\nfog_enabled = false\n\n[node"
        in patched
    )
    assert patched.count("adjustment_saturation") == 1
    with pytest.raises(ValueError, match="no longer"):
        match_look.set_properties(
            LEVEL, match_look.Section("sub_resource", "Gone"), {"a": 1}
        )
    swapped = match_look.swap_ext_resource(
        THEME, "res://ui/fonts/comic.ttf", "res://ui/fonts/body.ttf"
    )
    assert (
        '[ext_resource type="FontFile" path="res://ui/fonts/body.ttf" id="1_font"]'
        in swapped
    )


# --- Planning ------------------------------------------------------------------------


def test_plan_groups_fixes_by_file_area_and_app(planted: Path) -> None:
    audit = run_audit(planted)
    tasks = match_plan.plan(planted, audit)
    titles = [t.title for t in tasks]
    assert titles == [
        "Remap 1 texture to the palette",
        "Fix 1 model",
        "Resize 1 texture",
        "Match the lighting in 2 files",
        "Match 1 UI theme",
        "Normalise 1 sound to −16 LUFS",
    ]
    model = tasks[1]
    assert [s.tool for s in model.steps] == [
        "apply_scale",
        "set_pivot",
        "set_texel_density",
        "set_material_value",
    ]
    assert model.apps == ["blender"]
    assert model.targets == ["art/models/crate.glb", "art/sources/crate.blend"]
    lighting = tasks[3]
    tools = {
        s.arguments.get("resourcePath") or s.arguments.get("filePath"): s
        for s in lighting.steps
    }
    assert tools["env/world.tres"].tool == "manage_resource"
    assert tools["env/world.tres"].arguments["properties"] == {
        "tonemap_mode": 4,
        "tonemap_exposure": 1.1,
        "fog_enabled": False,
    }
    content = tools["levels/level.tscn"].arguments["content"]
    assert "adjustment_saturation = 1.1" in content and "1.5" not in content
    ui = tasks[4].steps
    # The font swap is a text patch, so the theme's own values join it: one writer.
    assert [s.tool for s in ui] == ["write_file"]
    theme = ui[0].arguments["content"]
    assert 'path="res://ui/fonts/body.ttf"' in theme
    assert "Label/font_sizes/font_size = 18" in theme
    assert "Label/colors/font_color = Color(0.1255, 0.1882, 0.251, 1)" in theme
    assert tasks[0].facts == ["1 texture on the palette"]
    assert tasks[-1].facts == ["−16 LUFS"]
    # Every task's files are its own: no two tasks claim the same file.
    claimed = [path for task in tasks for path in task.targets]
    assert len(claimed) == len(set(claimed))


def test_one_file_with_two_fixes_is_one_krita_call(planted: Path) -> None:
    (planted / "art/textures/wall.png").write_bytes(png(200, 200))
    tasks = match_plan.plan(planted, run_audit(planted))
    wall = next(t for t in tasks if "art/textures/wall.kra" in t.targets)
    assert len(wall.steps) == 1
    assert [op["op"] for op in wall.steps[0].arguments["ops"]] == [
        "resize",
        "palette_map",
    ]


def test_plan_follows_the_chosen_areas_and_findings(planted: Path) -> None:
    audit = run_audit(planted)
    assert [t.area for t in match_plan.plan(planted, audit, areas=["lighting"])] == [
        "lighting"
    ]
    one = next(f for f in audit.findings if f.check == "fog")
    only = match_plan.plan(planted, audit, findings=[one.id])
    assert len(only) == 1 and only[0].findings == [one.id]
    assert only[0].steps[0].arguments["properties"] == {"fog_enabled": False}


def test_large_areas_split_into_small_changes(planted: Path) -> None:
    for index in range(8):
        name = f"art/textures/tile{index}"
        (planted / f"{name}.kra").write_bytes(b"kra")
        (planted / f"{name}.png").write_bytes(png(256, 256))
        recipe(planted, f"tile{index}", "krita", [f"{name}.kra", f"{name}.png"])
    tasks = [
        t
        for t in match_plan.plan(planted, run_audit(planted))
        if t.area == "textures" and "Resize" in t.title
    ]
    assert [t.title for t in tasks] == ["Resize 6 textures", "Resize 3 textures"]


# --- Running the fixes ---------------------------------------------------------------


def test_a_scripted_task_makes_a_normal_reviewed_change(
    crew_project_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = crew_project_path
    test = CrewTest(project)
    from tests.test_crew import FakeConnection, ServerSpec

    async def connection(self: Any, server: str) -> FakeConnection:
        return FakeConnection(
            ServerSpec(name=server, command="x", source="first-party"), project
        )

    monkeypatch.setattr("joinery.gateway.ProjectGateway.connection", connection)

    async def go() -> None:
        crew = test.crew()
        task = await crew.start_scripted(
            "Match the lighting",
            "Match the lighting, as the art bible asks.",
            [
                ScriptedCall(
                    "joinery-sound",
                    "write",
                    {"path": str(project / "notes/env.txt"), "text": "agx\n"},
                    "Set tonemap_mode in Godot",
                )
            ],
            prompt=test.prompt(),
            targets=["notes/env.txt"],
            facts=["Tonemap → agx"],
        )
        assert task.origin == "match" and task.job is not None
        await task.job
        assert task.state == "in_review"
        preview = next(e for e in test.events if e["kind"] == "file_preview")
        assert preview["facts"] == ["Tonemap → agx"] and preview["origin"] == "joinery"
        steps = [e["text"] for e in test.events if e["kind"] == "progress"]
        assert "Set tonemap_mode in Godot…" in steps
        await crew.decide(task.transaction_id, True)
        assert (project / "notes/env.txt").read_text() == "agx\n"
        failing = await crew.start_scripted(
            "Broken",
            "Broken",
            [
                ScriptedCall(
                    "joinery-sound",
                    "missing",
                    {"path": str(project / "notes/x")},
                    "Nothing",
                )
            ],
            prompt=test.prompt(),
        )
        assert failing.job is not None
        await failing.job
        assert failing.state == "failed" and failing.message
        await crew.close()
        await test.gateway.close()

    asyncio.run(go())


class FakeTask:
    def __init__(self, title: str) -> None:
        self.id = title
        self.title = title
        self.state = "working"
        self.message = ""


class FakeCrew:
    """Tasks that work until released; counts how many ran at once."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.tasks: list[FakeTask] = []
        self.most = 0
        self.refuse: set[str] = set()

    def working(self) -> list[FakeTask]:
        return [t for t in self.tasks if t.state == "working"]

    async def start_scripted(
        self, title: str, _request: str, _calls: Any, **_kw: Any
    ) -> FakeTask:
        if title in self.refuse:
            raise ValueError("art/x is being changed by another task: Lamp.")
        task = FakeTask(title)
        self.tasks.append(task)
        self.most = max(self.most, len(self.working()))
        asyncio.get_running_loop().call_later(
            0.05, lambda: setattr(task, "state", "in_review")
        )
        return task

    async def stop(self, task_id: str) -> None:
        for task in self.tasks:
            if task.id == task_id:
                task.state = "stopped"


def test_fixes_run_as_parallel_tasks_within_the_limit(
    planted: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("bridge.match_runner.POLL", 0.01)
    crew = FakeCrew(limit=3)
    crew.refuse = {"Resize 1 texture"}
    events: list[dict[str, Any]] = []

    async def go() -> None:
        run = MatchRun(crew, planted, events.append, FakeWorker(), lambda: None)  # type: ignore[arg-type]
        run.start([], [])
        assert run.job is not None
        await run.job

    asyncio.run(go())
    assert crew.most == 2  # the chat's own request keeps one of the three
    assert len(crew.tasks) == 5
    states = [e["state"] for e in events]
    assert states[0] == "auditing" and states[-1] == "done"
    rows = {row["title"]: row for row in events[-1]["tasks"]}
    assert rows["Resize 1 texture"]["state"] == "failed"
    assert "being changed by another task" in rows["Resize 1 texture"]["message"]
    assert rows["Match 1 UI theme"]["state"] == "in_review"
    assert "audit" in next(e for e in events if e["state"] == "fixing")


def test_one_turn_at_a_time_still_runs_the_fixes_one_by_one(
    planted: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("bridge.match_runner.POLL", 0.01)
    crew = FakeCrew(limit=1)

    async def go() -> None:
        run = MatchRun(crew, planted, lambda _e: None, FakeWorker(), lambda: None)  # type: ignore[arg-type]
        run.start(["lighting", "sound"], [])
        with pytest.raises(ValueError, match="already"):
            run.start([], [])
        assert run.job is not None
        await run.job

    asyncio.run(go())
    assert crew.most == 1
    assert [t.title for t in crew.tasks] == [
        "Match the lighting in 2 files",
        "Normalise 1 sound to −16 LUFS",
    ]


# --- The typed operations the fixes use ----------------------------------------------


def test_krita_palette_map_and_resize_are_checked_before_krita_starts() -> None:
    from joinery.tools import krita_ops

    ops = krita_ops.check_ops(
        [
            {"op": "resize", "width": 128, "height": 64},
            {"op": "palette_map", "colours": ["#203040", "#C08040"], "max_delta_e": 10},
        ],
        krita_ops.IMAGE_OPS,
    )
    assert ops[0] == {"op": "resize", "name": "Resize", "width": 128, "height": 64}
    assert (
        ops[1]["colours"] == ["#203040", "#c08040"] and ops[1]["name"] == "Palette map"
    )
    code = krita_ops.paint_code(ops)
    compile(code, "krita", "exec")
    assert "def delta_e_2000" in code
    assert code.index("_joinery_resize(document, 128, 64)") < code.index(
        "_joinery_palette_map(document, 'Palette map'"
    )
    # A model's map keeps its size: resize is for paintings of their own.
    with pytest.raises(ValueError, match="op is one of"):
        krita_ops.check_ops([{"op": "resize", "width": 4, "height": 4}])
    for bad in (
        {"op": "palette_map", "colours": ["red"]},
        {"op": "palette_map", "colours": []},
        {"op": "resize", "width": 0, "height": 4},
        {"op": "resize", "width": 4.5, "height": 4},
    ):
        with pytest.raises(ValueError):
            krita_ops.check_ops([bad], krita_ops.IMAGE_OPS)


def test_krita_palette_map_matches_the_bible_measure() -> None:
    """The helper Krita runs, run here on bytes: off colours snap, near ones stay."""
    from joinery.tools import krita_ops

    class Document:
        def __init__(self) -> None:
            # B, G, R, A: magenta (far), a near-palette blue, a transparent pixel.
            self.data = bytes([255, 0, 255, 255, 66, 50, 34, 255, 0, 0, 0, 0])
            self.layers: list[bytes] = []

        def width(self) -> int:
            return 3

        def height(self) -> int:
            return 1

        def refreshProjection(self) -> None:
            return None

        def waitForDone(self) -> None:
            return None

        def pixelData(self, *_args: int) -> bytes:
            return self.data

        def createNode(self, _name: str, _kind: str) -> Any:
            document = self

            class Layer:
                def setPixelData(self, data: bytes, *_a: int) -> None:
                    document.layers.append(data)

            return Layer()

    scope: dict[str, Any] = {"_joinery_add": lambda *_a: None}
    helpers = krita_ops.COLOUR_MATHS + "\n" + krita_ops.PALETTE_HELPERS
    exec(helpers, scope)  # noqa: S102 - the very code Krita runs
    document = Document()
    scope["_joinery_palette_map"](
        document, "Palette map", ["#203040", "#c08040"], 12.0, 1.0
    )
    layer = document.layers[0]
    assert layer[0:4] == bytes([0x40, 0x30, 0x20, 255])  # magenta -> #203040
    assert layer[4:8] == bytes(4)  # already near the palette: left to show through
    assert layer[8:12] == bytes(4)


def test_blender_model_operations_are_typed_and_scope_guarded() -> None:
    from joinery.tools import blender_model_ops as model

    for code in (
        model.apply_scale_code(["Crate"]),
        model.set_pivot_code("bottom-centre"),
        model.reduce_triangles_code(500),
        model.texel_density_code(["Crate"], 256, True),
    ):
        compile(code, "blender", "exec")
        assert "_joinery_scope_check(" in code
    assert "set(['object:Crate'])" in model.apply_scale_code(["Crate"])
    with pytest.raises(ValueError):
        model.set_pivot_code("top")
    with pytest.raises(ValueError):
        model.reduce_triangles_code(2)
    with pytest.raises(ValueError):
        model.texel_density_code(["Crate"], 0, False)


def test_edit_image_refuses_anything_but_a_krita_painting(tmp_path: Path) -> None:
    from servers.paint import ops

    with pytest.raises(ValueError, match="Krita document"):
        asyncio.run(
            ops.edit_image(tmp_path, "art/a.png", [{"op": "fill", "color": [0, 0, 0]}])
        )
    with pytest.raises(ValueError, match="missing"):
        asyncio.run(
            ops.edit_image(tmp_path, "art/a.kra", [{"op": "fill", "color": [0, 0, 0]}])
        )
    recipe(tmp_path, "a", "krita", ["art/a.kra", "art/a.png", "art/a.webp"])
    assert ops.recorded_exports(tmp_path, "art/a.kra") == ["art/a.png"]


@pytest.fixture
def crew_project_path(crew_project: Path) -> Path:  # noqa: F811
    return crew_project
