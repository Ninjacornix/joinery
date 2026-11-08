"""Release provider checks must neither inherit credentials nor accept another CLI."""

import asyncio
import sys
from pathlib import Path

import pytest

from release.provider_smoke import isolated_environment, smoke


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


def test_smoke_refuses_wrong_executable_before_any_protocol_request() -> None:
    # A real subprocess with the wrong version must fail at preflight, before
    # app-server can be invoked. No Python protocol simulator is involved.
    with pytest.raises(RuntimeError, match="Expected codex-cli"):
        asyncio.run(smoke(sys.executable))
