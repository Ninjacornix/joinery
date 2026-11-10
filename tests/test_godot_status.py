"""Godot's status tells the truth: worker image, pinned install integrity and "Open in"."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest

from joinery import desktop, server_install
from joinery.catalog import load_catalog
from joinery.mcp_runtime import Readiness, ServerSpec
from joinery.workspace import index, open_asset
from tests.test_mcp_install_workflows import fake_commands


@pytest.fixture
def cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "server cache"
    monkeypatch.setenv("JOINERY_SERVER_CACHE", str(root))
    monkeypatch.setattr(
        server_install.shutil,
        "which",
        lambda name: "/runtime/node" if name == "node" else None,
    )
    return root


def legacy_godot_install() -> Path:
    """An install written before source hashes were recorded."""
    preferred = load_catalog().get("godot").mcp
    assert preferred is not None
    root = server_install.installation("godot")
    source = root / "source"
    (source / "build").mkdir(parents=True)
    (source / "build/index.js").write_text("// built\n")
    lock = source / "package-lock.json"
    lock.write_text('{"packages":{}}')
    spec = ServerSpec(
        name=preferred.server,
        command="/runtime/node",
        args=[str(source / "build/index.js")],
        source=preferred.repo,
        revision=preferred.ref,
    )
    (root / "server.json").write_text(
        json.dumps(
            {
                "spec": spec.model_dump(),
                "lock_sha256": hashlib.sha256(lock.read_bytes()).hexdigest(),
            }
        )
    )
    return root


# --- Pinned install integrity ---------------------------------------------------


def test_install_without_a_recorded_tree_hash_must_be_reinstalled(cache: Path) -> None:
    legacy_godot_install()
    with pytest.raises(
        server_install.ReinstallRequired, match="no recorded source hash"
    ):
        server_install.load_installed("godot")


def test_install_replaces_an_untrusted_copy_and_records_its_tree(
    cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = legacy_godot_install()
    calls = fake_commands(monkeypatch, "godot")
    spec = server_install.install("godot")
    assert calls, "the pin was installed again"
    metadata = json.loads((root / "server.json").read_text())
    assert metadata["tree_sha256"] == server_install.tree_hash(root / "source")
    assert server_install.load_installed("godot") == spec
    assert not list(root.parent.glob(".stale-*"))
    assert not list(root.parent.glob(".install-*"))


def test_failed_reinstall_keeps_the_old_copy(
    cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = legacy_godot_install()
    before = (root / "server.json").read_text()
    fake_commands(monkeypatch, "godot", revision="f" * 40)
    with pytest.raises(ValueError, match="pinned revision"):
        server_install.install("godot")
    assert (root / "server.json").read_text() == before


def test_configured_says_why_godot_cannot_start(cache: Path, tmp_path: Path) -> None:
    legacy_godot_install()
    project = tmp_path / "game"
    project.mkdir()
    (project / "joinery.toml").write_text('[tools]\nenabled = ["godot"]\n')
    _specs, missing = server_install.configured(project)
    godot = next(item for item in missing if item.name == "godot")
    assert godot.state == "not-installed"
    assert "no recorded source hash" in godot.detail


# --- inspect_servers checks the worker image Godot writes in ------------------


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
        ServerSpec(name="godot", command="/runtime/node", args=["index.js"]),
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

    monkeypatch.setattr(server_install, "_image_present", absent)
    found = inspect(servers)
    assert images == ["joinery-mcp-worker"]
    assert found["godot"]["state"] == "runtime-missing"
    assert (
        found["godot"]["summary"] == "Godot tools: worker missing. Run Joinery setup."
    )
    assert "godot" not in FakeConnection.started
    # The registry has no worker; it is ready as soon as it connects.
    assert found["joinery-registry"]["summary"] == "Project registry: ready"


def test_godot_ready_names_its_operations_and_the_worker(
    servers: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def present(image: str) -> bool:
        return True

    async def version(check: Any) -> tuple[int, str]:
        assert check.image == "joinery-mcp-worker"
        assert tuple(check.argv) == ("godot", "--version", "--headless")
        return 0, "4.7.2.stable.official.ed1daf0bf\n"

    monkeypatch.setattr(server_install, "_image_present", present)
    monkeypatch.setattr(server_install, "_worker_run", version)
    found = inspect(servers, probe=True)
    assert found["godot"]["state"] == "discovered" and found["godot"]["inspected"]
    assert (
        found["godot"]["summary"] == "Godot tools ready (3 operations, offline worker)"
    )


def test_godot_missing_from_the_worker_is_reported(
    servers: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def present(image: str) -> bool:
        return True

    async def broken(check: Any) -> tuple[int, str]:
        return 127, "godot: not found"

    monkeypatch.setattr(server_install, "_image_present", present)
    monkeypatch.setattr(server_install, "_worker_run", broken)
    found = inspect(servers, probe=True)
    assert found["godot"]["state"] == "runtime-missing"
    assert "missing from the offline worker" in found["godot"]["detail"]


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


def test_status_without_probe_skips_the_worker_run(
    servers: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def present(image: str) -> bool:
        return True

    async def never(check: Any) -> tuple[int, str]:
        raise AssertionError("no worker run without --probe")

    monkeypatch.setattr(server_install, "_image_present", present)
    monkeypatch.setattr(server_install, "_worker_run", never)
    assert inspect(servers)["godot"]["state"] == "discovered"


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
    ).startswith("Godot tools: not installed. Run Joinery setup.")


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
