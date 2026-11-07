"""The gate must hold the same across every supported agent's payload shape."""

import io
import json
from pathlib import Path
from typing import Any

import pytest

from joinery import hook
from joinery.agents import all_agents, get
from joinery.hook import normalise_event


def run_hook(monkeypatch: pytest.MonkeyPatch, event: Any) -> int:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    return hook.main()


def test_every_agent_has_auth_and_wiring_facts() -> None:
    from joinery.auth import for_agent

    for agent in all_agents():
        assert for_agent(agent.id).agent_id == agent.id
        assert agent.config_path and agent.pre_tool_event


# Each row is the same call spelled in one agent's style; all must be allowed
# (godot is enabled) or blocked (meshy is not) identically.
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Claude / Codex dunder spelling.
        ({"tool_name": "mcp__godot__scene_create", "tool_input": {}}, 0),
        ({"tool_name": "mcp__meshy__text_to_3d", "tool_input": {}}, 2),
        # Gemini single-underscore spelling.
        ({"tool_name": "mcp_godot_scene_create", "tool_input": {}}, 0),
        ({"tool_name": "mcp_meshy_text_to_3d", "tool_input": {}}, 2),
        # opencode: server in its own field, bare tool name.
        ({"tool": "scene_create", "server": "godot", "args": {}}, 0),
        ({"tool": "text_to_3d", "server": "meshy", "args": {}}, 2),
    ],
)
def test_mcp_gate_is_agent_neutral(
    monkeypatch: pytest.MonkeyPatch, raw: dict[str, Any], expected: int
) -> None:
    assert run_hook(monkeypatch, raw) == expected


def test_single_underscore_server_name_resolves_with_explicit_field() -> None:
    # A server whose own name contains "_" is ambiguous under single-underscore
    # spelling, so the explicit server field is authoritative.
    event = normalise_event(
        {"tool": "draw", "server": "joinery-paint", "args": {}}
    )
    assert event.server == "joinery-paint" and event.tool == "draw"


def test_shell_tool_names_normalise_across_agents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for shell in ("Bash", "run_shell_command"):
        event = {"tool_name": shell, "tool_input": {"command": "pip install diffusers"}}
        assert run_hook(monkeypatch, event) == 2


def test_fetch_tool_names_normalise_across_agents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for fetch in ("WebFetch", "web_fetch"):
        blocked = {"tool_name": fetch, "tool_input": {"url": "https://api.meshy.ai/x"}}
        assert run_hook(monkeypatch, blocked) == 2


def test_gemini_cwd_field_selects_project(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Gemini has no project-dir env var; it passes cwd on stdin. A custom tool
    # enabled only in that project must be allowed when cwd points there.
    (tmp_path / "joinery.toml").write_text(
        '[tools]\nenabled = []\n[[tools.custom]]\nid = "x"\nname = "X"\n'
        'domain = "2d"\nserver = "x"\ncommand = "x"\n'
    )
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    event = {"tool_name": "mcp_x_draw", "tool_input": {}, "cwd": str(tmp_path)}
    assert run_hook(monkeypatch, event) == 0


def test_mcp_call_without_server_is_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    # A malformed MCP call with no resolvable server fails closed.
    assert run_hook(monkeypatch, {"tool": "", "server": "", "args": {}}) == 0
    assert run_hook(monkeypatch, {"tool_name": "mcp__", "tool_input": {}}) == 2


def test_unknown_agent_rejected() -> None:
    with pytest.raises(KeyError):
        get("eclipse")


# ---- stopping vs blocking ---------------------------------------------------
# A generative attempt stops the turn; "not enabled here" only denies the call.


def run_with_agent(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    event: Any,
    agent: str | None,
) -> tuple[int, str, str]:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    code = hook.main(agent)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.mark.parametrize("agent", ["claude", "gemini"])
def test_generative_attempt_stops_the_turn(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], agent: str
) -> None:
    # json-stop agents: exit 0 with a decision that halts the loop *and* denies.
    event = {"tool_name": "Bash", "tool_input": {"command": "pip install diffusers"}}
    code, out, _ = run_with_agent(monkeypatch, capsys, event, agent)
    assert code == 0
    payload = json.loads(out)
    assert payload["continue"] is False
    assert "diffusers" in payload["stopReason"]
    # The deny fields are the floor, so it cannot fail open.
    assert payload["decision"] == "deny"
    assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize("agent", ["codex", "opencode"])
def test_generative_attempt_blocks_loudly_without_json_stop(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], agent: str
) -> None:
    event = {"tool_name": "Bash", "tool_input": {"command": "pip install diffusers"}}
    code, _, err = run_with_agent(monkeypatch, capsys, event, agent)
    assert code == 2
    assert "Rule Zero" in err and "diffusers" in err


def test_not_enabled_only_denies_the_call(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Aseprite is vetted but not selected: a config state, not a violation.
    event = {"tool_name": "mcp__aseprite__draw", "tool_input": {}}
    code, out, err = run_with_agent(monkeypatch, capsys, event, "claude")
    assert code == 2  # Refuses this call; the turn continues.
    assert out == ""
    assert "not enabled" in err and "Rule Zero" not in err


def test_generative_mcp_server_stops_even_when_unknown(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # meshy is not in the catalog at all, but it is a known generative service,
    # so it must stop the turn rather than read as a mere config gap.
    event = {"tool_name": "mcp__meshy__text_to_3d", "tool_input": {}}
    code, out, _ = run_with_agent(monkeypatch, capsys, event, "claude")
    assert code == 0
    assert json.loads(out)["continue"] is False


def test_missing_agent_flag_falls_back_to_blocking(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Without --agent we cannot know the stop path, so refuse the call outright.
    event = {"tool_name": "Bash", "tool_input": {"command": "pip install diffusers"}}
    code, out, err = run_with_agent(monkeypatch, capsys, event, None)
    assert code == 2 and out == "" and "Rule Zero" in err


def test_allowed_call_stays_silent(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    event = {"tool_name": "mcp__godot__scene_create", "tool_input": {}}
    code, out, err = run_with_agent(monkeypatch, capsys, event, "claude")
    assert (code, out, err) == (0, "", "")
