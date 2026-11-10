"""view_editor: the agent sees the editor viewport the user sees, read-only."""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from mcp import Client
from mcp.types import ImageContent, TextContent

from bridge.instructions import GAME_GUIDE
from bridge.stdio import editor_view
from joinery.gateway import ProjectGateway
from joinery.gateway_rpc import GatewayRPC, rpc
from joinery.hook import violation
from servers.joinery_registry.proxy import make_server

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    (tmp_path / "joinery.toml").write_text('[tools]\nenabled=["godot"]\n')
    monkeypatch.setattr("joinery.gateway.configured", lambda _: ([], []))
    return tmp_path


def screenshot(root: Path) -> Path:
    path = root / ".joinery/attachments/view-1.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(PNG)
    return path


def test_the_sidebar_answer_is_checked(tmp_path: Path) -> None:
    path = screenshot(tmp_path)
    camera = {"position": [0, 2, 5], "fov": 75}
    view = editor_view(
        tmp_path,
        {
            "success": True,
            "path": str(path),
            "view": "3D",
            "width": 800,
            "height": 600,
            "camera": camera,
        },
    )
    assert view["path"] == str(path.resolve())
    assert view["view"] == "3D" and view["camera"] == camera
    assert (view["width"], view["height"]) == (800, 600)
    assert "not an asset" in view["note"]
    with pytest.raises(ValueError, match="No scene is open"):
        editor_view(tmp_path, {"success": False, "message": "No scene is open."})
    stray = tmp_path / "art/door.png"
    stray.parent.mkdir()
    stray.write_bytes(PNG)
    with pytest.raises(ValueError, match="attached in the sidebar"):
        editor_view(tmp_path, {"success": True, "path": str(stray)})


def test_gateway_asks_the_host_and_specialists_may_look(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        gateway = ProjectGateway(project(tmp_path, monkeypatch))
        main = GatewayRPC(gateway)
        specialist = GatewayRPC(gateway, inspection_only=True)
        await main.start()
        await specialist.start()
        try:
            with pytest.raises(RuntimeError, match="No live editor"):
                await rpc(main.path, "editor_view", {})
            gateway.view_editor = AsyncMock(return_value={"path": "x", "view": "2D"})
            assert (await rpc(main.path, "editor_view", {}))["view"] == "2D"
            assert (await rpc(specialist.path, "editor_view", {}))["view"] == "2D"
        finally:
            await specialist.close()
            await main.close()

    asyncio.run(run())


def test_the_tool_returns_the_picture_and_the_camera(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = screenshot(tmp_path)
    data = {"path": str(path), "view": "3D", "camera": {"fov": 75}}

    async def fake(_socket: Path, method: str, _params: dict) -> dict:
        assert method == "editor_view"
        return data

    monkeypatch.setattr("servers.joinery_registry.proxy.rpc", fake)

    async def run() -> None:
        async with Client(make_server(tmp_path / "gateway.sock")) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            assert tools["view_editor"].annotations is not None
            assert tools["view_editor"].annotations.read_only_hint is True
            result = await client.call_tool("view_editor", {})
        image, text = result.content
        assert isinstance(image, ImageContent) and isinstance(text, TextContent)
        assert image.mime_type == "image/png"
        assert base64.b64decode(image.data) == PNG
        assert json.loads(text.text)["camera"] == {"fov": 75}

    asyncio.run(run())


def test_read_only_specialists_may_call_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project(tmp_path, monkeypatch)
    monkeypatch.setenv("JOINERY_INSPECTION_ONLY", "1")
    event = {
        "tool_name": "mcp__joinery-registry__view_editor",
        "tool_input": {},
        "cwd": str(tmp_path),
    }
    assert violation(event) is None


def test_agents_are_told_to_look_when_asked() -> None:
    assert "view_editor" in GAME_GUIDE
