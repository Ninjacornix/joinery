"""Inkscape's desktop-only MCP surface is refused in the offline worker, for every agent."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic import ValidationError

from joinery.catalog import McpSource, blocked_operation, load_catalog
from joinery.hook import mcp_violation
from joinery.mcp_runtime import MCPConnection, ServerSpec
from joinery.policy import Severity
from joinery.tools import get


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "joinery.toml").write_text('[tools]\nenabled=["godot","inkscape"]\n')
    return tmp_path


def test_live_tool_is_denied_not_stopped(project: Path) -> None:
    verdict = mcp_violation("inkscape", "inkscape_live", project)
    assert verdict is not None and verdict.severity is Severity.DENY


def test_extension_run_live_is_denied_but_headless_operations_are_not(
    project: Path,
) -> None:
    for operation in ("list", "describe", "run"):
        assert (
            mcp_violation(
                "inkscape", "inkscape_extension", project, {"operation": operation}
            )
            is None
        )
    verdict = mcp_violation(
        "inkscape", "inkscape_extension", project, {"operation": "run_live"}
    )
    assert verdict is not None and verdict.severity is Severity.DENY
    assert "run_live" in verdict.reason


def test_operation_blocks_apply_only_to_their_own_server_and_tool() -> None:
    catalog = load_catalog()
    live = {"operation": "run_live"}
    assert blocked_operation(catalog, "inkscape", "inkscape_extension", live)
    assert not blocked_operation(catalog, "inkscape", "inkscape_vector", live)
    assert not blocked_operation(catalog, "godot", "inkscape_extension", live)
    assert not blocked_operation(catalog, "inkscape", "inkscape_extension", {})
    assert not blocked_operation(
        catalog, "inkscape", "inkscape_extension", {"operation": ["run_live"]}
    )


@pytest.mark.parametrize(
    "payload",
    [
        # Claude Code and Codex
        {"tool_name": "mcp__inkscape__inkscape_extension"},
        # Gemini CLI
        {"tool_name": "mcp_inkscape_inkscape_extension", "server": "inkscape"},
        # opencode's plugin bridge
        {"tool": "inkscape_extension", "server": "inkscape"},
    ],
)
def test_agent_hook_refuses_run_live_for_every_agent(
    project: Path, monkeypatch: pytest.MonkeyPatch, payload: dict[str, str]
) -> None:
    from joinery.hook import violation

    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    arguments_key = "args" if "tool" in payload else "tool_input"
    live = {**payload, arguments_key: {"operation": "run_live", "target": "x"}}
    headless = {**payload, arguments_key: {"operation": "run", "target": "x"}}
    verdict = violation(live)
    assert verdict is not None and verdict.severity is Severity.DENY
    assert "run_live" in verdict.reason
    assert violation(headless) is None


def test_gateway_connection_refuses_run_live_before_starting_the_worker(
    project: Path,
) -> None:
    spec = ServerSpec(name="inkscape", command="docker", args=["run"])
    connection = MCPConnection(spec, project)

    async def call() -> None:
        await connection.call(
            "inkscape_extension", {"operation": "run_live", "target": "x"}
        )

    with pytest.raises(PermissionError, match="run_live"):
        asyncio.run(call())
    assert connection._task is None


def test_blocked_operations_must_name_tool_and_operation() -> None:
    base = {
        "server": "s",
        "repo": "https://example.invalid/r",
        "ref": "0" * 40,
        "tier": "community",
        "stars": 60,
        "last_push": "2026-11-01",
        "licence": "MIT",
    }
    assert McpSource(**base, blocked_operations=("tool:op",)).blocked_operations
    with pytest.raises(ValidationError, match="tool:operation"):
        McpSource(**base, blocked_operations=("run_live",))


def test_agents_are_told_where_lasting_svg_edits_go() -> None:
    notes = dict(get("inkscape").mcp_notes)
    assert "run_live" in notes["inkscape_extension"]
    for tool in ("inkscape_extension", "inkscape_vector", "inkscape_file"):
        assert "operate_vector" in notes[tool]
