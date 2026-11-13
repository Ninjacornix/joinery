"""Continuing a saved chat in each provider's own session, with fake provider processes."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path

import pytest

from bridge.acp_session import ACPSession
from bridge.claude_session import ClaudeSession, claude_transcript_exists
from bridge.protocol import Done, MessageDelta, Prompt

CLAUDE = """#!/usr/bin/env python3
import json, sys
from pathlib import Path
if '--help' in sys.argv:
    print('--effort --output-format --verbose --include-partial-messages')
    print('--permission-prompts --allowedTools --append-system-prompt --settings')
    print('--mcp-config --resume --session-id --model')
    raise SystemExit(0)
with open(Path(__file__).with_name('argv.jsonl'), 'a') as log:
    log.write(json.dumps(sys.argv[1:]) + '\\n')
sys.stdin.read()
flag = '--resume' if '--resume' in sys.argv else '--session-id'
session = sys.argv[sys.argv.index(flag) + 1]
print(json.dumps({'type': 'system', 'subtype': 'init', 'session_id': session}), flush=True)
print(json.dumps({'type': 'assistant', 'session_id': session, 'message': {'content': [{'type': 'text', 'text': 'Hi'}]}}), flush=True)
print(json.dumps({'type': 'result', 'subtype': 'success', 'session_id': session}), flush=True)
"""


@pytest.fixture
def claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    script = tmp_path / "bin/claude"
    script.parent.mkdir()
    script.write_text(CLAUDE)
    script.chmod(0o755)

    async def require(_agent: str, _project: Path) -> str:
        return str(script)

    monkeypatch.setattr("bridge.claude_session.require_provider", require)
    monkeypatch.setattr("bridge.claude_session.readonly_command", lambda argv, _: argv)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    project = tmp_path / "game"
    project.mkdir()
    return project


def _argv(project: Path) -> list[list[str]]:
    log = project.parent / "bin/argv.jsonl"
    return [json.loads(line) for line in log.read_text().splitlines()]


@pytest.mark.skipif(
    os.name == "nt",
    reason="Claude Code is not supported on Windows; its fake is a POSIX script",
)
def test_claude_resumes_a_saved_session_across_processes(
    claude: Path, tmp_path: Path
) -> None:
    saved = str(uuid.uuid4())
    folder = tmp_path / "claude-config/projects"
    folder.mkdir(parents=True)

    async def run() -> None:
        session = ClaudeSession()
        session.gateway_socket = tmp_path / "gateway.sock"
        await session.connect(claude)
        assert session.handle == ""  # Nothing to resume before a turn.
        # Claude Code no longer has this session for this project: no resume.
        assert not await session.resume(saved)
        assert not await session.resume("not-a-session")
        encoded = "".join(c if c.isalnum() else "-" for c in str(claude.resolve()))
        (folder / encoded).mkdir()
        (folder / encoded / f"{saved}.jsonl").write_text("{}\n")
        assert claude_transcript_exists(claude, saved)
        assert await session.resume(saved)
        assert session.handle == saved
        prompt = Prompt(text="Continue", project_dir=str(claude))
        events = [e async for e in session.send(prompt)]
        assert isinstance(events[-1], Done) and events[-1].ok
        await session.new_session()
        assert session.handle == ""
        events = [e async for e in session.send(prompt)]
        assert session.handle not in {"", saved}

    asyncio.run(asyncio.wait_for(run(), 20))
    resumed, fresh = _argv(claude)
    assert resumed[resumed.index("--resume") + 1] == saved
    assert "--session-id" in fresh and saved not in fresh


ACP = """#!/usr/bin/env python3
import json, sys
from pathlib import Path
loads = '--no-load' not in sys.argv
for line in sys.stdin:
    request = json.loads(line)
    method = request.get('method')
    with open(Path(__file__).with_name('acp.jsonl'), 'a') as log:
        log.write(json.dumps(request) + '\\n')
    def reply(value):
        print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'result': value}), flush=True)
    def update(text, session):
        print(json.dumps({'jsonrpc': '2.0', 'method': 'session/update', 'params': {'sessionId': session, 'update': {'sessionUpdate': 'agent_message_chunk', 'content': {'type': 'text', 'text': text}}}}), flush=True)
    if method == 'initialize':
        reply({'protocolVersion': 1, 'agentCapabilities': {'loadSession': loads}})
    elif method == 'session/new':
        reply({'sessionId': 'fresh'})
    elif method == 'session/load':
        session = request['params']['sessionId']
        if session == 'gone':
            print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'error': {'message': 'Session not found'}}), flush=True)
            continue
        # The agent replays the conversation before it answers.
        update('Earlier reply', session)
        reply({})
    elif method == 'session/prompt':
        update('New reply', request['params']['sessionId'])
        reply({'stopReason': 'end_turn'})
"""


def _acp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, loads: bool) -> Path:
    script = tmp_path / "agent.py"
    script.write_text(ACP)
    script.chmod(0o755)

    async def require(_agent: str, _project: Path) -> str:
        return str(script)

    monkeypatch.setattr("bridge.acp_session.require_provider", require)
    monkeypatch.setattr(
        "bridge.acp_session.readonly_command",
        lambda argv, _: argv if loads else [*argv, "--no-load"],
    )
    return script.with_name("acp.jsonl")


def test_acp_loads_a_saved_session_when_the_agent_can(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = _acp(tmp_path, monkeypatch, loads=True)

    async def run() -> None:
        session = ACPSession("gemini")
        session.gateway_socket = tmp_path / "gateway.sock"
        try:
            await session.connect(tmp_path)
            assert session.load_session and session.handle == "fresh"
            assert not await session.resume("gone")
            assert session.handle == "fresh"
            assert await session.resume("saved")
            assert session.handle == "saved"
            events = [
                e
                async for e in session.send(
                    Prompt(text="Hi", project_dir=str(tmp_path))
                )
            ]
            # The replayed history is already on screen; only the new reply comes.
            texts = [e.text for e in events if isinstance(e, MessageDelta)]
            assert texts == ["New reply"]
            await session.new_session()
            assert session.handle == "fresh"
        finally:
            await session.close()

    asyncio.run(asyncio.wait_for(run(), 10))
    requests = [json.loads(line) for line in log.read_text().splitlines()]
    load = next(
        r
        for r in requests
        if r.get("method") == "session/load" and r["params"]["sessionId"] == "saved"
    )
    assert load["params"]["cwd"] == str(tmp_path.resolve())
    assert load["params"]["mcpServers"][0]["name"] == "joinery-registry"
    prompt = next(r for r in requests if r.get("method") == "session/prompt")
    assert prompt["params"]["sessionId"] == "saved"


def test_acp_without_load_session_continues_from_a_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = _acp(tmp_path, monkeypatch, loads=False)

    async def run() -> None:
        session = ACPSession("opencode")
        session.gateway_socket = tmp_path / "gateway.sock"
        try:
            await session.connect(tmp_path)
            assert not session.load_session
            assert not await session.resume("saved")
            assert session.handle == "fresh"
        finally:
            await session.close()

    asyncio.run(asyncio.wait_for(run(), 10))
    assert '"session/load"' not in log.read_text()
