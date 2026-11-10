import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from joinery import readiness
from joinery.catalog import (
    Catalog,
    CustomTool,
    ProjectTools,
    load_catalog,
    load_project_tools,
)
from joinery.cli import check_mcp_config, main
from joinery.installer import (
    STATUS_LABELS,
    Selection,
    custom_choices,
    menu_tools,
    run_menu,
    tool_menu,
    validate_choice,
    write_mcp_config,
    write_selection,
)
from tests.test_readiness import CREATIVE_WORKER, FakeHost

SDXL = CustomTool(
    id="sdxl", name="SDXL", domain="2d", server="sdxl", command="sdxl-mcp"
)


def number(tool_id: str, catalog: Catalog | None = None) -> str:
    ids = [t.id for t in menu_tools(catalog or load_catalog())]
    return str(ids.index(tool_id) + 1)


def drive(
    answers: list[str], current: list[str], catalog: Catalog | None = None
) -> tuple[Selection, list[str]]:
    shown: list[str] = []
    pending = iter(answers)
    result = run_menu(
        catalog or load_catalog(), current, (), lambda _: next(pending), shown.append
    )
    return result, shown


def with_planned_tool() -> Catalog:
    """The shipped catalog plus a tool whose own MCP is still being built."""
    raw = load_catalog().model_dump(mode="python")
    raw["tool"] = [
        *raw["tool"],
        {
            "id": "future-synth",
            "name": "Future Synth",
            "domain": "audio",
            "status": "first-party-planned",
        },
    ]
    return Catalog.model_validate(raw)


def test_toggles_tools() -> None:
    result, _ = drive([f"{number('krita')} {number('blender')}", ""], ["blender"])
    assert result.tools == ["godot", "krita"]


def test_required_tool_cannot_be_removed() -> None:
    result, shown = drive([number("godot"), ""], [])
    assert result.tools == ["godot"]
    assert any("required" in line for line in shown)


def test_preferred_tool_without_mcp_cannot_be_selected() -> None:
    catalog = with_planned_tool()
    result, shown = drive([number("future-synth", catalog), ""], [], catalog)
    assert result.tools == ["godot"]
    assert any("own MCP in progress" in line for line in shown)


def test_user_can_add_own_program_with_own_mcp() -> None:
    answers = ["a", "My Painter", "2d", "painter", "painter-mcp", "--port 9", ""]
    result, shown = drive(answers, [])
    assert result.custom[0].id == "my-painter"
    assert result.custom[0].args == ("--port", "9")
    assert any("My Painter" in line for line in shown)


def test_invalid_custom_tool_is_refused() -> None:
    result, shown = drive(["a", "X", "2d", "blender", "x", "", ""], [])
    assert result.custom == []
    assert any("Not added" in line for line in shown)


def test_bad_input_is_ignored() -> None:
    result, shown = drive(["99 abc", ""], [])
    assert result.tools == ["godot"]
    assert any("Unknown choice" in line for line in shown)


def test_noninteractive_rejects_unavailable_tool() -> None:
    with pytest.raises(ValueError, match="future-synth"):
        validate_choice(with_planned_tool(), ["future-synth"])


def test_write_selection_keeps_other_settings(tmp_path: Path) -> None:
    config = tmp_path / "joinery.toml"
    config.write_text('[audio]\nx = 1\n\n[tools]\nenabled = [\n  "a",\n]\n')
    write_selection(Selection(["blender", "godot"], []), config)
    assert (
        config.read_text()
        == '[audio]\nx = 1\n\n[tools]\nenabled = ["blender", "godot"]\n'
    )


def test_write_selection_adds_section(tmp_path: Path) -> None:
    config = tmp_path / "joinery.toml"
    config.write_text("[audio]\nx = 1\n")
    write_selection(Selection(["godot"], []), config)
    assert '[tools]\nenabled = ["godot"]' in config.read_text()


def test_write_selection_starts_a_new_file_without_blank_lines(tmp_path: Path) -> None:
    config = tmp_path / "joinery.toml"
    write_selection(Selection(["godot"], []), config)
    assert config.read_text() == '[tools]\nenabled = ["godot"]\n'


def test_custom_tools_round_trip_and_replace(tmp_path: Path) -> None:
    config = tmp_path / "joinery.toml"
    other = CustomTool(
        id="o", name="O", domain="3d", server="o", command="o", args=("a b",)
    )
    write_selection(Selection(["godot"], [SDXL, other]), config)
    assert load_project_tools(config).custom == (SDXL, other)
    write_selection(Selection(["godot"], [other]), config)
    assert load_project_tools(config).custom == (other,)
    assert load_project_tools(config).enabled == ("godot",)


def test_mcp_config_registers_custom_and_keeps_existing(tmp_path: Path) -> None:
    (tmp_path / ".mcp.json").write_text('{"mcpServers": {"godot": {"command": "g"}}}')
    write_mcp_config(Selection(["godot"], [SDXL]), tmp_path)
    text = (tmp_path / ".mcp.json").read_text()
    assert '"godot"' in text and '"sdxl-mcp"' in text


