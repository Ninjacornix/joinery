"""Claude Code too old for Joinery's flags, or no read-only boundary, is said at
connect rather than on the first message."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from bridge import claude_session
from bridge.claude_session import REQUIRED_FLAGS, ClaudeSession, require_flags
from bridge.connect_errors import ConnectError

HELP = (
    "Usage: claude [options]\n" + "\n".join(f"  {f} <x>" for f in REQUIRED_FLAGS)
).encode()


def test_a_current_cli_passes() -> None:
    require_flags(HELP)
    require_flags(b"")  # The probe failed: nothing proven either way.


def test_a_cli_without_a_flag_is_out_of_date() -> None:
    old = HELP.replace(b"--permission-prompts", b"--permission-mode")
    with pytest.raises(ConnectError) as too_old:
        require_flags(old)
    assert too_old.value.reason == "outdated"
    assert "--permission-prompts" in str(too_old.value)
    assert "claude update" in str(too_old.value)


class Probe:
    returncode = 0

    def __init__(self, output: bytes) -> None:
        self.output = output

    async def communicate(self) -> tuple[bytes, bytes]:
        return self.output, b""


def connect(monkeypatch: pytest.MonkeyPatch, project: Path, output: bytes) -> None:
    async def provider(_agent: str, _project: Path) -> str:
        return "claude"

    async def spawn(*_argv: Any, **_kw: Any) -> Probe:
        return Probe(output)

    monkeypatch.setattr(claude_session, "require_provider", provider)
    monkeypatch.setattr(claude_session.asyncio, "create_subprocess_exec", spawn)
    session = ClaudeSession()
    session.gateway_socket = project / "gateway.sock"
    asyncio.run(session.connect(project))


def test_connect_checks_the_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(claude_session, "readonly_command", lambda argv, _p: argv)
    connect(monkeypatch, tmp_path, HELP)
    with pytest.raises(ConnectError) as too_old:
        connect(monkeypatch, tmp_path, HELP.replace(b"--session-id", b""))
    assert too_old.value.reason == "outdated"


def test_connect_checks_the_read_only_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def missing(_argv: list[str], _project: Path) -> list[str]:
        raise ConnectError("needs bubblewrap: sudo apt install bubblewrap", "sandbox")

    monkeypatch.setattr(claude_session, "readonly_command", missing)
    with pytest.raises(ConnectError) as boundary:
        connect(monkeypatch, tmp_path, HELP)
    assert boundary.value.reason == "sandbox"
    assert "apt install bubblewrap" in str(boundary.value)
