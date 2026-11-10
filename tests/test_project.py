import base64
import json
import os
import shlex
from pathlib import Path

import pytest

from joinery.catalog import ROOT, CustomTool, enabled_tools, load_catalog
from joinery.deps import check_deps
from joinery.installer import Selection
from joinery.lockfile import check_assets
from joinery.policy import load_denylist
from joinery.project import create_project


def test_new_project_is_plain_godot_with_joinery_files(tmp_path: Path) -> None:
    project = tmp_path / "game"
    catalog = load_catalog()
    create_project(project, "My Game", Selection(["blender", "godot"], []), catalog)
    godot = (project / "project.godot").read_text()
    assert 'config/name="My Game"' in godot and "{{" not in godot
    assert enabled_tools(catalog, project / "joinery.toml") == ["blender", "godot"]
    assert check_assets(project, catalog) == []
    assert not (project / ".mcp.json").exists()
    assert check_deps(project, load_denylist()) == []


def test_new_project_inherits_hook_and_telemetry_off(tmp_path: Path) -> None:
    project = tmp_path / "game"
    create_project(project, "G", Selection(["godot"], []), load_catalog())
    settings = json.loads((project / ".claude/settings.json").read_text())
    assert settings["env"]["DISABLE_TELEMETRY"] == "1"
    hook = settings["hooks"]["PreToolUse"][0]["hooks"][0]
    assert hook["type"] == "command"
    command = hook["command"]
    if os.name == "nt":
        prefix = "powershell.exe -NoProfile -NonInteractive -EncodedCommand "
        assert command.startswith(prefix)
        script = base64.b64decode(command[len(prefix) :]).decode("utf-16-le")
        assert "$info.FileName='uv'" in script
        assert "'joinery', 'hook', '--agent', 'claude'" in script
        engine = str(ROOT).replace("'", "''")
        assert "'--project', '" + engine + "'" in script
        assert "exit $process.ExitCode" in script
    else:
        assert shlex.split(command) == [
            "uv",
            "run",
            "--frozen",
            "--quiet",
            "--project",
            str(ROOT),
            "joinery",
            "hook",
            "--agent",
            "claude",
        ]


def test_new_project_wires_the_gate_for_every_agent(tmp_path: Path) -> None:
    project = tmp_path / "game"
    create_project(project, "G", Selection(["godot"], []), load_catalog())
    # Rule Zero must hold whichever agent the user runs in the project.
    for path in (
        ".claude/settings.json",
        ".codex/hooks.json",
        ".gemini/settings.json",
        ".opencode/plugin/joinery-gate.js",
    ):
        assert (project / path).is_file(), path


def test_custom_tool_lands_in_mcp_config(tmp_path: Path) -> None:
    tool = CustomTool(id="x", name="X", domain="2d", server="x", command="x-mcp")
    project = tmp_path / "game"
    create_project(project, "G", Selection(["godot"], [tool]), load_catalog())
    assert (
        json.loads((project / ".mcp.json").read_text())["mcpServers"]["x"]["command"]
        == "x-mcp"
    )


def test_refuses_non_empty_folder(tmp_path: Path) -> None:
    (tmp_path / "keep.txt").write_text("x")
    with pytest.raises(ValueError, match="not empty"):
        create_project(tmp_path, "G", Selection(["godot"], []), load_catalog())
    assert (tmp_path / "keep.txt").read_text() == "x"


def test_new_project_has_design_and_reference_folders(tmp_path: Path) -> None:
    project = tmp_path / "game"
    create_project(project, "G", Selection(["godot"], []), load_catalog())
    assert (project / "design").is_dir()
    # Reference boards stay out of Godot's import and every export.
    assert (project / "art/reference/.gdignore").is_file()
    assert "[game]" not in (project / "joinery.toml").read_text()
    assert check_assets(project, load_catalog()) == []


def test_new_project_from_blueprint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from joinery import blueprints
    from tests.blueprints.conftest import build_catalogue

    catalogue = build_catalogue(tmp_path / "catalogue")
    monkeypatch.setattr(blueprints, "BLUEPRINTS_DIR", catalogue.blueprints)
    monkeypatch.setattr(blueprints, "FEATURES_DIR", catalogue.features)
    project = tmp_path / "game"
    create_project(
        project, "G", Selection(["godot"], []), load_catalog(),
        blueprint="alpha", features=["controller"],
    )
    assert (project / "game/level.tscn").is_file()
    assert 'run/main_scene="res://core/main.tscn"' in (project / "project.godot").read_text()
    assert blueprints.read_record(project) == blueprints.GameRecord(
        blueprint="alpha", blueprint_version=1,
        features=["controller"], feature_versions={"controller": 1},
    )
    assert check_assets(project, load_catalog()) == []
    with pytest.raises(ValueError, match="Unknown blueprint: nope"):
        create_project(tmp_path / "other", "G", Selection(["godot"], []), load_catalog(), blueprint="nope")
    assert not (tmp_path / "other").exists()
    with pytest.raises(ValueError, match="need a blueprint"):
        create_project(tmp_path / "third", "G", Selection(["godot"], []), load_catalog(), features=["net"])


