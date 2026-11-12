"""Agent roles in the shared policy core: what each role may call, for every provider."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from mcp.types import CallToolResult, TextContent

from bridge.host import Bridge
from joinery.gateway_rpc import NO_NESTED_TASKS, GatewayRPC, rpc
from joinery.hook import violation
from joinery.policy import Severity
from joinery.roles import (
    ROLE_ENV,
    SPECS,
    read_only_command,
    reviewer_settings,
    set_reviewer,
    spec,
)


def payload(
    provider: str, tool: str, args: dict[str, Any], cwd: Path
) -> dict[str, Any]:
    """One registry tool call as each provider's hook sends it."""
    if provider == "claude":
        return {
            "tool_name": f"mcp__joinery-registry__{tool}",
            "tool_input": args,
            "cwd": str(cwd),
        }
    if provider == "codex":
        return {
            "tool_name": f"mcp__joinery_registry__{tool}",
            "tool_input": args,
            "cwd": str(cwd),
        }
    if provider == "gemini":
        return {
            "tool_name": f"mcp_joinery-registry_{tool}",
            "tool_input": args,
            "cwd": str(cwd),
        }
    return {
        "tool": tool,
        "args": args,
        "server": "joinery-registry",
        "directory": str(cwd),
    }


PROVIDERS = ("claude", "codex", "gemini", "opencode")


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name in ("CLAUDE_PROJECT_DIR", "CODEX_PROJECT_DIR", "JOINERY_INSPECTION_ONLY"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "joinery.toml").write_text('[tools]\nenabled=["godot","blender"]\n')
    return tmp_path


def judged(
    monkeypatch: pytest.MonkeyPatch, role: str, raw: dict[str, Any]
) -> Severity | None:
    if role:
        monkeypatch.setenv(ROLE_ENV, role)
    else:
        monkeypatch.delenv(ROLE_ENV, raising=False)
    verdict = violation(raw)
    return verdict.severity if verdict is not None else None


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize(
    "role,tool,args,allowed",
    [
        # Reviewers and debuggers never change, submit, delegate or start work.
        ("reviewer", "submit_for_review", {"summary": "x"}, False),
        ("reviewer", "edit_scene", {}, False),
        ("reviewer", "start_task", {"title": "t", "request": "r"}, False),
        ("reviewer", "inspect_in_parallel", {"tasks": []}, False),
        ("reviewer", "report_diagnosis", {"cause": "x"}, False),
        ("reviewer", "report_review", {"notes": []}, True),
        ("reviewer", "view_editor", {}, True),
        ("debugger", "submit_for_review", {"summary": "x"}, False),
        ("debugger", "edit_scene", {}, False),
        ("debugger", "report_review", {"notes": []}, False),
        ("debugger", "report_diagnosis", {"cause": "x"}, True),
        ("debugger", "reproduce_error", {}, True),
        # The design check reads and reports findings; it changes nothing.
        ("designer", "report_design", {"findings": []}, True),
        ("designer", "view_editor", {}, True),
        ("designer", "submit_for_review", {"summary": "x"}, False),
        ("designer", "edit_scene", {}, False),
        ("designer", "start_task", {"title": "t", "request": "r"}, False),
        ("designer", "report_review", {"notes": []}, False),
        ("designer", "blockout", {"layout": {}}, False),
        ("reviewer", "report_design", {"findings": []}, False),
        ("lead", "report_design", {"findings": []}, False),
        ("worker", "report_design", {"findings": []}, False),
        # Tasks are one level deep; only the reviewer and debugger report.
        ("worker", "start_task", {"title": "t", "request": "r"}, False),
        ("worker", "submit_for_review", {"summary": "x"}, True),
        ("worker", "report_review", {"notes": []}, False),
        ("lead", "start_task", {"title": "t", "request": "r"}, True),
        ("lead", "report_review", {"notes": []}, False),
        ("lead", "reproduce_error", {}, False),
        ("", "submit_for_review", {"summary": "x"}, True),
    ],
)
def test_each_role_may_call_only_its_own_tools_whichever_provider_runs_it(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    role: str,
    tool: str,
    args: dict[str, Any],
    allowed: bool,
) -> None:
    severity = judged(monkeypatch, role, payload(provider, tool, args, project))
    assert severity == (None if allowed else Severity.DENY)


