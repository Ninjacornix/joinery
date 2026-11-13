"""At connect, Joinery's own policy must let Joinery's own tools through in every
agent's real hook payload shape; a refusal stops the connection loudly."""

from __future__ import annotations

from pathlib import Path

import pytest

from bridge import connect_errors
from bridge.connect_errors import ConnectError, check_policy
from joinery.agents import all_agents
from joinery.hook import normalise_event, registry_probe, self_check
from joinery.policy import Severity, Verdict


@pytest.fixture(autouse=True)
def no_agent_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # The hook prefers these to the payload's cwd; a test run inside an agent sets them.
    for name in ("CLAUDE_PROJECT_DIR", "CODEX_PROJECT_DIR", "JOINERY_AGENT_ROLE"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("agent", [a.id for a in all_agents()])
def test_every_payload_shape_reaches_the_registry(agent: str, tmp_path: Path) -> None:
    (tmp_path / "joinery.toml").write_text('[tools]\nenabled=["godot"]\n')
    event = normalise_event(registry_probe(agent, tmp_path))
    assert event.kind == "mcp"
    assert event.server.replace("_", "-") == "joinery-registry"
    assert self_check(agent, tmp_path) is None


def test_a_refusal_stops_the_connection_as_a_joinery_bug(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    refused = Verdict(Severity.DENY, "MCP server 'joinery-registry' is not enabled")
    monkeypatch.setattr(connect_errors, "self_check", lambda _a, _p: refused)
    (tmp_path / "joinery.toml").write_text("")
    with pytest.raises(ConnectError) as stopped:
        check_policy("claude", tmp_path)
    assert stopped.value.reason == "gate"
    assert str(stopped.value).startswith("Joinery bug")
    assert "not enabled" in stopped.value.details


def test_the_check_never_hands_its_stdin_to_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # In the Windows chat host stdin is the editor's pipe, which a reader thread waits
    # on; a child sharing it hangs as it starts, and the connection with it.
    import subprocess

    from joinery import hook

    seen: list[object] = []
    real = subprocess.run

    def run(*args: object, **kwargs: object) -> object:
        seen.append(kwargs.get("stdin"))
        return real(*args, **kwargs)  # type: ignore[call-overload]

    monkeypatch.setattr(hook.subprocess, "run", run)
    (tmp_path / "joinery.toml").write_text('[tools]\nenabled=["godot"]\n')
    assert self_check("codex", tmp_path) is None
    assert seen == [subprocess.DEVNULL]
