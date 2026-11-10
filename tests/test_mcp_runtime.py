"""Real SDK stdio subprocess checks; no credentials, apps or inference required."""

import asyncio
import json
import sys
from pathlib import Path

import pytest
from mcp.types import CallToolResult, TextContent

from joinery.mcp_runtime import MCPConnection, ServerSpec, probe_problem


@pytest.fixture
def connection(tmp_path: Path) -> MCPConnection:
    (tmp_path / "joinery.toml").write_text('[tools]\nenabled=["godot"]\n')
    script = tmp_path / "fixture.py"
    script.write_text("""import asyncio,os
from pathlib import Path
from mcp.server import MCPServer
m=MCPServer('Joinery fixture')
@m.tool()
def inspect_project(label: str) -> dict:
 return {'label':label,'secret_present':'OPENAI_API_KEY' in os.environ}
@m.tool()
async def wait(seconds: float) -> dict:
 await asyncio.sleep(seconds)
 return {'done':True}
@m.tool()
def document_capabilities() -> dict:
 return {'runtime_available': False, 'tools': [{'id': 'krita', 'application_installed': False}]}
@m.tool()
def crash() -> dict:
 os._exit(23)
m.run()
""")
    return MCPConnection(
        ServerSpec(name="godot", command=sys.executable, args=[str(script)]), tmp_path
    )


def test_real_discovery_call_and_schema(
    connection: MCPConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-reach-the-tool")

    async def run() -> None:
        await connection.connect()
        try:
            assert connection.readiness.state == "discovered"
            assert connection.readiness.protocol
            assert set(connection.tools) == {
                "inspect_project",
                "wait",
                "document_capabilities",
                "crash",
            }
            with pytest.raises(ValueError, match="input at label"):
                await connection.call("inspect_project", {"label": 123})
            result = await connection.probe("inspect_project", {"label": "ok"})
            assert not result.is_error
            assert json.loads(result.content[0].text) == {"label": "ok", "secret_present": False}
            assert connection.readiness.inspected
        finally:
            await connection.close()
        assert connection.readiness.state == "disconnected"

    asyncio.run(run())


def test_disconnect_recovers(connection: MCPConnection) -> None:
    async def run() -> None:
        try:
            await connection.connect()
            with pytest.raises(RuntimeError):
                await connection.call("crash", {}, timeout=2)
            await asyncio.sleep(0.05)
            result = await connection.call("inspect_project", {"label": "reconnected"})
            assert not result.is_error
        finally:
            await connection.close()

    asyncio.run(run())


def test_cancel_owned_session(connection: MCPConnection) -> None:
    async def run() -> None:
        await connection.connect()
        task = asyncio.create_task(connection.call("wait", {"seconds": 100}))
        await asyncio.sleep(0.2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert connection._task is None
        assert connection.readiness.state == "disconnected"

    asyncio.run(run())


def test_unconfigured_server_not_launched(connection: MCPConnection) -> None:
    connection.spec.name = "unselected-test-server"
    with pytest.raises(PermissionError):
        asyncio.run(connection.connect())
    assert connection._task is None


def test_probe_fails_on_false_capabilities(connection: MCPConnection) -> None:
    async def run() -> None:
        try:
            await connection.connect()
            result = await connection.probe("document_capabilities", {})
            assert not result.is_error
            assert not connection.readiness.inspected
            assert "offline worker did not start" in connection.readiness.detail
            await connection.probe("inspect_project", {"label": "ok"})
            assert connection.readiness.inspected
        finally:
            await connection.close()

    asyncio.run(run())


def _result(text: str, error: bool = False) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=text)], is_error=error)


def test_probe_problem_wording() -> None:
    assert probe_problem("x.t", _result("all good")) == ""
    assert probe_problem("x.t", _result('{"runtime_available": true}')) == ""
    assert probe_problem("x.t", _result("\nboom\nmore", error=True)) == "x.t failed: boom"
    assert "did not start" in probe_problem("x.t", _result('{"runtime_available": false}'))
    assert probe_problem("x.t", _result('{"ok": false, "error": "no file"}')) == "x.t: no file"
    missing = '{"tools": [{"id": "gimp", "application_installed": false}]}'
    assert "missing" in probe_problem("x.t", _result(missing))
    some = '{"tools": [{"id": "gimp", "application_installed": false}, {"id": "krita"}]}'
    assert probe_problem("x.t", _result(some)) == ""