def _cli_new(tmp_path: Path, name: str, *args: str) -> tuple[int, Path]:
    from joinery.cli import main

    project = tmp_path / name
    return main(["new", str(project), "--tools", "godot", *args]), project


def test_new_blank_blueprint_is_the_wizard_project(tmp_path: Path) -> None:
    code, project = _cli_new(tmp_path, "blank", "--blueprint", "blank", "--features", "")
    assert code == 0
    # The addon ships inside the game, so its MIT licence goes with it.
    licence = (project / "addons/joinery/LICENSE").read_text()
    assert licence.startswith("MIT License")
    assert licence == (ROOT / "godot_addon/joinery/LICENSE").read_text()
    assert (project / "addons/joinery/plugin.cfg").is_file()
    godot = (project / "project.godot").read_text()
    assert 'enabled=PackedStringArray("res://addons/joinery/plugin.cfg")' in godot
    brief = (project / "design/brief.md").read_text()
    assert "| Pause (`pause`) | Esc / P | — | — |" in brief
    assert "{{" not in brief
    assert (project / "art/bible.md").is_file()
    assert check_assets(project, load_catalog()) == []


def test_new_first_person_with_features(tmp_path: Path) -> None:
    from joinery import blueprints

    code, project = _cli_new(
        tmp_path,
        "fp",
        "--blueprint",
        "first-person-3d",
        "--features",
        "controller,save-load",
    )
    assert code == 0
    assert (project / "addons/joinery/LICENSE").read_text().startswith("MIT License")
    record = blueprints.read_record(project)
    assert record is not None
    assert record.blueprint == "first-person-3d"
    assert record.features == ["controller", "save-load"]
    # Controls come from the applied input map, controller buttons included.
    brief = (project / "design/brief.md").read_text()
    assert "| Jump (`jump`) | Space | A | — |" in brief
    assert "| Pause (`pause`) | Esc / P | Start | — |" in brief
    # The blueprint's bible preset filled the technical sections as suggestions.
    bible = (project / "art/bible.md").read_text()
    assert "_Suggested by the Stylised PBR 3D preset for this starting point; change freely._" in bible
    assert "{{" not in bible
    assert check_assets(project, load_catalog()) == []


def test_new_without_features_flag_uses_the_blueprint_defaults(tmp_path: Path) -> None:
    from joinery import blueprints

    code, project = _cli_new(tmp_path, "td", "--blueprint", "top-down")
    assert code == 0
    record = blueprints.read_record(project)
    expected = blueprints.describe("top-down")["default_features"]
    assert record is not None and record.features == expected


@pytest.mark.parametrize(
    "features", ["controller,nope", "online-play,steam,translations,unknown"]
)
def test_bad_feature_mix_leaves_no_folder(
    tmp_path: Path, features: str, capsys: pytest.CaptureFixture[str]
) -> None:
    code, project = _cli_new(tmp_path, "bad", "--blueprint", "blank", "--features", features)
    assert code == 1
    assert "Unknown feature" in capsys.readouterr().err
    assert not project.exists()


def test_failed_apply_removes_the_half_made_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from joinery import blueprints

    def refuse(*_: object, **__: object) -> None:
        raise ValueError("game/level.tscn already exists")

    monkeypatch.setattr(blueprints, "apply", refuse)
    with pytest.raises(ValueError, match="already exists"):
        create_project(
            tmp_path / "half", "G", Selection(["godot"], []), load_catalog(), blueprint="blank"
        )
    assert not (tmp_path / "half").exists()


def test_new_without_blueprint_stays_empty_but_has_the_addon(tmp_path: Path) -> None:
    code, project = _cli_new(tmp_path, "empty")
    assert code == 0
    assert (project / "addons/joinery/LICENSE").is_file()
    assert not (project / "game").exists()
    assert not (project / "design/brief.md").exists()
    assert "[game]" not in (project / "joinery.toml").read_text()


def test_new_writes_the_history_choice(tmp_path: Path) -> None:
    from joinery.transactions import HistoryPolicy, load_policy

    code, project = _cli_new(tmp_path, "small", "--history", "keep-10")
    assert code == 0
    assert load_policy(project) == HistoryPolicy(keep=10, max_mb=1024)
    code, project = _cli_new(
        tmp_path, "own", "--history", "custom", "--history-keep", "3", "--history-max-mb", "300"
    )
    assert code == 0
    assert load_policy(project) == HistoryPolicy(keep=3, max_mb=300)
    # Without a choice the defaults apply, and joinery.toml doesn't restate them.
    code, project = _cli_new(tmp_path, "plain")
    assert code == 0
    assert "[history]" not in (project / "joinery.toml").read_text()
    assert load_policy(project).preset == "keep-50"
    # A bad choice stops before anything is created.
    code, project = _cli_new(tmp_path, "bad", "--history", "custom")
    assert code == 1
    assert not project.exists()
