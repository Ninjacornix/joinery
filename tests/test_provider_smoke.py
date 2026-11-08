"""Release provider checks must neither inherit credentials nor accept another CLI."""

import json
import sys
from pathlib import Path

import pytest

from release.provider_smoke import isolated_environment, main


def test_native_smoke_environment_excludes_auth_and_uses_empty_private_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "ACCESS_TOKEN",
        "CODEX_HOME",
        "HOME",
        "APPDATA",
        "USERPROFILE",
        "LOCALAPPDATA",
        "NODE_OPTIONS",
        "PYTHONPATH",
        "CODEX_CONFIG",
        "OPENAI_BASE_URL",
    ):
        monkeypatch.setenv(key, "must-not-reach-provider")
    environment = isolated_environment(tmp_path)
    assert "must-not-reach-provider" not in environment.values()
    assert not any("KEY" in name or "TOKEN" in name for name in environment)
    for key in ("HOME", "CODEX_HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
        path = Path(environment[key])
        assert path.is_relative_to(tmp_path) and path.is_dir()
        assert list(path.iterdir()) == []


def test_smoke_retains_failure_evidence_for_wrong_native_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A real subprocess with the wrong version must fail at preflight, before
    # app-server can be invoked. No Python protocol simulator is involved.
    destination = tmp_path / "evidence" / "provider.json"
    monkeypatch.setattr(
        sys,
        "argv",
        ["provider_smoke", "--codex", sys.executable, "--output", str(destination)],
    )
    with pytest.raises(RuntimeError, match="Expected codex-cli"):
        main()
    report = json.loads(destination.read_text())
    assert report["status"] == "failed" and report["error_type"] == "RuntimeError"
    assert "Expected codex-cli" in report["error"]
    assert "stdout" not in report and "stderr" not in report
