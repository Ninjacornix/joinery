"""One request that changes files through several tools is one accept/reject unit."""

import asyncio
from pathlib import Path
from typing import Any

import pytest
from mcp.types import CallToolResult, TextContent

from joinery.art_approval import approve
from joinery.gateway import ProjectGateway
from joinery.mcp_runtime import ServerSpec


class FakeConnection:
    """Stands in for a tool's MCP server: writes the file named in its call."""

    def __init__(self, spec: ServerSpec, _project: Path) -> None:
        self.spec = spec
        self.tools = {"write": object()}

    async def connect(self, timeout: float = 0) -> None:
        return None

    async def call(
        self, _tool: str, arguments: dict[str, Any], timeout: float = 0
    ) -> CallToolResult:
        target = Path(arguments["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(arguments["text"])
        return CallToolResult(content=[TextContent(type="text", text="ok")])

    async def close(self) -> None:
        return None


def approved_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    (tmp_path / "joinery.toml").write_text('[tools]\nenabled=["godot"]\n')
    (tmp_path / "art").mkdir()
    (tmp_path / "art/bible.md").write_text("Technical fixture direction.\n")
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes/existing.txt").write_text("before\n")
    approve(tmp_path, "tester")
    monkeypatch.setattr("joinery.gateway.configured", lambda _: ([], []))
    monkeypatch.setattr("joinery.gateway.MCPConnection", FakeConnection)
    return tmp_path


async def two_tool_request(gateway: ProjectGateway, project: Path) -> str:
    async def connection(server: str) -> FakeConnection:
        spec = ServerSpec(name=server, command="unused", source="first-party")
        return FakeConnection(spec, project)

    gateway.connection = connection  # type: ignore[method-assign]
    await gateway.call(
        "joinery-sound",
        "write",
        {"path": str(project / "notes/sound.txt"), "text": "door creak\n"},
    )
    await gateway.call(
        "joinery-paint",
        "write",
        {"path": str(project / "notes/existing.txt"), "text": "after\n"},
    )
    assert gateway.transaction is not None
    assert set(gateway.staged) == {"joinery-sound", "joinery-paint"}
    # Nothing reaches the project until the user decides.
    assert not (project / "notes/sound.txt").exists()
    assert (project / "notes/existing.txt").read_text() == "before\n"
    gateway.transaction.review()
    return gateway.transaction.id


@pytest.mark.parametrize("accept", [True, False])
def test_two_tools_share_one_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, accept: bool
) -> None:
    project = approved_project(tmp_path, monkeypatch)

    async def run() -> None:
        gateway = ProjectGateway(project)
        try:
            identifier = await two_tool_request(gateway, project)
            decision = await gateway.decide(identifier, accept)
            assert decision["accepted"] is accept
        finally:
            await gateway.close()

    asyncio.run(run())
    if accept:
        assert (project / "notes/sound.txt").read_text() == "door creak\n"
        assert (project / "notes/existing.txt").read_text() == "after\n"
    else:
        assert not (project / "notes/sound.txt").exists()
        assert (project / "notes/existing.txt").read_text() == "before\n"