@pytest.mark.parametrize("role", ["reviewer", "debugger", "designer"])
@pytest.mark.parametrize(
    "server,tool,allowed",
    [
        ("godot", "read_scene", True),
        ("blender", "get_objects_summary", True),
        ("godot", "add_node", False),
        ("godot", "write_file", False),
        ("blender", "execute_code", False),
        ("joinery-paint", "operate_document", False),
    ],
)
def test_read_only_roles_reach_audited_reads_only(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
    role: str,
    server: str,
    tool: str,
    allowed: bool,
) -> None:
    raw = payload(
        "claude",
        "call_tool",
        {"server": server, "tool": tool, "arguments": {}},
        project,
    )
    assert judged(monkeypatch, role, raw) == (None if allowed else Severity.DENY)


@pytest.mark.parametrize(
    "command,allowed",
    [
        ("sed -n 1,80p scripts/player.gd", True),
        ("bash -lc 'rg -n \"func _ready\" scripts'", True),
        ("cat .godot/editor/log.txt | tail -50", True),
        ("git log --oneline -5 && git diff HEAD~1", True),
        ("find scripts -name '*.gd'", True),
        ("rm -rf scripts", False),
        ("echo x > scripts/a.gd", False),
        ("sed -i s/a/b/ scripts/a.gd", False),
        ("sed -n 's/a/b/w out' scripts/a.gd", False),
        ("find . -name '*.gd' -delete", False),
        ('python3 -c \'open("a","w")\'', False),
        ("curl https://example.com", False),
        ("uv run joinery transactions accept --project . --id x", False),
        ("joinery bible --approve me", False),
        ("git commit -am x", False),
        ("cat $(echo a)", False),
    ],
)
def test_read_only_roles_run_only_reading_commands(
    project: Path, monkeypatch: pytest.MonkeyPatch, command: str, allowed: bool
) -> None:
    assert read_only_command(command) is allowed
    for role in ("reviewer", "debugger", "designer"):
        raw = {
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "cwd": str(project),
        }
        assert judged(monkeypatch, role, raw) == (None if allowed else Severity.DENY)


