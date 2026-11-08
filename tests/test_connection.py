import json
import os
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from joinery import connection


@pytest.fixture(autouse=True)
def isolated_keys(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for names in connection.KEY_VARS.values():
        for name in names:
            monkeypatch.delenv(name, raising=False)


def test_codex_existing_account_is_detected_without_leaking_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(connection.shutil, "which", lambda _: "/bin/codex")
    run = Mock(
        return_value=subprocess.CompletedProcess(
            [], 0, "", "Logged in using ChatGPT\nprivate account data"
        )
    )
    monkeypatch.setattr(connection.subprocess, "run", run)
    result = connection.status("codex")
    assert result["connected"] and result["method"] == "ChatGPT account"
    assert "private account data" not in json.dumps(result)
    assert run.call_args.args[0] == ["/bin/codex", "login", "status"]


def test_claude_status_hides_account_information(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(connection.shutil, "which", lambda _: "/bin/claude")
    output = json.dumps(
        {"loggedIn": True, "authMethod": "oauth", "email": "PRIVATE", "token": "SECRET"}
    )
    monkeypatch.setattr(
        connection.subprocess,
        "run",
        Mock(return_value=subprocess.CompletedProcess([], 0, output, "")),
    )
    result = connection.status("claude")
    assert result["connected"]
    assert "PRIVATE" not in json.dumps(result) and "SECRET" not in json.dumps(result)


def test_key_detection_does_not_expose_or_validate_the_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "DO_NOT_EXPOSE")
    monkeypatch.setattr(connection.shutil, "which", lambda _: "/bin/gemini")
    monkeypatch.setattr(Path, "is_file", lambda _: False)
    run = Mock(side_effect=AssertionError("Must not make a model call"))
    monkeypatch.setattr(connection.subprocess, "run", run)
    result = connection.status("gemini")
    assert result["connected"] and result["api_key_configured"]
    assert result["requires_key_confirmation"]
    assert "DO_NOT_EXPOSE" not in json.dumps(result)


def test_api_key_alone_does_not_claim_an_uninstalled_agent_is_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "PRIVATE")
    monkeypatch.setattr(connection.shutil, "which", lambda _: None)
    result = connection.status("codex")
    assert not result["installed"] and not result["connected"]
    assert result["api_key_configured"]


def test_failed_or_slow_probe_is_not_a_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(connection.shutil, "which", lambda _: "/bin/codex")
    monkeypatch.setattr(
        connection.subprocess,
        "run",
        Mock(side_effect=subprocess.TimeoutExpired("codex", 5)),
    )
    assert not connection.status("codex")["connected"]


def test_codex_key_is_sent_only_on_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "PRIVATE_KEY")
    monkeypatch.setattr(connection.shutil, "which", lambda _: "/bin/codex")
    run = Mock(return_value=subprocess.CompletedProcess([], 0, "SECRET", "SECRET"))
    monkeypatch.setattr(connection.subprocess, "run", run)
    result = connection.use_key("codex")
    assert run.call_args.args[0] == ["/bin/codex", "login", "--with-api-key"]
    assert run.call_args.kwargs["input"] == "PRIVATE_KEY"
    assert "PRIVATE_KEY" not in json.dumps(result) and "SECRET" not in json.dumps(
        result
    )


def test_login_opens_only_the_requested_provider_in_a_terminal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(connection.sys, "platform", "darwin")
    monkeypatch.setenv("OPENAI_API_KEY", "DO_NOT_PUT_IN_SCRIPT")
    monkeypatch.setattr(connection.shutil, "which", lambda _: "/Apps/Codex CLI/codex")
    script = tmp_path / "login.command"
    monkeypatch.setattr(
        connection.tempfile,
        "mkstemp",
        lambda **_: (os.open(script, os.O_CREAT | os.O_WRONLY, 0o600), str(script)),
    )
    run = Mock(return_value=subprocess.CompletedProcess([], 0, "", ""))
    monkeypatch.setattr(connection.subprocess, "run", run)
    assert connection.login("codex")["started"]
    assert run.call_args.args[0] == ["/usr/bin/open", "-a", "Terminal", str(script)]
    text = script.read_text()
    assert "'/Apps/Codex CLI/codex' login" in text
    assert "DO_NOT_PUT_IN_SCRIPT" not in text
    assert "PRIVATE_KEY" not in text


def test_cannot_start_login_when_cli_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(connection.shutil, "which", lambda _: None)
    with pytest.raises(ValueError, match="Install"):
        connection.login("codex")


@pytest.mark.parametrize(
    "contents,ready",
    [("{}", False), ("broken", False), ('{"refresh_token":"PRIVATE"}', True)],
)
def test_gemini_saved_signin_is_only_detected_from_credentials(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    contents: str,
    ready: bool,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(connection.shutil, "which", lambda _: "/bin/gemini")
    (tmp_path / ".gemini").mkdir()
    (tmp_path / ".gemini/oauth_creds.json").write_text(contents)
    result = connection.status("gemini")
    assert result["connected"] is ready
    assert "PRIVATE" not in json.dumps(result)
