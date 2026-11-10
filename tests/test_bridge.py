"""The bridge is agent-neutral and fails closed when the gate cannot hold."""

import asyncio
from pathlib import Path

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


def test_every_agent_prompt_reads_the_game_brief() -> None:
    from bridge.instructions import GAME_GUIDE
    from bridge.provider_process import INSTRUCTIONS, prompt_text

    # Claude Code gets INSTRUCTIONS as its system prompt; Gemini CLI and opencode get
    # prompt_text; Codex gets the same guide in its developer instructions
    # (tests/test_codex_chat.py).
    text = prompt_text(Prompt(text="hello", project_dir="/tmp/game"))
    for prompt in (INSTRUCTIONS, text):
        assert "Read game_brief and game_setup with art_direction" in prompt
        assert "extend the game through game_setup.extension_points" in prompt
        assert "suggest /feature add, never apply it" in prompt
        assert "Approve these rules" in prompt
    assert "one at a time" in GAME_GUIDE and "never act on" in GAME_GUIDE


def test_art_review_event_carries_the_board_and_offers(tmp_path: Path) -> None:
    from bridge.stdio import art_review_event

    (tmp_path / "art/reference").mkdir(parents=True)
    (tmp_path / "art/reference/quarry.png").write_bytes(b"png")
    (tmp_path / "art/bible.md").write_text(
        "# Art Bible — Quarry\n<!-- joinery-bible: 2 -->\n\n"
        "## 3. References\n### 3a. Reference board\n"
        "| # | Reference | Kind | Take this | Not that | File |\n"
        "|---|---|---|---|---|---|\n"
        "| R1 | my quarry photo | photo | warm stone | the haze | art/reference/quarry.png |\n"
        "| R2 | Hob | game | chunky shapes | the greens | |\n"
    )
    event = art_review_event(tmp_path)
    assert event["kind"] == "art_review" and event["version"] == 2
    assert [r["id"] for r in event["references"]] == ["R1", "R2"]
    assert event["references"][0]["file"] == "art/reference/quarry.png"
    assert event["references"][1]["file"] == ""
    assert event["suggestions"] and all(
        isinstance(s, str) for s in event["suggestions"]
    )


def test_game_suggestions_offer_missing_features_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bridge import stdio

    lines = [
        (
            "The brief says online play, but online play is not added. "
            "Add it with /feature add online-play?"
        ),
        "The brief says 3D, but the starting point is 2D. Which is right?",
    ]
    monkeypatch.setattr(stdio.brief, "consistency", lambda _project, _game: lines)
    event = stdio.game_suggestions(tmp_path)
    assert event == {
        "kind": "suggestions",
        "items": [
            {
                "text": "The brief says online play, but online play is not added.",
                "command": "/feature add online-play",
            }
        ],
    }