def test_nonpreferred_choices_are_custom_and_selectable() -> None:
    menu = tool_menu(load_catalog(), ProjectTools())
    tools = menu["tools"]
    assert isinstance(tools, list)
    godot = next(tool for tool in tools if tool["id"] == "godot")
    alternatives = [o for o in godot["mcp_options"] if o["state"] == "alternative"]
    assert {o["label"] for o in alternatives} == {
        "hi-godot/godot-ai",
        "tomyud1/godot-mcp",
    }
    assert all(o["usable"] and o["custom"]["domain"] == "engine" for o in alternatives)
    assert all(not o["custom"]["command"] for o in alternatives)
    assert all(o["custom"]["server"] != "godot" for o in alternatives)
    assert godot["mcp_options"][0]["state"] == "chosen"
    krita = next(tool for tool in tools if tool["id"] == "krita")
    assert krita["mcp_options"][0]["label"] == "joinery-paint"
    assert krita["available"]
    assert not krita["enabled"]


def test_setup_saves_custom_alternative_and_replaces_required_engine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    choice = {
        "id": "hi-godot-godot-ai",
        "name": "Godot (hi-godot/godot-ai)",
        "domain": "engine",
        "server": "hi-godot-godot-ai",
        "command": "/Applications/My MCP/server",
        "args": ["--name", "a b"],
    }
    assert (
        _cli(
            [
                "setup",
                "--project",
                str(tmp_path),
                "--tools",
                "blender",
                "--custom-mcps",
                json.dumps([choice]),
            ],
            monkeypatch,
        )
        == 0
    )
    config = tmp_path / "joinery.toml"
    selected = load_project_tools(config)
    assert selected.enabled == ("blender",)
    assert selected.custom[0].command == choice["command"]
    assert selected.custom[0].args == ("--name", "a b")
    assert check_mcp_config(tmp_path, config) == []
    registered = json.loads((tmp_path / ".mcp.json").read_text())["mcpServers"]
    assert registered[choice["server"]] == {
        "command": choice["command"],
        "args": choice["args"],
    }


