"""The bridge is agent-neutral and fails closed when the gate cannot hold."""

import asyncio

import pytest

from bridge import Bridge, GateUnavailable
from bridge.adapters import adapter_for, ready_agents
from bridge.host import unwireable
from bridge.protocol import Envelope, Prompt
from joinery.agents import all_agents


def test_bridge_opens_for_every_gated_agent() -> None:
    for agent in all_agents():
        if agent.id not in unwireable():
            assert Bridge(agent.id).agent.id == agent.id


def test_bridge_refuses_ungated_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    # Simulate an agent without a reliable blocking hook: the bridge must refuse.
    monkeypatch.setattr("bridge.host.unwireable", lambda: ["codex"])
    with pytest.raises(GateUnavailable):
        Bridge("codex")


def test_unknown_agent_rejected() -> None:
    with pytest.raises(KeyError):
        Bridge("eclipse")


def test_adapters_exist_for_all_agents() -> None:
    for agent in all_agents():
        assert adapter_for(agent.id).agent_id == agent.id


def test_supported_transports_are_implemented() -> None:
    assert ready_agents() == ["claude", "codex", "gemini", "opencode"]


def test_unconnected_transport_cannot_send() -> None:
    bridge = Bridge("claude")
    prompt = Prompt(text="make the door rustier", project_dir="/tmp/game")

    async def drive() -> None:
        async for _ in bridge.send(prompt):
            pass

    with pytest.raises(ValueError, match="not connected"):
        asyncio.run(drive())


def test_protocol_envelope_round_trips() -> None:
    prompt = Prompt(text="hello", targets=("@Door",), project_dir="/tmp/game")
    env = Envelope(session_id="s1", body=prompt)
    again = Envelope.model_validate_json(env.model_dump_json())
    assert isinstance(again.body, Prompt)
    assert again.body.text == "hello" and again.body.targets == ("@Door",)
