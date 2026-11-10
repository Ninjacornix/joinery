"""`connect verify`: one tiny message through the real bridge tells "connected" from "works"."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from joinery import connection
from joinery.connection import TurnResult


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("JOINERY_READINESS_CACHE", str(tmp_path / "cache"))
    for names in connection.KEY_VARS.values():
        for name in names:
            monkeypatch.delenv(name, raising=False)


def signed_in(
    monkeypatch: pytest.MonkeyPatch,
    *,
    installed: bool = True,
    method: str = "Claude account",
) -> None:
    def status(agent_id: str) -> dict[str, Any]:
        connected = installed and bool(method)
        return {
            "id": agent_id,
            "name": "Claude Code",
            "installed": installed,
            "connected": connected,
            "supported": True,
            "reason": "",
            "method": method if connected else "",
        }

    monkeypatch.setattr(connection, "status", status)


class Recorder:
    def __init__(self, result: TurnResult) -> None:
        self.result = result
        self.calls: list[tuple[str, Path, float]] = []

    def __call__(self, agent_id: str, project: Path, timeout: float) -> TurnResult:
        self.calls.append((agent_id, project, timeout))
        return self.result


@pytest.mark.parametrize(
    "result,state",
    [
        (TurnResult(True, True, True, "OK"), "ok"),
        (
            TurnResult(True, True, False, "API Error: 429 rate_limit_error account@x"),
            "quota",
        ),
        (TurnResult(True, True, False, "You've hit your usage limit."), "quota"),
        (TurnResult(True, True, False, "401 Unauthorized: token expired"), "auth"),
        (TurnResult(False, False, False, "Sign in with Claude Code first."), "auth"),
        (TurnResult(True, False, False, "", timed_out=True), "error"),
        (TurnResult(True, True, False, "something else"), "error"),
    ],
)
def test_outcomes_are_classified_and_recorded_without_provider_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, result: TurnResult, state: str
) -> None:
    signed_in(monkeypatch)
    turn = Recorder(result)
    report = connection.verify("claude", tmp_path, turn=turn)
    assert report["state"] == state and report["verified"] == (state == "ok")
    assert turn.calls == [("claude", tmp_path.resolve(), connection.VERIFY_TIMEOUT)]
    saved = json.loads(connection.checks_path().read_text())["claude"]
    assert saved["state"] == state and saved["method"] == "Claude account"
    # Provider messages can carry account details; only our wording is kept.
    stored = json.dumps(saved) + json.dumps(report)
    assert result.text == "" or result.text not in stored
    assert "account@x" not in stored


def test_not_installed_or_not_signed_in_makes_no_model_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    turn = Recorder(TurnResult(True, True, True, "OK"))
    signed_in(monkeypatch, installed=False)
    assert connection.verify("claude", tmp_path, turn=turn)["state"] == "not-installed"
    signed_in(monkeypatch, method="")
    assert connection.verify("claude", tmp_path, turn=turn)["state"] == "auth"
    assert turn.calls == []


def test_status_shows_the_verified_badge_only_for_the_same_sign_in(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(connection.sys, "platform", "linux")
    monkeypatch.setattr(connection.shutil, "which", lambda _: "/bin/claude")

    def claude_status(method: str) -> None:
        output = json.dumps({"loggedIn": True, "authMethod": method})
        monkeypatch.setattr(connection, "_probe", lambda _argv: (0, output))

    claude_status("oauth")
    before = connection.status("claude")
    assert not before["verified"] and before["verification"] is None
    assert before["summary"] == "Claude: signed in (Claude account)"
    connection.verify(
        "claude", tmp_path, turn=Recorder(TurnResult(True, True, True, "OK"))
    )
    after = connection.status("claude")
    assert after["verified"] and after["verification"]["state"] == "ok"
    assert after["summary"] == "Claude: signed in (Claude account), verified"
    # Switching to an API key is a different sign-in: verify it again.
    claude_status("api_key")
    assert not connection.status("claude")["verified"]


def test_summary_wording(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(connection.sys, "platform", "linux")
    monkeypatch.setattr(connection.shutil, "which", lambda _: None)
    gemini = connection.status("gemini")
    assert gemini["summary"] == (
        "Gemini CLI: not installed: " + connection.INSTALL_URLS["gemini"]
    )
    monkeypatch.setattr(connection.shutil, "which", lambda _: "/bin/opencode")
    monkeypatch.setattr(connection, "_probe", lambda _argv: (0, "0 credentials"))
    assert (
        connection.status("opencode")["summary"] == "opencode: installed, not signed in"
    )


FAKE_BRIDGE = """
import json, sys
mode = sys.argv[1]
def emit(event):
    print(json.dumps(event), flush=True)
if mode == "refuse":
    emit({"kind": "connection", "state": "error", "message": "The shared project policy hook is missing."})
    sys.exit(1)
emit({"kind": "progress", "text": "Connecting"})
emit({"kind": "connection", "state": "connected", "models": []})
prompt = json.loads(sys.stdin.readline())
assert prompt["kind"] == "prompt" and "OK" in prompt["text"], prompt
if mode == "hang":
    sys.stdin.readline()
    sys.exit(0)
emit({"kind": "execution", "model": "m", "name": "m", "mode": "chat"})
emit({"kind": "message_delta", "text": "O", "new_message": True})
emit({"kind": "message_delta", "text": "K"})
emit({"kind": "done", "ok": mode == "ok"})
sys.stdin.readline()
"""


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("ok", TurnResult(True, True, True, "OK")),
        ("fail", TurnResult(True, True, False, "OK")),
        (
            "refuse",
            TurnResult(
                False, False, False, "The shared project policy hook is missing."
            ),
        ),
        ("hang", TurnResult(True, False, False, "", timed_out=True)),
    ],
)
def test_bridge_turn_speaks_the_editor_chat_protocol(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: str, expected: TurnResult
) -> None:
    script = tmp_path / "fake_bridge.py"
    script.write_text(FAKE_BRIDGE)
    monkeypatch.setattr(
        connection,
        "chat_command",
        lambda agent_id, project: [sys.executable, "-I", str(script), mode],
    )
    assert connection.bridge_turn("claude", tmp_path, 3) == expected


def test_policy_hook_refusal_is_explained(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    signed_in(monkeypatch)
    turn = Recorder(
        TurnResult(False, False, False, "The shared project policy hook is missing.")
    )
    report = connection.verify("claude", tmp_path, turn=turn)
    assert report["state"] == "error"
    assert "policy hook is not set up" in report["message"]


def test_chat_command_is_the_editor_bridge(tmp_path: Path) -> None:
    argv = connection.chat_command("codex", tmp_path)
    assert argv[0] == sys.executable
    assert argv[3:] == [
        "chat",
        "--project",
        str(tmp_path),
        "--agent",
        "codex",
        "--addon-protocol",
        "2",
    ]
