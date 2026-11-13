"""Discovery, schema, error and readiness behavior for any application's MCP."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp.types import CallToolResult, TextContent, Tool

from joinery import mcp_runtime
from joinery.mcp_runtime import (
    MCPConnection,
    ServerSpec,
    host_environment,
    validate_schema,
)


class FixtureClient:
    """A protocol peer, never a native application or a provider connection."""

    protocol_version = "fixture-protocol"

    def __init__(self, pages):
        self.pages = pages
        self.cursors = []
        self.closed = False
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        self.closed = True

    async def list_tools(self, cursor=None):
        self.cursors.append(cursor)
        tools, next_cursor = self.pages[cursor]
        return SimpleNamespace(tools=tools, next_cursor=next_cursor)

    async def call_tool(self, name, arguments, **kwargs):
        self.calls.append((name, arguments))
        return CallToolResult(
            content=[
                TextContent(type="text", text="application rejected this operation")
            ],
            is_error=name == "fail",
        )


def tool(name="inspect", schema=None):
    return Tool(
        name=name,
        input_schema=schema
        or {
            "type": "object",
            "properties": {"source": {"type": "string"}},
            "required": ["source"],
            "additionalProperties": False,
        },
    )


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "joinery.toml").write_text('[tools]\nenabled=["godot"]\n')
    return tmp_path


def connection(project, monkeypatch, peer):
    monkeypatch.setattr(mcp_runtime, "Client", lambda *_args, **_kwargs: peer)
    return MCPConnection(
        ServerSpec(name="godot", command="fixture-not-executed"), project
    )


def test_paginated_discovery_requires_every_page_before_reporting_tools(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    peer = FixtureClient(
        {None: ([tool("inspect")], "page-2"), "page-2": ([tool("fail")], None)}
    )
    conn = connection(project, monkeypatch, peer)

    async def run():
        try:
            await conn.connect()
            assert peer.cursors == [None, "page-2"]
            assert set(conn.tools) == {"inspect", "fail"}
            assert conn.readiness.connected and conn.readiness.state == "discovered"
            assert conn.readiness.inspected is False
            assert conn.readiness.protocol == "fixture-protocol"
        finally:
            await conn.close()
        assert peer.closed and not conn.readiness.connected
        assert conn.tools == {}

    asyncio.run(run())


def test_duplicate_tool_names_do_not_become_successful_discovery(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    peer = FixtureClient({None: ([tool()], "next"), "next": ([tool()], None)})
    conn = connection(project, monkeypatch, peer)

    async def run():
        with pytest.raises(RuntimeError, match="could not connect"):
            await conn.connect()
        assert not conn.readiness.connected and conn.readiness.state == "error"
        assert conn._task is None and peer.closed

    asyncio.run(run())


@pytest.mark.parametrize(
    "reference",
    [
        "https://example.invalid/schema.json",
        "file:///tmp/schema.json",
        "../schema.json",
        "//example.invalid/schema.json",
    ],
)
@pytest.mark.parametrize("keyword", ["$ref", "$dynamicRef"])
def test_nested_schema_never_authorizes_external_lookup(
    reference: str, keyword: str
) -> None:
    with pytest.raises(ValueError, match="only local definitions"):
        validate_schema({"allOf": [{"properties": {"nested": {keyword: reference}}}]})


def test_local_schema_definitions_are_usable_for_real_input_validation(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    schema = {
        "type": "object",
        "$defs": {"part": {"type": "string", "minLength": 1}},
        "properties": {"part": {"$ref": "#/$defs/part"}},
        "required": ["part"],
    }
    peer = FixtureClient({None: ([tool("inspect", schema)], None)})
    conn = connection(project, monkeypatch, peer)

    async def run():
        try:
            await conn.connect()
            with pytest.raises(ValueError, match="part"):
                await conn.call("inspect", {"part": ""})
            assert peer.calls == []
            assert not (await conn.call("inspect", {"part": "Grip"})).is_error
            assert peer.calls == [("inspect", {"part": "Grip"})]
        finally:
            await conn.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "arguments", [{}, {"source": 123}, {"source": "art/a.blend", "execute": True}]
)
def test_invalid_arguments_never_get_sent_to_any_mcp_peer(
    project: Path, monkeypatch: pytest.MonkeyPatch, arguments: dict
) -> None:
    peer = FixtureClient({None: ([tool()], None)})
    conn = connection(project, monkeypatch, peer)

    async def run():
        try:
            await conn.connect()
            with pytest.raises(ValueError, match="Invalid godot.inspect input"):
                await conn.call("inspect", arguments)
            assert peer.calls == []
            assert conn.readiness.connected  # validation errors need no restart
        finally:
            await conn.close()

    asyncio.run(run())


def test_unknown_tool_is_not_forwarded_and_does_not_disconnect(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    peer = FixtureClient({None: ([tool()], None)})
    conn = connection(project, monkeypatch, peer)

    async def run():
        try:
            await conn.connect()
            with pytest.raises(ValueError, match="does not expose"):
                await conn.call("invented_magic_tool", {})
            assert peer.calls == [] and conn.readiness.connected
        finally:
            await conn.close()

    asyncio.run(run())


def test_application_error_is_preserved_and_does_not_certify_probe_success(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    peer = FixtureClient({None: ([tool("fail"), tool("inspect")], None)})
    conn = connection(project, monkeypatch, peer)

    async def run():
        try:
            result = await conn.probe("fail", {"source": "art/missing.blend"})
            assert result.is_error
            assert result.content[0].text == "application rejected this operation"
            assert not conn.readiness.inspected
            await conn.probe("inspect", {"source": "art/real.blend"})
            assert conn.readiness.inspected
        finally:
            await conn.close()

    asyncio.run(run())


def test_tool_timeout_closes_owned_session_and_allows_a_clean_reconnect(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = FixtureClient({None: ([tool()], None)})
    second = FixtureClient({None: ([tool()], None)})
    peers = iter([first, second])

    async def slow(*_args, **_kwargs):
        await asyncio.Event().wait()

    first.call_tool = slow
    monkeypatch.setattr(mcp_runtime, "Client", lambda *_args, **_kwargs: next(peers))
    conn = MCPConnection(
        ServerSpec(name="godot", command="fixture-not-executed"), project
    )

    async def run():
        with pytest.raises(TimeoutError):
            # Exercise the connection's own deadline (including its transport
            # grace period), rather than cancelling it with a test-side timer.
            await conn.call("inspect", {"source": "art/a"}, timeout=0.01)
        assert conn._task is None and first.closed
        try:
            assert not (await conn.call("inspect", {"source": "art/a"})).is_error
            assert second.calls == [("inspect", {"source": "art/a"})]
        finally:
            await conn.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "secret",
    [
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "AWS_SECRET_ACCESS_KEY",
        "GITHUB_TOKEN",
        "LD_PRELOAD",
    ],
)
def test_model_credentials_and_injected_libraries_are_not_inherited_by_tools(
    monkeypatch: pytest.MonkeyPatch, secret: str
) -> None:
    monkeypatch.setenv(secret, "unit-test-private-value")
    monkeypatch.setenv("DISPLAY", ":fixture")
    env = host_environment()
    assert secret not in env
    assert env["DISPLAY"] == ":fixture"


def test_missing_worker_image_reports_runtime_missing_without_launching_client(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    child = SimpleNamespace(wait=AsyncMock(return_value=1))
    spawn = AsyncMock(return_value=child)
    peer = AsyncMock()
    monkeypatch.setattr(mcp_runtime.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(mcp_runtime, "Client", peer)
    conn = MCPConnection(
        ServerSpec(
            name="godot", command="docker", worker_image="missing-fixture-worker"
        ),
        project,
    )
    monkeypatch.setattr(mcp_runtime.asyncio, "sleep", AsyncMock())
    # Plain words for the agent to pass on: no command line, no placeholder.
    with pytest.raises(ValueError, match="Refresh tools in the Joinery sidebar"):
        asyncio.run(conn.connect())
    # Asked twice: a busy Docker answering late is not a missing tool.
    assert spawn.await_count == 2
    assert conn.readiness.state == "runtime-missing"
    assert not conn.readiness.connected and conn._task is None
    peer.assert_not_called()
    assert spawn.await_args.args == (
        "docker",
        "image",
        "inspect",
        "missing-fixture-worker",
    )
