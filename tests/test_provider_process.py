"""Exercise the host boundary and actual ACP protocol flow without model calls."""

import asyncio
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from bridge.acp_session import ACPSession
from bridge.protocol import Done, MessageDelta, Prompt
from bridge.provider_process import readonly_command


@pytest.mark.skipif(sys.platform != "darwin", reason="native macOS sandbox test")
def test_provider_process_cannot_write_project(tmp_path: Path) -> None:
    target = tmp_path / "untouched.txt"
    target.write_text("original")
    code = (
        "from pathlib import Path; p=Path("
        + repr(str(target))
        + "); print(p.read_text()); p.write_text('changed')"
    )
    result = subprocess.run(
        readonly_command([sys.executable, "-c", code], tmp_path),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "original" in result.stdout
    assert target.read_text() == "original"


def test_acp_initializes_streams_and_checks_each_permission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = tmp_path / "agent.py"
    script.write_text("""#!/usr/bin/env python3
import json, sys
for line in sys.stdin:
    request=json.loads(line)
    method=request.get('method')
    def reply(value):
        print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':value}),flush=True)
    if method=='initialize':
        assert request['params']['protocolVersion']==1
        reply({'protocolVersion':1})
    elif method=='session/new':
        assert request['params']['mcpServers'][0]['name']=='joinery-registry'
        reply({'sessionId':'fixture'})
    elif method=='session/prompt':
        prompt_id=request['id']
        print(json.dumps({'jsonrpc':'2.0','id':99,'method':'session/request_permission','params':{'toolCall':{'title':'mcp__joinery-registry__inspect_editor','kind':'other','rawInput':{}},'options':[{'optionId':'once','kind':'allow_once'},{'optionId':'no','kind':'reject_once'}]}}),flush=True)
    elif request.get('id')==99:
        assert request['result']['outcome']['optionId']=='once'
        print(json.dumps({'jsonrpc':'2.0','method':'session/update','params':{'update':{'sessionUpdate':'agent_message_chunk','content':{'type':'text','text':'Verified'}}}}),flush=True)
        print(json.dumps({'jsonrpc':'2.0','id':prompt_id,'result':{'stopReason':'end_turn'}}),flush=True)
""")
    script.chmod(0o700)

    async def require(_agent: str, _project: Path) -> str:
        return str(script)

    monkeypatch.setattr("bridge.acp_session.require_provider", require)
    monkeypatch.setattr("bridge.acp_session.readonly_command", lambda argv, _: argv)

    async def run() -> None:
        session = ACPSession("gemini")
        session.gateway_socket = tmp_path / "gateway.sock"
        try:
            await session.connect(tmp_path)
            events = [
                event
                async for event in session.send(
                    Prompt(text="Inspect", project_dir=str(tmp_path))
                )
            ]
            assert any(
                isinstance(event, MessageDelta) and event.text == "Verified"
                for event in events
            )
            assert isinstance(events[-1], Done) and events[-1].ok
        finally:
            await session.close()

    asyncio.run(asyncio.wait_for(run(), 10))


@pytest.mark.parametrize("agent", ["gemini", "opencode"])
@pytest.mark.parametrize(
    "title",
    [
        "mcp__joinery-registry__submit_for_review",
        "joinery_registry: submit_for_review",
    ],
)
def test_acp_allows_submit_for_review(agent: str, title: str) -> None:
    session = ACPSession(agent)
    written: list[dict[str, Any]] = []

    async def write(value: dict[str, Any]) -> None:
        written.append(value)

    session._write = write  # type: ignore[method-assign]
    request = {
        "id": 7,
        "method": "session/request_permission",
        "params": {
            "toolCall": {"title": title, "kind": "other", "rawInput": {}},
            "options": [
                {"optionId": "yes", "kind": "allow_once"},
                {"optionId": "no", "kind": "reject_once"},
            ],
        },
    }
    asyncio.run(session._client_request(request))
    assert written[0]["result"]["outcome"] == {"outcome": "selected", "optionId": "yes"}


@pytest.mark.parametrize("agent", ["claude", "gemini", "opencode"])
def test_windows_requires_supported_backend_before_auth_or_hook_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: str
) -> None:
    from unittest.mock import Mock

    from bridge import provider_process

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(provider_process.shutil, "which", Mock(side_effect=AssertionError("No lookup")))
    monkeypatch.setattr(provider_process, "status", Mock(side_effect=AssertionError("No auth probe")))
    with pytest.raises(ValueError, match="Windows read-only host boundary"):
        asyncio.run(provider_process.require_provider(agent, tmp_path))
    with pytest.raises(ValueError, match="Windows read-only host boundary"):
        provider_process.readonly_command(["unrestricted.exe"], tmp_path)
