import io
import json
from pathlib import Path
from typing import Any

import pytest

from joinery import hook
from joinery.policy import command_violation, load_denylist, url_violation


def run_hook(monkeypatch: pytest.MonkeyPatch, event: Any) -> int:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    return hook.main()


@pytest.mark.parametrize(
    ("tool", "expected"),
    [
        ("mcp__godot__scene_create", 0),
        ("mcp__joinery-verify__run_scene", 0),
        ("mcp__joinery_registry__discover_tools", 0),
        ("mcp__joinery_registry__submit_for_review", 0),
        ("mcp__joinery_verify__run_scene", 0),
        ("mcp__blender__execute", 0),
        ("mcp__inkscape__inkscape_system", 2),  # Vetted, but not selected.
        ("mcp__meshy__text_to_3d", 2),
        ("mcp__claude_ai_Gmail__authenticate", 2),
        ("mcp__plugin_x_y__z", 2),
    ],
)
def test_mcp_allowlist(
    monkeypatch: pytest.MonkeyPatch, tool: str, expected: int
) -> None:
    assert run_hook(monkeypatch, {"tool_name": tool, "tool_input": {}}) == expected


def test_blocked_tool_on_allowed_server(monkeypatch: pytest.MonkeyPatch) -> None:
    event = {"tool_name": "mcp__godot__game_http_request", "tool_input": {}}
    assert run_hook(monkeypatch, event) == 2


def test_custom_mcp_follows_project_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "joinery.toml").write_text(
        '[tools]\nenabled = []\n[[tools.custom]]\nid = "x"\nname = "X"\n'
        'domain = "2d"\nserver = "x"\ncommand = "x"\n'
    )
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    assert run_hook(monkeypatch, {"tool_name": "mcp__x__draw", "tool_input": {}}) == 0
    assert (
        run_hook(monkeypatch, {"tool_name": "mcp__blender__x", "tool_input": {}}) == 2
    )


def test_broken_policy_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> None:
        raise RuntimeError("catalog unreadable")

    monkeypatch.setattr(hook, "load_catalog", boom)
    assert run_hook(monkeypatch, {"tool_name": "mcp__godot__x"}) == 2


def test_malformed_input_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    assert hook.main() == 2


def test_other_tools_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    event = {"tool_name": "Read", "tool_input": {"file_path": "/x"}}
    assert run_hook(monkeypatch, event) == 0


@pytest.mark.parametrize(
    "command",
    [
        "pip install diffusers",
        "python -m pip install 'diffusers==0.30'",
        "uv add ElevenLabs",
        "uv pip install stable_audio_tools",
        "cd x && npm install @fal-ai/client@1.2",
        "uvx blender-mcp",
        "FOO=1 pip3 install replicate",
        "curl https://api.meshy.ai/v2/text-to-3d",
        "wget sketchfab.com/models/1",
    ],
)
def test_denied_commands(command: str) -> None:
    assert command_violation(command, load_denylist()) is not None


@pytest.mark.parametrize(
    "command",
    [
        "uv run pytest",
        "pip install pydantic",
        "grep -r diffusers docs",
        "echo pip install diffusers",
        "curl https://notmeshy.ai/x",
        "git commit -m 'remove replicate'",
    ],
)
def test_allowed_commands(command: str) -> None:
    assert command_violation(command, load_denylist()) is None


def test_url_check_uses_host_only() -> None:
    denylist = load_denylist()
    assert url_violation("https://api.elevenlabs.io/v1", denylist)
    assert (
        url_violation("https://docs.godotengine.org/?q=elevenlabs.io", denylist) is None
    )


def _claude_payload(project: Path, tool: str, server: object) -> dict[str, Any]:
    """A PreToolUse payload as Claude Code sends it for a session MCP server."""
    return {
        "session_id": "0691144d-7c88-4c52-b387-a1b5d1473b37",
        "cwd": str(project),
        "permission_mode": "default",
        "effort": {"level": "medium"},
        "hook_event_name": "PreToolUse",
        "tool_name": tool,
        "tool_input": {},
        "tool_use_id": "toolu_01NgKz7LXDCfVmCxZVaAAsvd",
        "mcp_server": server,
    }


def test_claude_mcp_server_object_is_read_by_name(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "joinery.toml").write_text(
        '[tools]\nenabled = ["blender", "godot", "krita", "supercollider"]\n'
    )
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.setenv("JOINERY_STAGE", "none")
    event = _claude_payload(
        tmp_path,
        "mcp__joinery-registry__inspect_editor",
        {"name": "joinery-registry", "source": "dynamic"},
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    assert hook.main("claude") == 0
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    ("tool", "server", "expected"),
    [
        ("mcp__inkscape__x", {"name": "inkscape", "source": "project"}, 2),
        ("mcp__meshy__text_to_3d", {"name": "meshy", "source": "project"}, 0),
        ("mcp__inkscape__x", {"source": "project"}, 2),
        ("mcp__godot__scene_create", {"source": "project"}, 0),
    ],
)
def test_claude_mcp_server_object_keeps_the_gate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    tool: str,
    server: object,
    expected: int,
) -> None:
    (tmp_path / "joinery.toml").write_text('[tools]\nenabled = ["godot"]\n')
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.setattr(
        "sys.stdin", io.StringIO(json.dumps(_claude_payload(tmp_path, tool, server)))
    )
    assert hook.main("claude") == expected
    out = capsys.readouterr().out
    if "meshy" in tool:
        # A generative service still stops the turn (JSON on stdout, exit 0).
        assert json.loads(out)["continue"] is False
    else:
        assert out == ""
