"""Native endpoint restrictions and child-provider lifecycle without inference."""

import asyncio
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp.types import CallToolResult, TextContent

from bridge.protocol import Done, MessageDelta
from bridge.specialists import Specialists
from joinery.gateway_rpc import GatewayRPC, rpc
from joinery.hook import main as hook_main
from joinery.hook import violation
from joinery.policy import Severity


@pytest.mark.parametrize(
    "method,params",
    [
        ("call", {"server": "godot", "tool": "add_node", "arguments": {}}),
        (
            "call",
            {"server": "joinery-paint", "tool": "operate_document", "arguments": {}},
        ),
        ("editor_edit", {}),
        ("delegate", {"tasks": []}),
        ("submit", {"summary": "An ashtray"}),
    ],
)
def test_read_only_specialist_endpoint_cannot_mutate_or_delegate(
    tmp_path: Path, method, params
):
    async def no_calls(*_):
        pytest.fail("A restricted request reached a mutating gateway")

    gateway = SimpleNamespace(call=no_calls, close=no_calls)

    async def run():
        endpoint = GatewayRPC(gateway, inspection_only=True)
        try:
            await endpoint.start()
            with pytest.raises(RuntimeError):
                await rpc(endpoint.path, method, params)
        finally:
            await endpoint.close()
        assert not endpoint.directory.exists()

    asyncio.run(run())


def test_specialist_endpoint_allows_only_audited_reads(tmp_path: Path):
    called = []

    async def call(server, tool, arguments):
        called.append((server, tool))
        return CallToolResult(content=[TextContent(type="text", text="read evidence")])

    gateway = SimpleNamespace(
        call=call,
        serialise=lambda value: json.loads(value.model_dump_json()),
        transaction=None,
    )

    async def run():
        endpoint = GatewayRPC(gateway, inspection_only=True)
        try:
            await endpoint.start()
            result = await rpc(
                endpoint.path,
                "call",
                {"server": "godot", "tool": "read_scene", "arguments": {}},
            )
            assert result["content"][0]["text"] == "read evidence"
        finally:
            await endpoint.close()

    asyncio.run(run())
    assert called == [("godot", "read_scene")]


@pytest.mark.parametrize(
    "tool,args",
    [
        ("Bash", {"command": "echo test"}),
        ("mcp__joinery_registry__edit_scene", {}),
        (
            "mcp__joinery_registry__call_tool",
            {"server": "blender", "tool": "execute_code"},
        ),
        ("mcp__joinery_registry__inspect_in_parallel", {}),
        ("mcp__joinery_registry__submit_for_review", {"summary": "An ashtray"}),
        ("mcp__joinery-registry__submit_for_review", {"summary": "An ashtray"}),
    ],
)
def test_specialist_shared_hook_denies_escape_paths(
    tmp_path: Path, monkeypatch, tool, args
):
    monkeypatch.setenv("JOINERY_INSPECTION_ONLY", "1")
    (tmp_path / "joinery.toml").write_text('[tools]\nenabled=["godot","blender"]\n')
    result = violation({"tool_name": tool, "tool_input": args, "cwd": str(tmp_path)})
    assert result is not None and result.severity == Severity.DENY


def test_parallel_specialists_are_bounded_and_reaped_after_cancellation(
    tmp_path: Path, monkeypatch
):
    children = []

    class Child:
        def __init__(self, agent, *, inspection_only):
            assert inspection_only
            self.closed = False
            children.append(self)

        def bind_gateway(self, path):
            self.endpoint = path

        async def connect(self, project, profile):
            assert project == tmp_path

        async def send(self, prompt):
            assert prompt.selection.mode == "fast"
            if len(children) == 2:
                started.set()
            await asyncio.Event().wait()
            yield Done()

        async def close(self):
            self.closed = True

    monkeypatch.setattr("bridge.specialists.Bridge", Child)

    async def run():
        gateway = SimpleNamespace(
            project=tmp_path, editor_context={}, progress=lambda _: None
        )
        group = Specialists(gateway, "codex", "")
        batch = asyncio.create_task(
            group.inspect(
                [
                    {"name": "geometry", "question": "inspect geometry"},
                    {"name": "materials", "question": "inspect materials"},
                ]
            )
        )
        await asyncio.wait_for(started.wait(), 2)
        assert len(children) == 2
        await group.close()
        await asyncio.gather(batch, return_exceptions=True)
        assert all(child.closed and not child.endpoint.exists() for child in children)

    started = asyncio.Event()
    asyncio.run(run())


def test_one_specialist_failure_does_not_fabricate_success_or_discard_other_findings(
    tmp_path: Path, monkeypatch
):
    class Child:
        def __init__(self, agent, *, inspection_only):
            pass

        def bind_gateway(self, path):
            pass

        async def connect(self, project, profile):
            pass

        async def close(self):
            pass

        async def send(self, prompt):
            if "bad question" in prompt.text:
                raise ValueError("fixture failure")
            yield MessageDelta(text="verified evidence")
            yield Done()

    monkeypatch.setattr("bridge.specialists.Bridge", Child)

    async def run():
        gateway = SimpleNamespace(
            project=tmp_path, editor_context={}, progress=lambda _: None
        )
        group = Specialists(gateway, "codex", "")
        results = await group.inspect(
            [
                {"name": "bad", "question": "bad question"},
                {"name": "good", "question": "inspect"},
            ]
        )
        assert [r["status"] for r in results] == ["failed", "done"]
        assert results[1]["text"] == "verified evidence"

    asyncio.run(run())


@pytest.mark.parametrize("specialist", [True, False])
def test_a_rule_zero_hook_stops_the_chat_host(
    tmp_path: Path, monkeypatch, specialist: bool
):
    """The main agent's hook and a specialist's both reach the chat host."""
    (tmp_path / "joinery.toml").write_text('[tools]\nenabled=["godot"]\n')

    async def run():
        stopped = asyncio.Event()
        reasons = []

        async def on_stop(reason):
            reasons.append(reason)
            stopped.set()

        async def close():
            pass

        endpoint = GatewayRPC(
            SimpleNamespace(close=close), on_stop, inspection_only=specialist
        )
        try:
            await endpoint.start()
            if specialist:
                monkeypatch.setenv("JOINERY_INSPECTION_ONLY", "1")
            else:
                monkeypatch.delenv("JOINERY_INSPECTION_ONLY", raising=False)
            monkeypatch.setenv("JOINERY_POLICY_ENDPOINT", str(endpoint.path))
            monkeypatch.setattr(
                "sys.stdin",
                io.StringIO(
                    json.dumps(
                        {
                            "tool_name": "Bash",
                            "tool_input": {"command": "curl https://api.meshy.ai/v1"},
                            "cwd": str(tmp_path),
                        }
                    )
                ),
            )
            assert await asyncio.to_thread(hook_main, "claude") == 0
            await asyncio.wait_for(stopped.wait(), 2)
            assert "Rule Zero" in reasons[0]
        finally:
            await endpoint.close()

    asyncio.run(run())
