"""Godot's status tells the truth: its worker image, Docker and "Open in".

The pinned install itself is covered by tests/test_godot_worker.py.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest

from joinery import desktop, server_install
from joinery.mcp_runtime import Readiness, ServerSpec
from joinery.workspace import index, open_asset

# --- inspect_servers checks the worker image Godot runs in --------------------


class FakeConnection:
    """Stands in for an MCP connection; records whether anything started."""

    started: ClassVar[list[str]] = []
    is_error: ClassVar[bool] = False

    def __init__(self, spec: ServerSpec, project: Path) -> None:
        self.spec = spec
        self.readiness = Readiness(name=spec.name, installed=True)

    async def connect(self) -> None:
        FakeConnection.started.append(self.spec.name)
        self.readiness.state = "discovered"
        self.readiness.connected = True
        self.readiness.tools = ["get_project_info", "create_scene", "add_node"]

    async def probe(self, tool: str, arguments: dict[str, Any]) -> Any:
        if not FakeConnection.is_error:
            self.readiness.inspected = True
        return SimpleNamespace(
            is_error=FakeConnection.is_error,
            content=[SimpleNamespace(text="project.godot not found")],
        )

    async def close(self) -> None:
        return None


@pytest.fixture
def servers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    project = tmp_path / "game"
    project.mkdir()
    specs = [
        ServerSpec(
            name="godot",
            command="docker",
            args=["run", "joinery-mcp-worker"],
            worker_image="joinery-mcp-worker",
        ),
        ServerSpec(name="joinery-registry", command="python", source="first-party"),
    ]
    monkeypatch.setattr(server_install, "configured", lambda _p: (specs, []))
    monkeypatch.setattr(server_install, "MCPConnection", FakeConnection)
    FakeConnection.started = []
    FakeConnection.is_error = False
    return project


def inspect(project: Path, *, probe: bool = False) -> dict[str, dict[str, Any]]:
    result = asyncio.run(server_install.inspect_servers(project, probe))
    return {item["name"]: item for item in result}


def test_godot_without_its_worker_image_is_runtime_missing(
    servers: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    images: list[str] = []

    async def absent(image: str) -> bool:
        images.append(image)
        return False

    async def running() -> str:
        return ""

    monkeypatch.setattr(server_install, "_image_present", absent)
    monkeypatch.setattr(server_install, "_docker_problem", running)
    found = inspect(servers)
    assert images == ["joinery-mcp-worker"]
    assert found["godot"]["state"] == "runtime-missing"
    assert (
        found["godot"]["summary"] == "Godot tools need a refresh before they can work."
    )
    assert "godot" not in FakeConnection.started
    # The registry has no worker; it is ready as soon as it connects.
    assert found["joinery-registry"]["summary"] == "Project registry: ready"


def test_godot_without_docker_says_needs_docker(
    servers: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def absent(image: str) -> bool:
        return False

    async def stopped() -> str:
        return "Docker is not running."

    monkeypatch.setattr(server_install, "_image_present", absent)
    monkeypatch.setattr(server_install, "_docker_problem", stopped)
    found = inspect(servers, probe=True)
    assert found["godot"]["state"] == "runtime-missing"
    assert found["godot"]["detail"].startswith("Needs Docker: Docker is not running.")
    assert (
        found["godot"]["summary"]
        == "Godot tools: Needs Docker. Start Docker, then check again."
    )
    assert "godot" not in FakeConnection.started


def test_godot_ready_names_its_operations_and_the_worker(
    servers: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def present(image: str) -> bool:
        return True

    monkeypatch.setattr(server_install, "_image_present", present)
    found = inspect(servers, probe=True)
    assert found["godot"]["state"] == "discovered" and found["godot"]["inspected"]
    assert (
        found["godot"]["summary"] == "Godot tools ready (3 operations, runs offline)"
    )


def test_a_probe_that_returns_an_error_is_not_inspected(
    servers: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def present(image: str) -> bool:
        return True

    monkeypatch.setattr(server_install, "_image_present", present)
    FakeConnection.is_error = True
    found = inspect(servers, probe=True)
    assert found["godot"]["state"] == "error"
    assert "project.godot not found" in found["godot"]["summary"]


def test_status_without_probe_only_connects(
    servers: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def present(image: str) -> bool:
        return True

    monkeypatch.setattr(server_install, "_image_present", present)
    found = inspect(servers)["godot"]
    assert found["state"] == "discovered" and not found["inspected"]


def test_describe_wording() -> None:
    assert (
        server_install.describe({"name": "joinery-verify", "state": "discovered"})
        == "Checks: ready"
    )
    assert server_install.describe(
        {"name": "joinery-registry", "state": "error", "detail": "boom"}
    ) == ("Project registry: error: boom")
    assert server_install.describe(
        {"name": "godot", "state": "not-installed", "detail": "no recorded source hash"}
    ).startswith("Godot tools need a refresh before they can work.")


# --- "Open in Godot" is this editor -----------------------------------------------


@pytest.fixture
def editor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    binary = tmp_path / "bin" / "godot.macos.editor.arm64"
    binary.parent.mkdir()
    binary.write_text("editor")
    monkeypatch.setenv("JOINERY_GODOT_BIN", str(binary))
    monkeypatch.setattr(desktop.shutil, "which", lambda _name: None)
    return binary.resolve()


def test_open_in_godot_resolves_to_this_editor(editor: Path) -> None:
    assert desktop.launcher("godot", {}) == [str(editor)]


def test_a_configured_godot_still_wins(editor: Path, tmp_path: Path) -> None:
    app = tmp_path / "Stock.app"
    app.mkdir()
    override = desktop.DesktopOverride(command=str(app))
    if desktop.sys.platform == "darwin":
        assert desktop.launcher("godot", {"godot": override})[-1] == str(app)
    else:
        with pytest.raises(ValueError, match="Configured application"):
            desktop.launcher("godot", {"godot": override})


def test_no_editor_build_and_no_godot_is_reported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(desktop, "this_editor", lambda: None)
    monkeypatch.setattr(desktop.shutil, "which", lambda _name: None)
    monkeypatch.setattr(desktop.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(desktop.sys, "platform", "linux")
    with pytest.raises(ValueError, match="Application not found"):
        desktop.launcher("godot", {})


def test_workspace_offers_godot_and_opens_scenes_in_this_editor(
    editor: Path, tmp_path: Path
) -> None:
    project = tmp_path / "game"
    project.mkdir()
    (project / "joinery.toml").write_text('[tools]\nenabled = ["godot"]\n')
    (project / "project.godot").write_text("config_version=5\n")
    (project / "art").mkdir()
    (project / "art/prop.tscn").write_text(
        '[gd_scene format=3]\n[node name="Prop" type="Node"]\n'
    )
    data = index(project)
    godot = next(tool for tool in data["tools"] if tool["id"] == "godot")
    assert godot["available"] and godot["reason"] == ""
    argv = open_asset(project, "art/prop.tscn", "godot", dry_run=True)
    assert argv[0] == str(editor)
    assert argv[1:4] == ["--editor", "--path", str(project.resolve())]