@pytest.mark.parametrize("payload", ["{}", '[{"id":"x"}]', "[false]"])
def test_bad_custom_choices_leave_existing_config_untouched(
    tmp_path: Path,
    payload: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = tmp_path / "joinery.toml"
    original = '[tools]\nenabled = ["godot"]\n'
    config.write_text(original)
    assert (
        _cli(
            [
                "setup",
                "--project",
                str(tmp_path),
                "--tools",
                "",
                "--custom-mcps",
                payload,
            ],
            monkeypatch,
        )
        == 1
    )
    assert config.read_text() == original
    assert not (tmp_path / ".mcp.json").exists()


def test_blank_custom_command_is_not_saved() -> None:
    tool = SDXL.model_dump()
    tool["command"] = " "
    with pytest.raises(ValueError, match="launch command"):
        custom_choices(json.dumps([tool]), [])


def test_custom_choices_file_keeps_executable_and_arguments_literal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    choice = SDXL.model_dump()
    choice["command"] = '/Applications/Tool "quoted"/mcp'
    choice["args"] = ["$(touch NEVER_EXECUTE)", '"quoted"', "a b", "\n"]
    choices_file = tmp_path / "choices.json"
    choices_file.write_text(json.dumps([choice]))
    assert (
        _cli(
            [
                "setup",
                "--project",
                str(tmp_path),
                "--tools",
                "",
                "--custom-mcps-file",
                str(choices_file),
            ],
            monkeypatch,
        )
        == 0
    )
    custom = load_project_tools(tmp_path / "joinery.toml").custom[0]
    assert custom.command == choice["command"]
    assert list(custom.args) == choice["args"]
    assert not (tmp_path / "NEVER_EXECUTE").exists()


def test_missing_choices_file_leaves_project_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert (
        _cli(
            [
                "setup",
                "--project",
                str(tmp_path),
                "--tools",
                "",
                "--custom-mcps-file",
                str(tmp_path / "missing.json"),
            ],
            monkeypatch,
        )
        == 1
    )
    assert not (tmp_path / "joinery.toml").exists()


def _cli(arguments: list[str], monkeypatch: pytest.MonkeyPatch) -> int:
    monkeypatch.setattr(sys, "argv", ["joinery", *arguments])
    return main()


@pytest.fixture(autouse=True)
def no_real_docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeHost:
    """Every menu in these tests reads a fake host and a private cache."""
    monkeypatch.setenv("JOINERY_READINESS_CACHE", str(tmp_path / "readiness-cache"))
    engine = tmp_path / "godot.macos.editor.arm64"
    engine.write_text("editor")
    host = FakeHost(engine=engine)
    monkeypatch.setattr(readiness, "DockerHost", lambda: host)
    monkeypatch.setattr(readiness, "spawn_refresh", lambda *_a, **_k: False)
    return host


def _menu(host: FakeHost, catalog: Catalog | None = None) -> dict[str, dict[str, Any]]:
    tools = tool_menu(catalog or load_catalog(), ProjectTools(), host=host)["tools"]
    assert isinstance(tools, list)
    return {str(tool["id"]): tool for tool in tools}


def test_every_tool_carries_setup_copy_and_readiness(no_real_docker: FakeHost) -> None:
    readiness.status(catalog=True, refresh=True, host=no_real_docker)
    tools = _menu(no_real_docker)
    for tool in tools.values():
        assert tool["status"] in STATUS_LABELS
        assert tool["status_label"] == STATUS_LABELS[str(tool["status"])]
        assert tool["group"] in {"engine", "3d", "2d", "audio"}
        assert tool["blurb"], tool["id"]
        assert tool["get_url"] is None or str(tool["get_url"]).startswith("https://")
    assert tools["blender"]["blurb"] == "3D models and materials"
    assert tools["material-maker"]["group"] == "3d"


def test_menu_has_one_action_and_a_checked_time(no_real_docker: FakeHost) -> None:
    readiness.status(catalog=True, refresh=True, host=no_real_docker)
    tools = _menu(no_real_docker)
    assert tools["blender"]["status"] == "ready"
    assert tools["blender"]["status_reason"] == ""
    assert tools["blender"]["action"] is None
    assert str(tools["blender"]["checked_at"]).endswith("+00:00")
    assert "summary" in tools["blender"] and "features" in tools["blender"]
    no_real_docker.docker_reason = "Docker is not running."
    tools = _menu(no_real_docker)
    # A fresh snapshot still answers: nothing asked Docker again.
    assert tools["blender"]["status"] == "ready"


def test_missing_worker_image_offers_a_rebuild(no_real_docker: FakeHost) -> None:
    del no_real_docker.images[CREATIVE_WORKER]
    tools = _menu(no_real_docker)
    assert tools["krita"]["status"] == "unavailable"
    assert tools["krita"]["action"] == {"id": "rebuild", "label": "Rebuild"}
    assert tools["krita"]["status_reason"]


def test_setup_building_a_worker_is_preparing(no_real_docker: FakeHost) -> None:
    no_real_docker.setup_data = {"phase": "build", "image": CREATIVE_WORKER, "pid": 1}
    tools = _menu(no_real_docker)
    assert tools["krita"]["status"] == "preparing"
    assert tools["krita"]["status_label"] == "Downloading…"
    assert tools["krita"]["action"] is None


def test_unreachable_docker_is_named(no_real_docker: FakeHost) -> None:
    no_real_docker.docker_reason = "Docker is not running."
    tools = _menu(no_real_docker)
    assert tools["blender"]["status"] == "needs_docker"
    assert "Docker" in str(tools["blender"]["status_reason"])
    assert tools["blender"]["action"] == {"id": "start_docker", "label": "Start Docker"}


def test_no_docker_subprocess_while_the_snapshot_is_fresh(
    no_real_docker: FakeHost, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The real host this time: a fresh snapshot must answer without any process.
    monkeypatch.setattr(readiness, "DockerHost", readiness.DockerHost)
    readiness.status(catalog=True, refresh=True, host=no_real_docker)
    started: list[object] = []

    def refuse(*args: object, **kwargs: object) -> None:
        started.append(args)
        raise AssertionError("no subprocess while the cache is fresh")

    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(readiness.subprocess, "run", refuse)
    monkeypatch.setattr(readiness.subprocess, "Popen", refuse)
    tools = _menu_live()
    assert started == []
    assert tools["krita"]["status"] == "ready"


def _menu_live() -> dict[str, dict[str, Any]]:
    tools = tool_menu(load_catalog(), ProjectTools())["tools"]
    assert isinstance(tools, list)
    return {str(tool["id"]): tool for tool in tools}


def test_stale_snapshot_asks_again_and_refreshes_in_the_background(
    no_real_docker: FakeHost, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawned: list[object] = []
    monkeypatch.setattr(
        readiness, "spawn_refresh", lambda *a, **k: spawned.append(a) or True
    )
    # Nothing cached yet: the cheap checks run now, the rest in the background.
    tools = _menu(no_real_docker)
    assert tools["krita"]["checking"] is True
    assert spawned


def test_paid_tools_need_a_licence(no_real_docker: FakeHost) -> None:
    # The shipped catalog has no paid app today; the rule still has to hold
    # for one added later.
    raw = load_catalog().model_dump(mode="python")
    for tool in raw["tool"]:
        if tool["id"] == "krita":
            tool["paid"] = True
    tools = _menu(no_real_docker, Catalog.model_validate(raw))
    assert tools["krita"]["status"] == "needs_licence"
    assert "licence" in str(tools["krita"]["status_reason"])
    assert tools["krita"]["action"] == {"id": "get", "label": "Get it"}


def test_planned_tool_is_unavailable(no_real_docker: FakeHost) -> None:
    tools = _menu(no_real_docker, with_planned_tool())
    assert tools["future-synth"]["status"] == "unavailable"
    assert tools["future-synth"]["status_reason"]
    assert tools["future-synth"]["action"] is None


def test_chosen_connector_has_no_summary(no_real_docker: FakeHost) -> None:
    tools = _menu(no_real_docker)
    for tool in tools.values():
        options = tool["mcp_options"]
        assert isinstance(options, list)
        for option in options:
            if option["state"] == "chosen":
                assert option["summary"] == ""