def test_rule_zero_still_stops_every_role_first(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = {
        "tool_name": "Bash",
        "tool_input": {"command": "curl https://meshy.ai/api"},
        "cwd": str(project),
    }
    for role in ("lead", "worker", "reviewer", "debugger", "designer"):
        assert judged(monkeypatch, role, raw) == Severity.STOP
    fetch = {"tool_name": "WebFetch", "tool_input": {"url": "https://example.com"}}
    assert (
        judged(monkeypatch, "debugger", {**fetch, "cwd": str(project)}) == Severity.DENY
    )
    assert judged(monkeypatch, "lead", {**fetch, "cwd": str(project)}) is None
    assert (
        judged(monkeypatch, "designer", {**fetch, "cwd": str(project)}) == Severity.DENY
    )
    for native in ("Write", "Edit", "apply_patch", "Task"):
        assert (
            judged(
                monkeypatch,
                "designer",
                {"tool_name": native, "tool_input": {}, "cwd": str(project)},
            )
            == Severity.DENY
        )


def test_an_unknown_role_is_refused_not_read_as_the_lead(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = payload("claude", "submit_for_review", {"summary": "x"}, project)
    assert judged(monkeypatch, "admin", raw) == Severity.DENY
    with pytest.raises(ValueError, match="Unknown agent role"):
        spec("admin")


def test_roles_have_their_own_turns_and_time_boxes() -> None:
    assert set(SPECS) == {
        "lead",
        "worker",
        "reviewer",
        "debugger",
        "playtester",
        "accessibility",
        "performance",
        "designer",
        "sound-designer",
    }
    assert spec("designer").turn == "design" and spec("designer").read_only
    assert spec("lead").turn == "chat" and not spec("lead").read_only
    assert spec("worker").turn == "task"
    assert spec("reviewer").turn == "review" and spec("reviewer").read_only
    assert spec("reviewer").seconds == 90
    assert spec("debugger").turn == "diagnosis" and spec("debugger").read_only
    assert spec("playtester").turn == "playtest" and spec("playtester").read_only
    assert spec("accessibility").turn == "check" and spec("accessibility").read_only
    assert spec("performance").turn == "check" and spec("performance").read_only


# --- The gateway endpoint -------------------------------------------------------------


@pytest.mark.parametrize(
    "role,method,params,refusal",
    [
        ("reviewer", "submit", {"summary": "x"}, "cannot submit"),
        ("reviewer", "editor_edit", {}, "cannot change"),
        ("reviewer", "start_task", {"title": "t", "request": "r"}, NO_NESTED_TASKS),
        ("reviewer", "delegate", {"tasks": []}, "unavailable"),
        ("reviewer", "report_diagnosis", {"cause": "x"}, "Only the debugger"),
        ("reviewer", "reproduce", {}, "Only the debugger"),
        (
            "reviewer",
            "call",
            {"server": "godot", "tool": "write_file", "arguments": {}},
            "only inspect",
        ),
        ("reviewer", "accept", {"id": "x"}, "no approval"),
        ("debugger", "report_review", {"notes": []}, "Only the reviewer"),
        ("debugger", "submit", {"summary": "x"}, "cannot submit"),
        ("debugger", "decision", {"accept": True}, "no approval"),
        ("worker", "start_task", {"title": "t", "request": "r"}, NO_NESTED_TASKS),
        ("worker", "report_review", {"notes": []}, "Only the reviewer"),
        ("lead", "report_review", {"notes": []}, "Only the reviewer"),
        ("lead", "reproduce", {}, "Only the debugger"),
        ("designer", "submit", {"summary": "x"}, "cannot submit"),
        ("designer", "editor_edit", {}, "cannot change"),
        ("designer", "report_review", {"notes": []}, "Only the reviewer"),
        ("designer", "blockout", {"layout": {}}, "Specialists cannot build"),
        ("reviewer", "report_design", {"findings": []}, "Only the design check"),
        ("lead", "report_design", {"findings": []}, "Only the design check"),
    ],
)
def test_the_endpoint_refuses_what_the_role_may_not_do(
    role: str, method: str, params: dict[str, Any], refusal: str
) -> None:
    async def no_calls(*_: Any) -> None:
        pytest.fail("A refused request reached the gateway")

    async def close() -> None:
        return None  # The lead's endpoint closes its gateway; nothing else may run.

    gateway = SimpleNamespace(call=no_calls, close=close, turn=None)

    async def run() -> None:
        endpoint = GatewayRPC(gateway, role=role)  # type: ignore[arg-type]
        try:
            await endpoint.start()
            with pytest.raises(RuntimeError, match=refusal):
                await rpc(endpoint.path, method, params)
        finally:
            await endpoint.close()

    asyncio.run(run())


def test_the_reviewer_reports_through_its_endpoint_and_reads_audited_tools() -> None:
    called: list[tuple[str, str]] = []

    async def call(
        server: str, tool: str, _arguments: Any, *, view: bool = False
    ) -> CallToolResult:
        assert view  # a read-only role reads through the stage's read-only view
        called.append((server, tool))
        return CallToolResult(content=[TextContent(type="text", text="scene")])

    gateway = SimpleNamespace(
        call=call,
        serialise=lambda value: json.loads(value.model_dump_json()),
        transaction=None,
    )

    async def run() -> GatewayRPC:
        endpoint = GatewayRPC(gateway, role="reviewer")  # type: ignore[arg-type]
        try:
            await endpoint.start()
            await rpc(
                endpoint.path,
                "call",
                {"server": "godot", "tool": "read_scene", "arguments": {}},
            )
            with pytest.raises(RuntimeError, match="severity"):
                await rpc(
                    endpoint.path,
                    "report_review",
                    {"notes": [{"severity": "fatal", "text": "x"}]},
                )
            answer = await rpc(
                endpoint.path,
                "report_review",
                {
                    "notes": [
                        {
                            "severity": "warn",
                            "text": "The lamp  floats.",
                            "file": "res://lamp.tscn",
                        }
                    ]
                },
            )
            assert answer["recorded"] == 1
        finally:
            await endpoint.close()
        return endpoint

    endpoint = asyncio.run(run())
    assert called == [("godot", "read_scene")]
    assert endpoint.reports == [
        {
            "notes": [
                {"severity": "warn", "text": "The lamp floats.", "file": "lamp.tscn"}
            ]
        }
    ]


# --- Providers --------------------------------------------------------------------------


@pytest.mark.parametrize("agent", PROVIDERS)
def test_every_provider_runs_each_role_with_its_hook_and_transport(agent: str) -> None:
    reviewer = Bridge(agent, role="reviewer")
    session = reviewer._adapter.session  # type: ignore[attr-defined]
    assert reviewer.role == "reviewer"
    assert session.role == "reviewer" and session.inspection_only
    debugger = Bridge(agent, role="debugger")
    assert debugger._adapter.session.role == "debugger"  # type: ignore[attr-defined]
    designer = Bridge(agent, role="designer")
    session = designer._adapter.session  # type: ignore[attr-defined]
    assert session.role == "designer" and session.inspection_only
    worker = Bridge(agent, role="worker")
    session = worker._adapter.session  # type: ignore[attr-defined]
    assert session.role == "worker" and not session.inspection_only
    lead = Bridge(agent)
    assert lead.role == "lead" and lead._adapter.session.role == ""  # type: ignore[attr-defined]


# --- Settings ---------------------------------------------------------------------------


def test_reviewer_settings_default_on_and_save_beside_other_settings(
    tmp_path: Path,
) -> None:
    assert reviewer_settings(tmp_path).enabled is True
    (tmp_path / "joinery.toml").write_text(
        '[tools]\nenabled = ["godot"]\n\n[agents]\nparallel = 2\n\n[timeline]\nauto_commit = true\n'
    )
    set_reviewer(tmp_path, enabled=False, model="")
    saved = reviewer_settings(tmp_path)
    assert saved.enabled is False and saved.model == "" and saved.rounds == 2
    set_reviewer(tmp_path, enabled=True, model="gpt-5.2-codex")
    text = (tmp_path / "joinery.toml").read_text()
    assert "parallel = 2" in text and "auto_commit = true" in text
    assert text.count("[agents]") == 1 and text.count("reviewer =") == 1
    assert reviewer_settings(tmp_path).model == "gpt-5.2-codex"
    with pytest.raises(ValueError):
        set_reviewer(tmp_path, enabled=None, model='bad"model')
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    set_reviewer(fresh, enabled=False, model=None)
    assert reviewer_settings(fresh).enabled is False


def test_the_settings_page_reads_and_saves_the_reviewer_through_the_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from joinery import cli

    (tmp_path / "joinery.toml").write_text('[tools]\nenabled = ["godot"]\n')
    assert cli.main(["reviewer", "--project", str(tmp_path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "enabled": True,
        "model": "",
        "rounds": 2,
        "review": "always",
        "light_model": {},
    }
    argv = ["reviewer", "--project", str(tmp_path), "--set", "off", "--model", "m2"]
    assert cli.main([*argv, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["enabled"] is False
    assert reviewer_settings(tmp_path).model == "m2"
    bad = ["reviewer", "--project", str(tmp_path), "--model", 'a"b', "--json"]
    assert cli.main(bad) == 1
    assert "error" in json.loads(capsys.readouterr().out)
