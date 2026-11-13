"""A failed connection says why as a reason code, with its fix and the agent's words."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from bridge import provider_process
from bridge.claude_session import claude_exit_text
from bridge.codex_session import CodexSession
from bridge.connect_errors import (
    ConnectError,
    ProviderGone,
    StderrTail,
    event,
    reason_of,
    redact,
    tail,
)
from bridge.protocol import Prompt
from joinery.cli import main as cli_main
from joinery.connection import INSTALL_URLS


def test_claude_says_why_it_stopped() -> None:
    assert "sign in" in claude_exit_text("Error: not logged in")
    assert "claude update" in claude_exit_text("error: unknown option '--tools'")
    assert claude_exit_text("boom\npanic: disk full").endswith(": panic: disk full")
    assert claude_exit_text("") == "Claude Code stopped with an error."


def test_codex_keeps_its_last_words_when_it_stops() -> None:
    session = CodexSession()
    session.thread_id = "t"
    session._secrets = ["sk-account-secret-key"]
    session.stderr.feed(b"starting\nusing sk-account-secret-key\npanic: lost\n")

    async def request(_method: str, _params: dict[str, Any]) -> dict[str, Any]:
        await session._events.put({"method": "session/closed"})
        return {"turn": {"id": "turn-1"}}

    session.request = request  # type: ignore[method-assign]

    async def run() -> None:
        async for _ in session.send(Prompt(text="hi", project_dir="/p")):
            pass

    with pytest.raises(ProviderGone) as gone:
        asyncio.run(run())
    assert gone.value.details.endswith("panic: lost")
    assert "sk-account-secret-key" not in gone.value.details
    assert event("codex", "Codex CLI", gone.value)["reason"] == "crashed"


@pytest.mark.parametrize(
    "text,reason",
    [
        ("Codex CLI is not installed.", "not_installed"),
        ("Codex is not signed in.", "signed_out"),
        ("Error: 401 Unauthorized", "signed_out"),
        ("The shared project policy hook is missing.", "gate"),
        ("needs bubblewrap on Linux", "sandbox"),
        ("You've hit your usage limit", "quota"),
        ("error: unknown option '--tools'", "outdated"),
        ("Segmentation fault", "crashed"),
    ],
)
def test_reasons_are_read_from_the_words(text: str, reason: str) -> None:
    assert reason_of(ValueError(text)) == reason


def test_a_raised_reason_wins_and_carries_its_fix() -> None:
    found = event("claude", "Claude Code", ConnectError("It is gone.", "not_installed"))
    assert found["reason"] == "not_installed"
    assert found["url"] == INSTALL_URLS["claude"]
    assert found["name"] == "Claude Code" and found["state"] == "error"
    gone = event("codex", "Codex CLI", ProviderGone("Codex stopped.", "panic: x"))
    assert gone["reason"] == "crashed" and gone["details"] == "panic: x"
    assert "url" not in gone


def test_secrets_never_leave_in_details() -> None:
    text = (
        "OPENAI_API_KEY=sk-abcdefghijklmnop\n"
        "Authorization: Bearer abcdefghijklmnop\n"
        '{"api_key": "xyz123secret"}\n'
        "my-account-secret appears here"
    )
    clean = redact(text, ["my-account-secret"])
    for secret in ["sk-abcdefghijklmnop", "abcdefghijklmnop", "xyz123secret"]:
        assert secret not in clean
    assert "my-account-secret" not in clean and "[redacted]" in clean


def test_only_the_last_lines_are_kept() -> None:
    kept = StderrTail(3)
    kept.feed(b"one\ntwo\nthr")
    kept.feed(b"ee\nfour\nfive")
    assert kept.text() == "three\nfour\nfive"
    assert tail("\n".join(str(i) for i in range(30))).count("\n") == 19


def test_require_provider_says_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(provider_process, "require_supported", lambda _a: None)
    monkeypatch.setattr(provider_process.shutil, "which", lambda _a: None)
    with pytest.raises(ConnectError) as missing:
        asyncio.run(provider_process.require_provider("claude", tmp_path))
    assert missing.value.reason == "not_installed"
    monkeypatch.setattr(provider_process.shutil, "which", lambda _a: "/bin/claude")
    monkeypatch.setattr(provider_process, "status", lambda _a: {"connected": False})
    with pytest.raises(ConnectError) as signed_out:
        asyncio.run(provider_process.require_provider("claude", tmp_path))
    assert signed_out.value.reason == "signed_out"
    monkeypatch.setattr(provider_process, "status", lambda _a: {"connected": True})
    with pytest.raises(ConnectError) as gate:
        asyncio.run(provider_process.require_provider("claude", tmp_path))
    assert gate.value.reason == "gate"


def test_repair_writes_the_one_agents_gate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = tmp_path / "game"
    project.mkdir()
    code = cli_main(
        ["connect", "repair-gate", "--agent", "gemini", "--project", str(project)]
    )
    out: dict[str, Any] = json.loads(capsys.readouterr().out)
    assert code == 0 and "gemini" in out["message"]
    assert (project / ".gemini/settings.json").is_file()
    assert not (project / ".claude").exists()
